import os
import requests
import pyodbc
import math
import random
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, time
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
import sqlite3

app = FastAPI(title="Routing Optimization API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

SERVER   = os.getenv("DB_SERVER",   r'LAPTOP-EV7C4EMM')
DATABASE = os.getenv("DB_NAME",     'DuLichThongMinh')
SQLITE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dulich.db")

def get_db_connection():
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

# FIX BUG 1: Thêm user_preference và weight vào model
class OptimizationRequest(BaseModel):
    region: str
    start_time: str
    end_time: str
    trip_date: str = ""
    start_point: str = ""
    vehicle_type: str = "xe_may"
    start_lat: Optional[float] = None
    start_lon: Optional[float] = None
    user_preference: str = ""
    weight: int = 50

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
        raise HTTPException(status_code=500, detail=str(e))

def get_weather_factor(lat: float, lon: float) -> float:
    try:
        res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
        if res.get("current_weather", {}).get("weathercode", 0) >= 51:
            return 1.25
        return 1.0
    except Exception:
        return 1.0

# FIX BUG 2 & 5: Hàm được sửa để nhận đầu vào là giờ của từng chặng, KHÔNG PHẢI chỉ gọi 1 lần
def get_density_factor(t: time) -> float:
    morning_start = time(7, 0)
    morning_end   = time(9, 0)
    evening_start = time(17, 0)
    evening_end   = time(19, 0)
    if (morning_start <= t <= morning_end) or (evening_start <= t <= evening_end):
        return 1.8
    return 1.0

LARGE_VEHICLE_TYPES = {"xe_16_cho", "xe_29_cho", "xe_45_cho"}

VEHICLE_ACCESS_LEVEL = {
    "xe_45_cho": 1, "xe_29_cho": 1, "xe_16_cho": 1,
    "o_to": 2, "xe_may": 3, "xe_dap": 3, "di_bo": 3,
}

VEHICLE_ACCESS_NOTE = {
    "xe_45_cho": "Xe 45 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_29_cho": "Xe 29 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_16_cho": "Xe 16 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe rộng.",
    "o_to":      "Ô tô: đã loại các địa điểm trong hẻm nhỏ không có chỗ đậu xe.",
}

def get_vehicle_osrm_profile(vehicle_type: str):
    profile_map = {
        "o_to": "driving", "xe_may": "driving",
        "xe_16_cho": "driving", "xe_29_cho": "driving", "xe_45_cho": "driving",
        "xe_dap": "cycling", "di_bo": "foot",
    }
    return profile_map.get(vehicle_type, "driving")

def get_large_vehicle_restriction_factor(vehicle_type: str, t: time) -> float:
    if vehicle_type not in LARGE_VEHICLE_TYPES:
        return 1.0
    morning_start, morning_end = time(6, 0), time(9, 0)
    evening_start, evening_end = time(16, 0), time(20, 0)
    if (morning_start <= t <= morning_end) or (evening_start <= t <= evening_end):
        if vehicle_type == "xe_16_cho": return 1.5
        if vehicle_type == "xe_29_cho": return 1.8
        if vehicle_type == "xe_45_cho": return 2.2
    return 1.0

def get_global_osrm_matrix(points_list, vehicle_type: str = "xe_may"):
    osrm_profile = get_vehicle_osrm_profile(vehicle_type)
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
                    "duration": (durations[i][j] / 60.0), # Lấy base duration gốc (chưa tính kẹt xe)
                    "distance": distances[i][j] / 1000.0
                }
        return matrix_dict
    except Exception:
        for p1 in points_list:
            matrix_dict[p1["id"]] = {}
            for p2 in points_list:
                matrix_dict[p1["id"]][p2["id"]] = {
                    "duration": 15.0 if p1["id"] != p2["id"] else 0.0,
                    "distance": 5.0 if p1["id"] != p2["id"] else 0.0
                }
        return matrix_dict

# Logic Matching Text Hỗ trợ Sở thích (BUG 1)
def normalize_text(text: str) -> str:
    if not text: return ""
    text = unicodedata.normalize('NFD', text).encode('ascii', 'ignore').decode('utf-8')
    return text.lower().strip()

