"""Weather / traffic-density / large-vehicle restriction factors and vehicle profiles."""
import requests
from datetime import datetime

def get_weather_factor(lat: float, lon: float) -> float:
    try:
        res = requests.get(f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true", timeout=3).json()
        if res.get("current_weather", {}).get("weathercode", 0) >= 51:
            return 1.25
        return 1.0
    except:
        return 1.0

_RUSH_HOUR_RANGES = (
    (datetime.strptime("07:00", "%H:%M").time(), datetime.strptime("09:00", "%H:%M").time()),
    (datetime.strptime("17:00", "%H:%M").time(), datetime.strptime("19:00", "%H:%M").time()),
)

def get_density_factor(start_time_str: str) -> float:
    # TỐI ƯU HIỆU NĂNG: các mốc giờ cao điểm là hằng số, không cần strptime lại
    # mỗi lần gọi hàm — hàm này được gọi hàng triệu lần trong solve_tsptw_exact
    # (bitmask DP) nên chi phí strptime lặp lại từng là điểm nghẽn hiệu năng lớn
    # nhất (benchmark: n=13 candidate mất ~6.5s trước khi tối ưu, ~1.5s sau khi
    # tối ưu — xem ghi chú DP_MAX_CANDIDATES).
    try:
        t = datetime.strptime(start_time_str, "%H:%M").time()
        for lo, hi in _RUSH_HOUR_RANGES:
            if lo <= t <= hi:
                return 1.8
    except:
        pass
    return 1.0

# Danh sách các loại xe lớn bị hạn chế theo giờ
LARGE_VEHICLE_TYPES = {"xe_16_cho", "xe_29_cho", "xe_45_cho"}
_RESTRICTION_MORNING = (datetime.strptime("06:00", "%H:%M").time(), datetime.strptime("09:00", "%H:%M").time())
_RESTRICTION_EVENING = (datetime.strptime("16:00", "%H:%M").time(), datetime.strptime("20:00", "%H:%M").time())
_RESTRICTION_FACTOR_MAP = {"xe_16_cho": 1.5, "xe_29_cho": 1.8, "xe_45_cho": 2.2}

# Cấp độ tiếp cận tối đa theo phương tiện
# Cấp 1: bãi đỗ lớn, đường rộng (TTTM, di tích lớn) → tất cả xe
# Cấp 2: đường chính, đỗ được xe con → xe máy + ô tô cá nhân
# Cấp 3: hẻm/phố cổ hẹp, không bãi xe → chỉ xe máy, xe đạp, đi bộ
VEHICLE_ACCESS_LEVEL = {
    "xe_45_cho": 1,
    "xe_29_cho": 1,
    "xe_16_cho": 1,
    "o_to":      2,
    "xe_may":    3,
    "xe_dap":    3,
    "di_bo":     3,
}

VEHICLE_ACCESS_NOTE = {
    "xe_45_cho": "Xe 45 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_29_cho": "Xe 29 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "xe_16_cho": "Xe 16 chỗ: chỉ hiển thị địa điểm có bãi đỗ xe lớn.",
    "o_to":      "Ô tô: đã loại các địa điểm trong hẻm nhỏ không có chỗ đậu xe.",
}

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
        # TỐI ƯU: dùng hằng số module-level thay vì strptime lại mỗi lần gọi (hàm
        # này cũng nằm trên đường nóng của solve_tsptw_exact) — xem ghi chú ở
        # get_density_factor.
        in_restricted_hours = (
            (_RESTRICTION_MORNING[0] <= t <= _RESTRICTION_MORNING[1]) or
            (_RESTRICTION_EVENING[0] <= t <= _RESTRICTION_EVENING[1])
        )
        if in_restricted_hours:
            return _RESTRICTION_FACTOR_MAP.get(vehicle_type, 1.0)
    except:
        pass
    return 1.0
