import os
import requests
import pyodbc
import math
from datetime import datetime
from typing import List
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

@app.get("/api/locations")
def get_locations():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, ten, vi_do, kinh_do FROM DIA_DIEM")
        locations = [{"id": str(r.id), "ten": r.ten, "lat": r.vi_do, "lon": r.kinh_do} for r in cursor.fetchall()]
        conn.close()
        return {"status": "success", "data": locations}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def get_weather_factor(lat: float, lon: float) -> float:
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        res = requests.get(url, timeout=3).json()
        if res.get("current_weather", {}).get("weathercode", 0) >= 51: return 1.25
        return 1.0
    except: return 1.0

def get_density_factor(start_time: str) -> float:
    try:
        t = datetime.strptime(start_time, "%H:%M").time()
        if (t >= datetime.strptime("07:00", "%H:%M").time() and t <= datetime.strptime("09:00", "%H:%M").time()) or \
           (t >= datetime.strptime("17:00", "%H:%M").time() and t <= datetime.strptime("19:00", "%H:%M").time()):
            return 1.8
    except: pass
    return 1.0

def get_global_osrm_matrix(points_list):
    coords = ";".join([f"{p['lon']},{p['lat']}" for p in points_list])
    url = f"http://router.project-osrm.org/table/v1/driving/{coords}?annotations=duration"
    matrix_dict = {}
    try:
        response = requests.get(url, timeout=5).json()
        durations = response["durations"]
        for i, p1 in enumerate(points_list):
            matrix_dict[p1["id"]] = {}
            for j, p2 in enumerate(points_list):
                matrix_dict[p1["id"]][p2["id"]] = durations[i][j] / 60.0
        return matrix_dict
    except:
        for p1 in points_list:
            matrix_dict[p1["id"]] = {}
            for p2 in points_list:
                matrix_dict[p1["id"]][p2["id"]] = 15 if p1["id"] != p2["id"] else 0
        return matrix_dict

def calculate_total_time(route_indices, matrix_dict, points_data, k_weather, k_density):
    driving_time = sum(matrix_dict[points_data[route_indices[i]]["id"]][points_data[route_indices[i+1]]["id"]] for i in range(len(route_indices)-1))
    visit_time = sum(points_data[idx]["time"] for idx in route_indices)
    return (driving_time * k_weather) + (visit_time * k_density)

def two_opt_algorithm(route, matrix_dict, points_data, k_weather, k_density):
    best_route = route
    best_time = calculate_total_time(route, matrix_dict, points_data, k_weather, k_density)
    improved = True
    while improved:
        improved = False
        for i in range(0, len(best_route) - 1):
            for j in range(i + 1, len(best_route) + 1):
                if j - i <= 1: continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_time = calculate_total_time(new_route, matrix_dict, points_data, k_weather, k_density)
                if new_time < best_time:
                    best_route, best_time = new_route, new_time
                    improved = True
    return best_route, best_time

@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    try:
        t1 = datetime.strptime(request.start_time, "%H:%M")
        t2 = datetime.strptime(request.end_time, "%H:%M")
        available_minutes = (t2 - t1).total_seconds() / 60
        if available_minutes <= 0: available_minutes += 24 * 60
    except:
        available_minutes = 180

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, ten, vi_do, kinh_do, thoi_gian_tham_quan_phut, diem_gia_tri FROM DIA_DIEM")
    all_points = [{"id": str(r.id), "ten": r.ten, "lat": r.vi_do, "lon": r.kinh_do, "time": r.thoi_gian_tham_quan_phut, "score": r.diem_gia_tri} for r in cursor.fetchall()]
    conn.close()

    global_matrix = get_global_osrm_matrix(all_points)
    all_points.sort(key=lambda x: x["score"], reverse=True)

    initial_k_density = get_density_factor(request.start_time)
    
    def calc_dist(p1, p2): return math.sqrt((p1["lat"] - p2["lat"])**2 + (p1["lon"] - p2["lon"])**2) * 111

    def generate_routes_with_factor(k_dens):
        generated = []
        # Duyệt qua TẤT CẢ địa điểm thay vì chỉ lấy 3 điểm đầu tiên
        for origin in all_points:
            if len(generated) >= 3: 
                break # Dừng lại ngay khi đã gom đủ 3 lộ trình hợp lệ

            k_weather = get_weather_factor(origin["lat"], origin["lon"])
            
            selected_points = [origin]
            current_time = origin["time"] * k_dens
            
            unvisited = [p for p in all_points if p["id"] != origin["id"]]
            unvisited.sort(key=lambda p: calc_dist(origin, p))

            for p in unvisited:
                est_travel = (calc_dist(selected_points[-1], p) / 20.0) * 60 * k_weather
                est_visit = p["time"] * k_dens
                if current_time + est_travel + est_visit <= available_minutes:
                    selected_points.append(p)
                    current_time += (est_travel + est_visit)

            # Nếu điểm xuất phát này quá tốn thời gian, không ghép được điểm nào khác -> Loại và thử điểm tiếp theo
            if len(selected_points) < 2:
                continue 

            initial_route = list(range(len(selected_points)))
            best_route_indices, total_time = two_opt_algorithm(initial_route, global_matrix, selected_points, k_weather, k_dens)

            dropped_point = False
            while total_time > available_minutes and len(best_route_indices) > 2:
                start_id = selected_points[best_route_indices[0]]["id"]
                furthest_idx = max(best_route_indices[1:], key=lambda x: global_matrix[start_id][selected_points[x]["id"]])
                best_route_indices.remove(furthest_idx)
                dropped_point = True
                
                best_route_indices, total_time = two_opt_algorithm(best_route_indices, global_matrix, selected_points, k_weather, k_dens)

            final_route_details = [
                {
                    "id": selected_points[idx]["id"], 
                    "ten": selected_points[idx]["ten"], 
                    "lat": selected_points[idx]["lat"], 
                    "lon": selected_points[idx]["lon"]
                } for idx in best_route_indices
            ]

            generated.append({
                "route_id": len(generated) + 1,
                "dropped_point": dropped_point,
                "total_time_minutes": round(total_time, 1),
                "optimized_route": final_route_details
            })
        return generated

    generated_routes = generate_routes_with_factor(initial_k_density)

    if len(generated_routes) == 0 and initial_k_density > 1.0:
        generated_routes = generate_routes_with_factor(1.0)

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "routes": generated_routes
    }