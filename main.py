import os
import requests
import pyodbc
import math
from datetime import datetime, timedelta
from typing import List, Optional
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Routing Optimization API")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

SERVER = os.getenv("DB_SERVER", r'LAPTOP-EV7C4EMM')
DATABASE = os.getenv("DB_NAME", 'DuLichThongMinh')

def get_db_connection():
    return pyodbc.connect(f'DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};Trusted_Connection=yes;')

class OptimizationRequest(BaseModel):
    region: str
    start_time: str
    end_time: str
    trip_date: str = ""           # Ngày khởi hành (YYYY-MM-DD)
    start_point: str = ""         # Tên điểm xuất phát (tìm theo tên)
    vehicle_type: str = "xe_may"  # Phương tiện
    start_lat: Optional[float] = None   # Vĩ độ GPS (khi dùng vị trí hiện tại)
    start_lon: Optional[float] = None   # Kinh độ GPS

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
            return 1.25
        return 1.0
    except:
        return 1.0

def get_density_factor(start_time_str: str) -> float:
    try:
        t = datetime.strptime(start_time_str, "%H:%M").time()
        if (t >= datetime.strptime("07:00", "%H:%M").time() and t <= datetime.strptime("09:00", "%H:%M").time()) or \
           (t >= datetime.strptime("17:00", "%H:%M").time() and t <= datetime.strptime("19:00", "%H:%M").time()):
            return 1.8
    except:
        pass
    return 1.0

# Danh sách các loại xe lớn bị hạn chế theo giờ
LARGE_VEHICLE_TYPES = {"xe_16_cho", "xe_29_cho", "xe_45_cho"}

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
        morning_start = datetime.strptime("06:00", "%H:%M").time()
        morning_end   = datetime.strptime("09:00", "%H:%M").time()
        evening_start = datetime.strptime("16:00", "%H:%M").time()
        evening_end   = datetime.strptime("20:00", "%H:%M").time()

        in_restricted_hours = (
            (morning_start <= t <= morning_end) or
            (evening_start <= t <= evening_end)
        )
        if in_restricted_hours:
            restriction_map = {
                "xe_16_cho": 1.5,
                "xe_29_cho": 1.8,
                "xe_45_cho": 2.2,
            }
            return restriction_map.get(vehicle_type, 1.0)
    except:
        pass
    return 1.0

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

def calculate_cost_with_clock(route_indices, matrix_dict, points_data, k_weather, k_density, clock_start_dt, clock_end_dt, base_date):
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
            
            if p["loai_hinh"] == prev_p["loai_hinh"]:
                penalty += 1000
            
            travel_mins = matrix_dict[prev_p["id"]][p["id"]]["duration"] * k_weather
            current_clock += timedelta(minutes=travel_mins)
            
        if i > 1:
            prev_prev_idx = route_indices[i-2]
            prev_prev_p = points_data[prev_prev_idx]
            if p["loai_hinh"] == prev_prev_p["loai_hinh"]:
                penalty += 500
            
        if current_clock < p_open:
            current_clock = p_open
            
        visit_mins = p["time"] * k_density
        departure = current_clock + timedelta(minutes=visit_mins)
        
        if departure > p_close or departure > clock_end_dt:
            penalty += 10000 
            
        current_clock = departure
        
    total_minutes = (current_clock - clock_start_dt).total_seconds() / 60
    return total_minutes + penalty

