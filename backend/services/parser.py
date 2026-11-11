"""
LLM-as-Parser (Module 1 of Semantic Engine) — v2.

Câu lệnh tự nhiên → dữ liệu có cấu trúc cho Backend + cơ chế HỎI LẠI.

Ví dụ: "Đi Hà Nội 1 ngày, thích cafe và chụp ảnh, đi xe máy"
  → region=Hanoi, 08:00–20:00 (cả ngày), vehicle=xe_may, tags=[cafe, photo]

Luồng xử lý:
  1. Rule-based (LUÔN chạy, xác định, < 5 ms): thời gian, ngày, phương tiện,
     điểm xuất phát, khu vực, sở thích (tag), nhịp độ, ngân sách, số người,
     điểm bắt buộc / cần tránh.
  2. Ollama (tuỳ chọn) chỉ BỔ SUNG khi rule-based không nhận ra sở thích nào.
     LLM KHÔNG được ghi đè giờ / ngày / phương tiện: model 1B chạy local hay
     "bịa" các giá trị này, còn rule-based thì đúng hoặc để trống (để hỏi lại).
  3. Gộp với ngữ cảnh form Screen 1 (context) → xác định trường còn thiếu /
     mâu thuẫn → sinh câu hỏi lại (clarification_questions).

Hợp đồng đầu ra: xem docstring của parse_prompt(). Các khoá cũ
(time_limit_minutes, preferences, preference_weights, must_visit, avoid,
vehicle_hint, time_limit_explicit, source) được GIỮ NGUYÊN để
/api/optimize-route-hybrid và test cũ không phải sửa.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from services.ollama import generate as ollama_generate

# ============================================================
# TIỆN ÍCH CHUẨN HOÁ
# ============================================================

def _strip(text: str) -> str:
    """Bỏ dấu + hạ chữ thường (đ → d)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D").lower()


def _low(text: str) -> str:
    """Hạ chữ thường nhưng GIỮ dấu (NFC) — cần để phân biệt 'tối' / 'tới' / 'tôi'."""
    return unicodedata.normalize("NFC", text or "").lower()


def _normalize_typos(text: str) -> str:
    """Chuẩn hoá lỗi chính tả, teencode phổ biến trước khi phân tích."""
    if not text:
        return ""
    # "dị" / "dì" thay cho "đi" khi đi kèm động từ / hành động di chuyển hoặc vui chơi
    text = re.sub(
        r"\b(dị|dì)\s+(?=(?:xem|coi|ăn|chơi|tham quan|ghé|uống|cafe|cà phê|chụp|lượn|bộ|xe|về|đến|tới|hà nội|hn)\b)",
        "đi ",
        text,
        flags=re.I
    )
    # "dị" sau từ chỉ ý định (muốn, thích, định, cần, sẽ)
    text = re.sub(
        r"\b(muốn|thích|định|cần|sẽ|muon|thich|dinh|can|se)\s+(dị|dì)\b",
        r"\1 đi",
        text,
        flags=re.I
    )
    # "dị" ở đầu câu: "dị xem phim..." -> "đi xem phim..."
    text = re.sub(r"^\s*(dị|dì)\b", "đi", text, flags=re.I)
    # "tui" -> "tôi"
    text = re.sub(r"\btui\b", "tôi", text, flags=re.I)
    # "cf", "ca fe" -> "cà phê"
    text = re.sub(r"\b(cf|ca fe)\b", "cà phê", text, flags=re.I)
    return text


def _find(pattern: str, text: str) -> Optional[re.Match]:
    return re.search(pattern, text, re.I)


def _word(phrase: str) -> str:
    """Regex cụm từ theo ranh giới từ (unicode-aware)."""
    return r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"


_NEGATIONS = ("không", "khong", "ko", "chẳng", "chang", "đừng", "dung", "tránh", "tranh",
              "chả", "cha", "chưa", "chua", "bỏ qua", "bo qua", "trừ", "tru", "hổng")


def _is_negated(low_text: str, start: int) -> bool:
    """Cụm từ tại vị trí `start` có nằm sau một từ phủ định gần đó không
    (VD: 'không muốn đi bộ', 'không thích mua sắm')."""
    window = low_text[max(0, start - 18):start]
    return any(re.search(_word(n), window) for n in _NEGATIONS)


