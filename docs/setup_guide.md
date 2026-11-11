# Hướng Dẫn Cài Đặt và Khởi Chạy Hệ Thống

## 1. Yêu cầu hệ thống
- **Python**: Phiên bản 3.10 trở lên.
- **Ollama**: (Tùy chọn cho module LLM Semantic / ABSA) Tải tại [ollama.com](https://ollama.com).

## 2. Cài đặt môi trường
Tạo và kích hoạt môi trường ảo Python:
```bash
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate
```

Cài đặt các gói phụ thuộc:
```bash
pip install -r requirements.txt
```

## 3. Cấu hình biến môi trường
Sao chép file `.env.example` thành `.env`:
```bash
cp .env.example .env
```
Điền các API key nếu có (TomTom, SerpApi). Nếu không có API key, hệ thống sẽ tự động dùng bộ dữ liệu giả lập (synthetic fallback) mà không làm gián đoạn chương trình.

## 4. Khởi chạy hệ thống

### Khởi chạy Backend API (FastAPI)
```bash
uvicorn backend.main:app --reload --port 8000
```
- Swagger UI tài liệu API: `http://localhost:8000/docs`
- Giao diện người dùng: `http://localhost:8000/`

### Khởi chạy AI Engine (Ollama)
Chạy script tự động tải model:
```bash
scripts\setup_ai.bat
# Hoặc chạy lệnh trực tiếp:
ollama run llama3.2
```

## 5. Chạy thực nghiệm Nghiên cứu Khoa học (Paper Benchmark)
```bash
# Chạy thử nghiệm nhanh (Smoke test):
python run_experiments.py --quick --cases 6

# Chạy đầy đủ 30 kịch bản nghiên cứu (Tạo Bảng 1 & Bảng 2):
python run_experiments.py
```