def calculate_preference_score(point: dict, user_pref: str) -> float:
    if not user_pref: return 0.0
    pref_norm = normalize_text(user_pref)
    keywords = [k.strip() for k in pref_norm.split(",") if k.strip()]
    if not keywords: keywords = pref_norm.split()
    
    content = f"{point.get('mo_ta','')} {point.get('thong_tin_chi_tiet','')} {point.get('review','')} {point.get('phu_hop','')}"
    content_norm = normalize_text(content)
    
    matches = sum(1 for kw in keywords if kw in content_norm)
    return - (matches * 20.0)  # Trả về điểm âm để ưu tiên trong hàm tính cost

# Logic định danh lộ trình dựa vào điểm (BUG 3)
def generate_route_name(route_points):
    categories = [p.get("loai_hinh", "") for p in route_points if p.get("loai_hinh") and p.get("loai_hinh") != "diem_xuat_phat"]
    if not categories: return "Lộ trình Khám phá"
    counts = Counter(categories)
    top = counts.most_common(2)
    if len(top) == 1:
        return f"Hành trình {top[0][0]}"
    else:
        return f"Kết hợp {top[0][0]} & {top[1][0]}"

# Tái cấu trúc bộ Time Engine (BUG 5, 6, 8, 9 + FIX THỜI GIAN CHỜ)
def calculate_cost_with_clock(route_indices, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date, max_wait_tolerance):
    current_clock = clock_start_dt
    penalty = 0
    violation_index = -1
    
    for i in range(len(route_indices)):
        idx = route_indices[i]
        p = points_data[idx]
        p_open = datetime.combine(base_date, p["open_time"])
        p_close = datetime.combine(base_date, p["close_time"])
        
        if i > 0:
            prev_idx = route_indices[i-1]
            prev_p = points_data[prev_idx]
            
            # Diversity là soft constraint
            if p.get("loai_hinh") == prev_p.get("loai_hinh") and p.get("loai_hinh") != "diem_xuat_phat":
                penalty += 10
            
            base_travel_mins = matrix_dict[prev_p["id"]][p["id"]]["duration"]
            
            k_density = get_density_factor(current_clock.time())
            k_restriction = get_large_vehicle_restriction_factor(vehicle_type, current_clock.time())
            
            travel_mins = base_travel_mins * k_weather * k_density * k_restriction
            current_clock += timedelta(minutes=travel_mins)
            
        wait_mins = 0
        if current_clock < p_open:
            wait_mins = (p_open - current_clock).total_seconds() / 60
            current_clock = p_open
            
        # LOGIC MỚI: Xử lý phạt thời gian chờ
        if i > 0: # Bỏ qua điểm xuất phát đầu tiên (origin)
            if wait_mins > max_wait_tolerance:
                penalty += 10000 # Vi phạm Hard Constraint: Chờ quá lâu so với chiến lược
                if violation_index == -1:
                    violation_index = i
            else:
                # Soft penalty: Càng chờ lâu càng bị cộng điểm xấu, ép thuật toán tìm đường mượt hơn
                penalty += wait_mins * 2 
                
        visit_mins = p["time"] 
        departure = current_clock + timedelta(minutes=visit_mins)
        
        # Hard Constraint đóng cửa
        if departure > p_close or departure > clock_end_dt:
            penalty += 10000 
            if violation_index == -1: 
                violation_index = i
                
        current_clock = departure
        
    total_minutes = (current_clock - clock_start_dt).total_seconds() / 60
    return total_minutes, penalty, violation_index

def two_opt_algorithm(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date, max_wait_tolerance):
    best_route = route
    b_time, b_pen, b_vio = calculate_cost_with_clock(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date, max_wait_tolerance)
    best_cost = b_time + b_pen
    
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best_route) - 1):
            for j in range(i + 1, len(best_route) + 1):
                if j - i <= 1: continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                t_time, t_pen, t_vio = calculate_cost_with_clock(new_route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date, max_wait_tolerance)
                new_cost = t_time + t_pen
                if new_cost < best_cost:
                    best_route, best_cost = new_route, new_cost
                    b_time, b_pen, b_vio = t_time, t_pen, t_vio
                    improved = True
    return best_route, b_time, b_pen, b_vio