def _hhmm(h: int, m: int = 0) -> str:
    h = max(0, min(23, h))
    m = max(0, min(59, m))
    return f"{h:02d}:{m:02d}"


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _from_minutes(total: int) -> str:
    total = max(0, min(23 * 60 + 59, int(total)))
    return _hhmm(total // 60, total % 60)


# ============================================================
# 1. THỜI GIAN
# ============================================================
# Đồng hồ: 8h, 8h30, 8 giờ, 8:30, 8g30 (+ buổi: sáng/trưa/chiều/tối/đêm)
_PERIOD = r"(?:\s*(sáng|sang|trưa|trua|chiều|chieu|tối|đêm|dem))?"
_CLOCK = (r"(\d{1,2})\s*(?:h(?![a-zà-ỹ])|giờ|gio(?![a-z])|g(?=\d)|:(?=\d))\s*(\d{1,2})?"
          r"(?:\s*(?:phút|phut|p)(?![a-zà-ỹ]))?" + _PERIOD)
_RANGE_SEP = r"\s*(?:-|–|—|~|->|đến|den|tới|cho đến|cho tới)\s*"
_START_CUES = r"(?:lúc|luc|từ|tu|bắt đầu|bat dau|xuất phát|xuat phat|khởi hành|khoi hanh|đi lúc|di luc)\s*(?:khoảng|khoang|tầm|tam|vào|vao)?\s*"
_END_CUES = r"(?:đến|den|tới|trước|truoc|về lúc|ve luc|về trước|ve truoc|về|kết thúc|ket thuc|xong lúc|xong luc|xong trước|tới khoảng|đến khoảng)\s*(?:khoảng|khoang|tầm|tam)?\s*"

_PART_OF_DAY = [
    # (regex trên text đã bỏ dấu, start, end, nhãn)
    (r"\b(buoi sang|sang nay|sang mai)\b", "08:00", "11:30", "buổi sáng"),
    (r"\b(buoi trua|trua nay|trua mai)\b", "11:00", "14:00", "buổi trưa"),
    (r"\b(buoi chieu|chieu nay|chieu mai)\b", "13:30", "17:30", "buổi chiều"),
    (r"\b(buoi toi|toi nay|toi mai)\b", "18:00", "22:00", "buổi tối"),
]

_FULL_DAY = r"(?<!\w)(cả ngày|ca ngay|1 ngày|1 ngay|một ngày|mot ngay|trọn ngày|tron ngay|full day|nguyên ngày|nguyen ngay)(?!\w)"
_HALF_DAY = r"(?<!\w)(nửa ngày|nua ngay|half day|nửa buổi|nua buoi)(?!\w)"
_MULTI_DAY = r"(?<!\w)(\d+|hai|ba|bốn|bon|năm)\s*(?:ngày|ngay)(?:\s*\d*\s*(?:đêm|dem))?(?!\w)"
_WORD_NUM = {"hai": 2, "ba": 3, "bốn": 4, "bon": 4, "năm": 5}


def _apply_period(h: int, period: Optional[str]) -> int:
    p = _strip(period or "")
    if p == "sang":
        return 0 if h == 12 else h
    if p == "trua":
        return h + 12 if 1 <= h <= 4 else h
    if p == "chieu":
        return h + 12 if h < 12 else h
    if p in ("toi",):           # chỉ 'tối' có dấu mới vào được nhánh này
        return h + 12 if 1 <= h < 12 else h
    if p == "dem":
        if h == 12:
            return 0
        return h + 12 if 6 <= h < 12 else h
    return h


def _clock_from_match(m: re.Match, offset: int = 0) -> Optional[str]:
    try:
        h = int(m.group(1 + offset))
        mm = int(m.group(2 + offset) or 0)
        period = m.group(3 + offset)
    except (TypeError, ValueError):
        return None
    if h > 24 or mm > 59:
        return None
    h = _apply_period(h, period)
    if h == 24:
        h = 23
        mm = 59
    return _hhmm(h, mm)


def _extract_time(low: str, s: str) -> Dict[str, Any]:
    """Trả về {start, end, duration, full_day, half_day, part_of_day, multi_day, notes}."""
    out: Dict[str, Any] = {"start": None, "end": None, "duration": None, "full_day": False,
                           "half_day": False, "part_of_day": None, "multi_day": None}
    work = low

    # 1a. Khoảng giờ "từ 8h đến 17h", "8h-17h", "8 giờ sáng tới 9 giờ tối"
    m = _find(r"(?:từ|tu)?\s*" + _CLOCK + _RANGE_SEP + _CLOCK, work)
    if m:
        a = _clock_from_match(m, 0)
        b = _clock_from_match(m, 3)
        # "8h-5h chiều": nếu giờ kết thúc nhỏ hơn và không ghi buổi → hiểu là buổi chiều
        if a and b and _to_minutes(b) <= _to_minutes(a) and not m.group(6) and int(m.group(4)) < 12:
            b = _from_minutes(_to_minutes(b) + 12 * 60)
        if a and b:
            out["start"], out["end"] = a, b
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]

    # 1b. Giờ bắt đầu có từ dẫn: "xuất phát lúc 14h", "bắt đầu 9h sáng"
    if not out["start"]:
        m = _find(_START_CUES + _CLOCK, work)
        if m:
            out["start"] = _clock_from_match(m, 0)
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]

    # 1c. Giờ kết thúc có từ dẫn: "về trước 18h", "kết thúc lúc 21h"
    if not out["end"]:
        m = _find(_END_CUES + _CLOCK, work)
        if m:
            out["end"] = _clock_from_match(m, 0)
            work = work[:m.start()] + " " * (m.end() - m.start()) + work[m.end():]

    # 1d. Thời lượng: "3 tiếng", "2 tiếng rưỡi", "90 phút", "đi 5 giờ"
    m = _find(r"(\d+(?:[.,]\d+)?)\s*(?:tiếng|tieng)(\s*(?:rưỡi|ruoi))?", work)
    if m:
        out["duration"] = int(float(m.group(1).replace(",", ".")) * 60) + (30 if m.group(2) else 0)
    else:
        m = _find(r"(?:trong|khoảng|khoang|tầm|tam|mất|mat|chơi|choi|đi|di)\s*(?:khoảng|tầm)?\s*(\d{1,2})\s*(?:giờ|gio|h)(?![a-zà-ỹ\d])(?!\s*(?:sáng|sang|trưa|trua|chiều|chieu|tối|đêm|dem))", work)
        if m:
            out["duration"] = int(m.group(1)) * 60
        else:
            m = _find(r"(?<![\d:h])(\d{2,3})\s*(?:phút|phut)(?![a-zà-ỹ])", work)
            if m:
                out["duration"] = int(m.group(1))

    # 1e. Giờ đơn lẻ còn sót, có ghi buổi ("9h sáng") → coi là giờ bắt đầu
    if not out["start"] and not out["end"]:
        m = _find(r"(\d{1,2})\s*(?:h(?![a-zà-ỹ])|giờ|gio(?![a-z]))\s*(\d{1,2})?\s*(sáng|sang|trưa|trua|chiều|chieu|tối|đêm|dem)", work)
        if m:
            out["start"] = _clock_from_match(m, 0)

    # 1f. Cả ngày / nửa ngày / nhiều ngày / buổi
    md = _find(_MULTI_DAY, low)
    if md:
        raw = md.group(1)
        n = _WORD_NUM.get(raw, None) if not raw.isdigit() else int(raw)
        if n and n >= 2:
            out["multi_day"] = n
    if _find(_FULL_DAY, low) or out["multi_day"]:
        out["full_day"] = True
    if _find(_HALF_DAY, low):
        out["half_day"] = True
    for pat, a, b, label in _PART_OF_DAY:
        if re.search(pat, s):
            out["part_of_day"] = (a, b, label)
            break
    return out


# ============================================================
# 2. NGÀY ĐI
# ============================================================
_WEEKDAYS = {
    "2": 0, "hai": 0, "3": 1, "ba": 1, "4": 2, "tu": 2, "5": 3, "nam": 3,
    "6": 4, "sau": 4, "7": 5, "bay": 5,
}
_WEEKDAY_VI = ["thứ hai", "thứ ba", "thứ tư", "thứ năm", "thứ sáu", "thứ bảy", "chủ nhật"]


def _next_weekday(today: date, wd: int, next_week: bool = False) -> date:
    delta = (wd - today.weekday()) % 7
    d = today + timedelta(days=delta)
    if next_week:
        d += timedelta(days=7)
    return d


def _extract_date(original: str, s: str, today: date) -> Tuple[Optional[date], Optional[str]]:
    """Trả về (ngày, cảnh_báo)."""
    next_week = bool(re.search(r"\btuan sau|tuan toi\b", s))

    # dd/mm(/yyyy) hoặc "ngày 15 tháng 10"
    m = re.search(r"(?<![\d:])(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*(\d{2,4}))?(?![\d:h])", s)
    if not m:
        m2 = re.search(r"\bngay\s+(\d{1,2})\s+thang\s+(\d{1,2})(?:\s+nam\s+(\d{4}))?", s)
        m = m2
    if m:
        try:
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else today.year
            if y < 100:
                y += 2000
            cand = date(y, mo, d)
            if not m.group(3) and cand < today:
                cand = date(y + 1, mo, d)
            if cand < today:
                return None, f"Ngày {cand.strftime('%d/%m/%Y')} đã qua nên mình chưa dùng."
            return cand, None
        except ValueError:
            pass

    if re.search(r"\b(hom nay|ngay hom nay)\b", s):
        return today, None
    if re.search(r"\b(ngay kia|ngay mot)\b", s) or re.search(_word("mốt"), _low(original)):
        return today + timedelta(days=2), None
    # 'mai' viết thường (tránh nhầm tên riêng như 'Mai Hắc Đế', 'Hoa Mai')
    if re.search(r"\b(ngay mai)\b", s) or re.search(r"(?<!\w)mai(?!\w)", original):
        return today + timedelta(days=1), None
    if re.search(r"\b(chu nhat|cn)\b", s):
        return _next_weekday(today, 6, next_week), None
    m = re.search(r"\bthu\s*(2|3|4|5|6|7|hai|ba|tu|nam|sau|bay)\b", s)
    if m:
        return _next_weekday(today, _WEEKDAYS[m.group(1)], next_week), None
    if re.search(r"\bcuoi tuan\b", s):
        if today.weekday() >= 5 and not next_week:
            return today, None
        return _next_weekday(today, 5, next_week), None
    m = re.search(r"\bngay\s+(\d{1,2})\b(?!\s*(?:dem|ngay|tieng|gio))", s)
    if m:
        try:
            d = int(m.group(1))
            cand = date(today.year, today.month, d)
            if cand < today:
                nm = today.month % 12 + 1
                cand = date(today.year + (1 if nm == 1 else 0), nm, d)
            return cand, None
        except ValueError:
            pass
    return None, None


