"""Small helpers shared by the itinerary/session endpoints."""
from pydantic import BaseModel
from typing import List, Optional
from api.schemas import OptimizationRequest
from api.naming import THEME_EXPERIENCE_NAMES

# ============================================================
# API LỚP NGOÀI — mỗi endpoint chỉ là lớp mỏng bọc quanh run_route_generation()
# và itinerary_store, để không có chỗ nào sinh lại route ngoài tầm kiểm soát.
# ============================================================

def _params_from_request(request: OptimizationRequest) -> dict:
    """Tham số Screen 1 cần nhớ trong phiên để các bước sau tính lại đúng bối cảnh."""
    return {
        "region": request.region,
        "start_time": request.start_time,
        "end_time": request.end_time,
        "trip_date": request.trip_date,
        "start_point": request.start_point,
        "start_lat": request.start_lat,
        "start_lon": request.start_lon,
        "vehicle_type": request.vehicle_type,
        "user_preference": request.user_preference,
        "weight": request.weight,
        "ai_selected_ids": request.ai_selected_ids,
    }


def _request_from_session(session, overrides: dict = None) -> OptimizationRequest:
    params = dict(session.params)
    params.update(overrides or {})
    params.pop("ai_selected_ids", None)
    return OptimizationRequest(**{k: v for k, v in params.items() if v is not None})


class RouteSelectRequest(BaseModel):
    session_id: str
    route_id: str


class ItineraryUpdateRequest(BaseModel):
    session_id: str
    remove_ids: Optional[List[str]] = None    # Loại vĩnh viễn trong phiên (mục 6)
    restore_ids: Optional[List[str]] = None   # Bỏ loại trừ khi người dùng đổi ý
    add_ids: Optional[List[str]] = None       # Ghim thêm điểm (must-visit)


def _ensure_unique_names(routes: list) -> list:
    """
    Đảm bảo 3–5 lộ trình ở Screen 2 có tên độc nhất, phản ánh đúng trải nghiệm,
    tránh hoàn toàn việc trùng tên hay dùng tên đánh số khô khan.
    """
    seen = set()
    for idx, r in enumerate(routes, 1):
        name = r.get("name") or r.get("route_name") or f"Hành trình trải nghiệm {idx}"
        theme = r.get("theme", "diverse")
        options = THEME_EXPERIENCE_NAMES.get(theme, [])

        if name in seen:
            found_alt = False
            for opt in options:
                if opt not in seen:
                    name = opt
                    found_alt = True
                    break
            if not found_alt:
                suffixes = ["(Góc nhìn mới)", "(Nhịp điệu sâu lắng)", "(Cung đường mở rộng)", "(Phiên bản thong thả)"]
                for suf in suffixes:
                    cand = f"{name} {suf}"
                    if cand not in seen:
                        name = cand
                        found_alt = True
                        break
            if not found_alt:
                n = 2
                while f"{name} #{n}" in seen:
                    n += 1
                name = f"{name} #{n}"

        r["name"] = name
        r["route_name"] = name
        seen.add(name)
    return routes
