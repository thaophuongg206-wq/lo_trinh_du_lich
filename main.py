import os
import re
import unicodedata
import requests
try:
    import pyodbc
except ImportError:
    # Máy không cài driver ODBC vẫn chạy được: get_db_connection() đã có sẵn
    # nhánh fallback sang SQLite (dulich.db) — trước đây nhánh đó không bao giờ
    # tới được vì `import pyodbc` chết ngay từ dòng import.
    pyodbc = None
import math
import random
import io
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel, field_validator
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Routing Optimization API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

import sqlite3

# State itinerary phía backend (excluded_ids, route đã sinh, current_itinerary).
from itinerary_store import store

SERVER   = os.getenv("DB_SERVER",   r'LAPTOP-5K1IGMEK\SQLEXPRESS')
DATABASE = os.getenv("DB_NAME",     'DuLichThongMinh')
SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dulich.db")

def get_db_connection():
    """
    Thử kết nối SQL Server trước.
    Nếu thất bại (máy bạn bè chưa cài SQL Server), tự động dùng SQLite dulich.db có sẵn trong Git.
    """
    try:
        if pyodbc is None:
            raise RuntimeError("pyodbc chưa được cài — dùng SQLite")
        conn = pyodbc.connect(
            f'DRIVER={{ODBC Driver 17 for SQL Server}};'
            f'SERVER={SERVER};'
            f'DATABASE={DATABASE};'
            f'Trusted_Connection=yes;'
            f'TrustServerCertificate=yes;',
            timeout=2
        )
        return conn, "sqlserver"
    except Exception:
        conn = sqlite3.connect(SQLITE_DB)
        conn.row_factory = sqlite3.Row
        return conn, "sqlite"

def fetch_all_dict(cursor, db_type):
    if db_type == "sqlite":
        return [dict(r) for r in cursor.fetchall()]
    else:
        cols = [col[0] for col in cursor.description]
        return [dict(zip(cols, row)) for row in cursor.fetchall()]


class OptimizationRequest(BaseModel):
    region: str
    start_time: str
    end_time: str
    trip_date: str = ""           # Ngày khởi hành (YYYY-MM-DD)
    start_point: str = ""         # Tên điểm xuất phát (tìm theo tên)
    vehicle_type: str = "xe_may"  # Phương tiện
    start_lat: Optional[float] = None   # Vĩ độ GPS (khi dùng vị trí hiện tại)
    start_lon: Optional[float] = None   # Kinh độ GPS
    user_preference: str = ""     # Mô tả sở thích không gian của người dùng (VD: "yên tĩnh, view đẹp")
    weight: int = 50              # 0 = ưu tiên khoảng cách (đi gần) .. 100 = ưu tiên đúng sở thích (trải nghiệm)
    ai_selected_ids: Optional[List[str]] = None  # ID điểm đến do AI (Ollama) gợi ý từ /api/ai-suggest.
                                                   # ĐÂY CHỈ LÀ ƯU TIÊN (must-visit), KHÔNG phải danh sách
                                                   # duy nhất: Greedy vẫn được dùng toàn bộ candidate pool
                                                   # để fill-up quỹ thời gian còn trống (mục 5).

    # ---- STATE / RÀNG BUỘC (mục 6, 7, 8, 11) ----
    session_id: Optional[str] = None              # Phiên lập lộ trình; nếu có, backend nhớ excluded_ids
    excluded_ids: Optional[List[str]] = None      # Điểm bị loại BỔ SUNG cho lần gọi này (gộp với state phiên)
    num_routes: int = 5                           # Số route mong muốn (chặn trong khoảng 3..5)

    @field_validator("num_routes")
    @classmethod
    def validate_num_routes(cls, v):
        if v is None:
            return 5
        return max(3, min(5, int(v)))

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, v):
        if v is None:
            return 50
        if not (0 <= v <= 100):
            raise ValueError("weight phải nằm trong khoảng 0-100")
        return v

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


@app.get("/api/locations")
def get_locations():
    try:
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        # Đã lấy thêm url_hinh_anh và diem_gia_tri để Frontend hiển thị
        cursor.execute("SELECT id, ten, vi_do, kinh_do, loai_hinh, diem_gia_tri, url_hinh_anh, review FROM DIA_DIEM")
        rows = fetch_all_dict(cursor, db_type)
        locations = [{
            "id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"], 
            "loai_hinh": r["loai_hinh"], "rating": r["diem_gia_tri"], 
            "url_hinh_anh": r["url_hinh_anh"], "review": r["review"]
        } for r in rows]
        conn.close()
        return {"status": "success", "data": locations, "db_engine": db_type}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_weather_factor(lat: float, lon: float) -> float:
    try:
        res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
        if res.get("current_weather", {}).get("weathercode", 0) >= 51:
            return 1.25
        return 1.0
    except:
        return 1.0

_RUSH_HOUR_RANGES = (
    (datetime.strptime("07:00", "%H:%M").time(), datetime.strptime("09:00", "%H:%M").time()),
    (datetime.strptime("17:00", "%H:%M").time(), datetime.strptime("19:00", "%H:%M").time()),
)

def get_density_factor(start_time_str: str) -> float:
    # TỐI ƯU HIỆU NĂNG: các mốc giờ cao điểm là hằng số, không cần strptime lại
    # mỗi lần gọi hàm — hàm này được gọi hàng triệu lần trong solve_tsptw_exact
    # (bitmask DP) nên chi phí strptime lặp lại từng là điểm nghẽn hiệu năng lớn
    # nhất (benchmark: n=13 candidate mất ~6.5s trước khi tối ưu, ~1.5s sau khi
    # tối ưu — xem ghi chú DP_MAX_CANDIDATES).
    try:
        t = datetime.strptime(start_time_str, "%H:%M").time()
        for lo, hi in _RUSH_HOUR_RANGES:
            if lo <= t <= hi:
                return 1.8
    except:
        pass
    return 1.0

# Danh sách các loại xe lớn bị hạn chế theo giờ
LARGE_VEHICLE_TYPES = {"xe_16_cho", "xe_29_cho", "xe_45_cho"}
_RESTRICTION_MORNING = (datetime.strptime("06:00", "%H:%M").time(), datetime.strptime("09:00", "%H:%M").time())
_RESTRICTION_EVENING = (datetime.strptime("16:00", "%H:%M").time(), datetime.strptime("20:00", "%H:%M").time())
_RESTRICTION_FACTOR_MAP = {"xe_16_cho": 1.5, "xe_29_cho": 1.8, "xe_45_cho": 2.2}

# Cấp độ tiếp cận tối đa theo phương tiện
# Cấp 1: bãi đỗ lớn, đường rộng (TTTM, di tích lớn) → tất cả xe
# Cấp 2: đường chính, đỗ được xe con → xe máy + ô tô cá nhân
# Cấp 3: hẻm/phố cổ hẹp, không bãi xe → chỉ xe máy, xe đạp, đi bộ
VEHICLE_ACCESS_LEVEL = {
    "xe_45_cho": 1,
    "xe_29_cho": 1,
    "xe_16_cho": 1,
    "o_to":      2,
    "xe_may":    3,
    "xe_dap":    3,
    "di_bo":     3,
}

VEHICLE_ACCESS_NOTE = {
    "xe_45_cho": "Xe 45 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_29_cho": "Xe 29 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_16_cho": "Xe 16 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "o_to":      "Ô tô: đã loại các địa điểm trong hẻm nhỏ không có chỗ đậu xe.",
}

def get_vehicle_osrm_profile(vehicle_type: str):
    """
    Trả về (tên profile OSRM, hệ số tắc đường) theo loại phương tiện.
    - o_to      : Ô tô cá nhân  → driving, hệ số 1.8
    - xe_may    : Xe máy        → driving, hệ số 1.5 (linh hoạt hơn)
    - xe_16_cho : Xe 16 chỗ     → driving, hệ số 2.0 (cấm một số tuyến)
    - xe_29_cho : Xe 29 chỗ     → driving, hệ số 2.2 (cấm nhiều tuyến hơn)
    - xe_45_cho : Xe 45 chỗ     → driving, hệ số 2.5 (cấm nhiều nhất)
    - xe_dap    : Xe đạp        → cycling, hệ số 1.0
    - di_bo     : Đi bộ         → foot,    hệ số 1.0
    """
    profile_map = {
        "o_to":      ("driving", 1.8),
        "xe_may":    ("driving", 1.5),
        "xe_16_cho": ("driving", 2.0),
        "xe_29_cho": ("driving", 2.2),
        "xe_45_cho": ("driving", 2.5),
        "xe_dap":    ("cycling", 1.0),
        "di_bo":     ("foot",    1.0),
    }
    return profile_map.get(vehicle_type, ("driving", 1.8))

def get_large_vehicle_restriction_factor(vehicle_type: str, time_str: str) -> float:
    """
    Tính hệ số phạt do HẠN CHẾ XE LỚN theo giờ.
    Tại Hà Nội và nhiều TP lớn, xe từ 16 chỗ trở lên bị cấm vào
    nội đô trong giờ cao điểm: 6:00-9:00 và 16:00-20:00.
    
    Xe càng lớn → bị cấm nhiều tuyến hơn → phải đi đường vòng → mất thêm thời gian.
    Trả về hệ số nhân thêm vào thời gian di chuyển:
      - 1.0: Không bị hạn chế (ngoài giờ cấm hoặc xe nhỏ)
      - 1.5: Xe 16 chỗ trong giờ cấm (phải đi đường vòng ~50%)
      - 1.8: Xe 29 chỗ trong giờ cấm
      - 2.2: Xe 45 chỗ trong giờ cấm (bị cấm nhiều nhất)
    """
    if vehicle_type not in LARGE_VEHICLE_TYPES:
        return 1.0
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        # TỐI ƯU: dùng hằng số module-level thay vì strptime lại mỗi lần gọi (hàm
        # này cũng nằm trên đường nóng của solve_tsptw_exact) — xem ghi chú ở
        # get_density_factor.
        in_restricted_hours = (
            (_RESTRICTION_MORNING[0] <= t <= _RESTRICTION_MORNING[1]) or
            (_RESTRICTION_EVENING[0] <= t <= _RESTRICTION_EVENING[1])
        )
        if in_restricted_hours:
            return _RESTRICTION_FACTOR_MAP.get(vehicle_type, 1.0)
    except:
        pass
    return 1.0