# ============================================================
# 3. PHƯƠNG TIỆN · KHU VỰC · ĐIỂM XUẤT PHÁT · SỐ NGƯỜI
# ============================================================
_VEHICLE_PATTERNS = [
    # (regex trên text bỏ dấu, mã phương tiện) — thứ tự quan trọng: cụ thể trước
    (r"\b(29|30|35|45)\s*cho\b|\bxe khach\b|\bxe buyt du lich\b", "xe_29_cho"),
    (r"\b(16|15)\s*cho\b", "xe_16_cho"),
    (r"\b(o\s?to|oto|xe hoi|taxi|grab ?car|xe con|(4|5|7)\s*cho|lai xe|xe rieng)\b", "o_to"),
    (r"\b(xe may|honda|grab ?bike|xe om|vespa|xe tay ga|xe so)\b", "xe_may"),
    (r"\b(xe dap)\b", "xe_dap"),
    (r"\b(di bo|tan bo|cuoc bo|walking)\b", "di_bo"),
]
VEHICLE_LABELS = {
    "xe_may": "xe máy", "o_to": "ô tô", "xe_16_cho": "xe 16 chỗ",
    "xe_29_cho": "xe 29 chỗ", "di_bo": "đi bộ", "xe_dap": "xe đạp",
}

_HANOI = r"\b(ha noi|hanoi|hn|thu do)\b"
_OTHER_CITIES = [
    "da nang", "sai gon", "ho chi minh", "tphcm", "tp hcm", "hcm", "hai phong", "ha long",
    "nha trang", "da lat", "hoi an", "sapa", "sa pa", "ninh binh", "phu quoc", "vung tau",
    "can tho", "quy nhon", "phan thiet", "mui ne", "ha giang", "co do hue", "thanh pho hue",
]

_START_POINT_RE = re.compile(
    r"(?:xuất phát|xuat phat|khởi hành|khoi hanh|bắt đầu|bat dau|đi|di)\s+(?:từ|tu)\s+"
    r"(?!(?:\d{1,2}\s*(?:h(?![a-zà-ỹ])|giờ|gio|:)|(?:\d{1,2}\s*(?:sáng|trưa|chiều|tối))))"
    r"(?P<sp>[^,.;!?\n]+?)"
    r"(?=$|[,.;!?\n]|\s+(?:lúc|luc|vào|vao|và|va|rồi|roi|đến|den|tới|bằng|bang|đi|khoảng|khoang|tầm|tam|thích|thich|muốn|muon|sáng|chiều|tối)(?!\w))",
    re.I,
)
_STAY_RE = re.compile(
    r"(?:đang ở|dang o|mình ở|tôi ở|em ở|ở gần|o gan)\s+(?P<sp>(?:khách sạn|khach san|ks|homestay|hotel)[^,.;!?\n]*?)"
    r"(?=$|[,.;!?\n]|\s+(?:và|va|rồi|muốn|thích|lúc|đi)(?!\w))",
    re.I,
)
_VAGUE_START = {"nha", "nha minh", "nha toi", "nha em", "day", "cho toi", "cho minh", "o day", "nha rieng", "cho o"}


def _extract_vehicle(low: str, s: str) -> Tuple[Optional[str], List[str]]:
    found: List[Tuple[int, str]] = []
    for pat, code in _VEHICLE_PATTERNS:
        for m in re.finditer(pat, s):
            if _is_negated(s, m.start()):
                continue
            found.append((m.start(), code))
            break
    if not found:
        return None, []
    found.sort()
    codes = []
    for _, c in found:
        if c not in codes:
            codes.append(c)
    return codes[0], codes


def _extract_region(s: str) -> Tuple[str, Optional[str], bool]:
    """(region, thành_phố_khác_nếu_có, có_nhắc_Hà_Nội)"""
    has_hanoi = bool(re.search(_HANOI, s))
    other = next((c for c in _OTHER_CITIES if re.search(r"\b" + re.escape(c) + r"\b", s)), None)
    return "Hanoi", other, has_hanoi


def _extract_start_point(original: str) -> Optional[str]:
    for rx in (_START_POINT_RE, _STAY_RE):
        m = rx.search(original)
        if m:
            sp = m.group("sp").strip(" -:\"'“”")
            sp = re.sub(r"\s+", " ", sp)
            if 2 <= len(sp) <= 80:
                return sp
    return None


def _extract_group(s: str) -> Tuple[Optional[int], Optional[str]]:
    size = None
    m = re.search(r"\b(?:doan|nhom|team)?\s*(\d{1,3})\s*(?:nguoi|khach|thanh vien|ban|dua)\b", s)
    if m:
        size = int(m.group(1))
    gtype = None
    if re.search(r"\b(mot minh|1 minh|di le|solo)\b", s):
        gtype, size = "solo", size or 1
    elif re.search(r"\b(nguoi yeu|ban gai|ban trai|cap doi|vo chong|hen ho|2 vo chong)\b", s):
        gtype, size = "couple", size or 2
    elif re.search(r"\b(gia dinh|bo me|ong ba|tre con|tre em|con nho)\b", s):
        gtype = "family"
    elif re.search(r"\b(ban be|hoi ban|nhom ban|dong nghiep|cong ty|team)\b", s):
        gtype = "friends"
    return size, gtype


# ============================================================
# 4. SỞ THÍCH · NHỊP ĐỘ · NGÂN SÁCH · KHÔNG GIAN
# ============================================================
# Mỗi phần tử: cụm từ CÓ DẤU. Mặc định so khớp cả bản có dấu lẫn bản bỏ dấu;
# tiền tố "=" nghĩa là CHỈ so bản có dấu (tránh nhập nhằng: đền/đến, chùa/chưa,
# phở/phố, sông/sống, hồ/họ, bé/be...).
TAG_ICONS: Dict[str, str] = {
    "cafe": "☕ Cà phê",
    "photo": "📸 View đẹp / Sống ảo",
    "quiet": "🍃 Yên tĩnh, thư giãn",
    "lively": "🔥 Nhộn nhịp, sôi động",
    "food": "🍜 Ẩm thực, món ngon",
    "culture": "🏛️ Văn hóa - Lịch sử",
    "nature": "🌳 Thiên nhiên ven hồ",
    "entertainment": "🎬 Xem phim, giải trí",
    "shopping": "🛍️ Mua sắm",
    "nightlife": "🌙 Về đêm, dạo phố",
    "romantic": "❤️ Hẹn hò lãng mạn",
    "family": "👨‍👩‍👧 Gia đình",
}

