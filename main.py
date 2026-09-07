import os
import re
import unicodedata
import requests
import pyodbc
import math
import random
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Routing Optimization API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

import sqlite3

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

# 1. ĐÃ FIX: Bổ sung user_preference và weight vào request
class OptimizationRequest(BaseModel):
    region: str
    start_time: str
    end_time: str
    trip_date: str = ""           
    start_point: str = ""         
    vehicle_type: str = "xe_may"  
    start_lat: Optional[float] = None   
    start_lon: Optional[float] = None   
    user_preference: str = ""     # Text nhập vào: "yên tĩnh, view đẹp..."
    weight: int = 50              # Trọng số thanh trượt (0 -> 100)

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

# 2. ĐÃ FIX: Hàm tính kẹt xe ĐỘNG theo từng mốc giờ (Thay vì chết cứng 1 hệ số)
def get_dynamic_density_factor(current_time: datetime) -> float:
    try:
        t = current_time.time()
        if (datetime.strptime("07:00", "%H:%M").time() <= t <= datetime.strptime("09:00", "%H:%M").time()) or \
           (datetime.strptime("17:00", "%H:%M").time() <= t <= datetime.strptime("19:00", "%H:%M").time()):
            return 1.8
    except:
        pass
    return 1.0

# 3. MỚI: Hàm NLP cơ bản tính điểm "Gu trải nghiệm"
def calculate_preference_score(point: dict, preference_text: str) -> float:
    if not preference_text:
        return 0.0
    pref_lower = preference_text.lower()
    text_to_search = f"{point.get('mo_ta', '')} {point.get('thong_tin_chi_tiet', '')} {point.get('phu_hop', '')} {point.get('review', '')}".lower()
    
    # Tách từ khóa (bỏ dấu phẩy, lấy từ > 2 ký tự)
    keywords = [w.strip() for w in pref_lower.replace(',', ' ').split() if len(w.strip()) > 2]
    if not keywords: return 0.0
    
    match_count = sum(1 for kw in keywords if kw in text_to_search)
    return float(match_count)

LARGE_VEHICLE_TYPES = {"xe_16_cho", "xe_29_cho", "xe_45_cho"}

VEHICLE_ACCESS_LEVEL = {
    "xe_45_cho": 1, "xe_29_cho": 1, "xe_16_cho": 1,
    "o_to": 2, "xe_may": 3, "xe_dap": 3, "di_bo": 3,
}

VEHICLE_ACCESS_NOTE = {
    "xe_45_cho": "Xe 45 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_29_cho": "Xe 29 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_16_cho": "Xe 16 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "o_to":      "Ô tô: đã loại các địa điểm trong hẻm nhỏ không có chỗ đậu xe.",
}

def get_vehicle_osrm_profile(vehicle_type: str):
    profile_map = {
        "o_to": ("driving", 1.8), "xe_may": ("driving", 1.5),
        "xe_16_cho": ("driving", 2.0), "xe_29_cho": ("driving", 2.2), "xe_45_cho": ("driving", 2.5),
        "xe_dap": ("cycling", 1.0), "di_bo": ("foot", 1.0),
    }
    return profile_map.get(vehicle_type, ("driving", 1.8))

def get_large_vehicle_restriction_factor(vehicle_type: str, time_str: str) -> float:
    if vehicle_type not in LARGE_VEHICLE_TYPES:
        return 1.0
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        if (datetime.strptime("06:00", "%H:%M").time() <= t <= datetime.strptime("09:00", "%H:%M").time()) or \
           (datetime.strptime("16:00", "%H:%M").time() <= t <= datetime.strptime("20:00", "%H:%M").time()):
            restriction_map = {"xe_16_cho": 1.5, "xe_29_cho": 1.8, "xe_45_cho": 2.2}
            return restriction_map.get(vehicle_type, 1.0)
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
                    "duration": (durations[i][j] / 60.0) * K_TRAFFIC,  
                    "distance": distances[i][j] / 1000.0               
                }
        return matrix_dict
    except:
        for p1 in points_list:
            matrix_dict[p1["id"]] = {}
            for p2 in points_list:
                matrix_dict[p1["id"]][p2["id"]] = {
                    "duration": (15 if p1["id"] != p2["id"] else 0) * K_TRAFFIC,
                    "distance": 5.0 if p1["id"] != p2["id"] else 0
                }
        return matrix_dict

