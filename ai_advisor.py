import re
import json
import math
import requests
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"       # Model 1B sieu nhe, toi uu rieng cho may tinh chay CPU
OLLAMA_TIMEOUT = 180
OLLAMA_TIMEOUT_SECONDS = 180

# ============================================================
# ĐỘNG HOÁ CONTEXT CHO AI (thay cho all_points[:15] cứng)
# ------------------------------------------------------------
# Trước đây context_points = all_points[:15] khiến AI chỉ "nhìn thấy" 15 địa
# điểm đầu tiên trong DB, bất kể tổng số địa điểm là 20 hay 200. Giờ:
#   1) Nếu tổng dữ liệu đủ nhỏ (<= MIN_CANDIDATES) -> truyền toàn bộ.
#   2) Nếu dữ liệu lớn -> xếp hạng candidate theo mức độ liên quan (điểm
#      đánh giá + mức khớp sở thích người dùng), rồi lấy dần cho tới khi
#      chạm ngân sách ký tự ước lượng cho prompt (MAX_CONTEXT_CHARS) hoặc
#      trần an toàn MAX_CANDIDATES — không còn số 15 cố định vô căn cứ.
# Ngân sách ký tự là ước lượng thận trọng cho model nhỏ (1B, context ngắn);
# nếu đổi sang model có context lớn hơn (req.model khác), có thể nới
# MAX_CONTEXT_CHARS/MAX_CANDIDATES mà không cần sửa logic.
# ============================================================
MIN_CANDIDATES = 15      # dưới ngưỡng này thì không cần cắt bớt, đưa hết vào prompt
MAX_CANDIDATES = 60      # trần an toàn để tránh prompt phình quá lớn dù còn ngân sách ký tự
MAX_CONTEXT_CHARS = 6000  # ngân sách ký tự ước lượng dành riêng cho phần liệt kê địa điểm


class AISuggestRequest(BaseModel):
    region: str
    user_preference: str
    vehicle_type: str = "xe_may"
    start_time: str 
    end_time: str 
    model: Optional[str] = None
    session_id: Optional[str] = None   # Nếu có, AI không được gợi ý điểm đã bị loại


class TimelinePlace(BaseModel):
    id: str
    reason: str = ""


class TimelinePeriod(BaseModel):
    period: str
    title: str
    icon: str = ""
    description: str = ""
    places: List[TimelinePlace] = []


class AISuggestResponse(BaseModel):
    advice_text: str
    suggested_ids: List[str]
    invalid_ids: List[str] = []
    summary: str = ""
    timeline: List[TimelinePeriod] = []


class AIRefineRequest(BaseModel):
    """Yêu cầu tiếp nối trên màn hình bản đồ: AI phải hiểu itinerary hiện tại
    (current_ids, đúng thứ tự đang hiển thị) và câu lệnh tự do của người dùng
    (VD: 'Bỏ quán C đi', 'Thêm quán ăn trưa', 'Tôi muốn đi chậm hơn')."""
    region: str = "Hanoi"
    vehicle_type: str = "xe_may"
    start_time: str
    end_time: str
    user_preference: str = ""
    current_ids: List[str]
    instruction: str
    model: Optional[str] = None
    session_id: Optional[str] = None   # Để ghi excluded_ids vào state backend (mục 6)


class AIRefineResponse(BaseModel):
    advice_text: str
    suggested_ids: List[str]
    invalid_ids: List[str] = []
    removed_ids: List[str] = []        # Điểm vừa bị loại trong lượt này
    excluded_ids: List[str] = []       # Toàn bộ ràng buộc loại trừ của phiên
    session_id: Optional[str] = None


