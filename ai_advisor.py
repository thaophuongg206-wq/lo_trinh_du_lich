import re
import json
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


PROMPT_TEMPLATE = """Bạn là một người bạn đồng hành am hiểu du lịch, thấu hiểu tâm lý và tư vấn có gu.

1. THÔNG TIN CHUYẾN ĐI:
- Quỹ thời gian: {available_minutes} phút.
- Sở thích/Tâm trạng: "{user_preference}"

2. ĐỊNH LƯỢNG ĐIỂM ĐẾN BẮT BUỘC (Dựa trên {available_minutes} phút):
{quantification_rule}

3. KHO DỮ LIỆU ĐỊA ĐIỂM (Chỉ được chọn các id có trong danh sách này):
{context}

4. YÊU CẦU LẬP LUẬN:
- Chia hành trình thành các mốc thời gian trong ngày phù hợp với quỹ thời gian
  (ví dụ: sáng, trưa, chiều, tối — chỉ dùng những mốc thực sự nằm trong khung
  giờ đã cho, không bịa thêm mốc không liên quan).
- Mỗi mốc có 1-2 địa điểm kèm lý do ngắn gọn vì sao chọn (reason).
- Giọng văn thân thiện, xưng "mình" và gọi "bạn". Không xưng là "AI" hay "Trợ lý ảo".

5. RÀNG BUỘC ĐẦU RA JSON — TRẢ VỀ DUY NHẤT MỘT OBJECT JSON SAU, KHÔNG DÙNG MARKDOWN:
{{
  "summary": "1-2 câu mở đầu thân thiện, nhắc tới quỹ thời gian {available_minutes} phút",
  "timeline": [
    {{
      "period": "morning|noon|afternoon|evening",
      "title": "Tên mốc thời gian bằng tiếng Việt, ví dụ 'Buổi sáng'",
      "icon": "sunrise|sun|sunset|moon",
      "description": "1-2 câu mô tả cảm giác/nhịp điệu của mốc này",
      "places": [ {{"id": "12", "reason": "vì sao điểm này hợp với mốc này"}} ]
    }}
  ]
}}

TRẢ LỜI:"""


