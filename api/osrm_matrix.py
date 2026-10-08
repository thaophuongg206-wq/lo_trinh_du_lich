"""Global OSRM duration/distance matrix with vehicle profiles."""
from api.factors import get_vehicle_osrm_profile
import requests

def vehicle_restriction(vehicle_type: str) -> float:
    """Extra slowness of large vehicles when congestion comes from TomTom (not from the static K):
    K_vehicle / K_car for driving profiles (16-seat 1.11, 29-seat 1.22, 45-seat 1.39), else 1.0."""
    profile, K = get_vehicle_osrm_profile(vehicle_type)
    return max(1.0, K / 1.8) if profile == "driving" else 1.0


def get_global_osrm_matrix(points_list, vehicle_type: str = "xe_may", static_traffic: bool = True):
    """
    Ma trận thời gian/khoảng cách từ OSRM (có cache + Haversine fallback rõ ràng).
    duration = RAW OSRM x K_TRAFFIC của phương tiện  (static_traffic=True, hành vi legacy)
    duration = RAW OSRM free-flow                    (static_traffic=False: dùng khi TomTom live
                                                      cung cấp hệ số kẹt xe, tránh nhân đôi tắc đường)
    """
    try:
        from services.osrm import get_osrm_service
        _, K_TRAFFIC = get_vehicle_osrm_profile(vehicle_type)
        return get_osrm_service().matrix(points_list, vehicle_type=vehicle_type,
                                         k_override=K_TRAFFIC if static_traffic else 1.0)
    except Exception:
        pass

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
                    "duration": (durations[i][j] / 60.0) * K_TRAFFIC,
                    "distance": distances[i][j] / 1000.0
                }
        return matrix_dict
    except Exception:
        for p1 in points_list:
            matrix_dict[p1["id"]] = {}
            for p2 in points_list:
                matrix_dict[p1["id"]][p2["id"]] = {
                    "duration": (15 if p1["id"] != p2["id"] else 0) * K_TRAFFIC,
                    "distance": 5.0 if p1["id"] != p2["id"] else 0
                }
        return matrix_dict