# ĐÃ FIX: Bỏ biến k_density tĩnh, dùng hàm động cho travel time, KHÔNG nhân vào visit_time
def calculate_cost_with_clock(route_indices, matrix_dict, points_data, k_weather, clock_start_dt, clock_end_dt, base_date):
    current_clock = clock_start_dt
    penalty = 0
    violation_index = None

    for i in range(len(route_indices)):
        idx = route_indices[i]
        p = points_data[idx]
        p_open = datetime.combine(base_date, p["open_time"])
        p_close = datetime.combine(base_date, p["close_time"])

        if i > 0:
            prev_p = points_data[route_indices[i-1]]
            if p["loai_hinh"] == prev_p["loai_hinh"]:
                penalty += 1000
            
            dyn_dens = get_dynamic_density_factor(current_clock)
            travel_mins = matrix_dict[prev_p["id"]][p["id"]]["duration"] * k_weather * dyn_dens
            current_clock += timedelta(minutes=travel_mins)
            
        if i > 1 and p["loai_hinh"] == points_data[route_indices[i-2]]["loai_hinh"]:
            penalty += 500
            
        if current_clock < p_open:
            current_clock = p_open
            
        visit_mins = p["time"] # Bỏ nhân hệ số kẹt xe!
        departure = current_clock + timedelta(minutes=visit_mins)
        
        if departure > p_close or departure > clock_end_dt:
            penalty += 10000 
            
        current_clock = departure
        
    total_minutes = (current_clock - clock_start_dt).total_seconds() / 60
    return total_minutes + penalty

def two_opt_algorithm(route, matrix_dict, points_data, k_weather, clock_start_dt, clock_end_dt, base_date):
    best_route = route
    best_cost = calculate_cost_with_clock(route, matrix_dict, points_data, k_weather, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best_route) - 1):
            for j in range(i + 1, len(best_route) + 1):
                if j - i <= 1: continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_cost = calculate_cost_with_clock(new_route, matrix_dict, points_data, k_weather, clock_start_dt, clock_end_dt, base_date)
                if new_cost < best_cost:
                    best_route, best_cost, best_violation_idx = new_route, new_cost, new_violation_idx
                    improved = True
    return best_route, best_cost, best_violation_idx

