import re
import json
import math
import requests
from datetime import datetime
from typing import List, Optional, Tuple, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2:1b"       # Model 1B sieu nhe, toi uu rieng cho may tinh chay CPU
OLLAMA_TIMEOUT = 12
OLLAMA_TIMEOUT_SECONDS = 12

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
MIN_CANDIDATES = 6       # dưới ngưỡng này thì không cần cắt bớt, đưa hết vào prompt
MAX_CANDIDATES = 10      # trần an toàn để prompt gọn nhẹ, AI sinh phản hồi nhanh trong vài giây
MAX_CONTEXT_CHARS = 1500  # ngân sách ký tự ước lượng tối ưu cho phản hồi nhanh


class AISuggestRequest(BaseModel):
    region: str
    user_preference: str
    vehicle_type: str = "xe_may"
    start_time: str 
    end_time: str 
    model: Optional[str] = None
    session_id: Optional[str] = None   # Nếu có, AI không được gợi ý điểm đã bị loại
    trip_date: str = ""                # Ngày đi (YYYY-MM-DD), tùy chọn, làm bối cảnh cho prompt
    start_point: str = ""              # Tên điểm xuất phát, tùy chọn, làm bối cảnh cho prompt


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


class AIRefineOption(BaseModel):
    place_id: str
    ten: str
    loai_hinh: str = ""
    rating: Optional[float] = None
    so_luot_danh_gia: Optional[int] = None
    dia_chi: str = ""
    url_hinh_anh: str = ""
    distance_km: Optional[float] = None
    reason: str = ""
    action: str = "replace"     # "replace" | "add"
    tags: List[str] = []


class AIRefineRequest(BaseModel):
    """Yêu cầu tiếp nối trên màn hình bản đồ: AI phải hiểu itinerary hiện tại
    (current_ids, đúng thứ tự đang hiển thị) và câu lệnh tự do của người dùng
    (VD: 'Bỏ quán C đi', 'Thêm quán ăn trưa', 'Tôi muốn đi chậm hơn')."""
    region: str = "Hanoi"
    vehicle_type: str = "xe_may"
    start_time: str = "08:00"
    end_time: str = "17:00"
    user_preference: str = ""
    current_ids: List[str]
    instruction: str
    model: Optional[str] = None
    session_id: Optional[str] = None   # Để ghi excluded_ids vào state backend (mục 6)
    trip_date: str = ""                # Ngày đi, tùy chọn, làm bối cảnh cho prompt
    start_point: str = ""              # Tên điểm xuất phát, tùy chọn, làm bối cảnh cho prompt
    target_place_id: Optional[str] = None  # ID điểm muốn thay thế hoặc tham chiếu gần đây


class AIRefineResponse(BaseModel):
    advice_text: str
    suggested_ids: List[str]
    invalid_ids: List[str] = []
    removed_ids: List[str] = []        # Điểm vừa bị loại trong lượt này
    excluded_ids: List[str] = []       # Toàn bộ ràng buộc loại trừ của phiên
    session_id: Optional[str] = None
    intent: str = "options"            # "options" | "direct" | "select_previous" | "chat"
    target_place_id: Optional[str] = None
    target_place_name: Optional[str] = None
    options: List[AIRefineOption] = []
    chat_history: List[dict] = []


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
        from api.preferences import _strip_diacritics
        text = _strip_diacritics(instruction or "")
    except Exception:
        text = (instruction or "").lower()
    return any(kw in text for kw in _REMOVE_INTENT_KEYWORDS)


def _line_for_point(p: dict) -> str:
    desc = (p.get("thong_tin_chi_tiet") or p.get("mo_ta") or "").strip()
    if len(desc) > 80:
        desc = desc[:80] + "..."
    reviews = p.get("serpapi_reviews") or []
    rev_snippet = ""
    if reviews:
        clean_rev = reviews[0].replace("\n", " ").strip()
        if len(clean_rev) > 90:
            clean_rev = clean_rev[:90] + "..."
        rev_snippet = f" | Review thực tế: {clean_rev}"
    elif p.get("review"):
        clean_rev = p.get("review", "").replace("\n", " ").strip()
        if len(clean_rev) > 70:
            clean_rev = clean_rev[:70] + "..."
        rev_snippet = f" | Review: {clean_rev}"
    return f"- id={p['id']} | {p['ten']} | loại: {p.get('loai_hinh','?')} | mô tả: {desc}{rev_snippet}"


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
        from api.preferences import _extract_keywords, calculate_preference_score
        from services.parser import _extract_tags, _normalize_typos, _low, _strip
        norm_pref = _normalize_typos(user_preference or "")
        liked, _ = _extract_tags(_low(norm_pref), _strip(norm_pref))
        keywords = _extract_keywords(user_preference)

        def relevance(p):
            base = (p.get("score") or 0) / 5.0
            pref = calculate_preference_score(p, keywords, preferred_tags=liked)
            if (keywords or liked) and pref > 0.0:
                return base * 0.2 + pref * 0.8
            return base * 0.5 + pref * 0.5

        ranked = sorted(all_points, key=relevance, reverse=True)
    except Exception:
        # Fallback an toàn nếu import lỗi vì lý do nào đó: vẫn ưu tiên theo score,
        # không để cả tính năng AI sập chỉ vì bước xếp hạng nâng cao thất bại.
        ranked = sorted(all_points, key=lambda x: (x.get("score") or 0), reverse=True)

    if len(ranked) <= MIN_CANDIDATES:
        return ranked

    from categories import category_of
    selected = []
    total_chars = 0
    cat_counts = {}
    for p in ranked:
        p_cat = category_of(p)
        # Giới hạn TTTM/rạp phim tối đa 2 điểm trong context để nhường chỗ cho cafe/ẩm thực/di tích
        if p_cat == "tttm" and cat_counts.get("tttm", 0) >= 2:
            continue
        line_len = len(_line_for_point(p)) + 1
        if len(selected) >= MIN_CANDIDATES and (
            total_chars + line_len > MAX_CONTEXT_CHARS or len(selected) >= MAX_CANDIDATES
        ):
            break
        selected.append(p)
        cat_counts[p_cat] = cat_counts.get(p_cat, 0) + 1
        total_chars += line_len
    return selected