# ============================================================
# ĐẶT TÊN LỘ TRÌNH THEO NỘI DUNG THỰC TẾ (BUG 3 FIX)
# Backend quyết định tên dựa trên loai_hinh chiếm ưu thế trong route,
# KHÔNG dùng index xoay vòng qua danh sách tên cố định.
# ============================================================
# ============================================================
# ĐẶT TÊN LỘ TRÌNH THEO TRẢI NGHIỆM THẬT & CẢM XÚC
# Tên route phản ánh chủ đề, loại hình, nhịp độ và không khí trải nghiệm,
# TUYỆT ĐỐI KHÔNG dùng logic máy móc kiểu "Đa dạng - 9h36", "Theo sở thích - 12h".
# ============================================================
THEME_EXPERIENCE_NAMES = {
    "relax": [
        "Hà Nội chậm rãi & Những khoảng lặng",
        "Cafe, phố cũ và những khoảng nghỉ",
        "Thong thả ngắm phố & Tìm chút bình yên",
        "Nhịp sống êm đềm bên góc phố quen",
    ],
    "food": [
        "Một ngày khám phá Hà Nội qua ẩm thực",
        "Hành trình vị giác & Hương vị phố xưa",
        "Hà Nội đậm đà qua từng góc quán",
        "Hương vị truyền thống & Cà phê phố cổ",
    ],
    "culture": [
        "Dấu ấn văn hóa & Chiều sâu nghìn năm",
        "Hà Nội hoài niệm qua các di sản",
        "Lắng đọng ký ức lịch sử & Di tích xưa",
        "Hành trình di sản & Chiều sâu văn hiến",
    ],
    "time_optimized": [
        "Cung đường tinh gọn & Trải nghiệm liền mạch",
        "Khám phá trọng tâm, tối ưu nhịp điệu",
        "Hành trình kết nối nhanh các điểm đến",
        "Gọn gàng từng bước chân & Tiện lợi tối đa",
    ],
    "exploration": [
        "Góc nhìn mới & Những khám phá bất ngờ",
        "Hà Nội mở rộng qua những góc phố mới",
        "Hành trình vi vu & Khám phá nét độc đáo",
        "Chạm vào những điểm đến ít người biết",
    ],
    "highlight": [
        "Tinh hoa Hà Nội qua những điểm nổi bật",
        "Trọn vẹn các điểm đến được yêu thích nhất",
        "Những tọa độ biểu tượng không thể bỏ lỡ",
        "Hành trình điểm hẹn kinh kỳ",
    ],
    "preference": [
        "Hành trình thiết kế riêng theo gu của bạn",
        "Theo dòng cảm xúc & Không gian yêu thích",
        "Giai điệu bình yên theo đúng sở thích",
        "Hành trình dành riêng cho tâm hồn bạn",
    ],
    "diverse": [
        "Trọn vẹn sắc màu phố thị",
        "Hòa nhịp muôn màu trải nghiệm Hà Nội",
        "Góc nhìn đa chiều & Trải nghiệm phong phú",
        "Một ngày sống trọn chất Hà Thành",
    ],
}

CATEGORY_EXPERIENCE_NAMES = {
    "Cafe": "Cafe, phố cũ và những khoảng nghỉ",
    "Tham quan": "Dấu ấn văn hóa & Chiều sâu di sản",
    "Checkin": "Hà Nội qua những góc check-in",
    "TTTM": "Nhịp sống hiện đại & Mua sắm giải trí",
    "Ăn uống": "Một ngày khám phá ẩm thực phố thị",
}

THEME_EXPERIENCE_DESCRIPTIONS = {
    "relax": "Hành trình nhẹ nhàng, thong thả với ít điểm dừng để bạn dành nhiều thời gian cảm nhận từng nơi và tận hưởng bầu không khí mà không phải vội vã.",
    "food": "Chuyến du hành ẩm thực kết nối những món ngon nức tiếng và quán cafe có gu, thỏa mãn trọn vẹn vị giác của người sành ăn.",
    "culture": "Hành trình giàu chiều sâu cảm xúc qua các không gian di sản, kiến trúc hoài niệm, đưa bạn ngược dòng lịch sử lắng đọng cùng thủ đô.",
    "time_optimized": "Cung đường được tính toán tối ưu quãng đường di chuyển, tiết kiệm tối đa thời gian trên đường để bạn tận hưởng nhiều thời gian tham quan nhất.",
    "exploration": "Dành cho những ai thích đổi gió với cung đường mở rộng, chạm vào những góc phố và điểm hẹn mang nét độc đáo, bất ngờ.",
    "highlight": "Tuyển tập những địa danh tiêu biểu và được yêu thích nhất, hoàn hảo cho một ngày trải nghiệm tinh hoa thành phố.",
    "preference": "Lộ trình được tuyển chọn riêng bám sát mong muốn và tâm trạng của bạn, mang lại trải nghiệm chuẩn gu và đong đầy cảm xúc.",
    "diverse": "Hành trình đa sắc màu kết hợp hài hòa giữa tham quan, thưởng thức ẩm thực và thư giãn, đem lại trải nghiệm phong phú suốt cả ngày.",
}


def generate_route_name(route_points: list, route_index: int = 1, theme: str = "", user_preference: str = "") -> str:
    """
    Sinh tên lộ trình dựa trên trải nghiệm thật, theme, loại hình địa điểm và sở thích.
    Tuyệt đối không dùng tên máy móc.
    """
    idx_offset = max(0, route_index - 1)

    # 1. Nếu có theme và có danh sách tên trải nghiệm cho theme
    if theme in THEME_EXPERIENCE_NAMES:
        options = THEME_EXPERIENCE_NAMES[theme]
        # Nếu có loại hình chiếm ưu thế áp đảo (>60%), ưu tiên tên trải nghiệm của loại hình đó
        if route_points:
            counts = {}
            for p in route_points:
                lh = p.get("loai_hinh") or "Khác"
                counts[lh] = counts.get(lh, 0) + 1
            total = len(route_points)
            dominant, dcount = max(counts.items(), key=lambda kv: kv[1])
            if dcount / total >= 0.6 and dominant in CATEGORY_EXPERIENCE_NAMES and theme in ("diverse", "preference", "exploration"):
                return CATEGORY_EXPERIENCE_NAMES[dominant]

        return options[idx_offset % len(options)]

    # 2. Nếu không có theme, dựa trên cơ cấu loại hình
    if route_points:
        counts = {}
        for p in route_points:
            lh = p.get("loai_hinh") or "Khác"
            counts[lh] = counts.get(lh, 0) + 1
        total = len(route_points)
        dominant, dcount = max(counts.items(), key=lambda kv: kv[1])
        if dcount / total >= 0.6 and dominant in CATEGORY_EXPERIENCE_NAMES:
            return CATEGORY_EXPERIENCE_NAMES[dominant]
        if len(counts) >= 3:
            return "Trọn vẹn sắc màu phố thị"

    return f"Hành trình trải nghiệm {route_index}"


# ============================================================
# BỘ CHỦ ĐỀ LỘ TRÌNH (mục 4)
# ------------------------------------------------------------
# Mỗi route candidate được gắn một `theme`. Theme KHÔNG phải nhãn trang trí:
# nó quyết định scorer nào được dùng, pool candidate nào được cấp, và ngân
# sách thời gian nào được áp — tức là các route thực sự khác nhau về tập
# điểm / thứ tự / tổng thời gian / mức độ di chuyển.
# ============================================================
ROUTE_THEMES = {
    "time_optimized": {"label": "🏃 Tiết kiệm thời gian", "desc": "Đi gần, ít di chuyển, tận dụng tối đa thời gian tham quan."},
    "preference":     {"label": "🧭 Theo sở thích của bạn", "desc": "Bám sát mô tả mong muốn bạn đã nhập."},
    "relax":          {"label": "🌿 Thong thả", "desc": "Ít điểm hơn, mỗi điểm ở lâu hơn, không vội."},
    "food":           {"label": "🍜 Ẩm thực", "desc": "Xoay quanh quán ăn và cà phê."},
    "culture":        {"label": "🏛️ Văn hoá & tham quan", "desc": "Ưu tiên di tích, bảo tàng, điểm check-in."},
    "exploration":    {"label": "🧳 Khám phá", "desc": "Đi xa hơn một chút để gặp những điểm ít người chọn."},
    "diverse":        {"label": "🌈 Đa dạng", "desc": "Mỗi điểm một kiểu trải nghiệm khác nhau."},
    "highlight":      {"label": "⭐ Điểm nổi bật", "desc": "Gom các địa điểm được đánh giá cao nhất."},
}

# Nhóm loại hình dùng cho theme food / culture.
_FOOD_CATEGORIES = {"Ăn uống", "Cafe"}
_CULTURE_CATEGORIES = {"Tham quan", "Checkin"}


def _fmt_minutes(mins) -> str:
    """90 -> '1h30', 45 -> '45 phút'."""
    mins = int(round(mins or 0))
    if mins < 60:
        return f"{mins} phút"
    h, m = divmod(mins, 60)
    return f"{h}h{m:02d}" if m else f"{h}h"


def generate_route_description(route_points: list, theme: str, total_minutes, travel_minutes, user_preference: str = "") -> str:
    """
    Mô tả ngắn, gợi mở trải nghiệm và giúp người dùng hiểu rõ lộ trình này
    khác lộ trình kia ở đâu (nhịp độ, phong cách, thời gian di chuyển).
    """
    visitable = [p for p in route_points if p.get("loai_hinh") != "diem_xuat_phat"]
    counts = {}
    for p in visitable:
        lh = p.get("loai_hinh") or "Khác"
        counts[lh] = counts.get(lh, 0) + 1
    breakdown = ", ".join(f"{n} {lh.lower()}" for lh, n in sorted(counts.items(), key=lambda kv: -kv[1]))

    narrative = THEME_EXPERIENCE_DESCRIPTIONS.get(
        theme,
        "Hành trình kết hợp hài hòa các điểm đến để bạn có trải nghiệm trọn vẹn và thoải mái nhất."
    )

    parts = [f"{len(visitable)} điểm"]
    if breakdown:
        parts.append(breakdown)
    parts.append(f"di chuyển {_fmt_minutes(travel_minutes)}")
    parts.append(f"tổng {_fmt_minutes(total_minutes)}")

    stats = " · ".join(parts)
    return f"{narrative} ({stats})."


def get_global_osrm_matrix(points_list, vehicle_type: str = "xe_may"):
    """
    Lấy ma trận khoảng cách và thời gian di chuyển từ OSRM.
    Áp dụng profile phương tiện phù hợp và hệ số tắc đường tương ứng.
    """
    osrm_profile, K_TRAFFIC = get_vehicle_osrm_profile(vehicle_type)
    coords = ";".join([f"{p['lon']},{p['lat']}" for p in points_list])
    url = f"http://router.project-osrm.org/table/v1/{osrm_profile}/{coords}?annotations=duration,distance"
    matrix_dict = {}

    try:
        response = requests.get(url, timeout=5).json()
        durations = response["durations"]
        distances = response["distances"]
        for i, p1 in enumerate(points_list):
            matrix_dict[p1["id"]] = {}
            for j, p2 in enumerate(points_list):
                matrix_dict[p1["id"]][p2["id"]] = {
                    "duration": (durations[i][j] / 60.0) * K_TRAFFIC,  # Phút (đã nhân hệ số tắc đường)
                    "distance": distances[i][j] / 1000.0                # Km
                }
        return matrix_dict
    except:
        # Fallback: ước tính khi không gọi được OSRM
        for p1 in points_list:
            matrix_dict[p1["id"]] = {}
            for p2 in points_list:
                matrix_dict[p1["id"]][p2["id"]] = {
                    "duration": (15 if p1["id"] != p2["id"] else 0) * K_TRAFFIC,
                    "distance": 5.0 if p1["id"] != p2["id"] else 0
                }
        return matrix_dict

