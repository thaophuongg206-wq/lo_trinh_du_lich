import os
import requests
import pyodbc
from datetime import datetime
from typing import List
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Routing Optimization API")

# --- CẤU HÌNH KẾT NỐI DATABASE DÙNG CHUNG ---
SERVER = os.getenv("DB_SERVER", r'.\SQLEXPRESS')
DATABASE = os.getenv("DB_NAME", 'DuLichThongMinh')

def get_db_connection():
    conn_str = f'DRIVER={{SQL Server}};SERVER={SERVER};DATABASE={DATABASE};Trusted_Connection=yes;'
    return pyodbc.connect(conn_str)


# --- ĐỊNH NGHĨA CẤU TRÚC JSON ---
class Point(BaseModel):
    id: str
    lat: float
    lon: float

class OptimizationRequest(BaseModel):
    points: List[Point]
    start_time: str
    end_time: str


# --- API LẤY DANH SÁCH ĐỊA ĐIỂM TỪ DATABASE ---
@app.get("/api/locations")
def get_locations():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, ten, vi_do, kinh_do FROM DIA_DIEM")
        
        rows = cursor.fetchall()
        locations = []
        for row in rows:
            locations.append({
                "id": str(row.id),
                "ten": row.ten,
                "lat": row.vi_do,
                "lon": row.kinh_do
            })
        conn.close()
        return {"status": "success", "data": locations}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- HÀM GỌI API BÊN NGOÀI (EXTERNAL APIS) ---

def get_weather_factor(lat: float, lon: float) -> float:
    """Gọi Open-Meteo API kiểm tra thời tiết. Nếu mưa -> hệ số K_weather = 1.25, ngược lại = 1.0"""
    try:
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
        response = requests.get(url, timeout=3).json()
        weather_code = response.get("current_weather", {}).get("weathercode", 0)
        
        # Mã weathercode >= 51 tương ứng với các trạng thái mưa (Rain/Drizzle/Thunderstorm)
        if weather_code >= 51:
            return 1.25 
        return 1.0
    except:
        return 1.0  # Mặc định nếu API lỗi thì không điều chỉnh thời gian

def get_osrm_matrix(points: List[Point], k_weather: float):
    """Gọi OSRM API lấy ma trận thời gian thực tế giữa các tọa độ (tính bằng phút)"""
    # Tạo chuỗi tọa độ dạng: lon1,lat1;lon2,lat2;lon3,lat3
    coords = ";".join([f"{p.lon},{p.lat}" for p in points])
    url = f"http://router.project-osrm.org/table/v1/driving/{coords}?annotations=duration"
    
    try:
        response = requests.get(url, timeout=5).json()
        durations_seconds = response["durations"]
        
        # Chuyển đổi giây thành phút và nhân với hệ số thời tiết K_weather
        matrix = []
        for row in durations_seconds:
            new_row = [round((sec / 60.0) * k_weather, 1) for sec in row]
            matrix.append(new_row)
        return matrix
    except Exception as e:
        print("Lỗi OSRM, dùng tạm matrix mặc định:", e)
        # Backup nếu mất mạng
        num = len(points)
        return [[15 if i != j else 0 for j in range(num)] for i in range(num)]


# --- THUẬT TOÁN 2-OPT ---

def calculate_total_time(route, matrix):
    return sum(matrix[route[i]][route[i+1]] for i in range(len(route)-1))

def two_opt_algorithm(route, matrix):
    best_route = route
    best_time = calculate_total_time(route, matrix)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best_route) - 2):
            for j in range(i + 1, len(best_route)):
                if j - i == 1:
                    continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_time = calculate_total_time(new_route, matrix)
                if new_time < best_time:
                    best_route, best_time = new_route, new_time
                    improved = True
    return best_route, best_time


# --- ENDPOINT CHÍNH ---

@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    points = request.points
    num_points = len(points)
    
    if num_points < 2:
        return {"status": "error", "message": "Cần ít nhất 2 điểm để tạo lộ trình."}

    # 1. KIỂM TRA THỜI TIẾT TẠI ĐIỂM ĐẦU TIÊN
    k_weather = get_weather_factor(points[0].lat, points[0].lon)

    # 2. LẤY MA TRẬN THỜI GIAN THỰC TẾ TỪ OSRM (Đã nhân K_weather)
    real_matrix = get_osrm_matrix(points, k_weather)

    # 3. TỐI ƯU HÓA LỘ TRÌNH VỚI THUẬT TOÁN 2-OPT
    initial_route = list(range(num_points))
    best_route_indices, total_time = two_opt_algorithm(initial_route, real_matrix)

    # 4. XỬ LÝ RÀNG BUỘC THỜI GIAN (SOFT CONSTRAINTS)
    try:
        t1 = datetime.strptime(request.start_time, "%H:%M")
        t2 = datetime.strptime(request.end_time, "%H:%M")
        available_minutes = (t2 - t1).total_seconds() / 60
    except:
        available_minutes = 120

    message = "Tìm lộ trình tối ưu dựa trên bản đồ thực tế thành công!"
    dropped_point = None

    if total_time > available_minutes and len(best_route_indices) > 2:
        dropped_idx = best_route_indices.pop()
        dropped_point = points[dropped_idx].id
        total_time = calculate_total_time(best_route_indices, real_matrix)
        message = f"Thời gian khả dụng ({available_minutes} phút) không đủ. Tự động bỏ điểm '{dropped_point}'."

    final_route_ids = [points[idx].id for idx in best_route_indices]

    return {
        "status": "success",
        "message": message,
        "weather_adjusted": k_weather > 1.0,
        "metrics": {
            "total_time_minutes": round(total_time, 1),
            "available_minutes": available_minutes
        },
        "optimized_route": final_route_ids,
        "dropped_point": dropped_point,
        "real_matrix_osrm": real_matrix
    }