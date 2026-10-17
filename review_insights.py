"""
review_insights.py - Phân tích ĐẶC ĐIỂM của địa điểm từ review thật (bảng DIA_DIEM_REVIEW).

Mục tiêu (Task 3): biến đoạn review thành thông tin giúp người dùng quyết định và giúp recommendation:
  * khia_canh   : điểm cảm xúc -1..1 theo khía cạnh (đồ ăn, view, phục vụ, sạch sẽ, giá, không gian, vị trí, đông khách)
  * noi_bat     : điều khách hay khen   (VD "view / cảnh quan được khen")
  * luu_y       : điều khách hay phàn nàn (VD "hay đông khách", "giá cao")
  * ma_dac_diem : mã đặc điểm rút từ review (yen_tinh, view_dep, check_in, vintage, dong_khach, gia_hop_ly...)
                  -> dùng cho tính mức độ phù hợp sở thích (api/preferences.py)
  * diem_cam_xuc: 0..1, kết hợp số sao + từ khóa cảm xúc, review mới được ưu tiên hơn
  * trich_dan   : 1-2 review tiêu biểu (có số sao) để hiển thị trên Screen 2/3

Thuần từ điển (xác định, không cần Ollama/mạng). Hiểu tiếng Việt (có/không dấu) và tiếng Anh;
review ngôn ngữ khác chỉ đóng góp số sao. Kết quả được cache theo nội dung review.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Dict, Iterable, List, Optional

# ----------------------------------------------------------------------------- chuẩn hóa
def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")


def _tokens(s: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", fold(s))


# ----------------------------------------------------------------------------- từ điển
# Mỗi cụm là dãy token đã bỏ dấu; khớp theo ranh giới từ.
ASPECT_CUES: Dict[str, List[str]] = {
    "food": ["ngon", "do an", "mon", "pho", "bun", "banh", "cha", "ca phe", "coffee", "tra", "do uong", "cafe", "espresso",
             "food", "drink", "menu", "tasty", "delicious", "dish", "taste", "vi"],
    "view": ["view", "canh", "khung canh", "scenery", "scenic", "ngam", "landscape"],
    "service": ["phuc vu", "nhan vien", "nvien", "dich vu", "staff", "service", "thai do", "chu quan", "waiter", "friendly"],
    "cleanliness": ["sach", "ve sinh", "clean", "dirty", "ban", "bui"],
    "price": ["gia", "dat", "re", "mac", "cheap", "expensive", "price", "overpriced", "gia ca", "hop ly", "affordable"],
    "atmosphere": ["khong gian", "khong khi", "vibe", "atmosphere", "nhac", "trang tri", "thiet ke", "kien truc", "decor", "chill", "yen tinh", "cozy"],
    "location": ["vi tri", "dia diem", "gan", "de tim", "location", "gui xe", "do xe", "parking", "trung tam"],
}
ASPECT_LABEL = {
    "food": "đồ ăn/thức uống", "view": "view, cảnh quan", "service": "phục vụ", "cleanliness": "sạch sẽ",
    "price": "giá cả", "atmosphere": "không gian", "location": "vị trí",
}
POS = {"ngon", "tuyet", "tuyet voi", "dep", "thoang", "sach", "than thien", "nhiet tinh", "hop ly", "yen tinh", "chat luong",
       "tot", "thich", "xuat sac", "hai long", "dang thu", "dang den", "dang tien", "xin", "chill", "sang trong", "rong", "dang yeu",
       "great", "good", "nice", "beautiful", "amazing", "excellent", "delicious", "friendly", "love", "perfect", "recommend",
       "clean", "cozy", "lovely", "worth", "best", "wonderful", "tasty", "peaceful", "quiet", "affordable", "reasonable", "re"}
NEG = {"te", "ban", "dat", "on", "on ao", "cham", "that vong", "kem", "xau", "chat", "hoi", "nong", "xuong cap", "tham", "chan",
       "bad", "poor", "expensive", "dirty", "noisy", "slow", "rude", "overpriced", "disappointing", "disappointed", "waste",
       "terrible", "worst", "abandoned", "mediocre", "bland", "tourist trap", "avoid"}
NEGATORS = {"khong", "ko", "chua", "chang", "not", "no", "never", "dont", "don", "isnt", "wasnt"}

# Đặc điểm (mã -> (cụm từ, nhãn hiển thị)). Dùng đếm số review nhắc tới.
TRAITS: Dict[str, tuple] = {
    "yen_tinh":    (["yen tinh", "yen binh", "thanh binh", "tranh xa", "quiet", "peaceful", "tranquil", "calm"], "yên tĩnh"),
    "view_dep":    (["view dep", "canh dep", "view", "scenic", "scenery", "ngam canh", "view ho"], "view đẹp"),
    "check_in":    (["song ao", "check in", "checkin", "chup anh", "photo", "instagram", "instagrammable", "selfie"], "hợp chụp ảnh/check-in"),
    "vintage":     (["vintage", "hoai co", "xua", "retro", "co kinh", "nha co", "biet thu co", "old"], "phong cách vintage/hoài cổ"),
    "dong_khach":  (["dong khach", "dong nguoi", "dong duc", "chen chuc", "xep hang", "cho doi", "phai cho", "qua dong",
                     "queue", "crowded", "busy", "long wait", "packed", "chat cung", "kha dong", "rat dong", "luon dong"], "hay đông khách"),
    "gia_hop_ly":  (["gia hop ly", "gia re", "binh dan", "re", "cheap", "affordable", "reasonable price", "good price", "value"], "giá hợp lý"),
    "gia_cao":     (["dat", "gia cao", "mac", "expensive", "overpriced", "pricey"], "giá hơi cao"),
    "phuc_vu_tot": (["nhan vien nhiet tinh", "phuc vu tot", "nhan vien than thien", "nhiet tinh", "friendly staff", "great service", "nvien nhiet tinh"], "phục vụ nhiệt tình"),
    "phuc_vu_kem": (["phuc vu kem", "phuc vu cham", "nhan vien thai do", "rude", "slow service", "bad service"], "phục vụ chưa tốt"),
    "ngon":        (["ngon", "delicious", "tasty", "yummy", "dam da", "thom ngon"], "đồ ăn/uống ngon"),
    "sach_se":     (["sach se", "sach", "clean", "ve sinh tot"], "sạch sẽ"),
    "rong_rai":    (["rong rai", "thoang", "rong", "spacious", "thoang mat", "roomy"], "rộng rãi, thoáng"),
    "gia_dinh":    (["gia dinh", "tre em", "tre nho", "family", "kids", "children", "ban be", "nhom ban"], "hợp đi gia đình/nhóm"),
    "lich_su":     (["lich su", "di tich", "van hoa", "trang nghiem", "historic", "history", "cultural", "temple", "y nghia"], "giàu giá trị lịch sử, văn hóa"),
    "da_dang":     (["da dang", "nhieu mon", "nhieu lua chon", "variety", "wide selection", "nhieu hang"], "nhiều lựa chọn"),
}
# mã đặc điểm tiêu cực (đưa vào "luu_y" thay vì "noi_bat")
NEGATIVE_TRAITS = {"dong_khach", "gia_cao", "phuc_vu_kem"}


def _has(tokens: List[str], phrase: str) -> bool:
    p = phrase.split()
    n = len(p)
    return any(tokens[i:i + n] == p for i in range(len(tokens) - n + 1))


def _count_polarity(tokens: List[str]) -> tuple:
    """(pos, neg) có xử lý phủ định trong cửa sổ 2 từ trước từ cảm xúc."""
    pos = neg = 0
    for i, t in enumerate(tokens):
        cand = [t, " ".join(tokens[i:i + 2])]
        is_pos = any(c in POS for c in cand)
        is_neg = any(c in NEG for c in cand)
        if not (is_pos or is_neg):
            continue
        negated = any(w in NEGATORS for w in tokens[max(0, i - 2):i])
        if is_pos and not is_neg:
            neg, pos = (neg + 1, pos) if negated else (neg, pos + 1)
        elif is_neg and not is_pos:
            pos, neg = (pos + 1, neg) if negated else (pos, neg + 1)
    return pos, neg


def _sentences(text: str) -> List[str]:
    return [s for s in re.split(r"[.!?\n;]+|\s[-–]\s", text or "") if s.strip()]


def _clip(text: str, n: int = 170) -> str:
    t = re.sub(r"\s+", " ", (text or "").strip())
    if len(t) <= n:
        return t
    cut = t[:n].rsplit(" ", 1)[0]
    return cut + "…"


# ----------------------------------------------------------------------------- phân tích
_CACHE: Dict[tuple, dict] = {}


_VI_CHARS = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.I)


def _norm_reviews(reviews: Iterable) -> List[dict]:
    out = []
    for r in reviews or []:
        if isinstance(r, str):
            r = {"noi_dung": r}
        txt = str(r.get("noi_dung") or "").strip()
        if not txt:
            continue
        sao = r.get("so_sao")
        tuoi = r.get("tuoi_review_thang")
        out.append({"noi_dung": txt, "so_sao": float(sao) if sao not in (None, "") else None,
                    "tuoi_review_thang": float(tuoi) if tuoi not in (None, "") else None,
                    "ngon_ngu": r.get("ngon_ngu")})
    return out


def analyze_reviews(reviews: Iterable, key: Optional[str] = None) -> dict:
    """Phân tích danh sách review (dict {noi_dung, so_sao, tuoi_review_thang} hoặc str). Trả dict mô tả ở đầu file."""
    revs = _norm_reviews(reviews)
    ck = (key, tuple((r["noi_dung"], r["so_sao"]) for r in revs))
    if ck in _CACHE:
        return _CACHE[ck]

    n = len(revs)
    empty = {"so_review": 0, "diem_cam_xuc": None, "khia_canh": {}, "noi_bat": [], "luu_y": [],
             "ma_dac_diem": [], "trich_dan": []}
    if n == 0:
        _CACHE[ck] = empty
        return empty

    asp_sum = {a: 0.0 for a in ASPECT_CUES}
    asp_cnt = {a: 0 for a in ASPECT_CUES}
    trait_cnt = {t: 0 for t in TRAITS}
    sent_vals, weights = [], []

    for r in revs:
        toks_all = _tokens(r["noi_dung"])
        # theo câu: khía cạnh nào được nhắc trong câu thì nhận cảm xúc của câu đó (nếu câu không có từ cảm xúc thì lấy cả review)
        rpos, rneg = _count_polarity(toks_all)
        for s in _sentences(r["noi_dung"]):
            tk = _tokens(s)
            p, q = _count_polarity(tk)
            if p + q == 0:
                p, q = rpos, rneg
            for asp, cues in ASPECT_CUES.items():
                if any(_has(tk, c) for c in cues):
                    asp_cnt[asp] += 1
                    asp_sum[asp] += (p - q) / max(1, p + q)
        for code, (phrases, _label) in TRAITS.items():
            if any(_has(toks_all, ph) for ph in phrases):
                trait_cnt[code] += 1
        lex = (rpos - rneg) / max(1, rpos + rneg) if (rpos + rneg) else None
        parts = []
        if r["so_sao"] is not None:
            parts.append((r["so_sao"] - 1.0) / 4.0)
        if lex is not None:
            parts.append((lex + 1.0) / 2.0)
        if parts:
            sent_vals.append(sum(parts) / len(parts))
            age = r["tuoi_review_thang"] or 0.0
            weights.append(1.0 / (1.0 + age / 24.0))          # review mới nặng hơn

    khia_canh = {a: {"diem": round(asp_sum[a] / asp_cnt[a], 2), "so_nhac": asp_cnt[a]} for a in ASPECT_CUES if asp_cnt[a]}

    # đặc điểm xuất hiện ở >= ngưỡng review (ít review thì 1, nhiều review thì ~12%)
    thr = max(1, round(0.12 * n))
    codes = [c for c, k in sorted(trait_cnt.items(), key=lambda kv: -kv[1]) if k >= thr]
    # xung đột: giá hợp lý vs giá cao -> giữ cái được nhắc nhiều hơn
    if "gia_hop_ly" in codes and "gia_cao" in codes:
        codes.remove("gia_hop_ly" if trait_cnt["gia_cao"] > trait_cnt["gia_hop_ly"] else "gia_cao")
    if "phuc_vu_tot" in codes and "phuc_vu_kem" in codes:
        codes.remove("phuc_vu_tot" if trait_cnt["phuc_vu_kem"] > trait_cnt["phuc_vu_tot"] else "phuc_vu_kem")

    noi_bat = [TRAITS[c][1] for c in codes if c not in NEGATIVE_TRAITS][:4]
    luu_y = [TRAITS[c][1] for c in codes if c in NEGATIVE_TRAITS][:3]
    # khía cạnh bị chê rõ (điểm âm, được nhắc >=2 lần) nhưng chưa nằm trong luu_y
    for a, v in sorted(khia_canh.items(), key=lambda kv: kv[1]["diem"]):
        if v["diem"] <= -0.3 and v["so_nhac"] >= 2 and a not in ("price",):
            label = f"{ASPECT_LABEL[a]} chưa được khen"
            if label not in luu_y and len(luu_y) < 3:
                luu_y.append(label)

    diem = None
    if sent_vals:
        diem = round(sum(v * w for v, w in zip(sent_vals, weights)) / sum(weights), 3)

    # trích dẫn: ưu tiên >=4 sao, tiếng Việt (giao diện tiếng Việt), độ dài vừa phải, mới nhất
    def is_vi(r):
        lang = (r.get("ngon_ngu") or "").lower()
        if lang and lang != "khong_ro":      # "khong_ro" = chưa nhận diện -> đoán theo dấu tiếng Việt
            return lang.startswith("vi")
        return bool(_VI_CHARS.search(r["noi_dung"]))

    def quote_rank(r):
        L = len(r["noi_dung"])
        good_len = 1 if 40 <= L <= 260 else 0
        star = r["so_sao"] if r["so_sao"] is not None else 3
        return (star >= 4, is_vi(r), good_len, -(r["tuoi_review_thang"] if r["tuoi_review_thang"] is not None else 999))
    picks = sorted(revs, key=quote_rank, reverse=True)[:2]
    trich_dan = [{"sao": p["so_sao"], "noi_dung": _clip(p["noi_dung"])} for p in picks]

    res = {"so_review": n, "diem_cam_xuc": diem, "khia_canh": khia_canh, "noi_bat": noi_bat, "luu_y": luu_y,
           "ma_dac_diem": codes, "trich_dan": trich_dan}
    if len(_CACHE) > 2000:
        _CACHE.clear()
    _CACHE[ck] = res
    return res


# ----------------------------------------------------------------------------- tương thích với absa_service
def to_absa(insight: dict) -> Optional[dict]:
    """Chuyển kết quả sang định dạng của absa_service (sabsa_score 0..1 + aspects)."""
    if not insight or not insight.get("so_review") or insight.get("diem_cam_xuc") is None:
        return None
    asp_map = {"food": "food", "view": "view", "service": "service", "cleanliness": "cleanliness",
               "price": "price", "location": "location", "atmosphere": "atmosphere"}
    aspects = [{"aspect": asp_map[a], "sentiment": v["diem"]} for a, v in insight["khia_canh"].items() if a in asp_map]
    return {"sabsa_score": round(float(insight["diem_cam_xuc"]), 3), "aspects": aspects, "source": "review_db"}
