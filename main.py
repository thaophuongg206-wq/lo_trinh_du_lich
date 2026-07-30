import os
import requests
import pyodbc
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Routing Optimization API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

SERVER   = os.getenv("DB_SERVER",   r'.\SQLEXPRESS')
DATABASE = os.getenv("DB_NAME",     'DuLichThongMinh')

def get_db_connection():
    return pyodbc.connect(
        f'DRIVER={{ODBC Driver 17 for SQL Server}};'
        f'SERVER={SERVER};'
        f'DATABASE={DATABASE};'
        f'Trusted_Connection=yes;'
        f'TrustServerCertificate=yes;'
    )

class OptimizationRequest(BaseModel):
    region: str
    start_time: str
    end_time: str
    trip_date: str = ""
    start_point: str = ""
    vehicle_type: str = "xe_may"
    start_lat: Optional[float] = None
    start_lon: Optional[float] = None

@app.get("/api/locations")
def get_locations():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, ten, vi_do, kinh_do, loai_hinh FROM DIA_DIEM")
        locations = [{"id": str(r.id), "ten": r.ten, "lat": r.vi_do, "lon": r.kinh_do, "loai_hinh": r.loai_hinh} for r in cursor.fetchall()]
        conn.close()
        return {"status": "success", "data": locations}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_weather_factor(lat: float, lon: float) -> float:
    try:
        res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
        if res.get("current_weather", {}).get("weathercode", 0) >= 51:
            return 1.25 # Mưa làm chậm di chuyển 25%
        return 1.0
    except:
        return 1.0

def get_traffic_density_factor(start_time_str: str) -> float:
    """Hệ số tắc đường áp dụng cho THỜI GIAN DI CHUYỂN (không áp dụng cho thời gian tham quan)"""
    try:
        t = datetime.strptime(start_time_str, "%H:%M").time()
        # Giờ cao điểm sáng & chiều
        if (datetime.strptime("07:00", "%H:%M").time() <= t <= datetime.strptime("09:00", "%H:%M").time()) or \
           (datetime.strptime("16:30", "%H:%M").time() <= t <= datetime.strptime("19:00", "%H:%M").time()):
            return 1.5 # Giờ cao điểm di chuyển lâu hơn 50%
    except:
        pass
    return 1.0

LARGE_VEHICLE_TYPES = {"xe_16_cho", "xe_29_cho", "xe_45_cho"}

def get_vehicle_osrm_profile(vehicle_type: str):
    profile_map = {
        "o_to":      ("driving", 1.2),
        "xe_may":    ("driving", 1.0),
        "xe_16_cho": ("driving", 1.3),
        "xe_29_cho": ("driving", 1.4),
        "xe_45_cho": ("driving", 1.5),
        "xe_dap":    ("cycling", 1.0),
        "di_bo":     ("foot",    1.0),
    }
    return profile_map.get(vehicle_type, ("driving", 1.2))

def get_large_vehicle_restriction_factor(vehicle_type: str, time_str: str) -> float:
    if vehicle_type not in LARGE_VEHICLE_TYPES:
        return 1.0
    try:
        t = datetime.strptime(time_str, "%H:%M").time()
        in_restricted_hours = (
            (datetime.strptime("06:00", "%H:%M").time() <= t <= datetime.strptime("09:00", "%H:%M").time()) or
            (datetime.strptime("16:00", "%H:%M").time() <= t <= datetime.strptime("20:00", "%H:%M").time())
        )
        if in_restricted_hours:
            restriction_map = {"xe_16_cho": 1.3, "xe_29_cho": 1.5, "xe_45_cho": 1.8}
            return restriction_map.get(vehicle_type, 1.0)
    except:
        pass
    return 1.0

