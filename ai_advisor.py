"""
ai_advisor.py — Task 2.2: Prompt Engineering & API (Ollama + RAG)

Nhiệm vụ module này:
  1. Đọc context từ DB (RAG thô, không cần vector DB — dataset nhỏ theo từng region).
  2. Ép AI (Ollama local LLM) nhận diện câu text người dùng, trả về ĐÚNG 2 phần:
       (a) advice_text  — văn bản tư vấn tự nhiên ("Trợ lý ảo tư vấn")
       (b) selected_ids — mảng ID điểm đến (để feed thẳng vào two_opt_algorithm/
                           solve_tsptw_exact ở main.py, KHÔNG qua bước scorer nữa)
  3. Validate: lọc bỏ ID ảo giác (hallucination) — LLM có thể bịa ID không tồn tại
     trong DB, nếu không lọc thì bước sau (main.py) sẽ crash khi tra cứu.

Cách tích hợp vào main.py (chỉ cần 2 dòng ở cuối main.py):
    from ai_advisor import router as ai_router
    app.include_router(ai_router)
"""

import re
import json
import requests
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"          # đổi tuỳ model đã pull (qwen2.5, mistral, ...)
OLLAMA_TIMEOUT_SECONDS = 120


# ============================================================
# 1. REQUEST / RESPONSE MODELS
# ============================================================

class AISuggestRequest(BaseModel):
    region: str
    user_preference: str
    vehicle_type: str = "xe_may"
    model: Optional[str] = None    # cho phép override model nếu muốn thử nghiệm


class AISuggestResponse(BaseModel):
    advice_text: str
    suggested_ids: List[str]
    invalid_ids: List[str] = []    # ID bị AI "ảo giác" ra, đã lọc bỏ — trả kèm để debug/log,
                                    # KHÔNG dùng để hiển thị cho người dùng cuối


# ============================================================
# 2. XÂY DỰNG RAG CONTEXT
# ============================================================

def build_rag_context(points: list) -> str:
    """
    Format gọn từng điểm thành 1 dòng để nhét vào prompt. Giới hạn độ dài mô tả
    (120 ký tự) để tránh prompt quá dài khi region có nhiều điểm — nếu sau này
    một region có hàng trăm điểm, ĐÂY LÀ CHỖ CẦN NÂNG CẤP lên vector search/rerank
    thay vì nhét hết vào context (xem mục "cần tìm hiểu thêm" ở cuối file).
    """
    lines = []
    for p in points:
        desc = (p.get("thong_tin_chi_tiet") or p.get("mo_ta") or "").strip()
        if len(desc) > 120:
            desc = desc[:120] + "..."
        lines.append(
            f"- id={p['id']} | {p['ten']} | loại: {p.get('loai_hinh','?')} | "
            f"mở cửa: {p['open_time']}-{p['close_time']} | mô tả: {desc}"
        )
    return "\n".join(lines)


# ============================================================
# 3. PROMPT — dùng JSON MODE của Ollama thay vì delimiter thô
# ============================================================
# So với cách dùng "===TU_VAN===...===DIA_DIEM_ID===" (dễ vỡ nếu model thêm
# lời chào/markdown trước/sau), ép model trả về MỘT object JSON duy nhất và
# bật "format": "json" trong request tới Ollama sẽ đáng tin cậy hơn nhiều —
# Ollama dùng constrained decoding nên model KHÔNG THỂ trả ra thứ không phải
# JSON hợp lệ, giảm hẳn lỗi parse.

PROMPT_TEMPLATE = """Bạn là trợ lý tư vấn du lịch Hà Nội, nói chuyện tự nhiên, thân thiện.

Danh sách địa điểm khả dụng (chỉ được chọn ID trong danh sách này, KHÔNG được bịa ID mới):
{context}

Yêu cầu của người dùng: "{user_preference}"

Hãy trả lời bằng một object JSON DUY NHẤT theo đúng schema sau, không thêm chữ nào khác
ngoài object JSON:
{{
  "advice_text": "đoạn văn tự nhiên giải thích vì sao bạn chọn các địa điểm này, giọng văn tư vấn viên chứ không phải máy tính liệt kê",
  "ids": ["id1", "id2", "id3"]
}}
"""


def _build_prompt(context: str, user_preference: str) -> str:
    return PROMPT_TEMPLATE.format(context=context, user_preference=user_preference)


# ============================================================
# 4. GỌI OLLAMA
# ============================================================