PROMPT_TEMPLATE = """Bạn là một người bạn địa phương am hiểu sâu sắc từng con phố, quán xá Hà Nội, thấu hiểu tâm lý và tư vấn có gu. Bạn đang trò chuyện thân tình với một người bạn thân về chuyến đi sắp tới.

1. THÔNG TIN CHUYẾN ĐI:
- Quỹ thời gian: {available_minutes} phút.
{trip_context}
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
  + TUYỆT ĐỐI KHÔNG chọn 2 rạp chiếu phim hay 2 TTTM trong cùng một ngày (chỉ đi xem phim / TTTM tối đa 1 lần).

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

Bối cảnh chuyến đi (hãy tôn trọng khi điều chỉnh):
{trip_context}
- Sở thích chung của chuyến đi: "{user_preference}"

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


VEHICLE_LABELS = {
    "xe_may": "Xe máy",
    "o_to": "Ô tô",
    "xe_16_cho": "Xe 16 chỗ",
    "xe_29_cho": "Xe 29 chỗ",
    "di_bo": "Đi bộ",
    "xe_dap": "Xe đạp",
}


def build_trip_context(start_time: str = "", end_time: str = "", vehicle_type: str = "",
                       trip_date: str = "", start_point: str = "") -> str:
    """Dựng các dòng bối cảnh chuyến đi (giờ, ngày, xe, điểm xuất phát) cho prompt.
    Thông tin nào trống thì bỏ qua dòng đó, không bịa giá trị mặc định."""
    lines = []
    if trip_date:
        lines.append(f"- Ngày đi: {trip_date}")
    if start_time and end_time:
        lines.append(f"- Giờ đi: từ {start_time} đến {end_time}")
    if vehicle_type:
        lines.append(f"- Phương tiện: {VEHICLE_LABELS.get(vehicle_type, vehicle_type)}")
    if start_point:
        lines.append(f"- Điểm xuất phát: {start_point}")
    return "\n".join(lines) if lines else "- (Chưa có thêm thông tin)"


def build_prompt(context: str, user_preference: str, available_minutes: int, trip_context: str = "") -> str:
    """Tạo prompt với quy tắc định lượng thời gian và ngữ cảnh dữ liệu."""
    try:
        from services.parser import _extract_tags, _normalize_typos, _low, _strip
        norm_pref = _normalize_typos(user_preference or "")
        liked, _ = _extract_tags(_low(norm_pref), _strip(norm_pref))
    except Exception:
        liked = []

    has_lively = "lively" in liked or any(w in norm_pref for w in ("nao nhiet", "soi dong", "nhon nhip", "tap nap", "quay"))
    has_quiet = "quiet" in liked or any(w in norm_pref for w in ("yen tinh", "chill", "thu gian"))

    if has_lively:
        target_count = 4 if available_minutes >= 240 else 3
        rule = (
            f"- Người dùng muốn trải nghiệm sôi động, náo nhiệt, giàu năng lượng ({user_preference}), "
            f"HÃY CHỌN {target_count} địa điểm náo nhiệt, view đẹp, phố xá đông vui sầm uất. "
            f"TUYỆT ĐỐI KHÔNG dùng các từ 'thong thả', 'bình yên', 'chậm rãi' trong advice_text và description!"
        )
    elif liked and len(liked) <= 2:
        target_count = min(3, max(2, len(liked)))
        pacing = "thong thả" if has_quiet else "vừa vặn, thoải mái"
        rule = (
            f"- Người dùng chỉ có nhu cầu trải nghiệm cụ thể ({user_preference}), "
            f"HÃY CHỌN ĐÚNG {target_count} địa điểm phù hợp nhất, nhịp độ {pacing}. "
            f"TUYỆT ĐỐI KHÔNG tự ý chọn thêm các địa điểm ngoài sở thích (như quán ăn, di tích)."
        )
    elif available_minutes < 180:
        rule = "- Do thời gian ngắn (dưới 3 tiếng), HÃY CHỌN 2 đến 3 địa điểm."
    elif 180 <= available_minutes <= 360:
        rule = "- Thời gian lý tưởng (3 đến 6 tiếng), HÃY CHỌN 4 đến 5 địa điểm."
    else:
        rule = "- Quỹ thời gian dư dả (trên 6 tiếng), HÃY CHỌN 5 đến 7 địa điểm."

    return PROMPT_TEMPLATE.format(
        available_minutes=available_minutes,
        user_preference=user_preference,
        quantification_rule=rule,
        context=context,
        trip_context=trip_context or "- (Chưa có thêm thông tin)",
    )


def build_refine_prompt(context: str, current_points: list, instruction: str,
                        trip_context: str = "", user_preference: str = "") -> str:
    itinerary_lines = "\n".join(
        f"- id={p['id']} | {p['ten']} | loại: {p.get('loai_hinh','?')}" for p in current_points
    )
    return REFINE_PROMPT_TEMPLATE.format(
        current_itinerary=itinerary_lines or "(chưa có điểm nào)",
        instruction=instruction,
        context=context,
        trip_context=trip_context or "- (Chưa có thêm thông tin)",
        user_preference=user_preference or "(chưa nêu)",
    )


def call_ai_service(prompt: str, model: str = OLLAMA_MODEL) -> str:
    """Gọi LLM service qua API với constrained decoding (JSON format)."""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model or OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "format": "json", 
                "options": {
                    "temperature": 0.2,
                    "num_predict": 180,
                    "num_ctx": 1536,
                    "num_thread": 4,
                }, 
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

    try:
        from services.parser import _extract_tags, _normalize_typos, _low, _strip
        norm_pref = _normalize_typos(user_preference or "")
        liked, _ = _extract_tags(_low(norm_pref), _strip(norm_pref))
    except Exception:
        liked = []

    # Chọn số lượng điểm theo sở thích cụ thể hoặc quỹ thời gian
    if liked and len(liked) <= 3:
        target_count = min(3, max(2, len(liked)))
    elif available_minutes < 180:
        target_count = min(3, len(context_points))
    elif available_minutes <= 360:
        target_count = min(5, len(context_points))
    else:
        target_count = min(7, len(context_points))
    target_count = max(2, min(target_count, len(context_points)))

    from categories import category_of
    cat_map = {
        'cafe': 'cafe', 'entertainment': 'tttm', 'shopping': 'tttm',
        'food': 'an_uong', 'culture': 'tham_quan', 'nature': 'tham_quan',
        'photo': 'checkin', 'quiet': 'cafe'
    }
    liked_cats = {cat_map[t] for t in liked if t in cat_map}

    if liked_cats:
        prioritized_points = [p for p in context_points if category_of(p) in liked_cats] + \
                             [p for p in context_points if category_of(p) not in liked_cats]
    else:
        prioritized_points = context_points

    chosen = []
    cat_counts = {}
    fallback_caps = {
        "tttm": 1 if ('entertainment' in liked or 'shopping' in liked or not liked) else 0,
        "cafe": 2 if ("cafe" in liked or "photo" in liked or "quiet" in liked) else 1,
        "an_uong": 1 if ("food" in liked or not liked) else 0,
        "tham_quan": 1 if ("photo" in liked and "culture" not in liked) else (2 if ("culture" in liked or not liked) else 0),
        "checkin": 2 if ("photo" in liked) else 1,
    }

    # Ưu tiên lấy điểm phù hợp, không bao giờ lấy quá 1 rạp phim / TTTM
    for p in prioritized_points:
        if len(chosen) >= target_count:
            break
        p_cat = category_of(p)
        limit = fallback_caps.get(p_cat, 2)
        if cat_counts.get(p_cat, 0) < limit:
            chosen.append(p)
            cat_counts[p_cat] = cat_counts.get(p_cat, 0) + 1

    # Nếu sau khi lọc quota vẫn chưa đủ và còn điểm khác:
    if len(chosen) < min(2, len(context_points)):
        for p in context_points:
            if p not in chosen and category_of(p) != "tttm":
                chosen.append(p)
                if len(chosen) >= min(target_count, 2):
                    break

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

    try:
        from services.parser import _extract_tags, _normalize_typos, _low, _strip
        norm_pref = _normalize_typos(user_preference or "")
        liked, _ = _extract_tags(_low(norm_pref), _strip(norm_pref))
    except Exception:
        liked = []

    has_lively = "lively" in liked or any(w in norm_pref for w in ("nao nhiet", "soi dong", "nhon nhip", "tap nap", "quay"))
    has_quiet = "quiet" in liked or any(w in norm_pref for w in ("yen tinh", "chill", "thu gian"))

    if has_lively:
        advice_text = (
            "Mình đã sắp xếp cho bạn một hành trình tràn đầy năng lượng và sôi động! "
            "Từng điểm dừng chân đều được kết nối liền mạch, vừa có quán cà phê view đẹp để check-in sống ảo, "
            "vừa đưa bạn hòa mình vào không khí phố phường náo nhiệt đặc trưng của thủ đô."
        )
        summary = f"Gợi ý hành trình sôi động, náo nhiệt thiết kế riêng cho quỹ thời gian {available_minutes} phút của bạn."
    elif has_quiet:
        advice_text = (
            "Mình đã sắp xếp cho bạn một hành trình nhẹ nhàng và bình yên. "
            "Từng điểm dừng chân đều được kết nối liền mạch, vừa đủ để bạn thong thả thưởng thức trọn vẹn cảnh sắc nơi đây mà không lo vội vã."
        )
        summary = f"Gợi ý hành trình thảnh thơi thiết kế riêng cho quỹ thời gian {available_minutes} phút của bạn."
    else:
        advice_text = (
            "Mình đã sắp xếp cho bạn một hành trình thật nhiều cảm xúc và hài hòa. "
            "Từng điểm dừng chân đều được kết nối liền mạch, giúp bạn tận hưởng trọn vẹn nét đẹp và phong vị thủ đô."
        )
        summary = f"Gợi ý hành trình thiết kế riêng cho quỹ thời gian {available_minutes} phút của bạn."

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

    # Loại id trùng lặp và giới hạn tối đa 1 rạp phim / TTTM
    from categories import category_of
    id_map = {p["id"]: p for p in (context_points or [])}
    seen = set()
    cat_counts = {}
    suggested_ids = []
    for pid in ordered_ids:
        if pid not in seen:
            p_obj = id_map.get(pid)
            p_cat = category_of(p_obj) if p_obj else ""
            if p_cat == "tttm" and cat_counts.get("tttm", 0) >= 1:
                continue
            cat_counts[p_cat] = cat_counts.get(p_cat, 0) + 1
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
    from api.db import fetch_all_points

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
        trip_context = build_trip_context(
            req.start_time, req.end_time, req.vehicle_type, req.trip_date, req.start_point
        )
        prompt = build_prompt(context, req.user_preference, available_minutes, trip_context=trip_context)
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


def analyze_complex_refine_request(
    instruction: str,
    current_points: List[dict],
    all_points: List[dict],
    excluded_ids: set,
    target_place_id: Optional[str] = None,
    session: Optional[Any] = None,
    start_point_str: str = "",
) -> Tuple[str, Optional[dict], List[dict], str, List[str]]:
    """
    Phân tích yêu cầu AI Refine phức tạp (Ví dụ: "Tìm chỗ yên tĩnh hơn gần đây",
    "Thay quán này bằng nơi phù hợp trời mưa", "Tìm quán cafe view đẹp quanh đây"...).
    
    Thay vì tự tiện quyết định thay thế bằng 1 điểm duy nhất, AI trả về:
    - intent: "options" (trả về 2-3 lựa chọn để user chọn), "select_previous" (user chọn lại từ option trước), "direct" (lệnh bỏ điểm trực tiếp)
    - target_pt: Địa điểm đang xem xét thay thế (nếu có)
    - options: Danh sách 2-4 lựa chọn chi tiết (id, tên, rating, khoảng cách, lý do, tags, action)
    - advice_text: Lời khuyên thân thiện, ấm áp
    - suggested_ids: Danh sách gợi ý mặc định (tương thích ngược với client cũ)
    """
    from api.preferences import _strip_diacritics
    from api.scheduling import calc_dist

    raw_text = (instruction or "").strip()
    norm_text = _strip_diacritics(raw_text).lower()

    # 1. KIỂM TRA LỰA CHỌN TỪ MULTI-TURN CHAT TRƯỚC ĐÓ
    if session and session.get_chat_history():
        hist = session.get_chat_history()
        last_opts_msg = None
        for msg in reversed(hist):
            if msg.get("role") == "assistant" and msg.get("options"):
                last_opts_msg = msg
                break
        
        if last_opts_msg and last_opts_msg.get("options"):
            prev_opts = last_opts_msg["options"]
            selected_idx = None

            # Pattern: "chọn 1", "chọn số 2", "lấy quán 3", "phương án 1"...
            m = re.search(r"(?:chon|lay|lua|chot|phuong an|so|quan|cai thu)\s*#?([1-4])\b", norm_text)
            if m:
                idx_num = int(m.group(1))
                if 1 <= idx_num <= len(prev_opts):
                    selected_idx = idx_num - 1
            elif "thu nhat" in norm_text or "dau tien" in norm_text:
                selected_idx = 0
            elif "thu hai" in norm_text or "thu 2" in norm_text:
                if len(prev_opts) >= 2:
                    selected_idx = 1
            elif "thu ba" in norm_text or "thu 3" in norm_text:
                if len(prev_opts) >= 3:
                    selected_idx = 2
            else:
                for idx, o in enumerate(prev_opts):
                    o_name = _strip_diacritics(o.get("ten", "")).lower()
                    if o_name and o_name in norm_text:
                        selected_idx = idx
                        break

            if selected_idx is not None:
                chosen_opt = prev_opts[selected_idx]
                target_id = last_opts_msg.get("target_place_id")
                target_name = last_opts_msg.get("target_place_name")
                current_pids = [str(p["id"]) for p in current_points]

                if target_id and str(target_id) in current_pids:
                    new_pids = [chosen_opt["place_id"] if str(pid) == str(target_id) else str(pid) for pid in current_pids]
                    advice = f"Tuyệt vời! Mình đã thay '{target_name or 'địa điểm cũ'}' bằng '{chosen_opt['ten']}' vào lịch trình cho bạn."
                else:
                    new_pids = list(current_pids)
                    if chosen_opt["place_id"] not in new_pids:
                        new_pids.append(chosen_opt["place_id"])
                    advice = f"Tuyệt vời! Mình đã thêm '{chosen_opt['ten']}' vào lịch trình cho bạn."

                return "select_previous", None, [chosen_opt], advice, new_pids

    # 2. KIỂM TRA LỆNH XÓA / BỎ ĐIỂM TRỰC TIẾP
    has_remove = _has_remove_intent(raw_text)
    has_find_or_replace = any(w in norm_text for w in (
        "thay the", "thay bang", "thay sang", "thay quan", "thay diem", "thay cho",
        "doi sang", "doi thanh", "doi quan", "doi diem", "doi cho",
        "tim cho", "tim quan", "tim diem", "tim them", "goi y", "chuyen sang", "cho nao", "quan nao"
    ))
    if has_remove and not has_find_or_replace:
        return "direct", None, [], "", []

    # 3. NHẬN DIỆN CÁC TIÊU CHÍ PHỨC TẠP
    is_rain = any(k in norm_text for k in ("mua", "troi mua", "trong nha", "mai che", "dieu hoa", "tranh mua", "tranh nang", "rain", "indoor"))
    is_quiet = any(k in norm_text for k in ("yen tinh", "thanh binh", "chill", "tranh on", "doc sach", "tinh lang", "yen ang", "quiet", "peaceful"))
    is_view = any(k in norm_text for k in ("view dep", "chup anh", "song ao", "check in", "checkin", "ngam canh", "scenic", "view"))
    is_food = any(k in norm_text for k in ("an uong", "an trua", "quan an", "mon ngon", "dac san", "pho", "bun cha", "com", "food", "lunch"))
    is_cafe = any(k in norm_text for k in ("cafe", "ca phe", "coffee", "tra", "do uong"))
    is_culture = any(k in norm_text for k in ("bao tang", "di tich", "lich su", "van hoa", "tam linh", "chua", "den", "museum", "history"))
    is_budget = any(k in norm_text for k in ("gia re", "binh dan", "tiet kiem", "gia hop ly", "re", "cheap"))
    is_near = any(k in norm_text for k in ("gan day", "quanh day", "gan day hon", "gan", "near", "around"))

    # Nếu không khớp tiêu chí phức tạp nào và không có ý định thay/tìm, chuyển cho LLM tổng quát
    if not any([is_rain, is_quiet, is_view, is_food, is_cafe, is_culture, is_budget, is_near, has_find_or_replace]):
        return "general", None, [], "", []

    # 4. XÁC ĐỊNH TARGET POINT ĐỂ THAY THẾ (NẾU CÓ)
    target_pt = None
    if target_place_id:
        target_pt = next((p for p in current_points if str(p.get("id")) == str(target_place_id)), None)

    if not target_pt:
        for p in current_points:
            p_name_norm = _strip_diacritics(p.get("ten", "")).lower()
            if p_name_norm and (p_name_norm in norm_text or any(word in norm_text for word in p_name_norm.split() if len(word) > 4)):
                target_pt = p
                break

    if not target_pt and any(w in norm_text for w in ("thay quan", "thay diem", "thay cho", "doi quan", "doi diem", "thay nơi")):
        if is_rain:
            target_pt = next((p for p in current_points if p.get("trong_nha_ngoai_troi") == "ngoai_troi"), None)
        if not target_pt and is_quiet:
            target_pt = next((p for p in current_points if "dong_khach" in ((p.get("review_insight") or {}).get("ma_dac_diem") or [])), None)
        if not target_pt and (is_cafe or is_food):
            target_pt = next((p for p in current_points if p.get("loai_hinh") in ("Cafe", "Ăn uống")), None)
        if not target_pt and current_points:
            non_gps = [p for p in current_points if p.get("id") != "gps_current"]
            if non_gps:
                target_pt = non_gps[-1]

    ref_pt = target_pt or (current_points[-1] if current_points else None)

    # 5. TÌM VÀ ĐÁNH GIÁ CÁC ỨNG VIÊN
    current_pids = {str(p["id"]) for p in current_points}
    pool = [p for p in all_points if str(p["id"]) not in current_pids and str(p["id"]) not in excluded_ids]

    scored = []
    for cand in pool:
        score = 0.0
        badges = []
        reasons = []

        dist = None
        if ref_pt and "lat" in cand and "lon" in cand and "lat" in ref_pt and "lon" in ref_pt:
            try:
                dist = calc_dist(ref_pt, cand)
            except Exception:
                dist = None

        the_set = {t.get("ma") for t in (cand.get("the") or []) if isinstance(t, dict)}
        insight = cand.get("review_insight") or {}
        traits = set(insight.get("ma_dac_diem") or [])
        noi_bat = insight.get("noi_bat") or []
        io = cand.get("trong_nha_ngoai_troi")
        loai = cand.get("loai_hinh") or ""
        rating = float(cand.get("rating") or cand.get("diem_gia_tri") or 4.0)

        # Tiêu chí trời mưa / trong nhà
        if is_rain:
            if io == "trong_nha":
                score += 7.0
                badges.append("🌧️ Trong nhà")
                reasons.append("Không gian hoàn toàn trong nhà, ấm áp, có mái che tiện lợi không lo thời tiết.")
            elif io == "ca_hai":
                score += 3.5
                badges.append("🏛️ Trong nhà & mái che")
            elif io == "ngoai_troi":
                score -= 20.0
            if loai == "TTTM":
                score += 5.0
                badges.append("🎬 Mua sắm & Rạp phim")
                reasons.append("Tổ hợp mua sắm, vui chơi giải trí và ăn uống trong nhà tiện nghi.")
            elif loai == "Cafe" and io != "ngoai_troi":
                score += 4.0
                badges.append("☕ Ấm cúng")

        # Tiêu chí yên tĩnh
        if is_quiet:
            if "dong_khach" in traits:
                score -= 12.0
            if "yen_tinh" in the_set or "yen_tinh" in traits:
                score += 7.0
                badges.append("🤫 Yên tĩnh")
                reasons.append("Không gian thanh bình, tĩnh lặng, khách khen rất phù hợp để thư giãn và đọc sách.")
            elif loai == "Cafe":
                score += 2.5
                badges.append("☕ Không gian chill")

        # Tiêu chí view đẹp / chụp ảnh
        if is_view:
            if "view_dep" in the_set or "view_dep" in traits:
                score += 6.0
                badges.append("📸 View đẹp")
                reasons.append("Tầm nhìn đẹp, thoáng đãng, nhiều góc check-in ấn tượng.")
            if "chup_anh" in the_set or "check_in" in traits:
                score += 3.5
                badges.append("✨ Sống ảo")

        # Tiêu chí ăn uống
        if is_food:
            if loai == "Ăn uống" or "an_uong" in the_set or "an_trua" in the_set:
                score += 6.0
                badges.append("🍜 Ẩm thực ngon")
                if "ngon" in traits:
                    reasons.append("Ẩm thực đặc sản thơm ngon, hương vị đậm đà được thực khách đánh giá cao.")

        # Tiêu chí cafe
        if is_cafe:
            if loai == "Cafe" or "cafe" in the_set:
                score += 5.5
                badges.append("☕ Cà phê")

        # Tiêu chí văn hóa / di tích
        if is_culture:
            if "lich_su" in the_set or "van_hoa" in the_set or loai == "Tham quan":
                score += 6.0
                badges.append("🏛️ Di tích lịch sử")
                reasons.append("Di tích lịch sử văn hóa ý nghĩa, nhiều hiện vật và kiến trúc đặc sắc.")

        # Tiêu chí giá rẻ
        if is_budget:
            m = cand.get("muc_gia")
            if m is not None and m <= 1:
                score += 4.5
                badges.append("💰 Giá bình dân")
            elif "gia_hop_ly" in traits:
                score += 3.0
                badges.append("💰 Giá hợp lý")
            elif m is not None and m >= 3:
                score -= 6.0

        # Khoảng cách gần
        if dist is not None:
            if is_near:
                score += max(0.0, 7.0 - dist * 1.2)
            else:
                score += max(0.0, 3.5 - dist * 0.5)
            badges.append(f"📍 Cách {dist:.1f} km")

        # Điểm chất lượng chung
        score += (rating / 5.0) * 2.0
        if noi_bat and not reasons:
            reasons.append(f"Khách hay khen: {', '.join(noi_bat[:2])}")

        if not reasons:
            desc = cand.get("mo_ta") or cand.get("thong_tin_chi_tiet") or ""
            if desc:
                reasons.append(desc[:85].strip() + "...")
            else:
                reasons.append(f"Địa điểm {loai} chất lượng, được nhiều người yêu thích.")

        scored.append({
            "point": cand,
            "score": score,
            "dist": dist,
            "badges": badges,
            "reason": " ".join(reasons),
        })

    scored.sort(key=lambda x: -x["score"])
    top_picks = [item for item in scored if item["score"] > 0][:3]
    if not top_picks and pool:
        top_picks = scored[:3]

    if not top_picks:
        return "general", None, [], "", []

    options = []
    action = "replace" if target_pt else "add"
    for item in top_picks:
        p = item["point"]
        opt = {
            "place_id": str(p["id"]),
            "ten": p["ten"],
            "loai_hinh": p.get("loai_hinh", ""),
            "rating": float(p.get("rating") or p.get("diem_gia_tri") or 4.0),
            "so_luot_danh_gia": p.get("so_luot_danh_gia"),
            "dia_chi": p.get("dia_chi", ""),
            "url_hinh_anh": p.get("url_hinh_anh", ""),
            "distance_km": round(item["dist"], 1) if item["dist"] is not None else None,
            "reason": item["reason"],
            "action": action,
            "tags": item["badges"][:3],
        }
        options.append(opt)

    target_name = target_pt["ten"] if target_pt else None
    if target_pt:
        if is_rain:
            advice_text = f"Mình gợi ý {len(options)} địa điểm trong nhà ấm cúng, view đẹp, rất phù hợp khi trời mưa quanh khu vực '{target_name}' để bạn cân nhắc thay thế:"
        elif is_quiet:
            advice_text = f"Mình gợi ý {len(options)} không gian yên tĩnh, thư thái và ít ồn ào gần '{target_name}' để bạn lựa chọn:"
        elif is_view:
            advice_text = f"Mình gợi ý {len(options)} quán có view đẹp và nhiều góc chụp ảnh ấn tượng quanh '{target_name}':"
        else:
            advice_text = f"Mình gợi ý {len(options)} lựa chọn phù hợp nhất quanh khu vực '{target_name}'. Bạn xem và chọn điểm ưng ý nhé:"
    else:
        if is_rain:
            advice_text = f"Dưới đây là {len(options)} địa điểm trong nhà không ngại mưa gió gần đây. Bạn chọn nơi muốn ghé nhé:"
        elif is_quiet:
            advice_text = f"Dưới đây là {len(options)} không gian yên tĩnh, thư thái gần đây. Bạn chọn địa điểm ưng ý nhé:"
        else:
            advice_text = f"Dưới đây là {len(options)} địa điểm được đánh giá cao, rất phù hợp với yêu cầu '{raw_text}' của bạn:"

    if target_pt and options:
        suggested_ids = [options[0]["place_id"] if str(pid) == str(target_pt["id"]) else str(pid) for pid in [p["id"] for p in current_points]]
    elif options:
        suggested_ids = [str(p["id"]) for p in current_points] + [options[0]["place_id"]]
    else:
        suggested_ids = [str(p["id"]) for p in current_points]

    return "options", target_pt, options, advice_text, suggested_ids


def generate_fallback_refine(
    instruction: str,
    current_points: List[dict],
    all_points: List[dict],
    excluded_ids: set,
) -> Tuple[str, List[str], List[str]]:
    """
    Fallback thông minh khi LLM offline hoặc gặp sự cố kết nối.
    Xử lý các lệnh phổ biến:
    - Bỏ điểm theo tên hoặc theo số thứ tự (1, 2, cuối, đầu)
    - Giảm bớt điểm khi đi chậm / mệt
    - Thêm điểm (ăn trưa, cafe, checkin, tham quan) theo category và rating cao
    - Luôn trả về lời giải thích ấm áp, tự nhiên.
    """
    import re
    from api.preferences import _strip_diacritics
    from categories import category_of

    raw_text = (instruction or "").strip()
    norm = _strip_diacritics(raw_text).lower()

    current_ids = [str(p["id"]) for p in current_points]

    # 1. KIỂM TRA Ý ĐỊNH BỎ ĐIỂM
    if _has_remove_intent(raw_text):
        # 1.1 Khớp theo tên địa điểm trong current_points (ưu tiên khớp nguyên cụm tên dài nhất)
        matched_point = None
        exact_matches = []
        for p in current_points:
            p_name = _strip_diacritics(p.get("ten", "")).lower()
            p_clean = re.sub(r"\(.*?\)", "", p_name).strip()
            if p_clean and p_clean in norm:
                exact_matches.append((len(p_clean), p))
            elif p_name and p_name in norm:
                exact_matches.append((len(p_name), p))

        if exact_matches:
            exact_matches.sort(key=lambda x: -x[0])
            matched_point = exact_matches[0][1]
        else:
            stopwords = {"diem", "quan", "cho", "noi", "center", "plaza", "mall", "rap", "pho", "duong"}
            best_score = 0
            norm_words = set(re.findall(r"\w+", norm))
            for p in current_points:
                p_name = _strip_diacritics(p.get("ten", "")).lower()
                p_words = set(re.findall(r"\w+", p_name)) - stopwords
                if p_words:
                    overlap = len(p_words & norm_words)
                    if overlap > best_score and overlap >= 2:
                        best_score = overlap
                        matched_point = p

        if matched_point:
            drop_id = str(matched_point["id"])
            new_ids = [pid for pid in current_ids if pid != drop_id]
            advice = f"Mình đã loại bỏ '{matched_point['ten']}' khỏi lịch trình theo mong muốn của bạn."
            return advice, new_ids, []

        # 1.2 Khớp theo số thứ tự ("bỏ điểm 1", "bỏ điểm số 2", "bỏ quán 3", "bỏ 1"...)
        m = re.search(r"(?:bo|xoa|loai|bot)\s+(?:(?:diem|quan|cai|so|thu)\s+)*#?([1-9])\b", norm)
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(current_points):
                drop_pt = current_points[idx]
                new_ids = [pid for i, pid in enumerate(current_ids) if i != idx]
                advice = f"Mình đã loại bỏ điểm số {idx + 1} ('{drop_pt['ten']}') khỏi lịch trình của bạn."
                return advice, new_ids, []

        if any(w in norm for w in ("diem cuoi", "quan cuoi", "cho cuoi", "cai cuoi")):
            if len(current_points) > 1:
                drop_pt = current_points[-1]
                new_ids = current_ids[:-1]
                advice = f"Mình đã bỏ điểm cuối cùng ('{drop_pt['ten']}') khỏi lịch trình của bạn."
                return advice, new_ids, []

        if any(w in norm for w in ("diem dau", "quan dau", "cai dau")):
            if len(current_points) > 1:
                drop_pt = current_points[0]
                new_ids = current_ids[1:]
                advice = f"Mình đã bỏ điểm đầu tiên ('{drop_pt['ten']}') khỏi lịch trình của bạn."
                return advice, new_ids, []

        # 1.3 Ý định đi chậm hơn / mệt / giảm bớt
        if any(w in norm for w in ("di cham", "nghi ngoi", "met", "thong tha", "giam bot")):
            if len(current_points) > 2:
                drop_pt = min(current_points, key=lambda p: float(p.get("rating") or p.get("diem_gia_tri") or 3.0))
                new_ids = [pid for pid in current_ids if pid != str(drop_pt["id"])]
                advice = f"Mình đã lược bớt '{drop_pt['ten']}' để bạn có thêm thời gian nghỉ ngơi và di chuyển thong thả hơn."
                return advice, new_ids, []

    # 2. KIỂM TRA Ý ĐỊNH THÊM ĐIỂM
    is_food = any(w in norm for w in ("an uong", "an trua", "quan an", "mon ngon", "pho", "bun", "com", "food", "lunch", "an toi"))
    is_cafe = any(w in norm for w in ("cafe", "ca phe", "coffee", "tra", "do uong"))
    is_checkin = any(w in norm for w in ("checkin", "check in", "song ao", "chup anh", "view dep"))
    is_culture = any(w in norm for w in ("bao tang", "di tich", "lich su", "van hoa", "chua", "den"))

    target_cat = None
    target_label = "địa điểm"
    if is_food:
        target_cat = "an_uong"
        target_label = "quán ăn"
    elif is_cafe:
        target_cat = "cafe"
        target_label = "quán cà phê"
    elif is_checkin:
        target_cat = "checkin"
        target_label = "điểm check-in chụp ảnh"
    elif is_culture:
        target_cat = "tham_quan"
        target_label = "điểm tham quan văn hóa"

    if target_cat:
        available_cands = [
            p for p in all_points
            if str(p["id"]) not in current_ids and str(p["id"]) not in excluded_ids
            and category_of(p) == target_cat
        ]
        if not available_cands:
            available_cands = [
                p for p in all_points
                if str(p["id"]) not in current_ids and str(p["id"]) not in excluded_ids
            ]

        if available_cands:
            available_cands.sort(
                key=lambda p: (
                    float(p.get("rating") or p.get("diem_gia_tri") or 4.0),
                    int(p.get("so_luot_danh_gia") or 0)
                ),
                reverse=True
            )
            picked = available_cands[0]
            insert_pos = max(1, len(current_ids) // 2) if is_food else len(current_ids)
            new_ids = list(current_ids)
            new_ids.insert(insert_pos, str(picked["id"]))
            rating_str = f" (đánh giá {picked.get('rating', 4.5)}⭐)" if picked.get("rating") else ""
            advice = f"Mình đã bổ sung {target_label} '{picked['ten']}'{rating_str} vào lịch trình cho bạn."
            return advice, new_ids, []

    # 3. MẶC ĐỊNH
    advice = "Hệ thống AI đang chạy chế độ an toàn. Mình đã ghi nhận yêu cầu và giữ vững các điểm đến tối ưu cho bạn."
    return advice, list(current_ids), []


@router.post("/api/ai-refine", response_model=AIRefineResponse)
def refine_route(req: AIRefineRequest):
    """Tiếp nối hội thoại trên màn hình bản đồ: AI nhận itinerary hiện tại +
    câu lệnh tự do của người dùng ('Bỏ C đi', 'Thêm quán ăn trưa', 'Tìm chỗ yên tĩnh hơn gần đây'...).
    
    Hỗ trợ:
    - Trả về danh sách OPTIONS (lựa chọn) để user tự chọn thay vì ép 1 điểm duy nhất.
    - Lưu trữ lịch sử hội thoại (chat_history) trong session.
    - Nhận diện các yêu cầu phức tạp (trời mưa, yên tĩnh, view đẹp, gần đây...).
    """
    from api.db import fetch_all_points
    from itinerary_store import store

    session = store.get(req.session_id)
    excluded = set(session.excluded_ids) if session else set()

    # Lưu tin nhắn của user vào chat_history trong session
    if session is not None:
        session.add_chat_message(role="user", content=req.instruction)

    all_points = fetch_all_points(vehicle_type=req.vehicle_type, trip_date=req.trip_date)
    if not all_points:
        raise HTTPException(status_code=404, detail="Không có dữ liệu địa điểm phù hợp.")

    if excluded:
        all_points = [p for p in all_points if str(p["id"]) not in excluded]

    id_to_point = {str(p["id"]): p for p in all_points}
    current_points = [id_to_point[str(i)] for i in req.current_ids if str(i) in id_to_point]

    # Phân tích yêu cầu AI Refine phức tạp (options, yên tĩnh, trời mưa, multi-turn selection...)
    intent, target_pt, options, advice_text, suggested_ids = analyze_complex_refine_request(
        req.instruction,
        current_points,
        all_points,
        excluded,
        target_place_id=req.target_place_id,
        session=session,
        start_point_str=req.start_point,
    )

    if intent in ("options", "select_previous") and options:
        # Nếu người dùng vừa chọn từ option trước, đồng bộ ngay itinerary trong session
        if intent == "select_previous" and session is not None and session.current_itinerary:
            try:
                from api.routers.itinerary import recalculate_route_from_places
                cand_places = [id_to_point[str(pid)] for pid in suggested_ids if str(pid) in id_to_point]
                if cand_places:
                    updated_route, _ = recalculate_route_from_places(session, cand_places, ai_advice=advice_text)
                    session.set_current_itinerary(updated_route)
            except Exception:
                pass

        if session is not None:
            session.add_chat_message(
                role="assistant",
                content=advice_text,
                options=options,
                target_place_id=str(target_pt["id"]) if target_pt else None,
                target_place_name=target_pt["ten"] if target_pt else None,
            )

        return AIRefineResponse(
            advice_text=advice_text,
            suggested_ids=suggested_ids,
            invalid_ids=[],
            removed_ids=[],
            excluded_ids=sorted(excluded),
            session_id=req.session_id,
            intent=intent,
            target_place_id=str(target_pt["id"]) if target_pt else None,
            target_place_name=target_pt["ten"] if target_pt else None,
            options=options,
            chat_history=session.get_chat_history() if session else [],
        )

    # 4. Khi là lệnh bỏ điểm hoặc yêu cầu tổng quát khác
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
        trip_context = build_trip_context(
            req.start_time, req.end_time, req.vehicle_type, req.trip_date, req.start_point
        )
        prompt = build_refine_prompt(
            context, current_points, req.instruction,
            trip_context=trip_context, user_preference=req.user_preference,
        )
        raw_response = call_ai_service(prompt, model=req.model or OLLAMA_MODEL)
        advice_text, suggested_ids, invalid_ids = parse_refine_response(raw_response, valid_ids_set, req.current_ids)
    except Exception:
        # Fallback thông minh và an toàn khi service AI LLM lỗi hoặc offline
        advice_text, suggested_ids, invalid_ids = generate_fallback_refine(
            req.instruction, current_points, all_points, excluded
        )

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
        session.add_chat_message(role="assistant", content=advice_text)

    return AIRefineResponse(
        advice_text=advice_text,
        suggested_ids=suggested_ids,
        invalid_ids=invalid_ids,
        removed_ids=removed_ids,
        excluded_ids=sorted(excluded),
        session_id=req.session_id,
        intent="direct",
        options=[],
        chat_history=session.get_chat_history() if session else [],
    )