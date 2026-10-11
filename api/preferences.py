"""Keyword-based preference scoring (diacritics-insensitive)."""
import re
import unicodedata
from typing import Iterable, List, Optional

# ============================================================
# PREFERENCE MATCHING (BUG 1 FIX)
# user_preference (text) + weight (0-100) phải thực sự tham gia scoring.
# Dữ liệu mô tả trong DB (thong_tin_chi_tiet, review, phu_hop) không dấu,
# nên cần chuẩn hoá cả hai chiều (bỏ dấu) trước khi so khớp từ khoá.
# ============================================================

_VN_STOPWORDS = {
    "la", "va", "co", "cua", "nhieu", "rat", "mot", "toi", "muon", "thich",
    "khong", "gian", "de", "cho", "nhu", "hay", "o", "tai", "voi", "the",
    "nay", "duoc", "nhung", "cac", "nen", "hon", "it", "moi", "ve", "roi",
}

def _strip_diacritics(text: str) -> str:
    """Chuẩn hoá tiếng Việt: bỏ dấu, hạ chữ thường, để so khớp từ khoá
    ổn định bất kể người dùng gõ có dấu hay dữ liệu DB không dấu."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = text.replace("đ", "d").replace("Đ", "D")
    return text.lower()

def _extract_keywords(text: str):
    try:
        from services.parser import _normalize_typos
        text = _normalize_typos(text)
    except Exception:
        pass
    normalized = _strip_diacritics(text)
    words = re.findall(r"[a-z0-9]+", normalized)
    return [w for w in words if len(w) > 1 and w not in _VN_STOPWORDS]


# ============================================================
# SỞ THÍCH CÓ CẤU TRÚC (tag) — đầu ra của services/parser.py
# ------------------------------------------------------------
# Parser chuẩn hoá câu lệnh tự nhiên thành các tag cố định (cafe, food,
# photo...). Trước đây tag tiếng Anh này bị nối thẳng vào user_preference rồi
# đem so với dữ liệu DB tiếng Việt không dấu → gần như không khớp gì. Bảng dưới
# đây dịch mỗi tag sang (a) loại hình trong DB và (b) cụm từ không dấu thường
# xuất hiện trong mô tả/review/phu_hop của DB.
# ============================================================
PREFERENCE_TAGS = (
    "cafe", "food", "photo", "quiet", "culture", "shopping",
    "nature", "family", "nightlife", "romantic", "lively",
    "entertainment",
)

TAG_LABELS_VI = {
    "cafe": "cà phê", "food": "ẩm thực", "photo": "chụp ảnh / check-in",
    "quiet": "yên tĩnh, thư giãn", "culture": "văn hoá - lịch sử",
    "shopping": "mua sắm", "nature": "thiên nhiên, hồ, công viên",
    "family": "đi cùng gia đình", "nightlife": "vui chơi buổi tối",
    "romantic": "hẹn hò, lãng mạn", "lively": "sôi động, náo nhiệt",
    "entertainment": "xem phim, giải trí",
}

# loai_hinh (đã bỏ dấu) khớp trực tiếp với tag
TAG_CATEGORY = {
    "cafe": {"cafe"},
    "food": {"an uong"},
    "shopping": {"tttm"},
    "culture": {"tham quan"},
    "photo": {"checkin"},
    "entertainment": {"tttm"},
}

# Cụm từ không dấu dùng để dò trong mô tả DB (so khớp theo ranh giới từ)
TAG_DB_KEYWORDS = {
    "cafe": ["cafe", "ca phe", "coffee", "tra chieu"],
    "food": ["an uong", "am thuc", "quan an", "nha hang", "dac san", "pho bo", "bun cha", "banh", "an sang", "an trua", "an toi"],
    "photo": ["check-in", "check in", "checkin", "song ao", "view", "chup anh", "kien truc", "goc dep"],
    "quiet": ["yen tinh", "thu gian", "nhe nhang", "binh yen", "chill", "thu thai", "lang man"],
    "culture": ["van hoa", "lich su", "di tich", "bao tang", "pho co", "kien truc", "truyen thong", "co kinh", "gia truyen"],
    "shopping": ["mua sam", "thoi trang", "tttm", "sieu thi", "hang hieu"],
    "nature": ["ho guom", "ho tay", "ho truc bach", "ven ho", "quanh ho", "cong vien", "cay xanh", "thien nhien", "ngam canh"],
    "family": ["gia dinh", "tre em", "tre nho", "nhom dong"],
    "nightlife": ["ve dem", "buoi toi", "an toi", "bar", "pho di bo"],
    "romantic": ["hen ho", "lang man", "cap doi"],
    "lively": ["soi dong", "nhon nhip", "vui choi", "giai tri", "nhom dong"],
    "entertainment": ["xem phim", "coi phim", "rap phim", "rap chieu phim", "cinema", "cgv", "lotte cinema", "bhd", "truot bang", "heroworld", "khu vui choi", "khu giai tri"],
}

_INDOOR_KW = ["trong nha", "dieu hoa", "may lanh", "ngam", "tttm", "tang"]
_OUTDOOR_KW = ["ngoai troi", "ven ho", "quanh ho", "cong vien", "dao pho", "dao bo", "san vuon"]
_BUDGET_LOW_KW = ["binh dan", "pho thong", "gia re", "via he", "sinh vien", "gia mem"]
_BUDGET_HIGH_KW = ["cao cap", "hang sang", "sang trong", "hang hieu", "sang chanh", "luxury"]


def _has_phrase(haystack: str, phrase: str) -> bool:
    """So khớp cụm từ theo ranh giới từ (tránh 'bar' khớp nhầm 'barber')."""
    return re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", haystack) is not None


def _point_haystack(point: dict) -> str:
    return _strip_diacritics(" ".join([
        point.get("thong_tin_chi_tiet") or "",
        point.get("review") or "",
        point.get("phu_hop") or "",
        point.get("loai_hinh") or "",
        point.get("mo_ta") or "",
    ]))


def tag_matches(point: dict, tag: str, haystack: Optional[str] = None) -> bool:
    """Điểm có phù hợp tag sở thích không (theo loại hình hoặc từ khoá mô tả)."""
    category = _strip_diacritics(point.get("loai_hinh") or "").strip()
    if category in TAG_CATEGORY.get(tag, set()):
        return True
    hs = haystack if haystack is not None else _point_haystack(point)
    return any(_has_phrase(hs, kw) for kw in TAG_DB_KEYWORDS.get(tag, []))


def calculate_preference_score(point: dict, preference_keywords: list,
                               preferred_tags: Optional[Iterable[str]] = None,
                               indoor_pref: Optional[str] = None,
                               budget: Optional[int] = None) -> float:
    """
    Điểm phù hợp sở thích, thang 0..1 (càng cao càng phù hợp).
    - Nếu người dùng không nhập preference (không có keyword) → trả 0.5 (trung lập),
      để không âm thầm giả vờ đã áp dụng preference khi thực chất không có gì để so khớp.
    - Nếu điểm đến không có dữ liệu mô tả nào → cũng trả 0.5 (trung lập, minh bạch),
      thay vì mặc định 0 (bất lợi oan) hay 1 (ưu ái oan).
    - Gọi kiểu cũ (point, keywords) cho kết quả Y HỆT bản trước. Các tham số mới
      (preferred_tags / indoor_pref / budget) chỉ có tác dụng khi được truyền vào:
        * tag   : tỉ lệ tag khớp (theo loại hình DB hoặc cụm từ mô tả)
        * indoor: +/-0.1 nếu đúng/ngược không gian trong nhà / ngoài trời
        * budget: +/-0.1 nếu đúng/ngược mức giá (1 = bình dân, 3 = cao cấp)
    """
    tags = [t for t in (preferred_tags or []) if t in TAG_DB_KEYWORDS]
    has_extra = bool(tags) or indoor_pref in ("trong_nha", "ngoai_troi") or budget in (1, 3)

    if not preference_keywords and not has_extra:
        return 0.5

    haystack = _point_haystack(point) if has_extra else _strip_diacritics(" ".join([
        point.get("thong_tin_chi_tiet") or "",
        point.get("review") or "",
        point.get("phu_hop") or "",
        point.get("loai_hinh") or "",
    ]))
    if not haystack.strip():
        return 0.5

    components: List[float] = []
    if preference_keywords:
        matched = sum(1 for kw in preference_keywords if kw in haystack)
        components.append(min(1.0, matched / len(preference_keywords)))
    if tags:
        components.append(sum(1 for t in tags if tag_matches(point, t, haystack)) / len(tags))

    score = sum(components) / len(components) if components else 0.5

    if indoor_pref == "trong_nha":
        if any(_has_phrase(haystack, k) for k in _INDOOR_KW):
            score += 0.1
        elif any(_has_phrase(haystack, k) for k in _OUTDOOR_KW):
            score -= 0.1
    elif indoor_pref == "ngoai_troi":
        if any(_has_phrase(haystack, k) for k in _OUTDOOR_KW):
            score += 0.1
        elif any(_has_phrase(haystack, k) for k in _INDOOR_KW):
            score -= 0.1

    if budget == 1:
        if any(_has_phrase(haystack, k) for k in _BUDGET_LOW_KW):
            score += 0.1
        elif any(_has_phrase(haystack, k) for k in _BUDGET_HIGH_KW):
            score -= 0.1
    elif budget == 3:
        if any(_has_phrase(haystack, k) for k in _BUDGET_HIGH_KW):
            score += 0.1
        elif any(_has_phrase(haystack, k) for k in _BUDGET_LOW_KW):
            score -= 0.1

    return max(0.0, min(1.0, score))


def explain_match(point: dict, preference_keywords: list = None,
                  preferred_tags: Optional[Iterable[str]] = None) -> List[str]:
    """Lý do (tiếng Việt) vì sao một điểm hợp sở thích — dùng cho recommendation preview."""
    haystack = _point_haystack(point)
    reasons = [f"hợp sở thích {TAG_LABELS_VI.get(t, t)}"
               for t in (preferred_tags or []) if t in TAG_DB_KEYWORDS and tag_matches(point, t, haystack)]
    kws = [kw for kw in (preference_keywords or []) if kw in haystack]
    if kws:
        reasons.append("khớp từ khoá: " + ", ".join(kws[:4]))
    return reasons