def call_ollama(prompt: str, model: str = OLLAMA_MODEL) -> str:
    """
    Gọi Ollama ở chế độ JSON constrained decoding ("format": "json").
    Ném HTTPException(503) nếu Ollama không chạy/không phản hồi — KHÔNG được để
    lỗi kết nối làm sập cả request, vì đây là service phụ trợ, Frontend cần biết
    rõ "AI đang lỗi" để có thể fallback sang chọn điểm thủ công.
    """
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": prompt,
                "stream": False,
              #  "format": "json",       # ép Ollama chỉ sinh JSON hợp lệ
                "options": {"temperature": 0.3},   # giảm temperature để bớt "sáng tạo" ra ID lạ
            },
            timeout=OLLAMA_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Không kết nối được tới Ollama. Kiểm tra đã chạy `ollama serve` chưa.")
    except requests.exceptions.Timeout:
        raise HTTPException(status_code=504, detail=f"Ollama phản hồi quá {OLLAMA_TIMEOUT_SECONDS}s, thử lại sau.")
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Lỗi gọi Ollama: {e}")

    data = resp.json()
    return data.get("response", "")


# ============================================================
# 5. PARSE + VALIDATE (chống ID ảo giác)
# ============================================================

def parse_ai_response(raw: str, valid_ids: set) -> tuple[str, list, list]:
    """
    Trả về (advice_text, valid_selected_ids, invalid_ids_bi_loc_bo).
    Vẫn cố gắng parse ngay cả khi model lỡ in kèm text thừa trước/sau JSON
    (một số model dù bật format=json vẫn thỉnh thoảng thêm ```json ... ``` bao ngoài).
    """
    cleaned = raw.strip()
    # Gỡ markdown code fence nếu có, dù đã bật format=json (phòng hờ model cũ/khác)
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*|\s*```$", "", cleaned.strip(), flags=re.IGNORECASE)

    try:
        obj = json.loads(cleaned)
    except json.JSONDecodeError:
        # Cố gắng cứu vãn: tìm object JSON đầu tiên trong chuỗi bằng regex thô
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="AI trả về không phải JSON hợp lệ, vui lòng thử lại.")
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail="AI trả về JSON hỏng, vui lòng thử lại.")

    advice_text = str(obj.get("advice_text", "")).strip()
    raw_ids = obj.get("ids", [])
    if not isinstance(raw_ids, list):
        raw_ids = []
    raw_ids = [str(i) for i in raw_ids]

    # BẮT BUỘC: lọc ID ảo giác — nếu không, bước sau (main.py) sẽ KeyError khi
    # tra id_to_point / global_matrix với ID không tồn tại trong DB.
    valid_selected = [i for i in raw_ids if i in valid_ids]
    invalid = [i for i in raw_ids if i not in valid_ids]

    if not advice_text:
        advice_text = "Mình đã chọn một vài địa điểm phù hợp với yêu cầu của bạn."
    if not valid_selected:
        raise HTTPException(status_code=502, detail="AI không chọn được địa điểm hợp lệ nào từ danh sách đã cung cấp.")

    return advice_text, valid_selected, invalid


# ============================================================
# 6. ENDPOINT
# ============================================================

@router.post("/api/ai-suggest", response_model=AISuggestResponse)
def ai_suggest(req: AISuggestRequest):
    """
    Chuyển đổi "máy tính tính toán khô khan" sang "trợ lý ảo tư vấn":
    nhận text tự nhiên của người dùng, trả về (1) lời tư vấn tự nhiên và
    (2) mảng ID điểm đến đã được validate — để Frontend truyền tiếp sang
    /api/optimize-route (field ai_selected_ids) cho bước sắp xếp thứ tự.
    """
    # Import trễ (lazy import) để tránh vòng lặp import với main.py khi
    # ai_advisor được import ngược lại từ main.py.
    from main import fetch_all_points

    all_points = fetch_all_points(vehicle_type=req.vehicle_type)[:5]
    if not all_points:
        raise HTTPException(status_code=404, detail="Không có địa điểm nào khả dụng cho phương tiện này.")

    context = build_rag_context(all_points)
    prompt = _build_prompt(context, req.user_preference)
    raw = call_ollama(prompt, model=req.model or OLLAMA_MODEL)

    valid_ids = {p["id"] for p in all_points}
    advice_text, suggested_ids, invalid_ids = parse_ai_response(raw, valid_ids)

    return AISuggestResponse(
        advice_text=advice_text,
        suggested_ids=suggested_ids,
        invalid_ids=invalid_ids,
    )