def calc_dist(p1, p2):
    """Khoảng cách đường chim bay xấp xỉ (km), dùng làm fallback khi thiếu ma trận OSRM."""
    return math.sqrt((p1["lat"] - p2["lat"]) ** 2 + (p1["lon"] - p2["lon"]) ** 2) * 111

def get_travel_minutes(origin_id, dest_id, dist_km, matrix_dict, k_weather, vehicle_type, departure_dt):
    """
    Thời gian di chuyển (phút), tính ĐỘNG theo thời điểm khởi hành thực tế của
    CHẶNG ĐÓ (BUG 5 fix) — không dùng một hệ số cố định tính từ start_time cho
    toàn bộ chuyến đi.

    Congestion (k_density) và hạn chế xe lớn theo giờ (k_restriction) CHỈ tác
    động đến travel_time, KHÔNG được áp dụng cho visit_time (BUG 2 fix).
    """
    time_str = departure_dt.strftime("%H:%M")
    k_density_dynamic = get_density_factor(time_str)
    k_restriction_dynamic = get_large_vehicle_restriction_factor(vehicle_type, time_str)

    try:
        base_duration = matrix_dict[origin_id][dest_id]["duration"]
    except (KeyError, TypeError):
        base_duration = (dist_km / 20.0) * 60

    return base_duration * k_weather * k_density_dynamic * k_restriction_dynamic