def two_opt_algorithm(route, matrix_dict, points_data, k_weather, k_density, clock_start_dt, clock_end_dt, base_date):
    best_route = route
    best_cost = calculate_cost_with_clock(route, matrix_dict, points_data, k_weather, k_density, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        for i in range(0, len(best_route) - 1):
            for j in range(i + 1, len(best_route) + 1):
                if j - i <= 1: continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_cost = calculate_cost_with_clock(new_route, matrix_dict, points_data, k_weather, k_density, clock_start_dt, clock_end_dt, base_date)
                if new_cost < best_cost:
                    best_route, best_cost = new_route, new_cost
                    improved = True
    return best_route, best_cost

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
        return {"status": "error", "message": "Lỗi định dạng thời gian"}

    # 3. LẤY DANH SÁCH ĐỊA ĐIỂM TỪ DATABASE
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

        all_points.append({
            "id": str(r.id), "ten": r.ten, "lat": r.vi_do, "lon": r.kinh_do, 
            "time": r.thoi_gian_tham_quan_phut, "score": r.diem_gia_tri, "loai_hinh": r.loai_hinh,
            "open_time": open_time, "close_time": close_time
        })
    conn.close()

    # 4. LẤY MA TRẬN KHOẢNG CÁCH THEO PHƯƠNG TIỆN (vehicle_type)
    global_matrix = get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)

    # 5. XÁC ĐỊNH ĐIỂM XUẤT PHÁT (start_point)
    all_points.sort(key=lambda x: x["score"], reverse=True)

    if request.start_lat is not None and request.start_lon is not None:
        # TRƯỜNG HỢP 1: Người dùng dùng GPS → tạo điểm ảo "Vị trí hiện tại"
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
        }

        all_points_with_gps = [gps_point] + all_points
        global_matrix = get_global_osrm_matrix(all_points_with_gps, vehicle_type=request.vehicle_type)
        all_points = all_points_with_gps   # Cập nhật danh sách để route dùng đúng
        starting_points = [gps_point]      # Chỉ xuất phát từ vị trí GPS

    elif request.start_point and request.start_point.strip():
        # TRƯỜNG HỢP 2: Người dùng nhập tên điểm → tìm trong DB
        keyword = request.start_point.strip().lower()
        matched_points = [p for p in all_points if keyword in p["ten"].lower()]
        starting_points = matched_points[:1] if matched_points else all_points[:3]

    else:
        # TRƯỜNG HỢP 3: Không nhập gì → dùng top 3 điểm cao nhất
        starting_points = all_points[:3]
    # 6. TÍNH TOÁN LỘ TRÌNH TỐI Ưu
    initial_k_density = get_density_factor(request.start_time)

    # Hệ số hạn chế xe lớn theo giờ (chỉ áp dụng nếu là xe 16/29/45 chỗ)
    k_restriction = get_large_vehicle_restriction_factor(request.vehicle_type, request.start_time)

    def calc_dist(p1, p2): return math.sqrt((p1["lat"] - p2["lat"])**2 + (p1["lon"] - p2["lon"])**2) * 111

    def generate_routes_with_factor(k_dens):
        generated = []
        for origin in starting_points:
            k_weather = get_weather_factor(origin["lat"], origin["lon"])
            # Hệ số tổng hợp: thời tiết × hạn chế xe lớn
            k_total = k_weather * k_restriction
            
            origin_open = datetime.combine(base_date, origin["open_time"])
            origin_close = datetime.combine(base_date, origin["close_time"])
            
            current_clock = max(clock_start, origin_open)
            visit_mins = origin["time"] * k_dens
            departure = current_clock + timedelta(minutes=visit_mins)
            
            if departure > origin_close or departure > clock_end:
                continue 
                
            selected_points = [origin]
            unvisited = [p for p in all_points if p["id"] != origin["id"]]

            while True:
                best_next = None
                best_score = float('inf')
                best_departure = departure
                
                for p in unvisited:
                    est_travel = (calc_dist(selected_points[-1], p) / 20.0) * 60 * k_total
                    est_visit = p["time"] * k_dens
                    
                    arrival = departure + timedelta(minutes=est_travel)
                    p_open = datetime.combine(base_date, p["open_time"])
                    p_close = datetime.combine(base_date, p["close_time"])
                    
                    start_visit = max(arrival, p_open)
                    next_departure = start_visit + timedelta(minutes=est_visit)
                    
                    if next_departure <= p_close and next_departure <= clock_end:
                        dist = calc_dist(selected_points[-1], p)
                        
                        penalty = 0
                        if p["loai_hinh"] == selected_points[-1]["loai_hinh"]:
                            penalty += 30
                        elif len(selected_points) > 1 and p["loai_hinh"] == selected_points[-2]["loai_hinh"]:
                            penalty += 15
                            
                        score = dist + penalty
                        if score < best_score:
                            best_score = score
                            best_next = p
                            best_departure = next_departure
                            
                if best_next:
                    selected_points.append(best_next)
                    unvisited.remove(best_next)
                    departure = best_departure
                else:
                    break

            if len(selected_points) < 2:
                continue 

            initial_route = list(range(len(selected_points)))
            # Dùng k_total (= k_weather × k_restriction) để 2-opt cũng tính đúng hạn chế xe lớn
            best_route_indices, best_cost = two_opt_algorithm(initial_route, global_matrix, selected_points, k_total, k_dens, clock_start, clock_end, base_date)

            dropped_point = False
            while (best_cost >= 10000 or best_cost > available_minutes) and len(best_route_indices) > 2:
                start_id = selected_points[best_route_indices[0]]["id"]
                furthest_idx = max(best_route_indices[1:], key=lambda x: global_matrix[start_id][selected_points[x]["id"]]["duration"])
                best_route_indices.remove(furthest_idx)
                dropped_point = True
                # Dùng k_total ở đây cho nhất quán
                best_route_indices, best_cost = two_opt_algorithm(best_route_indices, global_matrix, selected_points, k_total, k_dens, clock_start, clock_end, base_date)

            final_route_details = []
            simulated_clock = clock_start
            
            for i in range(len(best_route_indices)):
                idx = best_route_indices[i]
                point = selected_points[idx]
                p_open = datetime.combine(base_date, point["open_time"])
                
                travel_time = 0
                if i > 0:
                    prev_point = selected_points[best_route_indices[i-1]]
                    travel_time = global_matrix[prev_point["id"]][point["id"]]["duration"] * k_weather
                    simulated_clock += timedelta(minutes=travel_time)
                    
                wait_time = 0
                if simulated_clock < p_open:
                    wait_time = (p_open - simulated_clock).total_seconds() / 60
                    simulated_clock = p_open
                    
                visit_time = point["time"] * k_dens
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
                travel_dur = global_matrix[selected_points[idx1]["id"]][selected_points[idx2]["id"]]["duration"] * k_weather
                travel_dist = global_matrix[selected_points[idx1]["id"]][selected_points[idx2]["id"]]["distance"]
                final_route_details[i]["travel_to_next"] = round(travel_dur, 1)
                final_route_details[i]["distance_to_next"] = round(travel_dist, 1)

            generated.append({
                "route_id": len(generated) + 1,
                "dropped_point": dropped_point,
                "total_time_minutes": round(best_cost, 1),
                "vehicle_type": request.vehicle_type,   # Trả về phương tiện đã dùng
                "trip_date": str(base_date),             # Trả về ngày khởi hành
                "optimized_route": final_route_details
            })
        return generated

    generated_routes = generate_routes_with_factor(initial_k_density)
    if len(generated_routes) == 0 and initial_k_density > 1.0:
        generated_routes = generate_routes_with_factor(1.0)

    if len(generated_routes) == 0:
        return {"status": "error", "message": "Quỹ thời gian quá ngắn hoặc các địa điểm đều chưa mở cửa vào khung giờ này!"}

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "routes": generated_routes
    }