@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    try:
        base_date = datetime.strptime(request.trip_date, "%Y-%m-%d").date() if request.trip_date else datetime.today().date()
    except:
        base_date = datetime.today().date()

    try:
        clock_start = datetime.strptime(request.start_time, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        clock_end = datetime.strptime(request.end_time, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        if clock_end <= clock_start: clock_end += timedelta(days=1)
        available_minutes = (clock_end - clock_start).total_seconds() / 60
    except:
        raise HTTPException(status_code=400, detail="Lỗi định dạng thời gian (start_time/end_time phải theo dạng HH:MM)")

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
            "mo_ta": r["mo_ta"] or "", "thong_tin_chi_tiet": r["thong_tin_chi_tiet"] or "",
            "review": r["review"] or "", "phu_hop": r["phu_hop"] or "",
            "cap_do_tiep_can": r["cap_do_tiep_can"] if r["cap_do_tiep_can"] is not None else 3,
        }
        # TÍNH ĐIỂM GU TRẢI NGHIỆM TỪ NLP NGAY TỪ ĐẦU
        point_data["pref_match"] = calculate_preference_score(point_data, request.user_preference)
        all_points.append(point_data)

    max_access = VEHICLE_ACCESS_LEVEL.get(request.vehicle_type, 3)
    all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]
    vehicle_note = VEHICLE_ACCESS_NOTE.get(request.vehicle_type, "")

    global_matrix = get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)
    all_points.sort(key=lambda x: x["score"], reverse=True)

    if request.start_lat is not None and request.start_lon is not None:
        gps_point = {
            "id": "gps_current", "ten": "📍 Vị trí của bạn", "lat": request.start_lat, "lon": request.start_lon,
            "time": 0, "loai_hinh": "diem_xuat_phat", "open_time": default_open, "close_time": default_close,
            "mo_ta": "", "thong_tin_chi_tiet": "", "review": "", "phu_hop": "", "pref_match": 0
        }
        all_points_with_gps = [gps_point] + all_points
        global_matrix = get_global_osrm_matrix(all_points_with_gps, vehicle_type=request.vehicle_type)
        all_points = all_points_with_gps   
        starting_points = [gps_point]      
    elif request.start_point and request.start_point.strip():
        keyword = request.start_point.strip().lower()
        matched_points = [p for p in all_points if keyword in p["ten"].lower()]
        if matched_points:
            starting_points = matched_points[:1]
        else:
            # ĐÃ FIX: Chặn lỗi không báo khi gõ sai địa chỉ
            return {"status": "error", "message": f"Không tìm thấy địa điểm '{request.start_point}' trong cơ sở dữ liệu. Vui lòng thử từ khóa khác hoặc dùng GPS!"}
    else:
        starting_points = all_points[:3]

    k_restriction = get_large_vehicle_restriction_factor(request.vehicle_type, request.start_time)
    def calc_dist(p1, p2): return math.sqrt((p1["lat"] - p2["lat"])**2 + (p1["lon"] - p2["lon"])**2) * 111

    def finalize_route(selected_points, k_weather, k_total, route_label):
        if len(selected_points) < 2: return None

        initial_route = list(range(len(selected_points)))
        best_route_indices, best_cost, violation_idx = two_opt_algorithm(
            initial_route, global_matrix, selected_points,
            k_weather, clock_start, clock_end, base_date
        )

        dropped_point = False
        # ĐÃ FIX: Xóa điểm cuối cùng gây tràn giờ thay vì xóa điểm xa nhất
        while best_cost >= 10000 and len(best_route_indices) > 2:
            best_route_indices.pop() 
            dropped_point = True
            best_route_indices, best_cost, violation_idx = two_opt_algorithm(
                best_route_indices, global_matrix, selected_points,
                k_weather, clock_start, clock_end, base_date
            )

        if len(best_route_indices) < 2 or best_cost >= 10000: return None

        final_route_details = []
        simulated_clock = clock_start

        for i in range(len(best_route_indices)):
            idx = best_route_indices[i]
            point = selected_points[idx]
            p_open = datetime.combine(base_date, point["open_time"])

            travel_time = 0
            if i > 0:
                prev_point = selected_points[best_route_indices[i - 1]]
                dyn_dens = get_dynamic_density_factor(simulated_clock)
                travel_time = global_matrix[prev_point["id"]][point["id"]]["duration"] * k_weather * dyn_dens
                simulated_clock += timedelta(minutes=travel_time)

            wait_time = 0
            if simulated_clock < p_open:
                wait_time = (p_open - simulated_clock).total_seconds() / 60
                simulated_clock = p_open

            arrive_time_str = simulated_clock.strftime("%H:%M")   
            visit_time = point["time"]  # Không nhân hệ số
            simulated_clock += timedelta(minutes=visit_time)
            depart_time_str = simulated_clock.strftime("%H:%M")   

            final_route_details.append({
                "id": point["id"], "ten": point["ten"], "lat": point["lat"], "lon": point["lon"],
                "loai_hinh": point.get("loai_hinh", ""), "mo_ta": point.get("mo_ta", ""),
                "thong_tin_chi_tiet": point.get("thong_tin_chi_tiet", ""), "review": point.get("review", ""),
                "phu_hop": point.get("phu_hop", ""), "visit_time": round(visit_time, 1), "wait_time": round(wait_time, 1),
                "arrive_time": arrive_time_str, "depart_time": depart_time_str, "travel_to_next": 0, "distance_to_next": 0
            })

        # travel_to_next hiển thị cho UI: dùng lại đúng thời điểm khởi hành thực tế
        # (arrive/depart đã mô phỏng ở trên) để nhất quán với travel_time đã dùng khi
        # tính lịch trình, thay vì tính lại bằng một công thức khác (tránh double logic).
        for i in range(len(final_route_details) - 1):
            idx1 = best_route_indices[i]
            idx2 = best_route_indices[i + 1]
            dyn_dens = get_dynamic_density_factor(datetime.strptime(final_route_details[i]["depart_time"], "%H:%M"))
            travel_dur = global_matrix[selected_points[idx1]["id"]][selected_points[idx2]["id"]]["duration"] * k_weather * dyn_dens
            travel_dist = global_matrix[selected_points[idx1]["id"]][selected_points[idx2]["id"]]["distance"]
            final_route_details[i]["travel_to_next"] = round(travel_dur, 1)
            final_route_details[i]["distance_to_next"] = round(travel_dist, 1)

        total_actual = (simulated_clock - clock_start).total_seconds() / 60
        route_pts = [selected_points[i] for i in best_route_indices]
        avg_preference = sum(p.get("preference_score", 0.5) for p in route_pts) / len(route_pts)

        return {
            "dropped_point": dropped_point, "total_time_minutes": round(total_actual, 1),
            "vehicle_type": request.vehicle_type, "trip_date": str(base_date),
            "strategy": route_label, "optimized_route": final_route_details
        }

    def build_route_greedy_custom(origin, candidate_pool, k_total, end_clock, score_fn):
        origin_open  = datetime.combine(base_date, origin["open_time"])
        origin_close = datetime.combine(base_date, origin["close_time"])
        current_clock = max(clock_start, origin_open)
        departure = current_clock + timedelta(minutes=origin["time"])
        if departure > origin_close or departure > end_clock: return None
        
        selected  = [origin]
        unvisited = list(candidate_pool)
        while True:
            best_next, best_score, best_dep = None, float('inf'), departure
            for p in unvisited:
                dyn_dens = get_dynamic_density_factor(departure)
                # ĐÃ FIX: Truyền trực tiếp thời gian OSRM thực tế (est_travel) vào hàm chấm điểm
                try:
                    est_travel = global_matrix[selected[-1]["id"]][p["id"]]["duration"] * dyn_dens
                except (KeyError, TypeError):
                    dist = calc_dist(selected[-1], p)
                    est_travel = (dist / 20.0) * 60 * k_total * dyn_dens
                    
                est_visit  = p["time"]
                arrival    = departure + timedelta(minutes=est_travel)
                p_open     = datetime.combine(base_date, p["open_time"])
                p_close    = datetime.combine(base_date, p["close_time"])
                sv         = max(arrival, p_open)
                nd         = sv + timedelta(minutes=est_visit)
                
                if nd <= p_close and nd <= end_clock:
                    sc = score_fn(selected[-1], p, est_travel)
                    if sc < best_score:
                        best_score, best_next, best_dep = sc, p, nd
            if best_next:
                selected.append(best_next)
                unvisited.remove(best_next)
                departure = best_dep
            else:
                break
        return selected if len(selected) >= 2 else None

    def generate_routes():
        generated = []
        seen_fingerprints = set()

        def try_add(pts, k_weather, label):
            if not pts or len(pts) < 2: return
            k_total_local = k_weather * k_restriction
            r = finalize_route(pts, k_weather, k_total_local, label)
            if not r: return
            fp = frozenset(p["id"] for p in r["optimized_route"])
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                r["route_id"] = len(generated) + 1
                generated.append(r)

        # XỬ LÝ TRỌNG SỐ (WEIGHT) GIỮA THỜI GIAN VÀ TRẢI NGHIỆM
        w_exp = request.weight / 100.0  # (Từ 0.0 đến 1.0)
        w_time = 1.0 - w_exp

        # Cost càng nhỏ càng tốt. (est_travel là cost, score/pref_match là lợi ích cần trừ đi)
        scorers = [
            (lambda last, p, travel: (travel * w_time) - ((p.get("score",0) + p.get("pref_match",0)*2) * 5 * w_exp), "⚖️ Cân bằng"),
            (lambda last, p, travel: (travel * w_time) + (150 if p.get("loai_hinh") == last.get("loai_hinh") else 0) - ((p.get("score",0) + p.get("pref_match",0)*2) * 5 * w_exp), "🌈 Đa dạng"),
            (lambda last, p, travel: travel - (p.get("pref_match",0) * 15 * w_exp), "🎯 Đúng gu trải nghiệm")
        ]

        for origin in starting_points:
            k_weather = get_weather_factor(origin["lat"], origin["lon"])
            k_total = k_weather * k_restriction
            base_unvisited = [p for p in all_points if p["id"] != origin["id"]]

            min_budget = 60
            budget_ratios = [0.25, 0.5, 0.75, 1.0]
            time_budgets = []
            for ratio in budget_ratios:
                mins = available_minutes * ratio
                if mins >= min_budget:
                    end_t = clock_start + timedelta(minutes=mins)
                    hrs = int(mins // 60)
                    mns = int(mins % 60)
                    label_t = f"{hrs}h{mns:02d}" if hrs > 0 else f"{int(mins)}ph"
                    time_budgets.append((end_t, label_t))

            for end_t, time_label in time_budgets:
                for score_fn, strat_name in scorers:
                    label = f"{strat_name} · {time_label}"
                    try_add(build_route_greedy_custom(origin, base_unvisited, k_total, end_t, score_fn), k_weather, label)

            # Random khám phá để đa dạng
            for seed in [13, 42, 99, 123]:
                rng = random.Random(seed)
                n = min(max(6, int(len(base_unvisited) * 0.4)), len(base_unvisited))
                sample = rng.sample(base_unvisited, n)
                try_add(build_route_greedy_custom(origin, sample, k_total, clock_end, scorers[2][0]), k_weather, f"🎲 Khám phá #{seed}")

        return generated

    generated_routes = generate_routes()
    if len(generated_routes) == 0:
        return {"status": "error", "message": "Quỹ thời gian quá ngắn hoặc các địa điểm đều chưa mở cửa vào khung giờ này!"}

    generated_routes.sort(key=lambda r: (-len(r["optimized_route"]), r["total_time_minutes"]))
    for i, r in enumerate(generated_routes): r["route_id"] = i + 1

    return {
        "status": "success", "available_minutes": available_minutes,
        "trip_date": str(base_date), "vehicle_type": request.vehicle_type,
        "vehicle_note": vehicle_note, "accessible_locations_count": len(all_points),
        "routes": generated_routes
    }