@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    try:
        if request.trip_date: base_date = datetime.strptime(request.trip_date, "%Y-%m-%d").date()
        else: base_date = datetime.today().date()
    except Exception:
        base_date = datetime.today().date()

    try:
        clock_start = datetime.strptime(request.start_time, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day)
        clock_end = datetime.strptime(request.end_time, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day)
        if clock_end <= clock_start:
            clock_end += timedelta(days=1)
        available_minutes = (clock_end - clock_start).total_seconds() / 60
    except Exception:
        raise HTTPException(status_code=400, detail="Lỗi định dạng thời gian.")

    conn, db_type = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
               c.gio_mo_cua, c.gio_dong_cua,
               d.mo_ta, d.thong_tin_chi_tiet, d.review, d.phu_hop,
               COALESCE(d.cap_do_tiep_can, 3) AS cap_do_tiep_can
        FROM DIA_DIEM d
        LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
    """
    cursor.execute(query)
    rows = fetch_all_dict(cursor, db_type)
    conn.close()
    
    all_points = []
    default_open = time(0, 0)
    default_close = time(23, 59)

    for r in rows:
        open_time = r["gio_mo_cua"] if r["gio_mo_cua"] else default_open
        close_time = r["gio_dong_cua"] if r["gio_dong_cua"] else default_close
        if isinstance(open_time, str): open_time = datetime.strptime(open_time[:5], "%H:%M").time()
        if isinstance(close_time, str): close_time = datetime.strptime(close_time[:5], "%H:%M").time()

        all_points.append({
            "id": str(r["id"]), "ten": r["ten"], "lat": r["vi_do"], "lon": r["kinh_do"], 
            "time": r["thoi_gian_tham_quan_phut"], "score": r["diem_gia_tri"], "loai_hinh": r["loai_hinh"],
            "open_time": open_time, "close_time": close_time,
            "mo_ta": r["mo_ta"] or "", "thong_tin_chi_tiet": r["thong_tin_chi_tiet"] or "",
            "review": r["review"] or "", "phu_hop": r["phu_hop"] or "",
            "cap_do_tiep_can": r["cap_do_tiep_can"] if r["cap_do_tiep_can"] is not None else 3,
        })

    max_access = VEHICLE_ACCESS_LEVEL.get(request.vehicle_type, 3)
    all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]
    vehicle_note = VEHICLE_ACCESS_NOTE.get(request.vehicle_type, "")

    all_points.sort(key=lambda x: x["score"] if x["score"] else 0, reverse=True)

    if request.start_lat is not None and request.start_lon is not None:
        gps_point = {
            "id": "gps_current", "ten": "📍 Vị trí của bạn", "lat": request.start_lat, "lon": request.start_lon,
            "time": 0, "loai_hinh": "diem_xuat_phat", "open_time": default_open, "close_time": default_close,
            "mo_ta": "", "thong_tin_chi_tiet": "", "review": "", "phu_hop": ""
        }
        all_points.insert(0, gps_point)
        starting_points = [gps_point]
    elif request.start_point and request.start_point.strip():
        keyword = request.start_point.strip().lower()
        matched_points = [p for p in all_points if keyword in p["ten"].lower()]
        if matched_points:
            starting_points = matched_points[:1]
        else:
            raise HTTPException(status_code=400, detail="Không tìm thấy địa điểm xuất phát trong cơ sở dữ liệu. Vui lòng nhập từ khóa khác hoặc dùng GPS.")
    else:
        starting_points = all_points[:3]

    global_matrix = get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)

    def calc_dist(p1, p2): return math.sqrt((p1["lat"] - p2["lat"])**2 + (p1["lon"] - p2["lon"])**2) * 111

    def finalize_route(selected_points, k_weather, route_label, max_wait_tolerance):
        if len(selected_points) < 2: return None

        initial_route = list(range(len(selected_points)))
        best_route_indices, b_time, b_pen, b_vio = two_opt_algorithm(
            initial_route, global_matrix, selected_points,
            k_weather, request.vehicle_type, clock_start, clock_end, base_date, max_wait_tolerance
        )

        dropped_point = False
        # Xóa chính xác index gây lố giờ / lố thời gian chờ
        while b_pen >= 10000 and len(best_route_indices) > 2:
            idx_to_remove = b_vio
            if idx_to_remove <= 0 or idx_to_remove >= len(best_route_indices):
                idx_to_remove = len(best_route_indices) - 1
            
            best_route_indices.pop(idx_to_remove)
            dropped_point = True
            
            best_route_indices, b_time, b_pen, b_vio = two_opt_algorithm(
                best_route_indices, global_matrix, selected_points,
                k_weather, request.vehicle_type, clock_start, clock_end, base_date, max_wait_tolerance
            )

        if len(best_route_indices) < 2 or b_pen >= 10000:
            return None

        final_route_details = []
        simulated_clock = clock_start

        for i in range(len(best_route_indices)):
            idx = best_route_indices[i]
            point = selected_points[idx]
            p_open = datetime.combine(base_date, point["open_time"])

            travel_time, travel_dist = 0, 0
            if i > 0:
                prev_point = selected_points[best_route_indices[i - 1]]
                b_dur = global_matrix[prev_point["id"]][point["id"]]["duration"]
                travel_dist = global_matrix[prev_point["id"]][point["id"]]["distance"]
                
                k_den = get_density_factor(simulated_clock.time())
                k_res = get_large_vehicle_restriction_factor(request.vehicle_type, simulated_clock.time())
                
                travel_time = b_dur * k_weather * k_den * k_res
                simulated_clock += timedelta(minutes=travel_time)

            wait_time = 0
            if simulated_clock < p_open:
                wait_time = (p_open - simulated_clock).total_seconds() / 60
                simulated_clock = p_open

            arrive_time_str = simulated_clock.strftime("%H:%M")
            visit_time = point["time"]
            simulated_clock += timedelta(minutes=visit_time)
            depart_time_str = simulated_clock.strftime("%H:%M")

            final_route_details.append({
                "id": point["id"], "ten": point["ten"],
                "lat": point["lat"], "lon": point["lon"],
                "loai_hinh": point.get("loai_hinh", ""),
                "mo_ta": point.get("mo_ta", ""),
                "thong_tin_chi_tiet": point.get("thong_tin_chi_tiet", ""),
                "review": point.get("review", ""),
                "phu_hop": point.get("phu_hop", ""),
                "visit_time": round(visit_time, 1),
                "wait_time": round(wait_time, 1),
                "arrive_time": arrive_time_str,
                "depart_time": depart_time_str,
                "travel_to_next": 0, "distance_to_next": 0
            })
            
            if i > 0:
                final_route_details[i-1]["travel_to_next"] = round(travel_time, 1)
                final_route_details[i-1]["distance_to_next"] = round(travel_dist, 1)

        total_actual = (simulated_clock - clock_start).total_seconds() / 60

        return {
            "dropped_point": dropped_point,
            "total_time_minutes": round(total_actual, 1),
            "vehicle_type": request.vehicle_type,
            "trip_date": str(base_date),
            "strategy": route_label,
            "route_name": generate_route_name(selected_points),
            "optimized_route": final_route_details
        }

    def build_route_greedy_custom(origin, candidate_pool, k_weather, end_clock, score_fn, max_wait_tolerance):
        origin_open  = datetime.combine(base_date, origin["open_time"])
        origin_close = datetime.combine(base_date, origin["close_time"])
        current_clock = max(clock_start, origin_open)
        departure = current_clock + timedelta(minutes=origin["time"])
        
        if departure > origin_close or departure > end_clock: return None
        selected, unvisited = [origin], list(candidate_pool)
        
        while True:
            best_next, best_score, best_dep = None, float('inf'), departure
            for p in unvisited:
                b_dist = global_matrix[selected[-1]["id"]].get(p["id"], {}).get("distance", 5.0)
                b_dur  = global_matrix[selected[-1]["id"]].get(p["id"], {}).get("duration", 15.0)
                
                k_den = get_density_factor(departure.time())
                k_res = get_large_vehicle_restriction_factor(request.vehicle_type, departure.time())
                
                est_travel = b_dur * k_weather * k_den * k_res
                arrival = departure + timedelta(minutes=est_travel)
                
                p_open = datetime.combine(base_date, p["open_time"])
                p_close = datetime.combine(base_date, p["close_time"])
                
                wait_mins = 0
                if arrival < p_open:
                    wait_mins = (p_open - arrival).total_seconds() / 60
                    
                # LOGIC MỚI: Nếu thời gian chờ vượt quá sức chịu đựng của chiến lược -> Bỏ qua điểm này ngay lập tức
                if wait_mins > max_wait_tolerance:
                    continue
                    
                sv = max(arrival, p_open)
                nd = sv + timedelta(minutes=p["time"])
                
                if nd <= p_close and nd <= end_clock:
                    sc = score_fn(selected[-1], p, b_dist)
                    
                    # Cộng thêm penalty chờ vào điểm số đánh giá để ưu tiên điểm mở cửa sẵn
                    sc += wait_mins * (2.0 if max_wait_tolerance <= 30 else 0.5) 
                    
                    if sc < best_score:
                        best_score, best_next, best_dep = sc, p, nd
                        
            if best_next:
                selected.append(best_next)
                unvisited.remove(best_next)
                departure = best_dep
            else:
                break
        return selected if len(selected) >= 2 else None

    def generate_routes_with_factor():
        generated, seen_fingerprints = [], set()

        def try_add(pts, k_weather, label, max_wait):
            if not pts or len(pts) < 2: return
            r = finalize_route(pts, k_weather, label, max_wait)
            if not r: return
            fp = frozenset(p["id"] for p in r["optimized_route"])
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                r["route_id"] = len(generated) + 1
                generated.append(r)

        w_ratio = max(0, min(100, request.weight)) / 100.0
        
        def custom_preference_scorer(last, p, d):
            pref_score = calculate_preference_score(p, request.user_preference)
            rating = p.get("score") or 1.0
            div_pen = 20 if p.get("loai_hinh") == last.get("loai_hinh") else 0
            return (d * (1 - w_ratio) * 10) - (rating * w_ratio * 10) + (pref_score * w_ratio) + div_pen

        # QUY ĐỊNH RÕ RÀNG MỨC ĐỘ ƯU TIÊN CHỜ ĐỢI
        # Format: (Hàm tính điểm, Tên chiến lược, Sức chịu đựng thời gian chờ tối đa - phút)
        scorers = [
            (lambda last, p, d: d, "Gần nhất (Tiết kiệm thời gian)", 15), # Ưu tiên thời gian -> Không thích chờ, tối đa 15p
            (lambda last, p, d: -(p.get("score") or 0) + (15 if p.get("loai_hinh") == last.get("loai_hinh") else 0), "Điểm đánh giá cao nhất", 45), # Địa điểm xịn -> Chờ lâu hơn tí (45p)
            (custom_preference_scorer, "Lộ trình Phù hợp sở thích", 60), # Đúng gu -> Sẵn sàng chờ (60p)
            (lambda last, p, d: d + (50 if p.get("loai_hinh") == last.get("loai_hinh") else 0), "Đa dạng trải nghiệm", 30), # Ưu tiên trải nghiệm nhưng vẫn cân bằng (30p)
        ]

        time_budgets = []
        for ratio in [0.25, 0.5, 0.75, 1.0]:
            mins = available_minutes * ratio
            if mins >= 60:
                end_t = clock_start + timedelta(minutes=mins)
                time_budgets.append(end_t)

        for origin in starting_points:
            k_weather = get_weather_factor(origin["lat"], origin["lon"])
            base_unvisited = [p for p in all_points if p["id"] != origin["id"]]
            
            for end_t in time_budgets:
                for score_fn, strat_name, max_wait in scorers:
                    # Truyền max_wait vào quá trình build
                    pts = build_route_greedy_custom(origin, base_unvisited, k_weather, end_t, score_fn, max_wait)
                    try_add(pts, k_weather, strat_name, max_wait)

        return generated

    generated_routes = generate_routes_with_factor()
    
    if len(generated_routes) == 0:
        raise HTTPException(status_code=400, detail="Các địa điểm gần bạn chưa mở cửa vào khung giờ này (hoặc thời gian đi quá ngắn). Vui lòng thử dời giờ xuất phát!")

    generated_routes.sort(key=lambda r: (-len(r["optimized_route"]), r["total_time_minutes"]))
    for i, r in enumerate(generated_routes): r["route_id"] = i + 1

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "vehicle_note": vehicle_note,
        "accessible_locations_count": len(all_points),
        "routes": generated_routes
    }