_TAG_PHRASES: Dict[str, List[str]] = {
    "cafe": ["cafe", "cà phê", "cafè", "coffee", "café", "trà sữa", "tiệm trà", "quán nước", "cf", "trà chanh", "cà phê trứng", "cafe trứng", "thưởng trà", "quán trà", "uống trà"],
    "food": ["ăn uống", "ẩm thực", "đồ ăn", "quán ăn", "ăn trưa", "ăn sáng", "ăn tối", "ăn vặt", "=phở",
             "bún chả", "bún", "bánh", "đặc sản", "nhà hàng", "món ngon", "ăn ngon", "food", "ăn",
             "bún đậu", "chả cá", "nem chua", "bánh mì", "nộm", "xôi", "ốc", "ăn đêm", "ăn nhẹ", "food tour", "la cà quán xá", "món ăn", "ăn thử", "thưởng thức ẩm thực"],
    "photo": ["chụp ảnh", "chụp hình", "check in", "check-in", "checkin", "sống ảo", "view đẹp", "view", "instagram",
              "góc đẹp", "selfie", "photo", "quay phim", "chụp choẹt", "ngắm cảnh", "hoàng hôn", "bình minh", "view hồ", "rooftop"],
    "quiet": ["yên tĩnh", "vắng vẻ", "ít người", "thư giãn", "nhẹ nhàng", "chill", "bình yên", "tránh đông",
              "không đông", "relax", "quiet", "thư thái", "tĩnh lặng", "yên ắng", "an yên", "đọc sách", "chữa lành", "làm việc", "thanh tịnh", "trầm lắng", "tĩnh tâm", "không ồn ào"],
    "culture": ["văn hóa", "văn hoá", "lịch sử", "di tích", "bảo tàng", "=đền", "=chùa", "phố cổ", "kiến trúc",
                "truyền thống", "cổ kính", "văn miếu", "lăng bác", "culture", "tâm linh", "hỏa lò", "hoả lò", "nhà tù", "phố sách", "chùa chiền", "di sản", "lăng tẩm", "hoàng thành", "thắp hương", "cầu an"],
    "shopping": ["mua sắm", "shopping", "trung tâm thương mại", "tttm", "mall", "siêu thị", "quần áo", "mua đồ", "chợ", "chợ đêm", "quà lưu niệm", "sắm đồ", "lượn mall"],
    "nature": ["thiên nhiên", "công viên", "hồ tây", "hồ gươm", "ven hồ", "=hồ", "cây xanh", "vườn", "=sông",
               "nature", "park", "ngoài trời", "dạo bộ", "hóng gió", "dạo hồ", "tản bộ", "đi dạo"],
    "family": ["gia đình", "trẻ em", "trẻ con", "=bé", "con nhỏ", "bố mẹ", "ông bà", "family", "kids"],
    "nightlife": ["về đêm", "buổi tối", "nightlife", "bar", "pub", "phố đi bộ", "chợ đêm", "đi đêm", "tạ hiện", "uống bia", "bia hơi", "nhạc sống"],
    "romantic": ["hẹn hò", "lãng mạn", "người yêu", "bạn gái", "bạn trai", "cặp đôi", "romantic", "2 người", "riêng tư"],
    "lively": ["sôi động", "náo nhiệt", "vui nhộn", "nhộn nhịp", "đông vui", "vui chơi", "giải trí", "tấp nập", "sầm uất", "náo nức", "quẩy",
               "sôi nổi", "hoạt động sôi nổi", "hoạt náo", "nhộn", "xôm", "xôm tụ", "rôm rả", "bung nóc", "cháy hết mình", "phố xá nhộn nhịp", "đông đúc", "năng động", "nhiều năng lượng"],
    "entertainment": ["xem phim", "chiếu phim", "rạp phim", "rạp chiếu phim", "rap phim", "rap chieu phim",
                      "chieu phim", "coi phim", "xem film", "xem movie", "cinema", "cgv", "lotte cinema",
                      "bhd", "bhd star", "giải trí", "vui chơi", "trượt băng", "bowling", "heroworld",
                      "khu vui chơi", "khu giải trí"],
}

_KNOWN_PLACES = [
    ("Cafe Giảng", ["cafe giang", "ca phe giang", "giang cafe", "ca phe trung", "cafe trung"]),
    ("Phở Thìn Bờ Hồ", ["pho thin", "pho thin bo ho"]),
    ("Bún chả Hương Liên", ["bun cha huong lien", "bun cha obama", "huong lien"]),
    ("Bánh tôm Hồ Tây", ["banh tom ho tay", "banh tom thanh nien", "banh tom"]),
    ("Vua chả cá", ["vua cha ca"]),
    ("Bún đậu Ngõ Trạm", ["bun dau ngo tram", "bun dau mam tom ngo tram"]),
    ("Phở cuốn Hương Mai", ["pho cuon huong mai", "pho cuon ngu xa", "huong mai ngu xa"]),
    ("Nộm Long Vi Dung", ["nom long vi dung", "nom hang bac", "long vi dung"]),
    ("Bánh mì Trâm", ["banh mi tram", "banh mi dinh liet"]),
    ("Miến lươn Chân Cầm", ["mien luon chan cam"]),
    ("Xôi Yến", ["xoi yen", "xoi yen nguyen huu huan"]),
    ("Ban Công Cafe", ["ban cong cafe", "cafe ban cong", "ban cong dinh liet"]),
    ("Loading T Cafe", ["loading t cafe", "loading t", "cafe loading t"]),
    ("Blackbird Coffee", ["blackbird coffee", "blackbird cafe", "blackbird chan cam"]),
    ("Cotero Coffee", ["cotero coffee", "cotero"]),
    ("La Mensa", ["la mensa", "la mensa tong dan"]),
    ("All Day Coffee", ["all day coffee", "all day quang trung"]),
    ("Yên Cafe", ["yen cafe", "cafe yen", "yen quan thanh"]),
    ("Hasu Cafe", ["hasu cafe", "hasu"]),
    ("Atelier Coffee", ["atelier coffee", "atelier"]),
    ("Đền Ngọc Sơn", ["den ngoc son", "ngoc son"]),
    ("Hồ Hoàn Kiếm", ["ho hoan kiem", "ho guom", "bo ho"]),
    ("Lăng Bác", ["lang bac", "lang chu tich", "lang ho chi minh", "lang chu tich ho chi minh"]),
    ("Văn Miếu", ["van mieu", "quoc tu giam", "van mieu quoc tu giam"]),
    ("Chùa Trấn Quốc", ["chua tran quoc", "tran quoc"]),
    ("Hồ Tây", ["ho tay", "duong thanh nien", "ven ho tay", "quanh ho tay"]),
    ("Nhà thờ Lớn", ["nha tho lon", "nha tho lon ha noi"]),
    ("Nhà tù Hỏa Lò", ["hoa lo", "nha tu hoa lo"]),
    ("Phố đi bộ", ["pho di bo", "pho di bo ho guom"]),
    ("Phố Tạ Hiện", ["ta hien", "pho ta hien", "bia ta hien"]),
    ("Chợ Đồng Xuân", ["cho dong xuan", "dong xuan"]),
    ("Tràng Tiền Plaza", ["trang tien plaza", "kem trang tien"]),
    ("Vincom Bà Triệu", ["vincom ba trieu", "vincom"]),
    ("Lotte Center", ["lotte center", "lotte lieu giai", "lotte cinema"]),
    ("Royal City", ["royal city", "vincom royal city"]),
    ("Aeon Mall", ["aeon mall", "aeon long bien"]),
    ("Rạp Quốc Gia", ["rap quoc gia", "chieu phim quoc gia", "trung tam chieu phim quoc gia"]),
]
_PACE_SLOW = ["thong thả", "chậm", "nhẹ nhàng", "không vội", "thư thái", "ít điểm", "từ từ", "nghỉ nhiều", "không gấp"]
_PACE_FAST = ["nhiều điểm", "càng nhiều càng tốt", "đi nhanh", "tranh thủ", "dày", "tối đa", "càng nhiều chỗ"]
_BUDGET_LOW = ["rẻ", "bình dân", "tiết kiệm", "sinh viên", "giá mềm", "ít tiền", "hạt dẻ"]
_BUDGET_MID = ["tầm trung", "vừa túi tiền", "giá vừa phải"]
_BUDGET_HIGH = ["sang trọng", "cao cấp", "sang chảnh", "xịn", "luxury", "đắt tiền", "thượng hạng"]
_INDOOR = ["trong nhà", "điều hòa", "điều hoà", "máy lạnh", "tránh nắng", "trời mưa", "khi mưa", "có mái che"]
_OUTDOOR = ["ngoài trời", "dạo bộ", "không khí trong lành", "thoáng đãng", "hóng gió", "picnic", "dã ngoại"]
_CROWD_HINTS = ["tránh đông", "ít người", "vắng", "không đông", "đông quá", "quá đông", "đông người", "ồn ào", "yên tĩnh"]
_TIME_HINTS = ["gần", "ít di chuyển", "tiết kiệm thời gian", "không đi xa", "quanh đây", "lân cận"]