REFINE_PROMPT_TEMPLATE = """Bạn là một người bạn đồng hành du lịch, đang trò chuyện tiếp nối với người
dùng về một lịch trình đã có sẵn (không phải tạo mới từ đầu).

1. LỊCH TRÌNH HIỆN TẠI (đúng thứ tự đang đi):
{current_itinerary}

2. YÊU CẦU MỚI CỦA NGƯỜI DÙNG:
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
- advice_text là 1 đoạn ngắn xác nhận lại điều vừa thay đổi, giọng thân thiện,
  xưng "mình" gọi "bạn".

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


def _clean_id(raw_id) -> str:
    return str(raw_id).replace("id", "").replace("[", "").replace("]", "").strip()


def parse_structured_response(raw: str, valid_ids: set) -> tuple[str, list, list, str, list]:
    """Parse output JSON có cấu trúc timeline. Nếu AI trả sai format/thiếu field,
    xử lý gracefully (bỏ qua phần lỗi, dùng giá trị mặc định an toàn) thay vì
    làm sập request — đúng yêu cầu 'backend phải có validation + fallback'."""
    obj = _load_json_object(raw)

    summary = str(obj.get("summary", "")).strip().replace("AI", "mình")
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
                        reason = str(pl.get("reason", "")).strip()
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
                # Bỏ qua mốc thời gian không có địa điểm hợp lệ nào thay vì
                # hiển thị một mốc rỗng vô nghĩa cho người dùng.
                continue
            timeline.append({
                "period": str(period_obj.get("period", "")).strip() or "khac",
                "title": str(period_obj.get("title", "")).strip() or "Điểm dừng chân",
                "icon": str(period_obj.get("icon", "")).strip(),
                "description": str(period_obj.get("description", "")).strip(),
                "places": places,
            })

    # advice_text (dùng cho phần hiển thị text liền mạch, tương thích ngược với
    # frontend hiện tại đang render advice_text dạng đoạn văn) được ghép lại từ
    # summary + mô tả từng mốc, để không mất nội dung nếu frontend chưa cập nhật
    # để render timeline dạng structured.
    advice_parts = [summary] if summary else []
    for period in timeline:
        if period["description"]:
            advice_parts.append(period["description"])
    advice_text = "\n\n".join(advice_parts).strip()

    # Loại id trùng lặp nhưng giữ đúng thứ tự xuất hiện đầu tiên.
    seen = set()
    suggested_ids = []
    for pid in ordered_ids:
        if pid not in seen:
            seen.add(pid)
            suggested_ids.append(pid)

    if not suggested_ids:
        raise HTTPException(status_code=502, detail="Không tìm được điểm đến phù hợp, vui lòng thử lại.")

    return advice_text, suggested_ids, invalid_ids, summary, timeline


def parse_refine_response(raw: str, valid_ids: set) -> tuple[str, list, list]:
    """Parse output JSON đơn giản {advice_text, ids} cho luồng tiếp nối trên map."""
    obj = _load_json_object(raw)
    advice_text = str(obj.get("advice_text", "")).strip().replace("AI", "mình")
    raw_ids = [_clean_id(i) for i in obj.get("ids", [])]

    valid_selected = [i for i in raw_ids if i and i in valid_ids]
    invalid = [i for i in raw_ids if i and i not in valid_ids]

    if not valid_selected:
        raise HTTPException(status_code=502, detail="Không thể cập nhật lịch trình, vui lòng thử lại.")

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

    # Lấy dữ liệu và chọn candidate theo mức độ liên quan (không còn cắt cứng [:15])
    all_points = fetch_all_points(vehicle_type=req.vehicle_type)

    # Tôn trọng ràng buộc loại trừ của phiên (nếu người dùng quay lại Screen 1
    # sau khi đã bỏ vài điểm, AI không được gợi ý lại đúng những điểm đó).
    from itinerary_store import store
    session = store.get(req.session_id)
    if session is not None and session.excluded_ids:
        all_points = [p for p in all_points if p["id"] not in session.excluded_ids]

    if not all_points:
        raise HTTPException(status_code=404, detail="Không có dữ liệu địa điểm phù hợp.")

    context_points = select_context_points(all_points, req.user_preference)

    # Xây dựng prompt và gọi service
    context = build_context_string(context_points)
    prompt = build_prompt(context, req.user_preference, available_minutes)
    raw_response = call_ai_service(prompt, model=req.model or OLLAMA_MODEL)

    # Xử lý kết quả trả về
    valid_ids_set = {p["id"] for p in context_points}
    advice_text, suggested_ids, invalid_ids, summary, timeline = parse_structured_response(raw_response, valid_ids_set)

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
    ai_selected_ids = suggested_ids để tính lại route/timeline/map — tái sử
    dụng đúng luồng fill-up Greedy đã có, không cần thuật toán tối ưu riêng."""
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

    # Điểm đã bị loại KHÔNG được đưa vào context của AI (mục 8): nếu vẫn để AI
    # nhìn thấy, chỉ cần người dùng nói "thêm quán ăn trưa" là nó gợi ý lại đúng
    # cái quán vừa bị bỏ, và điểm đó lại xuất hiện trên bản đồ.
    if excluded:
        all_points = [p for p in all_points if p["id"] not in excluded]

    id_to_point = {p["id"]: p for p in all_points}
    current_points = [id_to_point[i] for i in req.current_ids if i in id_to_point]

    # Candidate context cho AI: ưu tiên liên quan tới sở thích, LUÔN bao gồm các
    # điểm đang có trong itinerary hiện tại (để AI có thể "bỏ" đúng điểm đó dù
    # điểm đó không lọt top xếp hạng liên quan).
    ranked_context = select_context_points(all_points, req.user_preference)
    context_points = list(current_points)
    context_ids = {p["id"] for p in context_points}
    for p in ranked_context:
        if p["id"] not in context_ids:
            context_points.append(p)
            context_ids.add(p["id"])

    context = build_context_string(context_points)
    prompt = build_refine_prompt(context, current_points, req.instruction)
    raw_response = call_ai_service(prompt, model=req.model or OLLAMA_MODEL)

    valid_ids_set = {p["id"] for p in context_points}
    advice_text, suggested_ids, invalid_ids = parse_refine_response(raw_response, valid_ids_set)

    # Chốt chặn cuối: dù prompt có dặn thế nào, id đã bị loại vẫn không được lọt
    # ra ngoài — excluded_ids là ràng buộc của hệ thống, không phải gợi ý cho AI.
    suggested_ids = [i for i in suggested_ids if i not in excluded]

    # ── GHI Ý ĐỊNH "BỎ ĐIỂM" VÀO STATE BACKEND (mục 6, 8) ──
    # Đây là mắt xích từng bị đứt: trước đây việc bỏ điểm chỉ thể hiện bằng một
    # danh sách ngắn hơn trả về cho frontend, không có ai ghi nhớ, nên Greedy
    # ở lần tính kế tiếp nhặt lại điểm đó từ DB.
    removed_ids = []
    if session is not None:
        dropped = [i for i in req.current_ids if i not in suggested_ids and i not in excluded]
        if dropped and _has_remove_intent(req.instruction):
            removed_ids = session.exclude(dropped)
        # Điểm AI giữ lại/thêm mới trở thành must-visit cho lần tính lộ trình sau.
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