# ============================================================
# NHẬN DIỆN Ý ĐỊNH "BỎ ĐIỂM" (mục 6)
# ------------------------------------------------------------
# Chỉ ghi vào excluded_ids khi người dùng THỰC SỰ muốn bỏ điểm. Model 1B chạy
# local thỉnh thoảng trả thiếu id do lỗi parse; nếu cứ thấy danh sách ngắn đi là
# loại vĩnh viễn thì một lần AI lỗi sẽ khoá luôn địa điểm đó khỏi cả phiên.
# Có ý định bỏ  → ghi excluded_ids (ràng buộc cứng, không quay lại).
# Không có      → chỉ cập nhật itinerary lượt này, không khoá gì cả.
# Đường đi chắc chắn nhất vẫn là /api/itinerary/update với remove_ids tường minh.
# ============================================================
_REMOVE_INTENT_KEYWORDS = (
    "bo ", "bot", "xoa", "loai", "khong thich", "khong muon", "thay ",
    "doi ", "huy", "remove", "delete", "drop", "bo di", "chan",
)


def _has_remove_intent(instruction: str) -> bool:
    try:
        from main import _strip_diacritics
        text = _strip_diacritics(instruction or "")
    except Exception:
        text = (instruction or "").lower()
    return any(kw in text for kw in _REMOVE_INTENT_KEYWORDS)


def _line_for_point(p: dict) -> str:
    desc = (p.get("thong_tin_chi_tiet") or p.get("mo_ta") or "").strip()
    if len(desc) > 80:
        desc = desc[:80] + "..."
    return f"- id={p['id']} | {p['ten']} | loại: {p.get('loai_hinh','?')} | mô tả: {desc}"


def build_context_string(points: list) -> str:
    """Format danh sách địa điểm thành chuỗi string để đưa vào prompt."""
    return "\n".join(_line_for_point(p) for p in points)


def select_context_points(all_points: list, user_preference: str) -> list:
    """Chọn tập candidate đưa vào prompt AI, KHÔNG cắt cứng ở con số 15.

    - Xếp hạng theo mức độ liên quan (điểm đánh giá + khớp sở thích), tận
      dụng lại calculate_preference_score/_extract_keywords của main.py để
      không viết trùng logic chuẩn hoá tiếng Việt.
    - Lấy dần theo ngân sách ký tự ước lượng cho prompt, đảm bảo AI luôn có
      tối thiểu MIN_CANDIDATES lựa chọn (nếu DB có đủ) để lịch trình đa dạng.
    """
    if not all_points:
        return []

    try:
        from main import _extract_keywords, calculate_preference_score
        keywords = _extract_keywords(user_preference)

        def relevance(p):
            base = (p.get("score") or 0) / 10.0
            pref = calculate_preference_score(p, keywords)
            return base * 0.5 + pref * 0.5

        ranked = sorted(all_points, key=relevance, reverse=True)
    except Exception:
        # Fallback an toàn nếu import lỗi vì lý do nào đó: vẫn ưu tiên theo score,
        # không để cả tính năng AI sập chỉ vì bước xếp hạng nâng cao thất bại.
        ranked = sorted(all_points, key=lambda x: (x.get("score") or 0), reverse=True)

    if len(ranked) <= MIN_CANDIDATES:
        return ranked

    selected = []
    total_chars = 0
    for p in ranked:
        line_len = len(_line_for_point(p)) + 1
        if len(selected) >= MIN_CANDIDATES and (
            total_chars + line_len > MAX_CONTEXT_CHARS or len(selected) >= MAX_CANDIDATES
        ):
            break
        selected.append(p)
        total_chars += line_len
    return selected