def get_global_osrm_matrix(points_list, vehicle_type: str = "xe_may"):
    osrm_profile, k_vehicle = get_vehicle_osrm_profile(vehicle_type)
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
                    "duration": (durations[i][j] / 60.0) * k_vehicle, # Phút
                    "distance": distances[i][j] / 1000.0             # Km
                }
        return matrix_dict
    except:
        for p1 in points_list:
            matrix_dict[p1["id"]] = {}
            for p2 in points_list:
                matrix_dict[p1["id"]][p2["id"]] = {
                    "duration": (15.0 if p1["id"] != p2["id"] else 0.0) * k_vehicle,
                    "distance": 5.0 if p1["id"] != p2["id"] else 0.0
                }
        return matrix_dict

def calculate_cost_with_clock(route_indices, matrix_dict, points_data, k_travel_total, clock_start_dt, clock_end_dt, base_date):
    current_clock = clock_start_dt
    penalty = 0
    
    for i in range(len(route_indices)):
        idx = route_indices[i]
        p = points_data[idx]
        p_open = datetime.combine(base_date, p["open_time"])
        p_close = datetime.combine(base_date, p["close_time"])
        
        if i > 0:
            prev_idx = route_indices[i-1]
            prev_p = points_data[prev_idx]
            
            # Phạt nếu 2 điểm liên tiếp cùng loại hình
            if p["loai_hinh"] == prev_p["loai_hinh"]:
                penalty += 300
            
            travel_mins = matrix_dict[prev_p["id"]][p["id"]]["duration"] * k_travel_total
            current_clock += timedelta(minutes=travel_mins)
            
        if current_clock < p_open:
            current_clock = p_open
            
        visit_mins = p["effective_time"] # Sử dụng thời gian tham quan đã được khống chế hợp lý
        departure = current_clock + timedelta(minutes=visit_mins)
        
        if departure > p_close or departure > clock_end_dt:
            penalty += 10000 # Lỗi trễ giờ
            
        current_clock = departure
        
    total_minutes = (current_clock - clock_start_dt).total_seconds() / 60
    return total_minutes + penalty

def two_opt_algorithm(route, matrix_dict, points_data, k_travel_total, clock_start_dt, clock_end_dt, base_date):
    best_route = route
    best_cost = calculate_cost_with_clock(route, matrix_dict, points_data, k_travel_total, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best_route) - 1): # Giữ nguyên điểm đầu (vị trí xuất phát)
            for j in range(i + 1, len(best_route)):
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_cost = calculate_cost_with_clock(new_route, matrix_dict, points_data, k_travel_total, clock_start_dt, clock_end_dt, base_date)
                if new_cost < best_cost:
                    best_route, best_cost = new_route, new_cost
                    improved = True
    return best_route, best_cost

