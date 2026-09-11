import os
import re
import unicodedata
import requests
import pyodbc
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

SERVER   = os.getenv("DB_SERVER",   r'LAPTOP-EV7C4EMM')
DATABASE = os.getenv("DB_NAME",     'DuLichThongMinh')
SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dulich.db")

def get_db_connection():
    """
    Thử kết nối SQL Server trước.
    Nếu thất bại (máy bạn bè chưa cài SQL Server), tự động dùng SQLite dulich.db có sẵn trong Git.
    """
    try:
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
    ai_selected_ids: Optional[List[str]] = None  # ID điểm đến do AI (Ollama) đã chọn sẵn từ /api/ai-suggest.
                                                   # Nếu có, Backend BỎ QUA toàn bộ bước tự sinh candidate
                                                   # bằng scorer/greedy, chỉ lo sắp xếp lại thứ tự tối ưu
                                                   # và kiểm tra giờ giấc cho đúng tập điểm AI đã đề xuất.

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
        cursor.execute("SELECT id, ten, vi_do, kinh_do, loai_hinh FROM DIA_DIEM")
        rows = fetch_all_dict(cursor, db_type)
        locations = [{"id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"], "loai_hinh": r["loai_hinh"]} for r in rows]
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
_CATEGORY_ROUTE_NAME = {
    "Cafe": "Hơi thở thiên nhiên & Sống chậm",
    "Tham quan": "Không gian hoài niệm & Khám phá",
    "Checkin": "Khám phá góc phố & Check-in",
    "TTTM": "Mua sắm & Giải trí trọn vẹn",
    "Ăn uống": "Hành trình ẩm thực",
}

def generate_route_name(route_points: list, route_index: int) -> str:
    """
    Sinh tên lộ trình dựa trên đặc điểm thực tế (loai_hinh) của các điểm
    trong route đó. Nếu một loại hình chiếm >=60% số điểm, dùng tên chủ đề
    tương ứng. Nếu route có từ 3 loại hình khác nhau trở lên, đặt tên phản
    ánh sự đa dạng. Nếu không đủ dữ liệu để đặt tên có ý nghĩa, dùng tên
    an toàn "Lộ trình {index}" thay vì đoán bừa.
    """
    if not route_points:
        return f"Lộ trình {route_index}"

    counts = {}
    for p in route_points:
        lh = p.get("loai_hinh") or "Khác"
        counts[lh] = counts.get(lh, 0) + 1

    total = len(route_points)
    dominant, dcount = max(counts.items(), key=lambda kv: kv[1])

    if dcount / total >= 0.6 and dominant in _CATEGORY_ROUTE_NAME:
        return _CATEGORY_ROUTE_NAME[dominant]
    if len(counts) >= 3:
        return "Trải nghiệm trọn vẹn nhịp sống đô thị"
    return f"Lộ trình {route_index}"


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