# Từ "cấu trúc" (thời gian, phương tiện, khu vực...) — không tính là sở thích khi
# trích từ khoá nội dung, để câu "Đi Hà Nội 1 ngày bằng xe máy" không bị coi là có sở thích.
_STRUCT_STOP = {
    "ha", "noi", "hanoi", "hn", "di", "ngay", "gio", "tieng", "phut", "sang", "trua", "chieu", "toi",
    "dem", "buoi", "xe", "may", "bang", "tu", "den", "luc", "hom", "nay", "mai", "kia", "oto", "bo",
    "dap", "cho", "nguoi", "minh", "ban", "thu", "cuoi", "tuan", "xuat", "phat", "bat", "dau", "khoang",
    "tam", "truoc", "sau", "ve", "ket", "thuc", "nua", "ca", "mot", "hai", "ba", "choi", "tour", "lich",
    "trinh", "muon", "thich", "can", "giup", "len", "ke", "hoach", "va", "voi", "nhe", "oi", "chuyen",
    "dia", "diem", "quan", "khach", "san", "ks", "gan", "the", "nao", "gi", "dau", "duoc", "khong",
    "ko", "em", "anh", "chi", "toi", "tao", "chung", "cung", "dang", "se", "da", "roi", "nhung", "cac",
    "lam", "hay", "tro", "trong", "ngoai", "vao", "ra", "qua", "lai", "nua", "thi", "ma", "neu", "vi",
    "nen", "phai", "nhat", "dinh", "tranh", "moi", "tat", "tung", "tinh", "thanh", "pho", "o", "taxi",
    "grab", "honda", "lai", "rieng", "hoi", "thoi", "gian", "nhanh", "cham", "re", "dat", "tien",
    "full", "day", "half", "h", "g", "p", "k", "tr", "trieu", "nghin", "ngan", "tro", "lu", "nhom", "kham", "pha", "hoat", "dong",
    "doan", "gia", "dinh",
}
_ALLOWED_GENERIC_STOP = {
    "la", "va", "co", "cua", "nhieu", "rat", "mot", "toi", "muon", "thich",
    "khong", "gian", "de", "cho", "nhu", "hay", "o", "tai", "voi", "the",
    "nay", "duoc", "nhung", "cac", "nen", "hon", "it", "moi", "ve", "roi",
}


def _phrase_hits(low: str, s: str, phrase: str) -> List[int]:
    """Vị trí (trên text có dấu hoặc bỏ dấu, cùng độ dài) mà cụm từ xuất hiện."""
    exact = phrase.startswith("=")
    p = phrase[1:] if exact else phrase
    hits = [m.start() for m in re.finditer(_word(_low(p)), low)]
    if not exact:
        hits += [m.start() for m in re.finditer(_word(_strip(p)), s)]
    return hits


def _any_phrase(low: str, s: str, phrases: List[str], allow_negated: bool = False) -> bool:
    for ph in phrases:
        for pos in _phrase_hits(low, s, ph):
            if allow_negated or not _is_negated(low, pos):
                return True
    return False


def _extract_tags(low: str, s: str) -> Tuple[List[str], List[str]]:
    """(tag_thích, tag_không_thích). 'không thích mua sắm' → shopping vào danh sách tránh."""
    liked, disliked = [], []
    for tag, phrases in _TAG_PHRASES.items():
        pos_hit = neg_hit = False
        for ph in phrases:
            for pos in _phrase_hits(low, s, ph):
                if _is_negated(low, pos):
                    neg_hit = True
                else:
                    pos_hit = True
        # 'tránh đông' là sở thích yên tĩnh (bản thân cụm đã chứa 'tránh')
        if tag == "quiet" and _any_phrase(low, s, ["tránh đông", "không đông"], allow_negated=True):
            pos_hit, neg_hit = True, False
        if pos_hit:
            liked.append(tag)
        elif neg_hit:
            disliked.append(tag)
    return liked, disliked


def _extract_budget(low: str, s: str) -> Tuple[Optional[int], Optional[int]]:
    vnd = None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(k|nghin|ngan|tr|trieu|cu)\b", s)
    if m:
        val = float(m.group(1).replace(",", "."))
        unit = m.group(2)
        vnd = int(val * (1_000_000 if unit in ("tr", "trieu", "cu") else 1_000))
    level = None
    if _any_phrase(low, s, _BUDGET_HIGH):
        level = 3
    elif _any_phrase(low, s, _BUDGET_MID):
        level = 2
    elif _any_phrase(low, s, ["=rẻ"] + _BUDGET_LOW[1:]):
        level = 1
    if level is None and vnd:
        level = 1 if vnd <= 300_000 else (2 if vnd <= 1_000_000 else 3)
    return level, vnd


def _extract_list_after(original: str, cues: List[str]) -> List[str]:
    """Trích cụm danh từ sau từ dẫn ('nhất định phải đến X', 'tránh Y')."""
    cue_rx = "|".join(re.escape(c) for c in sorted(cues, key=len, reverse=True))
    rx = re.compile(
        r"(?<!\w)(?:" + cue_rx + r")\s+(?:đến|tới|ghé|đi|qua|ăn|chỗ|những chỗ|các chỗ|nơi|khu)?\s*"
        r"(?P<x>[^,.;!?\n]+?)(?=$|[,.;!?\n]|\s+(?:và|rồi|nhé|nha|lúc|bằng|vì|để|nhưng)(?!\w))",
        re.I,
    )
    out = []
    for m in rx.finditer(original):
        x = m.group("x").strip(" \"'“”")
        if 2 <= len(x) <= 60 and not re.match(r"^\d", x):
            out.append(x)
    return out


_MUST_CUES = ["nhất định phải", "nhat dinh phai", "bắt buộc", "bat buoc", "phải đến", "phai den",
              "phải ghé", "phai ghe", "muốn ghé", "muon ghe", "nhất định", "ưu tiên ghé", "nhớ ghé", "kiểu gì cũng"]
_AVOID_CUES = ["tránh", "không muốn đến", "không muốn đi", "không thích", "đừng", "bỏ qua", "không đi", "trừ",
               "khong muon den", "khong thich", "bo qua", "khong di"]
# Cụm 'tránh ...' không phải tên địa điểm / loại hình mà là sở thích khác
_AVOID_NOT_PLACE = ["đông", "nắng", "mưa", "ồn", "kẹt xe", "tắc đường", "đi xa", "xa", "vội", "mệt", "đi bộ nhiều",
                    "nóng", "giờ cao điểm"]


