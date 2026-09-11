# Dự Án: Hệ thống Tư vấn & Tối ưu Lộ trình Du lịch Thông Minh

Dự án cung cấp giải pháp lập kế hoạch du lịch toàn diện, kết hợp AI tạo sinh (Local LLM) để tư vấn hành trình và thuật toán Quy hoạch động (Bitmask DP) / Heuristic để tối ưu đường đi theo thời gian thực (tránh kẹt xe, khung giờ cấm).

## 🛠 Cấu trúc hệ thống & Luồng hoạt động

Dự án được chia thành 4 bước chạy nối tiếp nhau:

### Bước 1: Thu thập dữ liệu sạch (Data Pipeline)
- Chạy `python crawl_serpapi_maps.py` để lấy rating, reviews mới nhất và link ảnh độ phân giải cao từ Google Maps (lưu ra file `places_output.csv`).

### Bước 2: Nhập & Tiền xử lý Dữ liệu (Database)
- Khởi động backend, sử dụng Admin Tool trên giao diện Frontend để upload file CSV/Excel vào CSDL SQLite (`dulich.db`).
- Chạy `python clean_data.py` để loại bỏ HTML rác, khoảng trắng thừa, chuẩn bị "nguyên liệu sạch" cho AI phân tích.

### Bước 3: Khởi động Trợ lý AI (LLM & RAG)
- Chạy file `setup_ai.bat` (yêu cầu máy có cài Docker).
- Hệ thống sẽ tự động khởi tạo server Ollama và pull model `llama3.2`. 
- API nội bộ sẽ chạy tại: `http://localhost:11434`

### Bước 4: Khởi động Máy chủ Tối ưu (FastAPI Backend)
Yêu cầu cài đặt thư viện: `pip install fastapi uvicorn requests pyodbc pandas openpyxl`
- Chạy lệnh khởi động: 
  ```bash
  uvicorn main:app --reload