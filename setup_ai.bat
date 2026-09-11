@echo off
title Khoi tao AI - Du Lich Thong Minh
echo ========================================================
echo   KHOI TAO HE THONG AI (OLLAMA) CHO DU LICH THONG MINH
echo ========================================================
echo.

REM Kiem tra xem may tinh da cai Ollama chua
where ollama >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] LOI: KHONG TIM THAY OLLAMA TREN MAY NAY!
    echo ========================================================
    echo De chay duoc tinh nang AI, ban can cai dat Ollama.
    echo 1. Truy cap trang web: https://ollama.com
    echo 2. Bam Download va cai dat nhu phan mem binh thuong.
    echo 3. Sau khi cai xong, hay quay lai va chay lai file nay.
    echo ========================================================
    pause
    exit /b
)

echo [OK] Da tim thay Ollama tren he thong!
echo Dang kiem tra va tai model llama3.2 (Neu chua co se mat vai phut de tai)...
ollama run llama3.2

echo.
echo ========================================================
echo HOAN TAT! Server AI dang chay tai http://localhost:11434
echo Ban co the thu nho cua so nay va chay Backend.
echo ========================================================
pause