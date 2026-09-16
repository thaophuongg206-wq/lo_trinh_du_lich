import re
import json
import requests
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:1b"       # Model 1B sieu nhe, toi uu rieng cho may tinh chay CPU
OLLAMA_TIMEOUT = 180
OLLAMA_TIMEOUT_SECONDS = 180


class AISuggestRequest(BaseModel):
    region: str
    user_preference: str
    vehicle_type: str = "xe_may"
    start_time: str 
    end_time: str 
    model: Optional[str] = None


class AISuggestResponse(BaseModel):
    advice_text: str
    suggested_ids: List[str]
    invalid_ids: List[str] = []


def build_context_string(points: list) -> str:
    """Format danh sách địa điểm thành chuỗi string để đưa vào prompt."""
    lines = []
    for p in points:
        desc = (p.get("thong_tin_chi_tiet") or p.get("mo_ta") or "").strip()
        if len(desc) > 80:
            desc = desc[:80] + "..."
        lines.append(
            f"- id={p['id']} | {p['ten']} | loại: {p.get('loai_hinh','?')} | mô tả: {desc}"
        )
    return "\n".join(lines)


PROMPT_TEMPLATE = """Bạn là một người bạn đồng hành am hiểu du lịch, thấu hiểu tâm lý và tư vấn có gu.

1. THÔNG TIN CHUYẾN ĐI:
- Quỹ thời gian: {available_minutes} phút.
- Sở thích/Tâm trạng: "{user_preference}"

2. ĐỊNH LƯỢNG ĐIỂM ĐẾN BẮT BUỘC (Dựa trên {available_minutes} phút):
{quantification_rule}

3. KHO DỮ LIỆU ĐỊA ĐIỂM (Chỉ được chọn các id có trong danh sách này):
{context}

4. YÊU CẦU LẬP LUẬN:
- Viết một đoạn văn bản tư vấn (advice_text) có nhịp điệu thời gian tương ứng với các điểm đã chọn.
- Ví dụ: Sáng (hít thở sự trong lành) -> Trưa (hòa vào nhịp sống sôi động) -> Chiều (chữa lành, hoài niệm).
- Giọng văn thân thiện, xưng "mình" và gọi "bạn". Không xưng là "AI" hay "Trợ lý ảo".

5. RÀNG BUỘC ĐẦU RA JSON:
- Trả về DUY NHẤT một object JSON, không dùng markdown.
- Mảng "ids" CHỈ chứa các con số id.

Ví dụ JSON chuẩn:
{{
  "advice_text": "Chào bạn, với {available_minutes} phút, mình đã thiết kế một hành trình trọn vẹn cảm xúc. Buổi sáng, chúng ta sẽ đón không khí trong lành tại [Tên điểm 1]. Khi mặt trời lên cao, hãy cùng nạp năng lượng tại [Tên điểm 2]. Cuối cùng, góc nhỏ tại [Tên điểm 3] sẽ giúp bạn thư giãn.",
  "ids": ["12", "5", "8"]
}}

TRẢ LỜI:"""


def build_prompt(context: str, user_preference: str, available_minutes: int) -> str:
    """Tạo prompt với quy tắc định lượng thời gian và ngữ cảnh dữ liệu."""
    if available_minutes < 180:
        rule = "- Do thời gian ngắn (dưới 3 tiếng), HÃY CHỌN 2 đến 3 địa điểm."
    elif 180 <= available_minutes <= 360:
        rule = "- Thời gian lý tưởng (3 đến 6 tiếng), HÃY CHỌN 4 đến 5 địa điểm."
    else:
        rule = "- Quỹ thời gian dư dả (trên 6 tiếng), HÃY CHỌN 6 đến 8 địa điểm."

    return PROMPT_TEMPLATE.format(
        available_minutes=available_minutes,
        user_preference=user_preference,
        quantification_rule=rule,
        context=context
    )


def call_ai_service(prompt: str, model: str = OLLAMA_MODEL) -> str:
    """Gọi LLM service qua API với constrained decoding (JSON format)."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json", 
                "options": {"temperature": 0.2}, 
            },
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=503, detail=f"Lỗi kết nối service tư vấn: {e}")
    
    return resp.json().get("response", "")


def parse_response(raw: str, valid_ids: set) -> tuple[str, list, list]:
    """Xử lý và chuẩn hóa JSON trả về từ LLM, lọc bỏ ID không hợp lệ."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*|\s*```$", "", cleaned.strip(), flags=re.IGNORECASE)

    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="Dữ liệu trả về sai định dạng, vui lòng thử lại.")
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail="Dữ liệu JSON bị lỗi.")

    # Chuẩn hóa văn bản và làm sạch ID (loại bỏ ký tự thừa như [id...])
    advice_text = str(obj.get("advice_text", "")).replace("AI", "mình") 
    raw_ids = [str(i).replace("id", "").replace("[", "").replace("]", "") for i in obj.get("ids", [])]

    valid_selected = [i for i in raw_ids if i in valid_ids]
    invalid = [i for i in raw_ids if i not in valid_ids]

    if not valid_selected:
        raise HTTPException(status_code=502, detail="Không tìm được điểm đến phù hợp, vui lòng thử lại.")

    return advice_text, valid_selected, invalid


@router.post("/api/ai-suggest", response_model=AISuggestResponse)
def suggest_route(req: AISuggestRequest):
    """Endpoint chính xử lý yêu cầu tư vấn lộ trình từ người dùng."""
    from main import fetch_all_points

    # Tính toán thời gian thực tế
    try:
        fmt = "%H:%M"
        start = datetime.strptime(req.start_time, fmt)
        end = datetime.strptime(req.end_time, fmt)
        minutes = (end - start).total_seconds() / 60
        
        if minutes <= 0: 
            minutes += 24 * 60 
            
        available_minutes = int(minutes)
    except Exception:
        available_minutes = 240 

    # Lấy dữ liệu và giới hạn ngữ cảnh để tối ưu context window
    all_points = fetch_all_points(vehicle_type=req.vehicle_type)
    all_points.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    context_points = all_points[:15] 

    if not context_points:
        raise HTTPException(status_code=404, detail="Không có dữ liệu địa điểm phù hợp.")

    # Xây dựng prompt và gọi service
    context = build_context_string(context_points)
    prompt = build_prompt(context, req.user_preference, available_minutes)
    raw_response = call_ai_service(prompt, model=req.model or OLLAMA_MODEL)

    # Xử lý kết quả trả về
    valid_ids_set = {p["id"] for p in context_points}
    advice_text, suggested_ids, invalid_ids = parse_response(raw_response, valid_ids_set)

    return AISuggestResponse(
        advice_text=advice_text,
        suggested_ids=suggested_ids,
        invalid_ids=invalid_ids,
    )