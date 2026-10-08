"""Keyword-based preference scoring (diacritics-insensitive)."""
import re
import unicodedata

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
    normalized = _strip_diacritics(text)
    words = re.findall(r"[a-z0-9]+", normalized)
    return [w for w in words if len(w) > 1 and w not in _VN_STOPWORDS]

def calculate_preference_score(point: dict, preference_keywords: list) -> float:
    """
    Điểm phù hợp sở thích, thang 0..1 (càng cao càng phù hợp).
    - Nếu người dùng không nhập preference (không có keyword) → trả 0.5 (trung lập),
      để không âm thầm giả vờ đã áp dụng preference khi thực chất không có gì để so khớp.
    - Nếu điểm đến không có dữ liệu mô tả nào → cũng trả 0.5 (trung lập, minh bạch),
      thay vì mặc định 0 (bất lợi oan) hay 1 (ưu ái oan).
    """
    if not preference_keywords:
        return 0.5
    haystack = _strip_diacritics(" ".join([
        point.get("thong_tin_chi_tiet") or "",
        point.get("review") or "",
        point.get("phu_hop") or "",
        point.get("loai_hinh") or "",
    ]))
    if not haystack.strip():
        return 0.5
    matched = sum(1 for kw in preference_keywords if kw in haystack)
    return min(1.0, matched / len(preference_keywords))