@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    # 1. XỬ LÝ NGÀY & THỜI GIAN
    try:
        base_date = datetime.strptime(request.trip_date, "%Y-%m-%d").date() if request.trip_date else datetime.today().date()
    except:
        base_date = datetime.today().date()

    try:
        clock_start = datetime.strptime(request.start_time, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        clock_end = datetime.strptime(request.end_time, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        if clock_end <= clock_start:
            clock_end += timedelta(days=1)
        available_minutes = (clock_end - clock_start).total_seconds() / 60
    except:
        return {"status": "error", "message": "Lỗi định dạng thời gian"}

    # 2. LẤY DỮ LIỆU TỪ DATABASE
    conn = get_db_connection()
    cursor = conn.cursor()
    query = """
        SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
               c.gio_mo_cua, c.gio_dong_cua
        FROM DIA_DIEM d
        LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
    """
    cursor.execute(query)
    
    all_points = []
    default_open = datetime.strptime("00:00", "%H:%M").time()
    default_close = datetime.strptime("23:59", "%H:%M").time()

    for r in cursor.fetchall():
        open_time = r.gio_mo_cua if r.gio_mo_cua else default_open
        close_time = r.gio_dong_cua if r.gio_dong_cua else default_close
        
        if isinstance(open_time, str): open_time = datetime.strptime(open_time[:5], "%H:%M").time()
        if isinstance(close_time, str): close_time = datetime.strptime(close_time[:5], "%H:%M").time()

        raw_time = r.thoi_gian_tham_quan_phut if r.thoi_gian_tham_quan_phut else 60
        # LOGIC MỚI: Giới hạn thời gian tham quan tối đa 90-120 phút/điểm để không bị nuốt hết quỹ thời gian
        effective_time = min(raw_time, 120) 

        all_points.append({
            "id": str(r.id), "ten": r.ten, "lat": r.vi_do, "lon": r.kinh_do, 
            "time": raw_time,
            "effective_time": effective_time,
            "score": r.diem_gia_tri if r.diem_gia_tri else 5.0, 
            "loai_hinh": r.loai_hinh,
            "open_time": open_time, "close_time": close_time
        })
    conn.close()

    # 3. XÁC ĐỊNH ĐIỂM XUẤT PHÁT & DỰ TÍNH MA TRẬN OSRM
    if request.start_lat is not None and request.start_lon is not None:
        gps_point = {
            "id": "gps_current",
            "ten": "📍 Vị trí của bạn",
            "lat": request.start_lat,
            "lon": request.start_lon,
            "time": 0,
            "effective_time": 0,
            "score": 0,
            "loai_hinh": "diem_xuat_phat",
            "open_time": default_open,
            "close_time": default_close,
        }
        all_points = [gps_point] + all_points
        starting_points = [gps_point]
    elif request.start_point and request.start_point.strip():
        keyword = request.start_point.strip().lower()
        matched = [p for p in all_points if keyword in p["ten"].lower()]
        starting_points = matched[:1] if matched else all_points[:3]
    else:
        all_points.sort(key=lambda x: x["score"], reverse=True)
        starting_points = all_points[:3]

    global_matrix = get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)

    # 4. TÍNH CÁC HỆ SỐ ẢNH HƯỞNG ĐẾN THỜI GIAN DI CHUYỂN
    k_traffic = get_traffic_density_factor(request.start_time)
    k_restriction = get_large_vehicle_restriction_factor(request.vehicle_type, request.start_time)
    
    # Total factor chỉ áp dụng cho THỜI GIAN DI CHUYỂN (Travel Time)
    k_travel_total = k_traffic * k_restriction 

    # 5. THUẬT TOÁN THAM ĂN (GREEDY) + TỐI ƯU HIỆU SUẤT ĐIỂM DÙNG MA TRẬN OSRM THỰC TẾ
    generated = []

    for origin in starting_points:
        k_weather = get_weather_factor(origin["lat"], origin["lon"])
        k_travel_final = k_travel_total * k_weather

        origin_open = datetime.combine(base_date, origin["open_time"])
        current_clock = max(clock_start, origin_open)
        departure = current_clock + timedelta(minutes=origin["effective_time"])

        if departure > clock_end:
            continue

        selected_points = [origin]
        unvisited = [p for p in all_points if p["id"] != origin["id"]]

        while True:
            best_next = None
            best_efficiency = -1 # Tìm điểm có hiệu suất (Giá trị / Thời gian) tốt nhất
            best_departure = departure

            for p in unvisited:
                # Dùng trực tiếp Ma trận OSRM thay vì công thức đường chim bay
                travel_mins = global_matrix[selected_points[-1]["id"]][p["id"]]["duration"] * k_travel_final
                visit_mins = p["effective_time"]

                arrival = departure + timedelta(minutes=travel_mins)
                p_open = datetime.combine(base_date, p["open_time"])
                p_close = datetime.combine(base_date, p["close_time"])

                start_visit = max(arrival, p_open)
                next_departure = start_visit + timedelta(minutes=visit_mins)

                # Nếu điểm này không bị trễ giờ đóng cửa hoặc trễ giờ kết thúc lộ trình
                if next_departure <= p_close and next_departure <= clock_end:
                    total_time_cost = travel_mins + visit_mins + 0.1
                    
                    # Tính điểm thưởng đa dạng loại hình (tránh đi 2 địa điểm trùng loại hình)
                    type_penalty = 0.7 if p["loai_hinh"] == selected_points[-1]["loai_hinh"] else 1.0
                    
                    # Công thức tính hiệu suất: Score / Thời gian tiêu tốn
                    efficiency = (p["score"] * type_penalty) / total_time_cost

                    if efficiency > best_efficiency:
                        best_efficiency = efficiency
                        best_next = p
                        best_departure = next_departure

            if best_next:
                selected_points.append(best_next)
                unvisited.remove(best_next)
                departure = best_departure
            else:
                break # Không còn điểm nào nhét vừa khung giờ nữa

        if len(selected_points) < 2:
            continue

        # 6. TỐI ƯU HÓA LẠI THỨ TỰ BẰNG 2-OPT
        initial_route = list(range(len(selected_points)))
        best_route_indices, best_cost = two_opt_algorithm(
            initial_route, global_matrix, selected_points, k_travel_final, clock_start, clock_end, base_date
        )

        # Loại bỏ bớt điểm xa nhất nếu tổng thời gian bị tràn khung
        dropped_point = False
        while (best_cost >= 10000 or best_cost > available_minutes) and len(best_route_indices) > 2:
            start_id = selected_points[best_route_indices[0]]["id"]
            furthest_idx = max(best_route_indices[1:], key=lambda x: global_matrix[start_id][selected_points[x]["id"]]["duration"])
            best_route_indices.remove(furthest_idx)
            dropped_point = True
            best_route_indices, best_cost = two_opt_algorithm(
                best_route_indices, global_matrix, selected_points, k_travel_final, clock_start, clock_end, base_date
            )

        # 7. TẠO DỮ LIỆU TRẢ VỀ CHO FRONTEND
        final_route_details = []
        simulated_clock = clock_start

        for i in range(len(best_route_indices)):
            idx = best_route_indices[i]
            point = selected_points[idx]
            p_open = datetime.combine(base_date, point["open_time"])

            travel_time = 0
            if i > 0:
                prev_point = selected_points[best_route_indices[i-1]]
                travel_time = global_matrix[prev_point["id"]][point["id"]]["duration"] * k_travel_final
                simulated_clock += timedelta(minutes=travel_time)

            wait_time = 0
            if simulated_clock < p_open:
                wait_time = (p_open - simulated_clock).total_seconds() / 60
                simulated_clock = p_open

            visit_time = point["effective_time"]
            simulated_clock += timedelta(minutes=visit_time)

            final_route_details.append({
                "id": point["id"], "ten": point["ten"], "lat": point["lat"], "lon": point["lon"],
                "visit_time": round(visit_time, 1),
                "wait_time": round(wait_time, 1),
                "travel_to_next": 0,
                "distance_to_next": 0
            })

        for i in range(len(final_route_details) - 1):
            idx1 = best_route_indices[i]
            idx2 = best_route_indices[i+1]
            travel_dur = global_matrix[selected_points[idx1]["id"]][selected_points[idx2]["id"]]["duration"] * k_travel_final
            travel_dist = global_matrix[selected_points[idx1]["id"]][selected_points[idx2]["id"]]["distance"]
            final_route_details[i]["travel_to_next"] = round(travel_dur, 1)
            final_route_details[i]["distance_to_next"] = round(travel_dist, 1)

        generated.append({
            "route_id": len(generated) + 1,
            "dropped_point": dropped_point,
            "total_time_minutes": round(best_cost, 1),
            "vehicle_type": request.vehicle_type,
            "trip_date": str(base_date),
            "optimized_route": final_route_details
        })

    if len(generated) == 0:
        return {"status": "error", "message": "Quỹ thời gian quá ngắn hoặc các địa điểm đều chưa mở cửa vào khung giờ này!"}

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "routes": generated
    }