PROMPT_TEMPLATE = """Bạn là một người bạn địa phương am hiểu sâu sắc từng con phố, quán xá Hà Nội, thấu hiểu tâm lý và tư vấn có gu. Bạn đang trò chuyện thân tình với một người bạn thân về chuyến đi sắp tới.

1. THÔNG TIN CHUYẾN ĐI:
- Quỹ thời gian: {available_minutes} phút.
- Sở thích / Tâm trạng: "{user_preference}"

2. ĐỊNH LƯỢNG ĐIỂM ĐẾN BẮT BUỘC (Dựa trên {available_minutes} phút):
{quantification_rule}

3. KHO DỮ LIỆU ĐỊA ĐIỂM (Chỉ được chọn các id có trong danh sách này):
{context}

4. NGUYÊN TẮC KỂ CHUYỆN (RẤT QUAN TRỌNG):
- Giọng văn: Giống một người bạn địa phương đang hào hứng rủ rê, tâm sự chân thành, xưng "mình" và gọi "bạn". Tuyệt đối không xưng "AI", "hệ thống", hay "trợ lý ảo".
- Thứ tự ưu tiên: Trải nghiệm (Experience) > Cảm xúc (Emotion) > Bầu không khí (Atmosphere) > Câu chuyện liền mạch (Story) > Liệt kê địa danh (Places).
- Mô tả bầu không khí & nhịp điệu: Khắc họa cảm giác về ánh sáng, mùi hương, nhịp sống (ví dụ: sớm mai trong lành, trưa thảnh thơi trốn nắng, chiều tà lộng gió...).
- Tính liền mạch & bước chuyển tự nhiên: Giải thích tự nhiên vì sao điểm tiếp theo lại là lựa chọn tiếp nối hoàn hảo (ví dụ: sau khi dạo bộ mỏi chân thì ghé quán cafe yên tĩnh đón gió...).
- TUYỆT ĐỐI TRÁNH:
  + Không dùng văn phong máy móc kiểu mẫu: "Chúng ta bắt đầu...", "Sau một buổi sáng...", "Cuối ngày...", "Điểm 1 là...".
  + Không lặp lại cùng một cấu trúc câu, tránh lối hành văn gượng gạo, hành chính.
  + Không nhồi nhét tên địa điểm liên tục; không biến đoạn văn thành danh sách gạch đầu dòng.
  + TUYỆT ĐỐI KHÔNG đưa ID nội bộ (như (id=27), [id: 6], ID=12...) vào bất kỳ đoạn văn nào (advice_text, description, reason). ID CHỈ ĐƯỢC PHÉP nằm trong trường "id" của JSON!

5. RÀNG BUỘC ĐẦU RA JSON — TRẢ VỀ DUY NHẤT MỘT OBJECT JSON SAU, KHÔNG DÙNG MARKDOWN:
{{
  "advice_text": "Đoạn văn hoàn chỉnh, truyền cảm hứng, kể câu chuyện liền mạch cho cả hành trình như một bức tranh trải nghiệm sống động (khoảng 3-4 câu tinh tế, không chứa bất kỳ ID nào).",
  "timeline": [
    {{
      "period": "morning",
      "title": "Buổi sáng",
      "icon": "sunrise",
      "description": "Cảm giác không khí, ánh sáng và trải nghiệm mở đầu ngày mới",
      "places": [
        {{
          "id": "12",
          "reason": "Cảm xúc và trải nghiệm lý tưởng tại điểm này"
        }}
      ]
    }},
    {{
      "period": "noon",
      "title": "Buổi trưa",
      "icon": "sun",
      "description": "Khoảng dừng thư thả nạp năng lượng và tránh nắng",
      "places": [
        {{
          "id": "15",
          "reason": "Không gian dễ chịu, ẩm thực hợp khẩu vị"
        }}
      ]
    }},
    {{
      "period": "afternoon",
      "title": "Buổi chiều",
      "icon": "sunset",
      "description": "Nhịp điệu khi chiều buông, không gian dạo bước thảnh thơi đón hoàng hôn",
      "places": [
        {{
          "id": "20",
          "reason": "Thời điểm đẹp nhất trong ngày để ngắm cảnh"
        }}
      ]
    }}
  ]
}}

LƯU Ý: Chỉ chia các mốc thời gian thực sự phù hợp với quỹ thời gian {available_minutes} phút (nếu dưới 3 tiếng chỉ cần 1-2 mốc). Các giá trị 'period' hợp lệ gồm: 'morning', 'noon', 'afternoon', 'evening', 'night'.

TRẢ LỜI:"""


