from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import random
from datetime import datetime

app = FastAPI(title="Routing Optimization API")

# --- ĐỊNH NGHĨA CẤU TRÚC JSON ---
class Point(BaseModel):
    id: str
    lat: float
    lon: float

class OptimizationRequest(BaseModel):
    points: List[Point]
    start_time: str
    end_time: str

# --- CÁC HÀM THUẬT TOÁN (ALGORITHMS) ---

def calculate_total_time(route_indices, matrix):
    """Hàm tính tổng thời gian của một lộ trình cụ thể"""
    total = 0
    for i in range(len(route_indices) - 1):
        total += matrix[route_indices[i]][route_indices[i+1]]
    return total

def two_opt_algorithm(route_indices, matrix):
    """Thuật toán 2-OPT gỡ chéo đường đi để tối ưu lộ trình"""
    best_route = route_indices
    best_time = calculate_total_time(route_indices, matrix)
    improved = True
    
    while improved:
        improved = False
        # Chạy vòng lặp để thử đảo ngược từng đoạn đường
        for i in range(1, len(best_route) - 2):
            for j in range(i + 1, len(best_route)):
                if j - i == 1: continue
                
                # Tạo lộ trình mới bằng cách đảo ngược đoạn i đến j
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1] 
                new_time = calculate_total_time(new_route, matrix)
                
                # Nếu đường mới nhanh hơn, lưu lại làm đường tốt nhất
                if new_time < best_time:
                    best_route = new_route
                    best_time = new_time
                    improved = True
    return best_route, best_time


# --- ENDPOINT XỬ LÝ CHÍNH ---

@app.post("/api/optimize-route")
async def optimize_route(request: OptimizationRequest):
    num_points = len(request.points)
    
    if num_points < 2:
        return {"status": "error", "message": "Cần ít nhất 2 điểm để tạo lộ trình."}

    # 1. TẠO MA TRẬN THỜI GIAN (Dữ liệu giả lập ngẫu nhiên từ 10 - 40 phút)
    mock_matrix = [[random.randint(10, 40) if i != j else 0 for j in range(num_points)] for i in range(num_points)]

    # 2. CHẠY THUẬT TOÁN TÌM ĐƯỜNG TỐI ƯU
    initial_route = list(range(num_points)) # Ví dụ điểm gốc: 0 -> 1 -> 2
    best_route_indices, total_time = two_opt_algorithm(initial_route, mock_matrix)

    # 3. XỬ LÝ RÀNG BUỘC THỜI GIAN (Soft Constraints)
    try:
        # Tính xem user có bao nhiêu phút để đi chơi
        t1 = datetime.strptime(request.start_time, "%H:%M")
        t2 = datetime.strptime(request.end_time, "%H:%M")
        available_minutes = (t2 - t1).total_seconds() / 60
    except:
        available_minutes = 120 # Mặc định nếu nhập sai format giờ
        
    message = "Tìm đường thành công và tối ưu nhất!"
    dropped_point = None
    
    # LOGIC CẮT ĐIỂM: Nếu tổng thời gian đi > thời gian cho phép -> Cắt điểm cuối
    if total_time > available_minutes and len(best_route_indices) > 2:
        dropped_point_index = best_route_indices.pop() # Lấy điểm cuối cùng ra khỏi danh sách
        dropped_point = request.points[dropped_point_index].id
        total_time = calculate_total_time(best_route_indices, mock_matrix) # Tính lại thời gian
        message = f"Thời gian khả dụng quá ngắn ({available_minutes} phút). Hệ thống tự động loại bỏ điểm {dropped_point} để đảm bảo trải nghiệm."

    # Chuyển đổi Index (0, 1, 2) về lại đúng tên ID của điểm
    final_route_ids = [request.points[idx].id for idx in best_route_indices]

    # TRẢ KẾT QUẢ VỀ CHO FRONTEND
    return {
        "status": "success",
        "message": message,
        "metrics": {
            "total_time_minutes": total_time,
            "available_minutes": available_minutes
        },
        "optimized_route": final_route_ids,
        "dropped_point": dropped_point,
        "distance_matrix_mock": mock_matrix
    }