# ============================================================
# 5. LLM (TUỲ CHỌN) — chỉ bổ sung sở thích / must_visit / avoid
# ============================================================
def _llm_enrich(prompt: str, timeout: float) -> Optional[Dict[str, Any]]:
    from api.preferences import PREFERENCE_TAGS
    system = (
        "You are a strict JSON parser for a Hanoi day-trip planner. "
        "Read the Vietnamese or English user prompt and return ONLY a JSON object with keys:\n"
        f"  preferences (array, subset of: {', '.join(PREFERENCE_TAGS)}),\n"
        "  must_visit (array of place names the user insists on, may be empty),\n"
        "  avoid (array of place names or categories to skip, may be empty).\n"
        "Do not invent places. Use empty arrays if unsure."
    )
    res = ollama_generate(system + "\n\nUser prompt:\n" + prompt[:1500], timeout=timeout, json_mode=True)
    if not res.get("ok"):
        return None
    m = re.search(r"\{.*\}", res.get("text") or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except Exception:
        return None
    prefs = obj.get("preferences") or []
    if isinstance(prefs, str):
        prefs = [prefs]
    return {
        "preferences": [p for p in (str(x).strip().lower() for x in prefs) if p in PREFERENCE_TAGS],
        "must_visit": [str(x).strip() for x in (obj.get("must_visit") or []) if str(x).strip()][:10],
        "avoid": [str(x).strip() for x in (obj.get("avoid") or []) if str(x).strip()][:10],
    }


# ============================================================
# 6. CÂU HỎI LẠI
# ============================================================
_PREF_OPTIONS = [
    {"label": "☕ Cà phê & chụp ảnh", "value": "thích cà phê và chụp ảnh"},
    {"label": "🍜 Ẩm thực Hà Nội", "value": "thích khám phá ẩm thực"},
    {"label": "🏛️ Văn hoá – lịch sử", "value": "thích văn hoá lịch sử"},
    {"label": "🎬 Xem phim & giải trí", "value": "thích xem phim giải trí"},
    {"label": "🌿 Yên tĩnh, thư giãn", "value": "thích yên tĩnh thư giãn"},
    {"label": "🛍️ Mua sắm", "value": "thích mua sắm"},
]


def _q(field: str, question: str, options: Optional[List[Dict[str, str]]] = None,
       blocking: bool = True) -> Dict[str, Any]:
    return {"field": field, "question": question, "options": options or [], "blocking": blocking}


# ============================================================
# 7. HÀM CHÍNH
# ============================================================
def _use_llm_default() -> bool:
    return os.getenv("PARSER_USE_OLLAMA", "1").lower() in {"1", "true", "yes"}


def parse_prompt(prompt: str, context: Optional[Dict[str, Any]] = None,
                 use_llm: Optional[bool] = None) -> Dict[str, Any]:
    """
    Parse câu lệnh tự nhiên → dữ liệu có cấu trúc.

    context (tuỳ chọn, từ form Screen 1):
        start_time, end_time ("HH:MM"), trip_date ("YYYY-MM-DD"), vehicle_type,
        start_point, has_gps (bool), confirmed_fields (list[str]: các trường người
        dùng đã xác nhận, KHÔNG hỏi lại), today ("YYYY-MM-DD", để test).
        Không truyền context → thiếu giờ/phương tiện sẽ bị coi là thiếu và hỏi lại.

    Đầu ra (luôn đủ khoá):
        status                 : "ok" | "need_clarification"
        fields                 : {region, trip_date, start_time, end_time, duration_minutes,
                                  vehicle_type, start_point, preferred_tags, avoid_tags,
                                  preference_keywords, pace, budget, budget_vnd,
                                  indoor_outdoor, group_size, group_type, must_visit, avoid}
        sources                : {field: "prompt" | "form" | "default" | "derived"}
        missing_fields         : list[str]   (trường quan trọng còn thiếu/mâu thuẫn)
        clarification_questions: [{field, question, options[{label,value}], blocking}]
        clarification_question : str | None  (câu hỏi đầu tiên — tiện cho UI đơn giản)
        warnings               : list[str]   (không chặn, chỉ thông báo)
        understood_summary     : str         (AI đã hiểu gì, tiếng Việt)
        confidence             : float 0..1
        + khoá cũ: time_limit_minutes, time_limit_explicit, preferences,
          preference_weights, must_visit, avoid, vehicle_hint, raw_prompt, source
    """
    ctx = dict(context or {})
    has_ctx = context is not None
    confirmed = set(ctx.get("confirmed_fields") or [])
    original = _normalize_typos((prompt or "").strip())
    low = _low(original)
    s = _strip(original)
    try:
        today = datetime.strptime(ctx["today"], "%Y-%m-%d").date() if ctx.get("today") else date.today()
    except ValueError:
        today = date.today()

    sources: Dict[str, str] = {}
    warnings: List[str] = []

    # ---------- thời gian ----------
    t = _extract_time(low, s)
    start, end, duration = t["start"], t["end"], t["duration"]
    time_explicit = any([start, end, duration, t["full_day"], t["half_day"], t["part_of_day"]])

    if start:
        sources["start_time"] = "prompt"
    if end:
        sources["end_time"] = "prompt"

    if not start and not end and t["part_of_day"]:
        start, end = t["part_of_day"][0], t["part_of_day"][1]
        sources["start_time"] = sources["end_time"] = "prompt"
    if not start and not end and not duration and t["full_day"]:
        start, end = "08:00", "20:00"
        sources["start_time"] = sources["end_time"] = "prompt"
    if not duration and t["half_day"]:
        duration = 240

    form_start = ctx.get("start_time") or None
    form_end = ctx.get("end_time") or None
    if start and end:
        pass
    elif start and duration:
        end = _from_minutes(_to_minutes(start) + duration)
        sources["end_time"] = "derived"
    elif end and duration:
        start = _from_minutes(_to_minutes(end) - duration)
        sources["start_time"] = "derived"
    elif duration:
        start = form_start or "08:00"
        sources["start_time"] = "form" if form_start else "default"
        end = _from_minutes(_to_minutes(start) + duration)
        sources["end_time"] = "derived"
    elif start:
        if form_end and _to_minutes(form_end) > _to_minutes(start) + 60:
            end, sources["end_time"] = form_end, "form"
        else:
            end, sources["end_time"] = _from_minutes(_to_minutes(start) + 4 * 60), "default"
    elif end:
        if form_start and _to_minutes(form_start) < _to_minutes(end) - 60:
            start, sources["start_time"] = form_start, "form"
        else:
            start, sources["start_time"] = _from_minutes(max(0, _to_minutes(end) - 4 * 60)), "default"
    else:
        if form_start:
            start, sources["start_time"] = form_start, "form"
        if form_end:
            end, sources["end_time"] = form_end, "form"

    window = None
    if start and end:
        window = _to_minutes(end) - _to_minutes(start)
        if window <= 0 and sources.get("end_time") == "prompt" and _to_minutes(end) < 6 * 60:
            window += 24 * 60          # đi qua nửa đêm: 20h → 1h sáng
    duration_minutes = window if window is not None else duration

    # ---------- ngày ----------
    trip_date, date_warn = _extract_date(original, s, today)
    if date_warn:
        warnings.append(date_warn)
    if trip_date:
        sources["trip_date"] = "prompt"
        trip_date_str = trip_date.isoformat()
    elif ctx.get("trip_date"):
        trip_date_str, sources["trip_date"] = ctx["trip_date"], "form"
    else:
        trip_date_str, sources["trip_date"] = today.isoformat(), "default"

    if t["multi_day"]:
        warnings.append(f"Bạn nhắc tới {t['multi_day']} ngày — hiện mình lập lộ trình theo từng ngày, "
                        "nên sẽ lên kế hoạch cho ngày đầu tiên trước nhé.")

    # ---------- phương tiện ----------
    vehicle, all_vehicles = _extract_vehicle(low, s)
    if vehicle:
        sources["vehicle_type"] = "prompt"
        if len(all_vehicles) > 1:
            warnings.append("Bạn nhắc tới nhiều phương tiện (" + ", ".join(VEHICLE_LABELS[v] for v in all_vehicles)
                            + f") — mình dùng {VEHICLE_LABELS[vehicle]}.")
    elif ctx.get("vehicle_type"):
        vehicle, sources["vehicle_type"] = ctx["vehicle_type"], "form"

    # ---------- khu vực ----------
    region, other_city, has_hanoi = _extract_region(s)
    sources["region"] = "prompt" if has_hanoi else "default"

    # ---------- điểm xuất phát ----------
    start_point = _extract_start_point(original)
    start_point_vague = bool(start_point and _strip(start_point).strip() in _VAGUE_START)
    if start_point and not start_point_vague:
        sources["start_point"] = "prompt"
    elif ctx.get("start_point") or ctx.get("has_gps"):
        start_point = ctx.get("start_point") or ("GPS" if ctx.get("has_gps") else None)
        sources["start_point"] = "form"
        start_point_vague = False

    # ---------- sở thích ----------
    liked, disliked = _extract_tags(low, s)
    pace = "thong_tha" if _any_phrase(low, s, _PACE_SLOW) else ("nhanh" if _any_phrase(low, s, _PACE_FAST) else None)
    budget, budget_vnd = _extract_budget(low, s)
    indoor = "trong_nha" if _any_phrase(low, s, _INDOOR, allow_negated=True) else (
        "ngoai_troi" if _any_phrase(low, s, _OUTDOOR) else None)
    group_size, group_type = _extract_group(s)
    if group_type == "family" and "family" not in liked:
        liked.append("family")
    if group_type == "couple" and "romantic" not in liked:
        liked.append("romantic")

    must_visit = _extract_list_after(original, _MUST_CUES)
    avoid_raw = _extract_list_after(original, _AVOID_CUES)
    avoid = [a for a in avoid_raw if not any(_strip(k) in _strip(a) for k in _AVOID_NOT_PLACE)]

    # Tự động nhận diện địa danh / quán xá cụ thể có trong DB
    for place_name, kws in _KNOWN_PLACES:
        if any(re.search(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", s) for kw in kws):
            if place_name not in must_visit:
                must_visit.append(place_name)

    content_kw = []
    for w in re.findall(r"[a-z]+", s):
        if len(w) > 1 and w not in _STRUCT_STOP and w not in _ALLOWED_GENERIC_STOP and w not in content_kw:
            content_kw.append(w)

    # ---------- trọng số đa mục tiêu (giữ hợp đồng cũ) ----------
    weights = {"experience": 0.6, "time": 0.25, "crowd": 0.15}
    if _any_phrase(low, s, _CROWD_HINTS, allow_negated=True):
        weights = {"experience": 0.45, "time": 0.20, "crowd": 0.35}
    if _any_phrase(low, s, _TIME_HINTS) or pace == "nhanh":
        weights = {"experience": 0.40, "time": 0.45, "crowd": 0.15}

    # ---------- LLM bổ sung (chỉ khi rule-based không thấy sở thích) ----------
    source = "rule_based"
    if use_llm is None:
        use_llm = _use_llm_default()
    if use_llm and original and not liked and not must_visit:
        enriched = _llm_enrich(original, timeout=float(os.getenv("PARSER_OLLAMA_TIMEOUT", "25")))
        if enriched:
            source = "rule_based+ollama"
            liked = [p for p in enriched["preferences"] if p not in disliked] or liked
            must_visit = must_visit or enriched["must_visit"]
            avoid = avoid or enriched["avoid"]

    # ---------- hỏi lại ----------
    questions: List[Dict[str, Any]] = []
    if other_city and not has_hanoi and "region" not in confirmed:
        questions.append(_q("region",
            f"Hiện mình mới có dữ liệu địa điểm ở Hà Nội, chưa hỗ trợ {other_city.title()}. "
            "Bạn có muốn lên lịch trình ở Hà Nội không?",
            [{"label": "Đồng ý, đi Hà Nội", "value": "đi Hà Nội"}]))
    if (not start_point or start_point_vague) and "start_point" not in confirmed:
        q = ("Bạn xuất phát từ khu vực nào ở Hà Nội? (VD: Hồ Hoàn Kiếm, khách sạn ở phố cổ…) "
             "— hoặc bấm “Dùng GPS”.")
        if start_point_vague:
            q = f"“{start_point}” của bạn ở khu vực nào của Hà Nội? Bạn ghi rõ địa chỉ hoặc bấm “Dùng GPS” giúp mình nhé."
        questions.append(_q("start_point", q))
    if (not start or not end) and "time" not in confirmed:
        questions.append(_q("time", "Bạn định đi trong khung giờ nào? (VD: từ 8h đến 17h, hoặc đi khoảng 4 tiếng)",
                            [{"label": "Cả ngày (8h–20h)", "value": "đi cả ngày"},
                             {"label": "Buổi sáng", "value": "đi buổi sáng"},
                             {"label": "Buổi chiều", "value": "đi buổi chiều"},
                             {"label": "Buổi tối", "value": "đi buổi tối"}]))
    elif window is not None and "time" not in confirmed:
        if window <= 0:
            questions.append(_q("time", f"Giờ kết thúc ({end}) đang sớm hơn giờ bắt đầu ({start}). Bạn kiểm tra lại khung giờ giúp mình nhé?"))
        elif window < 60:
            questions.append(_q("time", f"Khung giờ {start}–{end} chỉ có {window} phút, hơi ngắn để đi được điểm nào. "
                                        "Bạn có muốn kéo dài thêm không?",
                                [{"label": "Đi 2 tiếng", "value": "đi khoảng 2 tiếng"},
                                 {"label": "Đi nửa ngày", "value": "đi nửa ngày"}]))
    if not vehicle and "vehicle_type" not in confirmed:
        questions.append(_q("vehicle_type", "Bạn sẽ di chuyển bằng gì?",
                            [{"label": "🏍️ Xe máy", "value": "đi xe máy"}, {"label": "🚗 Ô tô", "value": "đi ô tô"},
                             {"label": "🚶 Đi bộ", "value": "đi bộ"}, {"label": "🚲 Xe đạp", "value": "đi xe đạp"}]))
    elif group_size and "vehicle_type" not in confirmed:
        if group_size >= 17 and vehicle != "xe_29_cho":
            questions.append(_q("vehicle_type",
                f"Đoàn {group_size} người mà đi {VEHICLE_LABELS.get(vehicle, vehicle)} thì khá vất vả. Bạn có muốn đổi phương tiện không?",
                [{"label": "🚌 Xe 29 chỗ", "value": "đi xe 29 chỗ"},
                 {"label": f"Vẫn đi {VEHICLE_LABELS.get(vehicle, vehicle)}", "value": f"vẫn đi {VEHICLE_LABELS.get(vehicle, vehicle)}"}]))
        elif 8 <= group_size < 17 and vehicle in ("xe_may", "o_to", "di_bo", "xe_dap"):
            questions.append(_q("vehicle_type",
                f"Nhóm {group_size} người — bạn muốn đi {VEHICLE_LABELS.get(vehicle, vehicle)} hay thuê xe 16 chỗ cho tiện?",
                [{"label": "🚐 Xe 16 chỗ", "value": "đi xe 16 chỗ"},
                 {"label": f"Vẫn đi {VEHICLE_LABELS.get(vehicle, vehicle)}", "value": f"vẫn đi {VEHICLE_LABELS.get(vehicle, vehicle)}"}]))
    if not liked and not content_kw and not must_visit and "preferences" not in confirmed:
        questions.append(_q("preferences", "Bạn thích kiểu trải nghiệm nào để mình chọn điểm cho hợp gu?", _PREF_OPTIONS))

    missing = [q["field"] for q in questions if q["blocking"]]
    status = "need_clarification" if missing else "ok"

    # ---------- độ tin cậy ----------
    signals = [bool(liked or content_kw), sources.get("start_time") in ("prompt", "form"),
               sources.get("vehicle_type") in ("prompt", "form"), sources.get("start_point") in ("prompt", "form")]
    confidence = round(0.25 + 0.75 * (sum(signals) / len(signals)) - 0.1 * len(missing), 2)
    confidence = max(0.0, min(1.0, confidence))

    fields = {
        "region": region,
        "trip_date": trip_date_str,
        "start_time": start,
        "end_time": end,
        "duration_minutes": duration_minutes,
        "vehicle_type": vehicle,
        "start_point": start_point if not start_point_vague else None,
        "preferred_tags": liked,
        "avoid_tags": disliked,
        "preference_keywords": content_kw[:12],
        "pace": pace,
        "budget": budget,
        "budget_vnd": budget_vnd,
        "indoor_outdoor": indoor,
        "group_size": group_size,
        "group_type": group_type,
        "must_visit": must_visit[:10],
        "avoid": avoid[:10],
    }
    fields["custom_phrases"] = _extract_custom_phrases(original, liked, must_visit)

    out = {
        "status": status,
        "fields": fields,
        "sources": sources,
        "missing_fields": missing,
        "clarification_questions": questions,
        "clarification_question": questions[0]["question"] if questions else None,
        "warnings": warnings,
        "badge_text": build_badge_text(fields, sources, original),
        "understood_summary": build_summary(fields, sources, original),
        "confidence": confidence,
        # ---- khoá cũ (giữ tương thích /api/optimize-route-hybrid & test cũ) ----
        "time_limit_minutes": max(60, min(720, int(duration_minutes))) if (time_explicit and duration_minutes and duration_minutes > 0) else 360,
        "time_limit_explicit": bool(time_explicit),
        "preferences": liked or ["general"],
        "preference_weights": weights,
        "must_visit": must_visit[:10],
        "avoid": avoid[:10] + [t_ for t_ in disliked],
        "vehicle_hint": vehicle or "xe_may",
        "raw_prompt": original,
        "source": source,
    }
    if not has_ctx:
        out["note"] = "Không có context form: các trường thiếu được đánh dấu để hỏi lại."
    return out


def _extract_custom_phrases(original: str, liked: List[str], must_visit: List[str]) -> List[str]:
    """Trích xuất các yêu cầu / cụm từ tự do người dùng ghi mà chưa được bao hàm trong tag hay địa danh cụ thể."""
    if not original:
        return []
    clauses = re.split(r'[,;.!?\n]|\b(?:và|với|hoặc|cùng|nhưng|mà|để|kèm|thêm|rồi)\b', original, flags=re.I)
    results = []
    covered_words = set()
    for t in liked:
        for ph in _TAG_PHRASES.get(t, []):
            covered_words.update(_strip(ph.lstrip('=')).split())
    for mv in must_visit:
        covered_words.update(_strip(mv).split())

    for c in clauses:
        clean = c.strip(' "\'“”()[]')
        clean = re.sub(
            r'^(?:thích|muốn|yêu cầu|cần|có|đi|trải nghiệm|khám phá|ghé|xem|uống|ăn|tìm|ngắm|thử|thong thả|nhẹ nhàng|chậm rãi|nhanh)\s+',
            '', clean, flags=re.I
        ).strip()
        if len(clean) >= 3:
            s_clean = _strip(clean)
            words = [w for w in re.findall(r'[a-z0-9]+', s_clean) if w not in _STRUCT_STOP and w not in _ALLOWED_GENERIC_STOP]
            uncovered = [w for w in words if w not in covered_words]
            if uncovered:
                results.append(clean[:50])
    return results


def build_badge_text(fields: Dict[str, Any], sources: Dict[str, str], raw_prompt: str = "") -> str:
    """Tạo chuỗi badge ngắn gọn, trực quan cho thanh pill 'AI đã hiểu' trên màn hình 1."""
    parts = []
    # 1. Thời gian
    if fields.get("start_time") and fields.get("end_time"):
        parts.append(f"{fields['start_time']}–{fields['end_time']}")
    # 2. Phương tiện
    if fields.get("vehicle_type"):
        v = VEHICLE_LABELS.get(fields["vehicle_type"], fields["vehicle_type"])
        parts.append(v)
    # 3. Điểm xuất phát
    if fields.get("start_point"):
        sp = "GPS" if fields["start_point"] == "GPS" else fields["start_point"]
        parts.append(f"Từ: {sp}")

    items = []
    # 4. Tags sở thích (đầy đủ các category)
    pref_tags = fields.get("preferred_tags") or []
    for t in pref_tags:
        if t in TAG_ICONS:
            items.append(TAG_ICONS[t])

    # 5. Địa điểm cụ thể (tự động nhận diện từ DB / quán xá)
    for p in (fields.get("must_visit") or []):
        items.append(f"📍 {p}")

    # 6. Tránh
    for a in (fields.get("avoid") or []):
        items.append(f"🚫 Tránh {a}")

    # 7. Nhịp độ / Ngân sách / Không gian
    if fields.get("pace") == "thong_tha":
        items.append("⏳ Thong thả")
    elif fields.get("pace") == "nhanh":
        items.append("⚡ Nhiều điểm")
    if fields.get("budget") == 1:
        items.append("💰 Tiết kiệm")
    elif fields.get("budget") == 3:
        items.append("✨ Sang trọng")
    if fields.get("indoor_outdoor") == "trong_nha":
        items.append("🏠 Trong nhà")
    elif fields.get("indoor_outdoor") == "ngoai_troi":
        items.append("⛅ Ngoài trời")

    # 8. Yêu cầu tùy biến tự do của người dùng
    custom = fields.get("custom_phrases")
    if custom is None:
        custom = _extract_custom_phrases(raw_prompt, pref_tags, fields.get("must_visit") or [])
    for cp in custom[:2]:
        items.append(f"✨ {cp}")

    if items:
        parts.append(" · ".join(items))
    elif raw_prompt:
        clean_p = raw_prompt[:50] + ("..." if len(raw_prompt) > 50 else "")
        parts.append(f"✨ {clean_p}")

    return " | ".join(parts)


def build_summary(fields: Dict[str, Any], sources: Dict[str, str], raw_prompt: str = "") -> str:
    """Câu tóm tắt 'AI đã hiểu gì' — hiển thị cho người dùng kiểm tra lại."""
    from api.preferences import TAG_LABELS_VI
    parts = []
    try:
        d = datetime.strptime(fields["trip_date"], "%Y-%m-%d").date()
        parts.append(f"ngày {d.strftime('%d/%m/%Y')} ({_WEEKDAY_VI[d.weekday()]})")
    except Exception:
        pass
    if fields.get("start_time") and fields.get("end_time"):
        dur = fields.get("duration_minutes") or 0
        dur_txt = f"{dur // 60}h{dur % 60:02d}" if dur % 60 else f"{dur // 60} tiếng"
        parts.append(f"{fields['start_time']}–{fields['end_time']} ({dur_txt})")
    if fields.get("vehicle_type"):
        parts.append("đi " + VEHICLE_LABELS.get(fields["vehicle_type"], fields["vehicle_type"]))
    if fields.get("start_point"):
        sp = "vị trí GPS" if fields["start_point"] == "GPS" else fields["start_point"]
        parts.append(f"xuất phát từ {sp}")
    if fields.get("group_size"):
        parts.append(f"{fields['group_size']} người")
    head = "Mình hiểu là: đi Hà Nội" + (", " + ", ".join(parts) if parts else "") + "."
    prefs = [TAG_LABELS_VI.get(t, t) for t in fields.get("preferred_tags") or []]
    tail = []
    if prefs:
        tail.append("thích " + ", ".join(prefs))
    if fields.get("avoid_tags"):
        tail.append("không thích " + ", ".join(TAG_LABELS_VI.get(t, t) for t in fields["avoid_tags"]))
    if fields.get("pace") == "thong_tha":
        tail.append("nhịp độ thong thả")
    elif fields.get("pace") == "nhanh":
        tail.append("muốn đi được nhiều điểm")
    if fields.get("budget"):
        tail.append({1: "chi phí tiết kiệm", 2: "chi phí tầm trung", 3: "chi phí cao cấp"}[fields["budget"]])
    if fields.get("indoor_outdoor") == "trong_nha":
        tail.append("ưu tiên trong nhà")
    elif fields.get("indoor_outdoor") == "ngoai_troi":
        tail.append("ưu tiên ngoài trời")
    if fields.get("must_visit"):
        tail.append("muốn ghé " + ", ".join(fields["must_visit"]))
    if fields.get("avoid"):
        tail.append("tránh " + ", ".join(fields["avoid"]))

    custom = fields.get("custom_phrases")
    if custom is None:
        custom = _extract_custom_phrases(raw_prompt, fields.get("preferred_tags") or [], fields.get("must_visit") or [])
    if custom:
        tail.append("quan tâm thêm: " + ", ".join(custom[:3]))
    elif fields.get("preference_keywords") and not prefs:
        tail.append("quan tâm: " + ", ".join(fields["preference_keywords"][:5]))

    return head + (" " + "; ".join(tail).capitalize() + "." if tail else "")
