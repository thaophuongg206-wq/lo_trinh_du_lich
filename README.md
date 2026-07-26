# Routing Optimization API - Backend (Du Lịch Thông Minh)

Hệ thống API hỗ trợ tối ưu hóa lộ trình du lịch dựa trên ma trận khoảng cách thực tế (OSRM), yếu tố thời tiết (Open-Meteo) và thuật toán 2-Opt.

## 🚀 Công nghệ sử dụng
* **Language:** Python 3.x
* **Framework:** FastAPI, Uvicorn
* **Database:** Microsoft SQL Server (`pyodbc`)
* **External APIs:** OSRM (Routing), Open-Meteo (Weather)

## 🛠️ Hướng dẫn cài đặt & Chạy ứng dụng

1. **Kích hoạt môi trường ảo (venv):**
   ```bash
   .\venv\Scripts\activate
   ```

2. **Cài đặt thư viện:**
   ```bash
   pip install fastapi uvicorn requests pyodbc
   ```

3. **Cấu hình Database:**
   * Mở SQL Server Management Studio (SSMS).
   * Chạy file `DuLichThongMinh.sql` để khởi tạo Database `DuLichThongMinh` và bảng `DIA_DIEM`.

4. **Khởi chạy Server FastAPI:**
   ```bash
   uvicorn main:app --reload
   ```
   * Server sẽ chạy tại địa chỉ: `[http://127.0.0.1:8000](http://127.0.0.1:8000)`
   * Trang tài liệu API (Swagger UI): `[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)`

## 📌 Danh sách API Chính
* `GET /api/locations`: Lấy danh sách toàn bộ địa điểm từ CSDL.
* `POST /api/optimize-route`: Tính toán và trả về lộ trình tối ưu theo khung thời gian.