REFINE_PROMPT_TEMPLATE = """Bạn là một người bạn địa phương am hiểu du lịch, đang trò chuyện tiếp nối với bạn mình về một lịch trình đã có sẵn (không phải tạo mới từ đầu).

1. LỊCH TRÌNH HIỆN TẠI (đúng thứ tự đang đi):
{current_itinerary}

2. YÊU CẦU MỚI CỦA BẠN:
"{instruction}"

3. KHO DỮ LIỆU ĐỊA ĐIỂM CÓ THỂ CHỌN THÊM (chỉ được chọn id trong danh sách này):
{context}

4. QUY TẮC BẮT BUỘC:
- Hiểu "bỏ X" nghĩa là loại id của X khỏi danh sách kết quả.
- Hiểu "thêm quán ăn trưa/cà phê/..." nghĩa là tìm 1 địa điểm phù hợp (đúng loại
  hình được yêu cầu) trong kho dữ liệu và thêm id đó vào danh sách kết quả, ở vị
  trí hợp lý theo mạch thời gian.
- "Đi chậm hơn"/"thêm thời gian nghỉ" nghĩa là NÊN bớt bớt số điểm để lịch trình
  thong thả hơn, không nhất thiết phải thêm điểm mới.
- Danh sách "ids" trả về là TOÀN BỘ tập id sau khi đã áp dụng thay đổi (không
  chỉ phần thay đổi), giữ nguyên các id không bị ảnh hưởng.
- advice_text là 1-2 câu ngắn gọn, ấm áp, giọng thân thiện (xưng "mình" gọi "bạn"), giải thích thay đổi một cách tự nhiên.
- TUYỆT ĐỐI KHÔNG đưa ID nội bộ (như id=12, (id=5)...) vào advice_text.

5. RÀNG BUỘC ĐẦU RA JSON — TRẢ VỀ DUY NHẤT MỘT OBJECT JSON, KHÔNG DÙNG MARKDOWN:
{{
  "advice_text": "...",
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


def build_refine_prompt(context: str, current_points: list, instruction: str) -> str:
    itinerary_lines = "\n".join(
        f"- id={p['id']} | {p['ten']} | loại: {p.get('loai_hinh','?')}" for p in current_points
    )
    return REFINE_PROMPT_TEMPLATE.format(
        current_itinerary=itinerary_lines or "(chưa có điểm nào)",
        instruction=instruction,
        context=context,
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


def _load_json_object(raw: str) -> dict:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(json)?\s*|\s*```$", "", cleaned.strip(), flags=re.IGNORECASE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if not match:
            raise HTTPException(status_code=502, detail="Dữ liệu trả về sai định dạng, vui lòng thử lại.")
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            raise HTTPException(status_code=502, detail="Dữ liệu JSON bị lỗi.")


def sanitize_ai_text(text: str) -> str:
    """Làm sạch các ID nội bộ rác do AI sinh ra (VD: (id=27), [id: 6], ID 28...),
    nhưng tuyệt đối KHÔNG xóa nhầm giờ (09:00), rating (4.8), cự ly (3.7 km),
    thời lượng (1h30, 45 phút) hay các con số tự nhiên trong văn bản."""
    if not text:
        return ""
    # 1. Dạng bọc ngoặc: (id=27), [id: 6], (ID 12), {id=5}, (mã: 3), (mã số: 10), (id 27), v.v.
    text = re.sub(r'(?i)\s*[\(\[\{]\s*(?:id|mã|ma|mã\s*số|ma\s*so)\s*[:=\s#]?\s*\d+\s*[\)\]\}]', '', text)
    # 2. Dạng không bọc ngoặc có tiền tố: id=27, id: 6, ID=10, id 27, ID 27, mã: 5, mã số 10
    text = re.sub(r'(?i)\b(?:id|mã|ma|mã\s*số|ma\s*so)\s*[:=#\s]\s*\d+\b', '', text)
    # 3. Dọn dẹp ngoặc rỗng nếu sót lại: () [] {}
    text = re.sub(r'[\(\[\{]\s*[\)\]\}]', '', text)
    # 4. Làm sạch khoảng trắng thừa và dấu câu sát nhau
    text = re.sub(r' {2,}', ' ', text)
    text = re.sub(r'\s+([,.\?!;])', r'\1', text)
    return text.strip()


def _clean_id(raw_id) -> str:
    return str(raw_id).replace("id", "").replace("[", "").replace("]", "").replace("=", "").strip()


def generate_fallback_suggestion(context_points: list, available_minutes: int, user_preference: str = "") -> tuple[str, list, list, str, list]:
    """Tạo phương án gợi ý dự phòng an toàn, chất lượng cao khi AI model bị lỗi,
    timeout hoặc trả về JSON không hợp lệ. Đảm bảo API KHÔNG BAO GIỜ bị sập."""
    if not context_points:
        return "Mình rất tiếc chưa tìm được địa điểm phù hợp trong khu vực này.", [], [], "", []

    # Chọn số lượng điểm theo quỹ thời gian
    if available_minutes < 180:
        target_count = min(3, len(context_points))
    elif available_minutes <= 360:
        target_count = min(5, len(context_points))
    else:
        target_count = min(7, len(context_points))
    target_count = max(2, target_count)

    chosen = context_points[:target_count]
    suggested_ids = [p["id"] for p in chosen]

    # Phân bổ các điểm vào các mốc thời gian
    timeline = []
    periods_def = [
        ("morning", "Buổi sáng", "sunrise", "Khởi đầu ngày mới với bầu không khí trong lành, dạo bước qua những con phố tĩnh lặng và cảm nhận nhịp sống chậm."),
        ("noon", "Buổi trưa", "sun", "Khoảng nghỉ trưa thư thái, thưởng thức hương vị ẩm thực đặc trưng và trốn cái oi ả trong không gian bình yên."),
        ("afternoon", "Buổi chiều", "sunset", "Chiều tà lộng gió, thời điểm đẹp nhất để ngắm hoàng hôn buông và thả hồn theo những góc phố nên thơ."),
        ("evening", "Buổi tối", "moon", "Khi phố lên đèn rực rỡ, tận hưởng nhịp sống đêm sôi động và kết lại một ngày thật nhiều xúc cảm."),
    ]

    # Chia đều các điểm vào các mốc thời gian phù hợp
    n_periods = min(len(periods_def), max(1, (target_count + 1) // 2))
    slice_size = max(1, math.ceil(len(chosen) / n_periods))

    for p_idx in range(n_periods):
        sub_places = chosen[p_idx * slice_size : (p_idx + 1) * slice_size]
        if not sub_places:
            continue
        p_code, p_title, p_icon, p_desc = periods_def[p_idx]
        places_data = []
        for pl in sub_places:
            lh = pl.get("loai_hinh", "điểm đến").lower()
            ten = pl.get("ten", "")
            places_data.append({
                "id": pl["id"],
                "reason": f"Không gian {lh} đặc trưng tại {ten}, rất thích hợp để thư giãn và cảm nhận trọn vẹn nhịp sống Hà Nội."
            })
        timeline.append({
            "period": p_code,
            "title": p_title,
            "icon": p_icon,
            "description": p_desc,
            "places": places_data,
        })

    advice_text = (
        "Mình đã sắp xếp cho bạn một hành trình thật nhiều cảm xúc và hài hòa. "
        "Từng điểm dừng chân đều được kết nối liền mạch, vừa đủ để bạn thong thả thưởng thức trọn vẹn cảnh sắc và hương vị nơi đây mà không lo vội vã."
    )
    summary = f"Gợi ý hành trình thảnh thơi thiết kế riêng cho quỹ thời gian {available_minutes} phút của bạn."

    return advice_text, suggested_ids, [], summary, timeline


def parse_structured_response(raw: str, valid_ids: set, context_points: list = None, available_minutes: int = 240, user_preference: str = "") -> tuple[str, list, list, str, list]:
    """Parse output JSON có cấu trúc timeline. Validate chặt chẽ:
    - JSON hợp lệ
    - timeline là array
    - ID địa điểm hợp lệ
    - sanitize sạch sẽ ID rác trong advice_text, description, reason...
    Nếu AI trả sai định dạng hoặc không có ID hợp lệ, tự động kích hoạt fallback an toàn,
    không làm sập hệ thống."""
    try:
        obj = _load_json_object(raw)
    except Exception:
        # Fallback an toàn khi AI không trả JSON hợp lệ
        return generate_fallback_suggestion(context_points or [], available_minutes, user_preference)

    raw_advice = str(obj.get("advice_text", "")).strip().replace("AI", "mình")
    summary = sanitize_ai_text(str(obj.get("summary", "")).strip().replace("AI", "mình"))
    raw_timeline = obj.get("timeline")
    timeline = []
    ordered_ids = []
    invalid_ids = []

    if isinstance(raw_timeline, list):
        for period_obj in raw_timeline:
            if not isinstance(period_obj, dict):
                continue
            places_raw = period_obj.get("places") or []
            places = []
            if isinstance(places_raw, list):
                for pl in places_raw:
                    if isinstance(pl, dict):
                        pid = _clean_id(pl.get("id", ""))
                        reason = sanitize_ai_text(str(pl.get("reason", "")).strip())
                    else:
                        pid = _clean_id(pl)
                        reason = ""
                    if not pid:
                        continue
                    if pid in valid_ids:
                        places.append({"id": pid, "reason": reason})
                        ordered_ids.append(pid)
                    else:
                        invalid_ids.append(pid)

            if not places:
                continue

            period_desc = sanitize_ai_text(str(period_obj.get("description", "")).strip())
            period_title = sanitize_ai_text(str(period_obj.get("title", "")).strip()) or "Điểm dừng chân"
            timeline.append({
                "period": str(period_obj.get("period", "")).strip() or "other",
                "title": period_title,
                "icon": str(period_obj.get("icon", "")).strip() or "location-dot",
                "description": period_desc,
                "places": places,
            })

    # advice_text: ưu tiên trường advice_text trực tiếp từ JSON, nếu trống thì ghép từ summary + timeline
    if raw_advice:
        advice_text = sanitize_ai_text(raw_advice)
    else:
        advice_parts = [summary] if summary else []
        for period in timeline:
            if period["description"]:
                advice_parts.append(period["description"])
        advice_text = sanitize_ai_text("\n\n".join(advice_parts).strip())

    # Loại id trùng lặp nhưng giữ đúng thứ tự xuất hiện đầu tiên
    seen = set()
    suggested_ids = []
    for pid in ordered_ids:
        if pid not in seen:
            seen.add(pid)
            suggested_ids.append(pid)

    # Nếu AI không chọn được ID hợp lệ nào -> fallback an toàn
    if not suggested_ids:
        return generate_fallback_suggestion(context_points or [], available_minutes, user_preference)

    return advice_text, suggested_ids, invalid_ids, summary, timeline


def parse_refine_response(raw: str, valid_ids: set, current_ids: list = None) -> tuple[str, list, list]:
    """Parse output JSON đơn giản {advice_text, ids} cho luồng tiếp nối trên map.
    Validate và sanitize sạch sẽ ID rác trong advice_text."""
    try:
        obj = _load_json_object(raw)
        raw_text = str(obj.get("advice_text", "")).strip().replace("AI", "mình")
        advice_text = sanitize_ai_text(raw_text)
        raw_ids = [_clean_id(i) for i in obj.get("ids", [])]
        valid_selected = [i for i in raw_ids if i and i in valid_ids]
        invalid = [i for i in raw_ids if i and i not in valid_ids]
    except Exception:
        valid_selected = []
        invalid = []
        advice_text = ""

    # Fallback an toàn nếu AI trả rỗng hoặc không có ID hợp lệ
    if not valid_selected:
        valid_selected = [i for i in (current_ids or []) if i in valid_ids]
        advice_text = advice_text or "Mình đã ghi nhận mong muốn của bạn và cập nhật lại lịch trình cho phù hợp nhất."

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

    # Lấy dữ liệu và chọn candidate theo mức độ liên quan
    all_points = fetch_all_points(vehicle_type=req.vehicle_type)

    # Tôn trọng ràng buộc loại trừ của phiên
    from itinerary_store import store
    session = store.get(req.session_id)
    if session is not None and session.excluded_ids:
        all_points = [p for p in all_points if p["id"] not in session.excluded_ids]

    if not all_points:
        raise HTTPException(status_code=404, detail="Không có dữ liệu địa điểm phù hợp.")

    context_points = select_context_points(all_points, req.user_preference)
    valid_ids_set = {p["id"] for p in context_points}

    # Xây dựng prompt và gọi service với fallback an toàn
    try:
        context = build_context_string(context_points)
        prompt = build_prompt(context, req.user_preference, available_minutes)
        raw_response = call_ai_service(prompt, model=req.model or OLLAMA_MODEL)
        advice_text, suggested_ids, invalid_ids, summary, timeline = parse_structured_response(
            raw_response, valid_ids_set, context_points, available_minutes, req.user_preference
        )
    except Exception:
        # Fallback an toàn tuyệt đối khi service AI gặp sự cố
        advice_text, suggested_ids, invalid_ids, summary, timeline = generate_fallback_suggestion(
            context_points, available_minutes, req.user_preference
        )

    return AISuggestResponse(
        advice_text=advice_text,
        suggested_ids=suggested_ids,
        invalid_ids=invalid_ids,
        summary=summary,
        timeline=timeline,
    )


@router.post("/api/ai-refine", response_model=AIRefineResponse)
def refine_route(req: AIRefineRequest):
    """Tiếp nối hội thoại trên màn hình bản đồ: AI nhận itinerary hiện tại +
    câu lệnh tự do của người dùng ('Bỏ C đi', 'Thêm quán ăn trưa'...), trả về
    tập id đã cập nhật. Frontend gọi lại /api/optimize-route với
    ai_selected_ids = suggested_ids để tính lại route/timeline/map."""
    from main import fetch_all_points
    from itinerary_store import store

    try:
        fmt = "%H:%M"
        start = datetime.strptime(req.start_time, fmt)
        end = datetime.strptime(req.end_time, fmt)
        minutes = (end - start).total_seconds() / 60
        if minutes <= 0:
            minutes += 24 * 60
    except Exception:
        pass

    session = store.get(req.session_id)
    excluded = set(session.excluded_ids) if session else set()

    all_points = fetch_all_points(vehicle_type=req.vehicle_type)
    if not all_points:
        raise HTTPException(status_code=404, detail="Không có dữ liệu địa điểm phù hợp.")

    if excluded:
        all_points = [p for p in all_points if p["id"] not in excluded]

    id_to_point = {p["id"]: p for p in all_points}
    current_points = [id_to_point[i] for i in req.current_ids if i in id_to_point]

    ranked_context = select_context_points(all_points, req.user_preference)
    context_points = list(current_points)
    context_ids = {p["id"] for p in context_points}
    for p in ranked_context:
        if p["id"] not in context_ids:
            context_points.append(p)
            context_ids.add(p["id"])

    valid_ids_set = {p["id"] for p in context_points}

    try:
        context = build_context_string(context_points)
        prompt = build_refine_prompt(context, current_points, req.instruction)
        raw_response = call_ai_service(prompt, model=req.model or OLLAMA_MODEL)
        advice_text, suggested_ids, invalid_ids = parse_refine_response(raw_response, valid_ids_set, req.current_ids)
    except Exception:
        # Fallback an toàn khi service AI lỗi
        advice_text = "Mình đã ghi nhận mong muốn của bạn và cập nhật lại hành trình."
        suggested_ids = [i for i in req.current_ids if i in valid_ids_set]
        invalid_ids = []

    # Chốt chặn cuối: id đã bị loại không được lọt ra ngoài
    suggested_ids = [i for i in suggested_ids if i not in excluded]

    # Ghi ý định bỏ điểm vào state backend
    removed_ids = []
    if session is not None:
        dropped = [i for i in req.current_ids if i not in suggested_ids and i not in excluded]
        if dropped and _has_remove_intent(req.instruction):
            removed_ids = session.exclude(dropped)
        session.pin(suggested_ids)
        excluded = set(session.excluded_ids)

    return AIRefineResponse(
        advice_text=advice_text,
        suggested_ids=suggested_ids,
        invalid_ids=invalid_ids,
        removed_ids=removed_ids,
        excluded_ids=sorted(excluded),
        session_id=req.session_id,
    )