def calculate_cost_with_clock(route_indices, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Mô phỏng tuần tự Current Time -> Travel -> Arrival -> Visit -> Next Departure
    cho toàn bộ route, trả về (cost, violation_index).
    violation_index là vị trí (trong route_indices) của điểm ĐẦU TIÊN gây vi phạm
    giờ đóng cửa / vượt khung giờ cho phép — dùng để xác định CHÍNH XÁC điểm cần
    xử lý khi route không khả thi (BUG 6 fix), thay vì đoán mù theo khoảng cách.
    """
    current_clock = clock_start_dt
    penalty = 0
    violation_index = None

    for i in range(len(route_indices)):
        idx = route_indices[i]
        p = points_data[idx]
        p_open = datetime.combine(base_date, p["open_time"])
        p_close = datetime.combine(base_date, p["close_time"])

        if i > 0:
            prev_idx = route_indices[i - 1]
            prev_p = points_data[prev_idx]

            if p["loai_hinh"] == prev_p["loai_hinh"]:
                penalty += 1000

            dist_km = calc_dist(prev_p, p)
            travel_mins = get_travel_minutes(prev_p["id"], p["id"], dist_km, matrix_dict, k_weather, vehicle_type, current_clock)
            current_clock += timedelta(minutes=travel_mins)

        if i > 1:
            prev_prev_idx = route_indices[i - 2]
            prev_prev_p = points_data[prev_prev_idx]
            if p["loai_hinh"] == prev_prev_p["loai_hinh"]:
                penalty += 500

        if current_clock < p_open:
            current_clock = p_open

        visit_mins = p.get("time") or 0   # Visit time KHÔNG nhân với bất kỳ hệ số traffic nào
        departure = current_clock + timedelta(minutes=visit_mins)

        if departure > p_close or departure > clock_end_dt:
            penalty += 10000
            if violation_index is None:
                violation_index = i

        current_clock = departure

    total_minutes = (current_clock - clock_start_dt).total_seconds() / 60
    return total_minutes + penalty, violation_index

def or_opt_pass(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Cải tiến bổ sung cho 2-opt: 2-opt chỉ đảo NGƯỢC một đoạn liên tiếp, nên có những
    cách sắp xếp tốt hơn (dời MỘT điểm sang vị trí khác trong route) mà 2-opt không
    bao giờ thử tới. Or-opt bù đắp đúng chỗ yếu này — đặc biệt hữu ích với route có
    ràng buộc giờ mở/đóng cửa, nơi việc "nhấc" một điểm hay bị time-window chặn ra
    khỏi vị trí ban đầu thường dễ khả thi hơn là đảo ngược cả một đoạn dài.
    """
    best_route = route[:]
    best_cost, best_violation = calculate_cost_with_clock(best_route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best_route)):  # không dời điểm xuất phát (index 0)
            node = best_route[i]
            remaining = best_route[:i] + best_route[i + 1:]
            for j in range(1, len(remaining) + 1):
                if j == i:
                    continue
                candidate = remaining[:j] + [node] + remaining[j:]
                cost, viol = calculate_cost_with_clock(candidate, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
                if cost < best_cost:
                    best_route, best_cost, best_violation = candidate, cost, viol
                    improved = True
                    break
            if improved:
                break
    return best_route, best_cost, best_violation

def optimize_sequence(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Kết hợp 2-opt + Or-opt luân phiên (heuristic cổ điển, chất lượng tốt hơn hẳn dùng
    một mình 2-opt — 2-opt dễ bị kẹt ở local optimum mà Or-opt gỡ ra được, và ngược
    lại). Dùng cho tập điểm LỚN, nơi bitmask DP (solve_tsptw_exact) không còn khả thi
    về mặt hiệu năng.
    """
    r, c, v = two_opt_algorithm(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
    for _ in range(3):  # vài vòng luân phiên là đủ hội tụ trong thực tế, tránh loop vô hạn
        r2, c2, v2 = or_opt_pass(r, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
        r3, c3, v3 = two_opt_algorithm(r2, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
        if c3 >= c - 1e-6:  # không còn cải thiện đáng kể -> dừng
            r, c, v = r3, c3, v3
            break
        r, c, v = r3, c3, v3
    return r, c, v

# Ngưỡng số điểm candidate (KHÔNG tính điểm xuất phát) để còn chạy exact DP trong thời
# gian hợp lý cho một HTTP request đồng bộ. Benchmark thực tế trong sandbox (sau khi đã
# tối ưu get_density_factor/get_large_vehicle_restriction_factor bỏ strptime lặp lại):
#   n=10 → ~0.2s, n=11 → ~0.5s, n=12 → ~1.2s, n=13 → ~2.9s
# Chọn 11 làm điểm an toàn (<1s). BUSINESS RULE NEEDS CONFIRMATION: máy chủ thật của
# nhóm có thể nhanh/chậm hơn sandbox này — nên benchmark lại và tinh chỉnh số này,
# hoặc cân nhắc chạy DP trong background task (không chờ đồng bộ) nếu muốn nâng ngưỡng.
DP_MAX_CANDIDATES = 11

def solve_tsptw_exact(points, matrix_dict, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Giải CHÍNH XÁC (không phải heuristic) bài toán sắp xếp thứ tự thăm điểm có ràng
    buộc giờ mở/đóng cửa (TSP with Time Windows), bằng quy hoạch động bitmask kiểu
    Held-Karp — chất lượng cao hơn 2-opt/Or-opt vì 2-opt chỉ CẢI THIỆN dần từ 1 lời
    giải khởi tạo (có thể kẹt ở tối ưu cục bộ), còn DP này duyệt HẾT không gian trạng
    thái khả thi nên đảm bảo tìm được: (1) tập điểm ghé được NHIỀU NHẤT trong khung
    giờ cho phép, và (2) trong các cách đạt được số điểm đó, cách hoàn thành SỚM NHẤT.

    Giả định (FIFO property): xuất phát/đến sớm hơn ở một điểm không bao giờ khiến
    kết quả về sau tệ hơn xuất phát muộn — hợp lý với mô hình giao thông có tính chu
    kỳ theo giờ trong ngày ở đây, nhưng đây là giả định kỹ thuật cần lưu ý nếu sau
    này mô hình hoá giao thông phức tạp hơn (VD: có sự kiện đột xuất chỉ xảy ra ở
    một mốc giờ cụ thể chứ không theo khung).

    points[0] LUÔN là điểm xuất phát (origin), cố định. points[1:] là các điểm candidate.
    Trả về (order_indices, total_cost, violation_index) để tương thích với chữ ký của
    two_opt_algorithm/or_opt_pass — nhưng ở đây violation_index luôn None vì DP chỉ giữ
    lại những trạng thái ĐÃ được xác nhận khả thi (không có điểm nào bị "gắn cờ vi phạm"
    còn sót lại trong route trả về).
    Trả về None nếu n > DP_MAX_CANDIDATES (caller cần fallback sang optimize_sequence).
    """
    n = len(points) - 1
    if n <= 0:
        return [0], 0.0, None
    if n > DP_MAX_CANDIDATES:
        return None

    origin = points[0]
    cands = points[1:]

    origin_open = datetime.combine(base_date, origin["open_time"])
    origin_close = datetime.combine(base_date, origin["close_time"])
    start_clock = max(clock_start_dt, origin_open)
    origin_finish = start_clock + timedelta(minutes=(origin.get("time") or 0))
    if origin_finish > origin_close or origin_finish > clock_end_dt:
        return [0], 999999.0, 0  # điểm xuất phát tự nó đã vi phạm giờ giấc (hiếm, gần như không xảy ra)

    size = 1 << n
    dp = [[None] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]

    for j in range(n):
        p = cands[j]
        dist = calc_dist(origin, p)
        travel = get_travel_minutes(origin["id"], p["id"], dist, matrix_dict, k_weather, vehicle_type, origin_finish)
        arrival = origin_finish + timedelta(minutes=travel)
        p_open = datetime.combine(base_date, p["open_time"])
        p_close = datetime.combine(base_date, p["close_time"])
        start_visit = max(arrival, p_open)
        finish = start_visit + timedelta(minutes=(p.get("time") or 0))
        if finish <= p_close and finish <= clock_end_dt:
            dp[1 << j][j] = finish

    for mask in range(size):
        for last in range(n):
            if not (mask & (1 << last)) or dp[mask][last] is None:
                continue
            cur_time = dp[mask][last]
            p_last = cands[last]
            for nxt in range(n):
                if mask & (1 << nxt):
                    continue
                p_next = cands[nxt]
                dist = calc_dist(p_last, p_next)
                travel = get_travel_minutes(p_last["id"], p_next["id"], dist, matrix_dict, k_weather, vehicle_type, cur_time)
                arrival = cur_time + timedelta(minutes=travel)
                p_open = datetime.combine(base_date, p_next["open_time"])
                p_close = datetime.combine(base_date, p_next["close_time"])
                start_visit = max(arrival, p_open)
                finish = start_visit + timedelta(minutes=(p_next.get("time") or 0))
                if finish <= p_close and finish <= clock_end_dt:
                    new_mask = mask | (1 << nxt)
                    if dp[new_mask][nxt] is None or finish < dp[new_mask][nxt]:
                        dp[new_mask][nxt] = finish
                        parent[new_mask][nxt] = last

    # Chọn trạng thái ghé được NHIỀU điểm nhất; hoà thì chọn hoàn thành sớm nhất
    best_mask, best_last, best_finish, best_count = 0, -1, None, -1
    for mask in range(size):
        cnt = bin(mask).count("1")
        for j in range(n):
            if dp[mask][j] is None:
                continue
            if cnt > best_count or (cnt == best_count and dp[mask][j] < best_finish):
                best_mask, best_last, best_finish, best_count = mask, j, dp[mask][j], cnt

    if best_last == -1:
        # Không candidate nào khả thi trong khung giờ -> chỉ còn điểm xuất phát
        return [0], (origin_finish - clock_start_dt).total_seconds() / 60, None

    order = []
    mask, last = best_mask, best_last
    while last != -1:
        order.append(last)
        prev = parent[mask][last]
        mask ^= (1 << last)
        last = prev
    order.reverse()

    full_order = [0] + [idx + 1 for idx in order]
    total_cost = (best_finish - clock_start_dt).total_seconds() / 60
    return full_order, total_cost, None

def two_opt_algorithm(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    best_route = route
    best_cost, best_violation_idx = calculate_cost_with_clock(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        # Bắt đầu từ i=1 để KHÔNG đảo điểm xuất phát (index 0 luôn cố định)
        for i in range(1, len(best_route) - 1):
            for j in range(i + 1, len(best_route) + 1):
                if j - i <= 1: continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_cost, new_violation_idx = calculate_cost_with_clock(new_route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
                if new_cost < best_cost:
                    best_route, best_cost, best_violation_idx = new_route, new_cost, new_violation_idx
                    improved = True
    return best_route, best_cost, best_violation_idx

def fetch_all_points(vehicle_type: str = None):
    """
    Lấy toàn bộ địa điểm từ DB (dùng chung cho /api/optimize-route và /api/ai-suggest,
    tránh lặp lại logic parse giờ mở/đóng cửa ở nhiều nơi).
    Nếu truyền vehicle_type, lọc luôn theo khả năng tiếp cận của phương tiện.
    """
    conn, db_type = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
               c.gio_mo_cua, c.gio_dong_cua,
               d.mo_ta, d.thong_tin_chi_tiet, d.review, d.phu_hop, d.url_hinh_anh,
               COALESCE(d.cap_do_tiep_can, 3) AS cap_do_tiep_can
        FROM DIA_DIEM d
        LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
    """
    cursor.execute(query)
    rows = fetch_all_dict(cursor, db_type)
    conn.close()

    all_points = []
    default_open = datetime.strptime("00:00", "%H:%M").time()
    default_close = datetime.strptime("23:59", "%H:%M").time()

    for r in rows:
        open_time = r["gio_mo_cua"] if r["gio_mo_cua"] else default_open
        close_time = r["gio_dong_cua"] if r["gio_dong_cua"] else default_close

        if isinstance(open_time, str): open_time = datetime.strptime(open_time[:5], "%H:%M").time()
        if isinstance(close_time, str): close_time = datetime.strptime(close_time[:5], "%H:%M").time()

        point_data = {
            "id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"],
            "time": r["thoi_gian_tham_quan_phut"], "score": r["diem_gia_tri"], "loai_hinh": r["loai_hinh"],
            "open_time": open_time, "close_time": close_time,
            "mo_ta": r["mo_ta"] or "",
            "thong_tin_chi_tiet": r["thong_tin_chi_tiet"] or "",
            "review": r["review"] or "",
            "phu_hop": r["phu_hop"] or "",
            "url_hinh_anh": r["url_hinh_anh"] or "",
            "cap_do_tiep_can": r["cap_do_tiep_can"] if r["cap_do_tiep_can"] is not None else 3,
        }
        all_points.append(point_data)

    if vehicle_type:
        max_access = VEHICLE_ACCESS_LEVEL.get(vehicle_type, 3)
        all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]

    return all_points


# ============================================================
# CHỌN 3–5 LỘ TRÌNH THỰC SỰ KHÁC NHAU (mục 4)
# ------------------------------------------------------------
# LỖI CŨ: try_add() lấy fingerprint = (thứ tự id, label). Vì label khác nhau
# giữa 6 scorer × 6 mốc ngân sách, CÙNG MỘT tập điểm được ghi nhận tới vài
# chục lần như những "route khác nhau". Sau đó danh sách được sort theo
# (-số điểm, tổng thời gian) rồi cắt [:5] → 5 phần tử đầu gần như luôn là
# CÙNG một lộ trình dài nhất, chỉ khác cái tên chiến lược. Đúng hiện tượng
# "5 route chỉ đổi tên" mà mục 4 cấm.
#
# CÁCH SỬA:
#   1) Khử trùng lặp theo TẬP ĐIỂM (frozenset id), không theo nhãn.
#   2) Chọn tập cuối bằng max-min diversity: route đầu lấy theo chất lượng,
#      các route sau phải đủ KHÁC (Jaccard overlap dưới ngưỡng) so với mọi
#      route đã chọn, đồng thời ưu tiên theme chưa xuất hiện.
#   3) Nếu dữ liệu nghèo, nới ngưỡng dần và chấp nhận trả 3 hoặc 4 route
#      (mục 4 cho phép) thay vì bịa thêm route trùng cho đủ 5.
# ============================================================
JACCARD_LIMITS = (0.45, 0.60, 0.75, 1.01)  # nới dần khi không đủ route
MIN_ROUTES = 3


def _route_id_set(route: dict) -> frozenset:
    return frozenset(
        p["id"] for p in route.get("optimized_route", []) if p.get("id") != "gps_current"
    )


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _route_quality(route: dict, available_minutes: float) -> float:
    """Chất lượng một route: nhiều điểm, khớp sở thích, dùng hết quỹ thời gian."""
    n_places = len(_route_id_set(route))
    utilization = 0.0
    if available_minutes > 0:
        utilization = min(1.0, (route.get("total_time_minutes") or 0) / available_minutes)
    pref = route.get("avg_preference_score", 0.5)
    return n_places * 1.0 + pref * 2.0 + utilization * 2.0


def select_diverse_routes(candidates: list, available_minutes: float, max_routes: int = 5) -> list:
    """Chọn tối đa `max_routes` lộ trình khác nhau thực sự; có thể trả về 3–4
    nếu dữ liệu không cho phép nhiều hơn mà vẫn giữ được sự khác biệt."""
    if not candidates:
        return []

    scored = sorted(
        ({"route": r, "ids": _route_id_set(r), "q": _route_quality(r, available_minutes)} for r in candidates),
        key=lambda c: -c["q"],
    )

    max_routes = max(MIN_ROUTES, min(5, max_routes))
    chosen = [scored[0]]
    used_themes = {scored[0]["route"].get("theme")}

    for limit in JACCARD_LIMITS:
        if len(chosen) >= max_routes:
            break
        # Hai lượt quét cho mỗi ngưỡng: lượt 1 chỉ nhận theme CHƯA xuất hiện, lượt 2
        # mới nhận theme trùng. Nhờ vậy 3–5 route trả về trải đều các chủ đề thay vì
        # dồn hết vào một chiến lược tình cờ cho điểm chất lượng cao.
        for prefer_new_theme in (True, False):
            for cand in scored:
                if len(chosen) >= max_routes:
                    break
                if any(cand["route"] is c["route"] for c in chosen):
                    continue
                theme = cand["route"].get("theme")
                if prefer_new_theme and theme in used_themes:
                    continue
                overlap = max(_jaccard(cand["ids"], c["ids"]) for c in chosen)
                if overlap > limit:
                    continue
                chosen.append(cand)
                used_themes.add(theme)

    # Sắp xếp hiển thị: route mạnh nhất lên đầu để Screen 2 gợi ý mặc định đúng.
    chosen.sort(key=lambda c: -c["q"])
    return [c["route"] for c in chosen]


def build_timeline(places: list) -> list:
    """
    Timeline phẳng cho Screen 2/3: xen kẽ 'visit' và 'travel'. Sinh TỪ chính
    `places` (arrive/depart/travel_to_next đã được finalize_route mô phỏng),
    nên map, timeline và route summary luôn nói cùng một câu chuyện (mục 11).
    """
    timeline = []
    for i, p in enumerate(places):
        timeline.append({
            "type": "visit",
            "order": i + 1,
            "place_id": p["id"],
            "name": p["ten"],
            "loai_hinh": p.get("loai_hinh", ""),
            "start": p.get("arrive_time"),
            "end": p.get("depart_time"),
            "duration": p.get("visit_time", 0),
            "wait_time": p.get("wait_time", 0),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
        })
        if i < len(places) - 1 and (p.get("travel_to_next") or 0) > 0:
            timeline.append({
                "type": "travel",
                "from_id": p["id"],
                "to_id": places[i + 1]["id"],
                "from": p["ten"],
                "to": places[i + 1]["ten"],
                "start": p.get("depart_time"),
                "end": places[i + 1].get("arrive_time"),
                "duration": p.get("travel_to_next", 0),
                "distance_km": p.get("distance_to_next", 0),
            })
    return timeline


def format_route_object(route: dict, index: int) -> dict:
    """
    Đóng gói route thành MỘT THỰC THỂ ĐỘC LẬP theo schema mục 3.

    Giữ nguyên các khoá cũ (strategy / route_name / optimized_route /
    total_time_minutes) để app.js hiện tại không vỡ — mục 13 yêu cầu không
    sửa frontend ngoài phạm vi cần thiết.
    """
    places = route.get("optimized_route", [])
    travel_time = sum((p.get("travel_to_next") or 0) for p in places)
    visit_time = sum((p.get("visit_time") or 0) for p in places)
    wait_time = sum((p.get("wait_time") or 0) for p in places)
    distance_km = sum((p.get("distance_to_next") or 0) for p in places)
    theme = route.get("theme", "diverse")
    total = route.get("total_time_minutes", 0)

    route_points = [{"loai_hinh": p.get("loai_hinh"), "ten": p.get("ten")} for p in places]
    existing_name = route.get("route_name")
    if not existing_name or existing_name.startswith("Lộ trình ") or existing_name.startswith("Hành trình trải nghiệm "):
        name = generate_route_name(route_points, index, theme)
    else:
        name = existing_name

    route.update({
        "route_id": f"route_{index}",
        "name": name,
        "theme": theme,
        "theme_label": ROUTE_THEMES.get(theme, {}).get("label", theme),
        "description": generate_route_description(places, theme, total, travel_time),
        "places": places,
        "timeline": build_timeline(places),
        "total_duration": round(total, 1),
        "travel_time": round(travel_time, 1),
        "visit_time": round(visit_time, 1),
        "wait_time": round(wait_time, 1),
        "distance_km": round(distance_km, 1),
        "place_count": len(places),
        # ---- khoá cũ, giữ cho tương thích ngược ----
        "route_name": name,
    })
    return route


def run_route_generation(request: OptimizationRequest,
                         excluded_ids: set = None,
                         must_visit_ids: set = None,
                         force_visit_ids: set = None,
                         mode: str = "multi"):
    """
    LÕI SINH LỘ TRÌNH — dùng chung cho mọi endpoint (mục 10, 11).

    Trước đây toàn bộ logic này nằm trực tiếp trong @app.post("/api/optimize-route"),
    nên không endpoint nào khác (chọn route, cập nhật itinerary, chatbot) tái sử
    dụng được — mỗi luồng lại tự gọi lại HTTP và sinh lại route mới. Tách ra thành
    hàm thuần để:
      * /api/routes            → mode="multi"  : sinh 3–5 route candidate
      * /api/itinerary/update  → mode="single" : tính lại đúng MỘT lịch trình
      * /api/optimize-route    → giữ nguyên hành vi cũ cho frontend hiện tại

    excluded_ids : RÀNG BUỘC CỨNG. Áp ngay tại nguồn candidate pool nên mọi nhánh
                   phía sau (greedy, fill-up, DP, 2-opt) đều tự động tôn trọng
                   (mục 6, 7). KHÔNG hề đụng tới database.
    must_visit_ids: điểm phải giữ (từ AI hoặc từ itinerary hiện tại) — chỉ là ưu
                   tiên tuyệt đối trong greedy, KHÔNG giới hạn candidate pool (mục 5).
    force_visit_ids: điểm người dùng VỪA CHỦ ĐỘNG yêu cầu thêm. Ưu tiên cao hơn
                   must_visit một bậc, để khi quỹ thời gian đã gần đầy thì điểm
                   mới vẫn chen được vào và điểm auto-fill bị đẩy ra — đúng tinh
                   thần "A → B → X → D → E" của mục 9.
    """
    excluded_ids = set(excluded_ids or ())
    force_visit_ids = set(force_visit_ids or ()) - excluded_ids
    must_visit_ids = set(must_visit_ids or ()) | force_visit_ids
    # Điểm vừa bị loại thì không thể đồng thời là must-visit.
    must_visit_ids -= excluded_ids

    # 1. XỬ LÝ NGÀY KHỞI HÀNH (trip_date)
    try:
        if request.trip_date:
            base_date = datetime.strptime(request.trip_date, "%Y-%m-%d").date()
        else:
            base_date = datetime.today().date()
    except:
        base_date = datetime.today().date()

    # 2. XỬ LÝ THỜI GIAN BẮT ĐẦU / KẾT THÚC
    try:
        clock_start = datetime.strptime(request.start_time, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        clock_end = datetime.strptime(request.end_time, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        if clock_end <= clock_start:
            clock_end += timedelta(days=1)
        available_minutes = (clock_end - clock_start).total_seconds() / 60
    except:
        raise HTTPException(status_code=400, detail="Lỗi định dạng thời gian (start_time/end_time phải theo dạng HH:MM)")

    # 3. LẤY DANH SÁCH ĐỊA ĐIỂM TỪ DATABASE
    all_points = fetch_all_points()

    # 3b. LỌC ĐỊA ĐIỂM THEO KHẢ NĂNG TIẾP CẬN CỦA PHƯƠNG TIỆN
    max_access = VEHICLE_ACCESS_LEVEL.get(request.vehicle_type, 3)
    all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]
    vehicle_note = VEHICLE_ACCESS_NOTE.get(request.vehicle_type, "")

    # 3b'. ÁP RÀNG BUỘC LOẠI TRỪ NGAY TẠI NGUỒN (mục 6, 7)
    # Đây là điểm mấu chốt: lọc ở ĐÂY nghĩa là excluded_ids trở thành ràng buộc
    # thật của TOÀN BỘ pipeline phía sau — ma trận OSRM, greedy, fill-up, DP,
    # 2-opt đều không bao giờ nhìn thấy điểm đã bị loại. Không phải "ẩn ở
    # frontend", không phải "xoá khỏi một mảng tạm", và tuyệt đối không xoá dữ
    # liệu khỏi database: bản ghi vẫn nguyên trong DIA_DIEM, chỉ là phiên này
    # không được dùng nó.
    if excluded_ids:
        all_points = [p for p in all_points if p["id"] not in excluded_ids]
        if not all_points:
            return {
                "status": "error",
                "message": "Bạn đã loại quá nhiều địa điểm, không còn lựa chọn nào phù hợp.",
                "excluded_ids": sorted(excluded_ids),
            }

    # 3c. TÍNH PREFERENCE SCORE CHO TỪNG ĐIỂM (BUG 1 FIX)
    # user_preference + weight phải thực sự tham gia scoring/route generation,
    # không chỉ được nhận vào rồi bỏ qua.
    preference_keywords = _extract_keywords(request.user_preference)
    pref_weight = request.weight / 100.0  # 0 = ưu tiên khoảng cách, 1 = ưu tiên đúng sở thích
    for p in all_points:
        p["preference_score"] = calculate_preference_score(p, preference_keywords)

    # 4. LẤY MA TRẬN KHOẢNG CÁCH THEO PHƯƠNG TIỆN (vehicle_type)
    global_matrix = get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)

    # 5. XÁC ĐỊNH ĐIỂM XUẤT PHÁT (start_point)
    all_points.sort(key=lambda x: (x["score"] if x["score"] is not None else 0), reverse=True)

    if request.start_lat is not None and request.start_lon is not None:
        # TRƯỜNG HỢP 1: Người dùng dùng GPS → tạo điểm ảo "Vị trí hiện tại"

        # Khai báo thời gian hoạt động mặc định cho điểm GPS
        default_open = datetime.strptime("00:00", "%H:%M").time()
        default_close = datetime.strptime("23:59", "%H:%M").time()

        # Điểm này không có trong DB, thời gian tham quan = 0 phút
        gps_point = {
            "id": "gps_current",
            "ten": "📍 Vị trí của bạn",
            "lat": request.start_lat,
            "lon": request.start_lon,
            "time": 0,              
            "loai_hinh": "diem_xuat_phat",
            "open_time": default_open,
            "close_time": default_close,
            "mo_ta": "", "thong_tin_chi_tiet": "", "review": "", "phu_hop": "",
            "preference_score": 0.5,
        }

        all_points_with_gps = [gps_point] + all_points
        global_matrix = get_global_osrm_matrix(all_points_with_gps, vehicle_type=request.vehicle_type)
        all_points = all_points_with_gps   # Cập nhật danh sách để route dùng đúng
        starting_points = [gps_point]      # Chỉ xuất phát từ vị trí GPS

    elif request.start_point and request.start_point.strip():
        # TRƯỜNG HỢP 2: Người dùng nhập tên điểm → tìm trong DB
        keyword = request.start_point.strip().lower()
        matched_points = [p for p in all_points if keyword in p["ten"].lower()]
        if matched_points:
            starting_points = matched_points[:1]  # Chỉ xuất phát từ điểm tìm được
        else:
            # BUG 7 FIX: KHÔNG được âm thầm fallback sang all_points[:1] (điểm bất kỳ).
            # Trả lỗi rõ ràng để Frontend thông báo cho người dùng, thay vì tự ý
            # chọn một điểm xuất phát không liên quan đến địa chỉ họ đã nhập.
            raise HTTPException(
                status_code=400,
                detail=f"Không tìm thấy địa điểm xuất phát '{request.start_point}'. "
                       f"Vui lòng kiểm tra lại địa chỉ hoặc sử dụng định vị GPS."
            )

    else:
        # TRƯỜNG HỢP 3: Không nhập gì (không GPS, không tên) → KHÔNG được tự ý
        # chọn đại điểm xuất phát. Đây cũng là fallback nguy hiểm giống BUG 7,
        # nên áp dụng cùng nguyên tắc: phải có GPS hoặc địa chỉ hợp lệ mới chạy.
        raise HTTPException(
            status_code=400,
            detail="Vui lòng nhập điểm xuất phát hoặc sử dụng định vị GPS."
        )
    # 6. TÍNH TOÁN LỘ TRÌNH TỐI ƯU
    # LƯU Ý: k_density (mật độ giao thông) và k_restriction (hạn chế xe lớn theo giờ)
    # KHÔNG còn được tính một lần từ request.start_time rồi dùng cho toàn bộ chuyến đi
    # (đó chính là BUG 5). Hai hệ số này giờ được get_travel_minutes() tính LẠI động,
    # theo đúng thời điểm khởi hành thực tế của TỪNG CHẶNG di chuyển.

    # (Đã loại bỏ hàm build_route_greedy() vì là dead code — không được gọi ở bất kỳ
    #  đâu trong flow hiện tại, chỉ build_route_greedy_custom() mới thực sự sinh route.
    #  Hàm cũ còn giữ nguyên lỗi visit_time*k_dens nên loại bỏ để tránh gây nhầm lẫn
    #  hoặc bị dùng nhầm trong tương lai. Xem phần báo cáo audit.)

    def finalize_route(selected_points, k_weather, route_label, route_index, route_theme="diverse"):
        """
        Sắp xếp thứ tự thăm điểm tối ưu, xử lý drop-point khi route vi phạm giờ
        đóng cửa, rồi tạo chi tiết lộ trình cuối cùng (kèm route_name theo nội
        dung thực tế). Trả về dict lộ trình hoặc None nếu không hợp lệ.

        Chọn thuật toán sắp xếp theo số lượng điểm:
        - Số điểm nhỏ (<= DP_MAX_CANDIDATES): dùng solve_tsptw_exact — quy hoạch
          động, đảm bảo tối ưu THẬT SỰ (không chỉ "cải thiện dần" như 2-opt), và
          tự nhiên xử lý luôn việc "bỏ bớt điểm nếu không kịp giờ" mà không cần
          vòng lặp drop-point kiểu cũ.
        - Số điểm lớn hơn: fallback về optimize_sequence (2-opt + Or-opt luân
          phiên), kèm vòng lặp drop-point dựa trên violation_index (BUG-6 fix).
        """
        # Chốt chặn thứ ba: không route nào được finalize nếu còn chứa điểm bị loại.
        if excluded_ids:
            selected_points = [p for p in selected_points if p["id"] not in excluded_ids]
        if len(selected_points) < 2:
            return None

        n_candidates = len(selected_points) - 1
        dropped_point = False

        if n_candidates <= DP_MAX_CANDIDATES:
            dp_result = solve_tsptw_exact(
                selected_points, global_matrix, k_weather, request.vehicle_type,
                clock_start, clock_end, base_date
            )
            best_route_indices, best_cost, violation_idx = dp_result
            if len(best_route_indices) < len(selected_points):
                dropped_point = True   # DP tự loại bớt điểm không kịp giờ, không phải bug
        else:
            initial_route = list(range(len(selected_points)))
            best_route_indices, best_cost, violation_idx = optimize_sequence(
                initial_route, global_matrix, selected_points,
                k_weather, request.vehicle_type, clock_start, clock_end, base_date
            )

            # Vòng lặp drop-point (chỉ cần cho nhánh fallback lớn — DP ở trên đã tự
            # chọn tập điểm khả thi nhiều nhất nên không cần bước này).
            # BUG 6 FIX: xoá đúng ĐIỂM GÂY VI PHẠM (violation_idx do calculate_cost_with_clock
            # xác định), KHÔNG xoá điểm xa nhất so với điểm xuất phát (furthest_idx).
            max_drop_attempts = len(best_route_indices)
            attempts = 0
            while best_cost >= 10000 and len(best_route_indices) > 2 and attempts < max_drop_attempts:
                attempts += 1
                if violation_idx is None or not (0 <= violation_idx < len(best_route_indices)):
                    break
                point_to_drop = best_route_indices[violation_idx]
                if point_to_drop == best_route_indices[0]:
                    break
                best_route_indices = [x for x in best_route_indices if x != point_to_drop]
                dropped_point = True
                best_route_indices, best_cost, violation_idx = optimize_sequence(
                    best_route_indices, global_matrix, selected_points,
                    k_weather, request.vehicle_type, clock_start, clock_end, base_date
                )

        if len(best_route_indices) < 2 or best_cost >= 10000:
            return None

        final_route_details = []
        simulated_clock = clock_start

        for i in range(len(best_route_indices)):
            idx = best_route_indices[i]
            point = selected_points[idx]
            p_open = datetime.combine(base_date, point["open_time"])

            travel_time = 0
            if i > 0:
                prev_point = selected_points[best_route_indices[i - 1]]
                dist_km = calc_dist(prev_point, point)
                travel_time = get_travel_minutes(prev_point["id"], point["id"], dist_km, global_matrix, k_weather, request.vehicle_type, simulated_clock)
                simulated_clock += timedelta(minutes=travel_time)

            wait_time = 0
            if simulated_clock < p_open:
                wait_time = (p_open - simulated_clock).total_seconds() / 60
                simulated_clock = p_open

            arrive_time_str = simulated_clock.strftime("%H:%M")   # Giờ đến
            visit_time = point.get("time") or 0   # Visit time KHÔNG nhân hệ số traffic (BUG 2 fix)
            simulated_clock += timedelta(minutes=visit_time)
            depart_time_str = simulated_clock.strftime("%H:%M")   # Giờ rời

            final_route_details.append({
                "id": point["id"], "ten": point["ten"],
                "lat": point["lat"], "lon": point["lon"],
                "loai_hinh": point.get("loai_hinh", ""),
                "mo_ta": point.get("mo_ta", ""),
                "thong_tin_chi_tiet": point.get("thong_tin_chi_tiet", ""),
                "review": point.get("review", ""),
                "phu_hop": point.get("phu_hop", ""), "url_hinh_anh": point.get("url_hinh_anh", ""),
               
                "visit_time": round(visit_time, 1),
                "wait_time": round(wait_time, 1),
                "arrive_time": arrive_time_str,
                "depart_time": depart_time_str,
                "travel_to_next": 0,
                "distance_to_next": 0
            })

        # travel_to_next hiển thị cho UI: dùng lại đúng thời điểm khởi hành thực tế
        # (arrive/depart đã mô phỏng ở trên) để nhất quán với travel_time đã dùng khi
        # tính lịch trình, thay vì tính lại bằng một công thức khác (tránh double logic).
        for i in range(len(final_route_details) - 1):
            idx1 = best_route_indices[i]
            idx2 = best_route_indices[i + 1]
            p1, p2 = selected_points[idx1], selected_points[idx2]
            departure_dt = datetime.combine(base_date, datetime.strptime(final_route_details[i]["depart_time"], "%H:%M").time())
            dist_km = calc_dist(p1, p2)
            travel_dur = get_travel_minutes(p1["id"], p2["id"], dist_km, global_matrix, k_weather, request.vehicle_type, departure_dt)
            try:
                travel_dist = global_matrix[p1["id"]][p2["id"]]["distance"]
            except (KeyError, TypeError):
                travel_dist = dist_km
            final_route_details[i]["travel_to_next"] = round(travel_dur, 1)
            final_route_details[i]["distance_to_next"] = round(travel_dist, 1)

        total_actual = (simulated_clock - clock_start).total_seconds() / 60
        route_pts = [selected_points[i] for i in best_route_indices]
        avg_preference = sum(p.get("preference_score", 0.5) for p in route_pts) / len(route_pts)

        covered_must = len([p for p in route_pts if p["id"] in must_visit_ids])

        return {
            "dropped_point": dropped_point,
            "total_time_minutes": round(total_actual, 1),
            "vehicle_type": request.vehicle_type,
            "trip_date": str(base_date),
            "strategy": route_label,
            "theme": route_theme,
            "route_name": generate_route_name(route_pts, route_index, route_theme, request.user_preference),
            "avg_preference_score": round(avg_preference, 3),
            "must_visit_covered": covered_must,
            "must_visit_total": len(must_visit_ids),
            "optimized_route": final_route_details
        }

    def build_route_greedy_custom(origin, candidate_pool, k_weather, end_clock, score_fn,
                                   must_visit_ids=None):
        """
        Greedy builder với scorer tùy chỉnh.
        score_fn(last_point, candidate, dist_km) -> float  (nhỏ hơn = ưu tiên hơn)
        end_clock: thời điểm kết thúc tối đa (clock_end hoặc fake ngắn hơn).
        must_visit_ids: tập ID điểm bắt buộc ghé (từ AI), được ưu tiên tuyệt đối.

        travel_time dùng get_travel_minutes() (tính động theo thời điểm khởi hành
        thực tế của từng chặng — BUG 5 fix). visit_time KHÔNG nhân hệ số traffic
        (BUG 2 fix).
        """
        must_visit_ids = must_visit_ids or set()
        origin_open  = datetime.combine(base_date, origin["open_time"])
        origin_close = datetime.combine(base_date, origin["close_time"])
        current_clock = max(clock_start, origin_open)
        departure = current_clock + timedelta(minutes=(origin.get("time") or 0))
        if departure > origin_close or departure > end_clock:
            return None
        selected  = [origin]

        # ── CANDIDATE POOL CỦA GREEDY (mục 7) ──
        # Chốt chặn thứ hai, cố ý trùng với bộ lọc ở bước 3b': kể cả khi sau này
        # có ai đó truyền thẳng một pool chưa lọc vào hàm này, điểm đã bị loại
        # vẫn không thể lọt vào lộ trình. `used_ids` đảm bảo không ghé trùng điểm.
        used_ids  = {origin["id"]}
        unvisited = [
            p for p in candidate_pool
            if p["id"] not in used_ids
            and p["id"] not in excluded_ids
        ]
        while True:
            best_next  = None
            best_score = float('inf')
            best_dep   = departure
            for p in unvisited:
                if p["id"] in used_ids or p["id"] in excluded_ids:
                    continue
                dist = calc_dist(selected[-1], p)
                est_travel = get_travel_minutes(selected[-1]["id"], p["id"], dist, global_matrix, k_weather, request.vehicle_type, departure)
                est_visit  = p.get("time") or 0
                arrival    = departure + timedelta(minutes=est_travel)
                p_open     = datetime.combine(base_date, p["open_time"])
                p_close    = datetime.combine(base_date, p["close_time"])
                sv         = max(arrival, p_open)
                nd         = sv + timedelta(minutes=est_visit)
                if nd <= p_close and nd <= end_clock:
                    sc = score_fn(selected[-1], p, dist)
                    # ƯU TIÊN PHÂN TẦNG:
                    #   force_visit (user vừa yêu cầu thêm) > must_visit (đang giữ) > phần còn lại.
                    # Nếu để force và must cùng một mức, khi quỹ giờ gần đầy Greedy sẽ
                    # nhồi hết các điểm đang giữ trước rồi hết chỗ cho điểm mới.
                    if p["id"] in force_visit_ids:
                        sc = -9999999 + sc * 0.001
                    elif p["id"] in must_visit_ids:
                        sc = -99999 + sc * 0.001
                    if sc < best_score:
                        best_score = sc
                        best_next  = p
                        best_dep   = nd
            if best_next:
                selected.append(best_next)
                used_ids.add(best_next["id"])
                unvisited.remove(best_next)
                departure = best_dep
            else:
                break
        return selected if len(selected) >= 2 else None

    # Biên độ (km-tương-đương) tối đa mà mức độ KHÔNG phù hợp preference có thể
    # "phạt" vào điểm số greedy. Dùng để hoà trộn preference_score (thang 0..1)
    # với distance (thang km) theo đúng pref_weight người dùng chọn.
    PREF_PENALTY_SCALE_KM = 6.0

    def _preference_penalty(p):
        return (1.0 - p.get("preference_score", 0.5)) * PREF_PENALTY_SCALE_KM

    def generate_routes_with_factor():
        """
        Sinh KHO route candidate theo nhiều CHIẾN LƯỢC KHÁC NHAU THỰC SỰ (mục 4).

        Mỗi chiến lược khác nhau ở ít nhất một trong: candidate pool được cấp,
        hàm chấm điểm greedy, và ngân sách thời gian. Nhờ vậy các route khác
        nhau về tập địa điểm, thứ tự, tổng thời gian, mức độ di chuyển và đặc
        điểm trải nghiệm — không phải cùng một lộ trình mang 5 cái tên.

        Trả về danh sách candidate thô; việc chọn ra 3–5 lộ trình cuối cùng do
        select_diverse_routes() đảm nhiệm.
        """
        generated    = []
        seen_id_sets = {}   # frozenset(id) -> vị trí trong `generated`

        def try_add(pts, k_weather, label, theme):
            if not pts or len(pts) < 2:
                return
            route_index = len(generated) + 1
            r = finalize_route(pts, k_weather, label, route_index, route_theme=theme)
            if not r:
                return

            # KHỬ TRÙNG LẶP THEO TẬP ĐIỂM, KHÔNG THEO NHÃN (mục 4).
            # Cùng một tập địa điểm thì dù nhãn chiến lược khác nhau vẫn chỉ là
            # MỘT lộ trình — trước đây đưa nhãn vào fingerprint chính là nguyên
            # nhân sinh ra hàng chục "route" trùng nhau.
            fp = frozenset(p["id"] for p in r["optimized_route"] if p["id"] != "gps_current")
            if not fp:
                return
            if fp in seen_id_sets:
                # Giữ bản tốt hơn: phủ được nhiều must-visit hơn, rồi tới nhanh hơn.
                old = generated[seen_id_sets[fp]]
                better = (r.get("must_visit_covered", 0), -r["total_time_minutes"]) > \
                         (old.get("must_visit_covered", 0), -old["total_time_minutes"])
                if better:
                    generated[seen_id_sets[fp]] = r
                return
            seen_id_sets[fp] = len(generated)
            generated.append(r)

        for origin in starting_points:
            k_weather      = get_weather_factor(origin["lat"], origin["lon"])
            # Toàn bộ candidate pool (đã trừ excluded_ids ở bước 3b') — AI chỉ là
            # ƯU TIÊN, không phải danh sách duy nhất, nên Greedy vẫn được quyền
            # fill-up từ đây khi còn dư thời gian (mục 5).
            base_unvisited = [p for p in all_points if p["id"] != origin["id"]]
            if not base_unvisited:
                continue

            pool_near  = sorted(base_unvisited, key=lambda p: calc_dist(origin, p))
            pool_value = sorted(base_unvisited, key=lambda p: -(p.get("score") or 0))
            pool_food  = [p for p in base_unvisited if p.get("loai_hinh") in _FOOD_CATEGORIES]
            pool_cult  = [p for p in base_unvisited if p.get("loai_hinh") in _CULTURE_CATEGORIES]
            pool_long  = sorted(base_unvisited, key=lambda p: -(p.get("time") or 0))
            pool_far   = list(reversed(pool_near))

            # ── SCORER THEO CHỦ ĐỀ ──
            # (theme, nhãn hiển thị, pool, score_fn, hệ số ngân sách thời gian)
            # Lưu ý ngân sách khác nhau → tổng thời gian khác nhau, đúng yêu cầu
            # "các route cần khác nhau về tổng thời gian" (mục 4).
            strategies = [
                ("time_optimized", base_unvisited,
                 lambda last, p, d: d, 1.0),

                ("preference", base_unvisited,
                 lambda last, p, d: d * (1 - pref_weight) + _preference_penalty(p) * pref_weight, 1.0),

                # Thong thả: quỹ thời gian ngắn hơn + ưu tiên điểm ở được lâu
                # → ít điểm hơn, mỗi điểm lâu hơn, ít di chuyển hơn.
                ("relax", pool_long,
                 lambda last, p, d: d * 0.6 - (p.get("time") or 0) * 0.05, 0.7),

                ("food", pool_food or base_unvisited,
                 lambda last, p, d: d - (3.0 if p.get("loai_hinh") in _FOOD_CATEGORIES else 0), 1.0),

                ("culture", pool_cult or base_unvisited,
                 lambda last, p, d: d - (3.0 if p.get("loai_hinh") in _CULTURE_CATEGORIES else 0), 1.0),

                # Khám phá: bắt đầu từ nửa xa của bản đồ → tập điểm lệch hẳn.
                ("exploration", pool_far[: max(8, len(pool_far) // 2)],
                 lambda last, p, d: d / ((p.get("score") or 1) + 1), 1.0),

                # Đa dạng: phạt nặng nếu trùng loại hình với điểm vừa ghé.
                ("diverse", base_unvisited,
                 lambda last, p, d: d + (150 if p.get("loai_hinh") == last.get("loai_hinh") else 0), 1.0),

                ("highlight", pool_value[: max(8, len(pool_value) // 2)],
                 lambda last, p, d: -(p.get("score") or 0) + d * 0.1, 1.0),
            ]

            for theme, pool, score_fn, ratio in strategies:
                if not pool:
                    continue
                end_t = clock_start + timedelta(minutes=available_minutes * ratio)
                label = ROUTE_THEMES.get(theme, {}).get("label", theme)
                try_add(
                    build_route_greedy_custom(origin, pool, k_weather, end_t, score_fn,
                                              must_visit_ids=must_visit_ids),
                    k_weather, label, theme
                )

            # ── BIẾN THỂ ĐỘ DÀI: cùng chủ đề nhưng quỹ thời gian khác nhau ──
            # Cho người dùng lựa chọn "đi nhẹ nhàng nửa ngày" so với "đi trọn ngày".
            for ratio in (0.5, 0.75):
                mins = available_minutes * ratio
                if mins < 60:
                    continue
                end_t = clock_start + timedelta(minutes=mins)
                label = f"{ROUTE_THEMES['time_optimized']['label']} · {_fmt_minutes(mins)}"
                try_add(
                    build_route_greedy_custom(origin, base_unvisited, k_weather, end_t,
                                              lambda last, p, d: d,
                                              must_visit_ids=must_visit_ids),
                    k_weather, label, "time_optimized"
                )
                label = f"{ROUTE_THEMES['relax']['label']} · {_fmt_minutes(mins)}"
                try_add(
                    build_route_greedy_custom(origin, pool_long, k_weather, end_t,
                                              lambda last, p, d: d * 0.6 - (p.get("time") or 0) * 0.05,
                                              must_visit_ids=must_visit_ids),
                    k_weather, label, "relax"
                )

            # ── LOẠI TRỪ ĐIỂM TOP → ép ra tập địa điểm lệch hẳn ──
            # Lưu ý: đây là biến thể TẠO SỰ ĐA DẠNG trong lúc sinh candidate,
            # hoàn toàn khác với excluded_ids (ràng buộc của người dùng).
            for skip in range(min(4, len(pool_value))):
                pool_excl = [p for p in base_unvisited if p["id"] != pool_value[skip]["id"]]
                try_add(
                    build_route_greedy_custom(origin, pool_excl, k_weather, clock_end,
                                              lambda last, p, d: d / ((p.get("score") or 1) + 1),
                                              must_visit_ids=must_visit_ids),
                    k_weather, f"{ROUTE_THEMES['exploration']['label']} #{skip + 1}", "exploration"
                )

            # ── Random sample nhiều seed: mở rộng kho candidate cho bước chọn đa dạng ──
            for seed in (7, 13, 42, 77, 99, 111):
                rng    = random.Random(seed)
                n      = max(6, int(len(base_unvisited) * (0.3 + (seed % 5) * 0.1)))
                n      = min(n, len(base_unvisited))
                sample = rng.sample(base_unvisited, n)
                try_add(
                    build_route_greedy_custom(origin, sample, k_weather, clock_end,
                                              lambda last, p, d: d / ((p.get("score") or 1) + 1),
                                              must_visit_ids=must_visit_ids),
                    k_weather, f"{ROUTE_THEMES['exploration']['label']} #{seed}", "exploration"
                )

        return generated


    # ============================================================
    # MUST-VISIT (AI + itinerary hiện tại) — FILL-UP LOGIC (mục 5)
    # ------------------------------------------------------------
    # ai_selected_ids CHỈ LÀ ƯU TIÊN, không phải danh sách duy nhất. Cố ý KHÔNG
    # ghi đè all_points bằng (starting_points + ai_points): làm thế sẽ cắt mất
    # toàn bộ candidate còn lại và Greedy hết đường fill-up khi còn dư thời gian.
    # Toàn bộ candidate pool (đã trừ excluded_ids) vẫn nằm trong all_points.
    # ============================================================
    if request.ai_selected_ids:
        valid_ids = {p["id"] for p in all_points}
        ai_ids = {str(i) for i in request.ai_selected_ids} & valid_ids
        # Điểm AI gợi ý nhưng người dùng đã loại thì KHÔNG được quay lại (mục 8):
        # excluded_ids đã bị loại khỏi all_points nên phép giao ở trên tự lọc sạch.
        if not ai_ids and not must_visit_ids:
            return {"status": "error", "message": "Không có ID điểm đến hợp lệ nào."}
        must_visit_ids = (must_visit_ids | ai_ids) - excluded_ids

    generated_routes = generate_routes_with_factor()

    if len(generated_routes) == 0:
        return {
            "status": "error",
            "message": "Quỹ thời gian quá ngắn hoặc các địa điểm đều chưa mở cửa vào khung giờ này!",
            "excluded_ids": sorted(excluded_ids),
        }

    # ============================================================
    # MODE "single": tính lại ĐÚNG MỘT lịch trình (mục 9)
    # Dùng cho /api/itinerary/update sau khi người dùng xoá điểm. Chọn candidate
    # giữ được nhiều điểm-phải-giữ nhất, rồi mới tới nhiều điểm / dùng hết quỹ
    # thời gian. Điểm đã xoá không thể quay lại vì đã bị loại khỏi all_points.
    # ============================================================
    if mode == "single":
        def _rank(r):
            covered_force = len([p for p in r["optimized_route"] if p["id"] in force_visit_ids])
            return (
                covered_force,                      # điểm user vừa thêm phải vào được trước
                r.get("must_visit_covered", 0),     # rồi mới tới giữ lại nhiều nhất
                len(r["optimized_route"]),
                -abs(available_minutes - r["total_time_minutes"]),
            )

        best = max(generated_routes, key=_rank)
        itinerary = format_route_object(best, 1)
        return {
            "status": "success",
            "available_minutes": available_minutes,
            "trip_date": str(base_date),
            "vehicle_type": request.vehicle_type,
            "vehicle_note": vehicle_note,
            "accessible_locations_count": len(all_points),
            "user_preference": request.user_preference,
            "weight": request.weight,
            "excluded_ids": sorted(excluded_ids),
            "itinerary": itinerary,
            # Khoá cũ để app.js hiện tại render được ngay không cần sửa.
            "routes": [itinerary],
        }

    # ============================================================
    # MODE "multi": chọn 3–5 lộ trình KHÁC NHAU THỰC SỰ (mục 4)
    # Không còn sort theo (-số điểm, thời gian) rồi cắt [:5] — cách đó luôn trả
    # về 5 biến thể của cùng một lộ trình dài nhất.
    # ============================================================
    selected = select_diverse_routes(generated_routes, available_minutes, request.num_routes)
    routes = _ensure_unique_names([format_route_object(r, i + 1) for i, r in enumerate(selected)])

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "vehicle_note": vehicle_note,
        "accessible_locations_count": len(all_points),
        "user_preference": request.user_preference,
        "weight": request.weight,
        "excluded_ids": sorted(excluded_ids),
        "candidates_generated": len(generated_routes),
        "routes": routes,
    }


# ============================================================
# API LỚP NGOÀI — mỗi endpoint chỉ là lớp mỏng bọc quanh run_route_generation()
# và itinerary_store, để không có chỗ nào sinh lại route ngoài tầm kiểm soát.
# ============================================================

def _params_from_request(request: OptimizationRequest) -> dict:
    """Tham số Screen 1 cần nhớ trong phiên để các bước sau tính lại đúng bối cảnh."""
    return {
        "region": request.region,
        "start_time": request.start_time,
        "end_time": request.end_time,
        "trip_date": request.trip_date,
        "start_point": request.start_point,
        "start_lat": request.start_lat,
        "start_lon": request.start_lon,
        "vehicle_type": request.vehicle_type,
        "user_preference": request.user_preference,
        "weight": request.weight,
        "ai_selected_ids": request.ai_selected_ids,
    }


def _request_from_session(session, overrides: dict = None) -> OptimizationRequest:
    params = dict(session.params)
    params.update(overrides or {})
    params.pop("ai_selected_ids", None)
    return OptimizationRequest(**{k: v for k, v in params.items() if v is not None})


class RouteSelectRequest(BaseModel):
    session_id: str
    route_id: str


class ItineraryUpdateRequest(BaseModel):
    session_id: str
    remove_ids: Optional[List[str]] = None    # Loại vĩnh viễn trong phiên (mục 6)
    restore_ids: Optional[List[str]] = None   # Bỏ loại trừ khi người dùng đổi ý
    add_ids: Optional[List[str]] = None       # Ghim thêm điểm (must-visit)


def _ensure_unique_names(routes: list) -> list:
    """
    Đảm bảo 3–5 lộ trình ở Screen 2 có tên độc nhất, phản ánh đúng trải nghiệm,
    tránh hoàn toàn việc trùng tên hay dùng tên đánh số khô khan.
    """
    seen = set()
    for idx, r in enumerate(routes, 1):
        name = r.get("name") or r.get("route_name") or f"Hành trình trải nghiệm {idx}"
        theme = r.get("theme", "diverse")
        options = THEME_EXPERIENCE_NAMES.get(theme, [])

        if name in seen:
            found_alt = False
            for opt in options:
                if opt not in seen:
                    name = opt
                    found_alt = True
                    break
            if not found_alt:
                suffixes = ["(Góc nhìn mới)", "(Nhịp điệu sâu lắng)", "(Cung đường mở rộng)", "(Phiên bản thong thả)"]
                for suf in suffixes:
                    cand = f"{name} {suf}"
                    if cand not in seen:
                        name = cand
                        found_alt = True
                        break
            if not found_alt:
                n = 2
                while f"{name} #{n}" in seen:
                    n += 1
                name = f"{name} #{n}"

        r["name"] = name
        r["route_name"] = name
        seen.add(name)
    return routes


@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    """
    ENDPOINT CŨ — GIỮ NGUYÊN HÀNH VI cho app.js hiện tại (mục 10: "tận dụng API
    hiện tại nếu có", mục 13: không sửa frontend ngoài phạm vi cần thiết).

    Khác biệt duy nhất: nếu request có session_id thì nó tôn trọng excluded_ids
    của phiên — nghĩa là luồng chatbot "Bỏ A rồi thêm quán ăn trưa" của frontend
    hiện tại cũng được sửa lỗi mà KHÔNG cần đổi một dòng nào ở app.js.
    """
    session = store.get_or_create(request.session_id, _params_from_request(request))
    session.exclude(request.excluded_ids or [])

    result = run_route_generation(
        request,
        excluded_ids=session.excluded_ids,
        must_visit_ids=session.pinned_ids,
        mode="multi",
    )
    result["session_id"] = session.session_id
    if result.get("status") == "success":
        session.set_routes(result["routes"])
        # Route đầu tiên là cái Screen 3 hiển thị mặc định → cũng là itinerary hiện tại.
        session.select_route(result["routes"][0]["route_id"])
    return result


@app.post("/api/routes")
async def create_routes(request: OptimizationRequest):
    """
    SCREEN 1 → BACKEND → 3–5 ROUTE HOÀN CHỈNH (mục 2).

    Trả về route ĐÃ HOÀN CHỈNH (places + timeline + tổng thời gian) để Screen 2
    render thẳng, không còn cảnh Screen 2 nhận một rổ địa điểm rồi Screen 3 mới
    tự tách route.
    """
    session = store.get_or_create(request.session_id, _params_from_request(request))
    session.exclude(request.excluded_ids or [])

    result = run_route_generation(
        request,
        excluded_ids=session.excluded_ids,
        must_visit_ids=session.pinned_ids,
        mode="multi",
    )
    if result.get("status") != "success":
        result["session_id"] = session.session_id
        return result

    session.set_routes(result["routes"])
    result["session_id"] = session.session_id
    return result


@app.get("/api/routes")
async def list_routes(session_id: str):
    """Lấy lại đúng tập route đã sinh. KHÔNG sinh lại (mục 10)."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    return {
        "status": "success",
        "session_id": session.session_id,
        "routes": session.list_routes(),
        "selected_route_id": session.selected_route_id,
        "excluded_ids": sorted(session.excluded_ids),
    }


@app.post("/api/routes/select")
async def select_route(req: RouteSelectRequest):
    """
    SCREEN 2 CHỌN ROUTE → SCREEN 3 (mục 10).

    Chọn route_3 thì trả về ĐÚNG route_3 đã lưu — không chạy lại thuật toán,
    nên Screen 3 không bao giờ hiển thị một lộ trình khác với cái người dùng
    vừa nhìn thấy ở Screen 2.
    """
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    route = session.select_route(req.route_id)
    if route is None:
        raise HTTPException(
            status_code=404,
            detail=f"Không tìm thấy {req.route_id} trong phiên này. Các route hiện có: {list(session.route_order)}",
        )
    return {
        "status": "success",
        "session_id": session.session_id,
        "route_id": route["route_id"],
        "itinerary": route,
        "excluded_ids": sorted(session.excluded_ids),
        "routes": [route],
    }


@app.get("/api/itinerary")
async def get_itinerary(session_id: str):
    """
    SOURCE OF TRUTH (mục 11): map data, timeline, route summary, AI context đều
    lấy từ đây nên không thể xảy ra cảnh Map = route A còn Timeline = route B.
    """
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    itinerary = session.current_itinerary
    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "itinerary": itinerary,
        "map_data": [
            {
                "id": p["id"], "ten": p["ten"], "lat": p["lat"], "lon": p["lon"],
                "loai_hinh": p.get("loai_hinh", ""), "arrive_time": p.get("arrive_time"),
                "depart_time": p.get("depart_time"),
            }
            for p in (itinerary or {}).get("places", [])
        ],
        "timeline": (itinerary or {}).get("timeline", []),
        "summary": {
            "name": (itinerary or {}).get("name"),
            "theme": (itinerary or {}).get("theme"),
            "total_duration": (itinerary or {}).get("total_duration"),
            "travel_time": (itinerary or {}).get("travel_time"),
            "visit_time": (itinerary or {}).get("visit_time"),
            "place_count": (itinerary or {}).get("place_count"),
        },
        "ai_context_ids": session.current_place_ids(),
        "excluded_ids": sorted(session.excluded_ids),
        "pinned_ids": sorted(session.pinned_ids),
    }


@app.post("/api/itinerary/update")
async def update_itinerary(req: ItineraryUpdateRequest):
    """
    XOÁ ĐIỂM / THÊM ĐIỂM RỒI TÍNH LẠI (mục 8, 9).

    A → B → C → D → E, xoá C thì kết quả là A → B → D → E (hoặc A → B → X → D → E
    nếu Greedy fill-up được điểm mới) — nhưng C không bao giờ quay lại, kể cả ở
    những lần cập nhật sau như "thêm quán ăn trưa", vì C nằm trong excluded_ids
    của phiên chứ không phải chỉ bị bỏ khỏi một mảng tạm.
    """
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")

    session.restore(req.restore_ids or [])
    newly_excluded = session.exclude(req.remove_ids or [])

    # Giữ lại các điểm đang có trong itinerary (trừ điểm vừa xoá) làm must-visit.
    forced = {str(i) for i in (req.add_ids or [])} - session.excluded_ids
    keep = set(session.current_place_ids()) - session.excluded_ids
    keep |= forced
    session.pinned_ids = keep

    opt_request = _request_from_session(session)
    result = run_route_generation(
        opt_request,
        excluded_ids=session.excluded_ids,
        must_visit_ids=keep,
        force_visit_ids=forced,
        mode="single",
    )
    if result.get("status") != "success":
        result["session_id"] = session.session_id
        result["excluded_ids"] = sorted(session.excluded_ids)
        return result

    session.set_current_itinerary(result["itinerary"])
    result.update({
        "session_id": session.session_id,
        "removed_ids": newly_excluded,
        "excluded_ids": sorted(session.excluded_ids),
        "selected_route_id": session.selected_route_id,
    })
    return result


@app.get("/api/session")
async def get_session_state(session_id: str):
    """Trạng thái thô của phiên — hữu ích cho debug và cho test tự động."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    return {"status": "success", **session.snapshot()}


@app.post("/api/admin/import-excel")
async def import_excel_tool(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        # 1. Hỗ trợ đọc file CSV (từ tool cào) và Excel
        if file.filename.endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contents), encoding='utf-8-sig').replace({np.nan: None})
        else:
            df = pd.read_excel(io.BytesIO(contents)).replace({np.nan: None})
        
        # 2. Kết nối tự động vào đúng SQL Server
        conn, db_type = get_db_connection()
        cursor = conn.cursor()
        
        updated_count = 0
        for index, row in df.iterrows():
            # Lấy tên địa điểm (Hỗ trợ cả header 'ten_dia_diem' hoặc 'ten')
            ten = str(row.get('ten_dia_diem', row.get('ten', ''))).strip()
            if not ten or ten == 'None':
                continue
            
            # Chỉ lấy link ảnh đầu tiên nếu có nhiều ảnh
            anh_raw = row.get('anh', row.get('url_hinh_anh'))
            url_hinh_anh = str(anh_raw).split(" ||| ")[0] if anh_raw and str(anh_raw) not in ['None', 'nan', ''] else None
            
            diem_gia_tri = row.get('rating', row.get('diem_gia_tri'))
            diem_gia_tri = diem_gia_tri if not pd.isna(diem_gia_tri) and str(diem_gia_tri) != 'None' else None

            # Cập nhật vào DB
            cursor.execute("SELECT id FROM DIA_DIEM WHERE ten = ?", (ten,))
            if cursor.fetchone() and url_hinh_anh:
                cursor.execute("""
                    UPDATE DIA_DIEM 
                    SET url_hinh_anh=?, diem_gia_tri=ISNULL(?, diem_gia_tri)
                    WHERE ten=?
                """, (url_hinh_anh, diem_gia_tri, ten))
                updated_count += 1
                
        conn.commit()
        conn.close()
        return {"status": "success", "message": f"Hoàn tất! Đã cập nhật ảnh và rating cho {updated_count} địa điểm."}
    except Exception as e:
        return {"status": "error", "message": f"Lỗi đọc file: {str(e)}"}

# Nhúng API của AI Advisor vào hệ thống chính   
from ai_advisor import router as ai_router
app.include_router(ai_router)