@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
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

    def finalize_route(selected_points, k_weather, route_label, route_index):
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

        return {
            "dropped_point": dropped_point,
            "total_time_minutes": round(total_actual, 1),
            "vehicle_type": request.vehicle_type,
            "trip_date": str(base_date),
            "strategy": route_label,
            "route_name": generate_route_name(route_pts, route_index),
            "avg_preference_score": round(avg_preference, 3),
            "optimized_route": final_route_details
        }

    def build_route_greedy_custom(origin, candidate_pool, k_weather, end_clock, score_fn):
        """
        Greedy builder với scorer tùy chỉnh.
        score_fn(last_point, candidate, dist_km) -> float  (nhỏ hơn = ưu tiên hơn)
        end_clock: thời điểm kết thúc tối đa (clock_end hoặc fake ngắn hơn).

        travel_time dùng get_travel_minutes() (tính động theo thời điểm khởi hành
        thực tế của từng chặng — BUG 5 fix). visit_time KHÔNG nhân hệ số traffic
        (BUG 2 fix).
        """
        origin_open  = datetime.combine(base_date, origin["open_time"])
        origin_close = datetime.combine(base_date, origin["close_time"])
        current_clock = max(clock_start, origin_open)
        departure = current_clock + timedelta(minutes=(origin.get("time") or 0))
        if departure > origin_close or departure > end_clock:
            return None
        selected  = [origin]
        unvisited = list(candidate_pool)
        while True:
            best_next  = None
            best_score = float('inf')
            best_dep   = departure
            for p in unvisited:
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
                    if sc < best_score:
                        best_score = sc
                        best_next  = p
                        best_dep   = nd
            if best_next:
                selected.append(best_next)
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
        generated         = []
        seen_fingerprints = set()

        def try_add(pts, k_weather, label):
            if not pts or len(pts) < 2:
                return
            route_index = len(generated) + 1
            r = finalize_route(pts, k_weather, label, route_index)
            if not r:
                return
            fp = frozenset(p["id"] for p in r["optimized_route"])
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                r["route_id"] = len(generated) + 1
                generated.append(r)

        w_exp = request.weight / 100.0
        w_time = 1.0 - w_exp

        scorers = [
            (lambda last, p, travel: (travel * w_time) - ((p.get("score",0) + p.get("pref_match",0)*2) * 5 * w_exp), "⚖️ Cân bằng"),
            (lambda last, p, travel: (travel * w_time) + (150 if p.get("loai_hinh") == last.get("loai_hinh") else 0) - ((p.get("score",0) + p.get("pref_match",0)*2) * 5 * w_exp), "🌈 Đa dạng"),
            (lambda last, p, travel: travel - (p.get("pref_match",0) * 15 * w_exp), "🎯 Đúng gu trải nghiệm")
        ]

        for origin in starting_points:
            k_weather      = get_weather_factor(origin["lat"], origin["lon"])
            base_unvisited = [p for p in all_points if p["id"] != origin["id"]]

            pool_near  = sorted(base_unvisited, key=lambda p: calc_dist(origin, p))
            pool_value = sorted(base_unvisited, key=lambda p: -(p.get("score") or 0))

            # ── Scorers với hành vi thực sự khác nhau ──
            # BUG 1 FIX: "Theo sở thích" và "Cân bằng" giờ thực sự dùng preference_score
            # (từ user_preference) trộn với distance theo đúng tỉ lệ pref_weight (từ weight).
            # "Gần nhất" / "Nổi bật" vẫn giữ nguyên thuần khoảng cách / điểm đánh giá — đây
            # là lựa chọn có chủ đích (người dùng có thể muốn xem route thuần "gần nhất"),
            # không phải lỗi bỏ sót preference.
            scorers = [
                (lambda last, p, d: d,                                                         "🏃 Gần nhất"),
                (lambda last, p, d: -(p.get("score") or 0),                                   "⭐ Nổi bật"),
                (lambda last, p, d: d * (1 - pref_weight) + _preference_penalty(p) * pref_weight, "🧭 Theo sở thích của bạn"),
                (lambda last, p, d: (d / ((p.get("score") or 1) + 1)) * (1 - pref_weight) + _preference_penalty(p) * pref_weight, "⚖️ Cân bằng"),
                (lambda last, p, d: d + (150 if p.get("loai_hinh") == last.get("loai_hinh") else 0), "🌈 Đa dạng"),
                (lambda last, p, d: -(p.get("score") or 0) + (80 if p.get("loai_hinh") == last.get("loai_hinh") else 0), "🎯 Giá trị & đa dạng"),
            ]

            # ── Ngân sách thời gian: tạo lộ trình theo nhiều độ dài ──
            # Tính số mốc thời gian dựa trên quỹ thực tế
            min_budget = 60  # ít nhất 1 giờ
            budget_ratios = [0.25, 0.35, 0.5, 0.65, 0.8, 1.0]
            time_budgets = []
            for ratio in budget_ratios:
                mins = available_minutes * ratio
                if mins >= min_budget:
                    end_t = clock_start + timedelta(minutes=mins)
                    hrs   = int(mins // 60)
                    mns   = int(mins % 60)
                    label_t = f"{hrs}h{mns:02d}" if hrs > 0 else f"{int(mins)}ph"
                    time_budgets.append((end_t, label_t, ratio))

            # ── MA TRẬN: Mỗi ngân sách × Mỗi scorer ──
            for end_t, time_label, ratio in time_budgets:
                for score_fn, strat_name in scorers:
                    label = f"{strat_name} · {time_label}"
                    try_add(
                        build_route_greedy_custom(origin, base_unvisited, k_weather, end_t, score_fn),
                        k_weather, label
                    )

                # Pool giới hạn chỉ áp dụng cho ngân sách ≥ 50%
                if ratio >= 0.5:
                    near_n = max(8, len(base_unvisited) // 3)
                    val_n  = max(8, len(base_unvisited) // 3)
                    try_add(build_route_greedy_custom(origin, pool_value[:val_n], k_weather, end_t,
                                                       lambda last, p, d: d), k_weather, f"🏆 Top điểm · {time_label}")
                    try_add(build_route_greedy_custom(origin, pool_near[:near_n], k_weather, end_t,
                                                       lambda last, p, d: -(p.get("score") or 0)), k_weather, f"🗺️ Lân cận · {time_label}")

            # ── Loại trừ điểm top → lộ trình thực sự khác biệt ──
            for skip in range(min(5, len(pool_value))):
                pool_excl = [p for p in base_unvisited if p["id"] != pool_value[skip]["id"]]
                try_add(
                    build_route_greedy_custom(origin, pool_excl, k_weather, clock_end,
                                              lambda last, p, d: d / ((p.get("score") or 1) + 1)),
                    k_weather, f"🔀 Thay thế #{skip+1}"
                )

            # ── Random sample nhiều seed ──
            for seed in [7, 13, 42, 77, 99, 111, 123, 200]:
                rng    = random.Random(seed)
                # Thay đổi kích thước sample theo seed để đa dạng hơn
                n      = max(6, int(len(base_unvisited) * (0.3 + (seed % 5) * 0.1)))
                n      = min(n, len(base_unvisited))
                sample = rng.sample(base_unvisited, n)
                try_add(
                    build_route_greedy_custom(origin, sample, k_weather, clock_end,
                                              lambda last, p, d: d / ((p.get("score") or 1) + 1)),
                    k_weather, f"🎲 Khám phá #{seed}"
                )

        return generated



    # ============================================================
    # NHÁNH AI-SELECTED (Task 2.3): nếu Frontend đã gọi /api/ai-suggest trước và
    # truyền ai_selected_ids lên đây, BỎ QUA toàn bộ bước tự sinh candidate bằng
    # scorer/greedy — chỉ còn nhiệm vụ sắp xếp lại thứ tự (2-opt/DP) + kiểm tra
    # giờ giấc cho đúng tập điểm AI đã chọn. Đây chính là yêu cầu "đôi chân thuật
    # toán vẫn phải sắp xếp lại để đi không bị vòng vèo, tính đúng giờ kẹt xe".
    # ============================================================
    if request.ai_selected_ids:
        id_to_point = {p["id"]: p for p in all_points}
        ai_points = [id_to_point[i] for i in request.ai_selected_ids if i in id_to_point]
        invalid_ids = [i for i in request.ai_selected_ids if i not in id_to_point]
        if invalid_ids:
            # Không âm thầm bỏ qua — để Frontend/log biết AI đã đề xuất ID không hợp lệ
            # (đã lọc ở /api/ai-suggest rồi, nhưng phòng trường hợp gọi thẳng endpoint này).
            pass
        if not ai_points:
            return {"status": "error", "message": "Không có ID điểm đến hợp lệ nào trong ai_selected_ids."}

        origin = starting_points[0]
        selected_points = [origin] + [p for p in ai_points if p["id"] != origin["id"]]
        k_weather = get_weather_factor(origin["lat"], origin["lon"])
        result = finalize_route(selected_points, k_weather, "🤖 AI đề xuất", 1)
        if not result:
            return {"status": "error", "message": "AI đã chọn điểm nhưng không sắp xếp được lịch trình hợp lệ trong khung giờ đã cho (có thể do giờ đóng cửa hoặc quỹ thời gian quá ngắn)."}
        result["route_id"] = 1
        return {
            "status": "success",
            "available_minutes": available_minutes,
            "trip_date": str(base_date),
            "vehicle_type": request.vehicle_type,
            "vehicle_note": vehicle_note,
            "accessible_locations_count": len(all_points),
            "invalid_ai_ids": invalid_ids,
            "routes": [result]
        }

    # BUG 5 FIX (tiếp): trước đây hệ thống phải "thử lại" toàn bộ route generation
    # với k_density=1.0 nếu không sinh được route nào, vì hệ số congestion tĩnh có
    # thể chặn nhầm toàn bộ route hợp lệ. Giờ congestion được tính ĐỘNG theo từng
    # chặng/thời điểm thực tế nên không còn cần cơ chế thử-lại "giả định không tắc
    # đường" này nữa — chỉ cần sinh route một lần với dữ liệu thời gian thực.
    generated_routes = generate_routes_with_factor()

    if len(generated_routes) == 0:
        return {"status": "error", "message": "Quỹ thời gian quá ngắn hoặc các địa điểm đều chưa mở cửa vào khung giờ này!"}

    # Sắp xếp lộ trình: nhiều điểm tham quan hơn trước, rồi thời gian ngắn hơn, rồi
    # (tie-breaker) route phù hợp sở thích người dùng hơn được ưu tiên hiển thị trước.
    generated_routes.sort(key=lambda r: (-len(r["optimized_route"]), r["total_time_minutes"], -r.get("avg_preference_score", 0.5)))
    for i, r in enumerate(generated_routes):
        r["route_id"] = i + 1
        # Tên fallback "Lộ trình N" phải khớp route_id cuối cùng sau khi sắp xếp lại.
        if r.get("route_name", "").startswith("Lộ trình "):
            r["route_name"] = f"Lộ trình {i + 1}"

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "vehicle_note": vehicle_note,
        "accessible_locations_count": len(all_points),
        "user_preference": request.user_preference,
        "weight": request.weight,
        "routes": generated_routes
    }

@app.post("/api/admin/import-excel")
async def import_excel_tool(file: UploadFile = File(...)):
    try:
        # Đọc dữ liệu từ file upload
        contents = await file.read()
        df = pd.read_excel(io.BytesIO(contents)).replace({np.nan: None})
        
        # Kết nối SQLite (dulich.db)
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dulich.db")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        inserted_count = 0
        updated_count = 0
        
        for index, row in df.iterrows():
            ten = str(row.get('ten', '')).strip()
            if not ten or ten == 'None':
                continue
                
            vi_do = row.get('vi_do')
            kinh_do = row.get('kinh_do')
            loai_hinh = row.get('loai_hinh')
            diem_gia_tri = row.get('diem_gia_tri')
            thoi_gian_tham_quan_phut = row.get('thoi_gian_tham_quan_phut')
            url_hinh_anh = row.get('url_hinh_anh')
            
            # Kiểm tra địa điểm đã có chưa
            cursor.execute("SELECT id FROM DIA_DIEM WHERE ten = ?", (ten,))
            existing = cursor.fetchone()
            
            if existing:
                # Nếu có -> UPDATE
                cursor.execute("""
                    UPDATE DIA_DIEM 
                    SET vi_do=?, kinh_do=?, loai_hinh=?, diem_gia_tri=?, thoi_gian_tham_quan_phut=?, url_hinh_anh=?
                    WHERE ten=?
                """, (vi_do, kinh_do, loai_hinh, diem_gia_tri, thoi_gian_tham_quan_phut, url_hinh_anh, ten))
                updated_count += 1
            else:
                # Nếu chưa có -> INSERT
                cursor.execute("""
                    INSERT INTO DIA_DIEM (ten, vi_do, kinh_do, loai_hinh, diem_gia_tri, thoi_gian_tham_quan_phut, url_hinh_anh, cap_do_tiep_can)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 3)
                """, (ten, vi_do, kinh_do, loai_hinh, diem_gia_tri, thoi_gian_tham_quan_phut, url_hinh_anh))
                inserted_count += 1
                
        conn.commit()
        conn.close()
        
        return {
            "status": "success", 
            "message": f"Hoàn tất! Đã thêm mới {inserted_count} và cập nhật {updated_count} địa điểm."
        }
    except Exception as e:
        return {"status": "error", "message": f"Lỗi đọc file: {str(e)}"}