"""Diverse route selection (Jaccard) and response formatting."""
from api.naming import ROUTE_THEMES, generate_route_description, generate_route_name

# ============================================================
# CHỌN 3–5 LỘ TRÌNH THỰC SỰ KHÁC NHAU (mục 4)
# ------------------------------------------------------------
# LỖI CŨ: try_add() lấy fingerprint = (thứ tự id, label). Vì label khác nhau
# giữa 6 scorer × 6 mốc ngân sách, CÙNG MỘT tập điểm được ghi nhận tới vài
# chục lần như những "route khác nhau". Sau đó danh sách được sort theo
# (-số điểm, tổng thời gian) rồi cắt [:5] → 5 phần tử đầu gần như luôn là
# CÙNG một lộ trình dài nhất, chỉ khác cái tên chiến lược. Đúng hiện tượng
# "5 route chỉ đổi tên" mà mục 4 cấm.
#
# CÁCH SỬA:
#   1) Khử trùng lặp theo TẬP ĐIỂM (frozenset id), không theo nhãn.
#   2) Chọn tập cuối bằng max-min diversity: route đầu lấy theo chất lượng,
#      các route sau phải đủ KHÁC (Jaccard overlap dưới ngưỡng) so với mọi
#      route đã chọn, đồng thời ưu tiên theme chưa xuất hiện.
#   3) Nếu dữ liệu nghèo, nới ngưỡng dần và chấp nhận trả 3 hoặc 4 route
#      (mục 4 cho phép) thay vì bịa thêm route trùng cho đủ 5.
# ============================================================
JACCARD_LIMITS = (0.45, 0.60, 0.75, 1.01)  # nới dần khi không đủ route
MIN_ROUTES = 3


def _route_id_set(route: dict) -> frozenset:
    return frozenset(
        p["id"] for p in route.get("optimized_route", []) if p.get("id") != "gps_current"
    )


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _route_quality(route: dict, available_minutes: float) -> float:
    """Chất lượng một route: nhiều điểm, khớp sở thích, dùng hết quỹ thời gian."""
    n_places = len(_route_id_set(route))
    utilization = 0.0
    if available_minutes > 0:
        utilization = min(1.0, (route.get("total_time_minutes") or 0) / available_minutes)
    pref = route.get("avg_preference_score", 0.5)
    return n_places * 1.0 + pref * 2.0 + utilization * 2.0


def select_diverse_routes(candidates: list, available_minutes: float, max_routes: int = 5) -> list:
    """Chọn tối đa `max_routes` lộ trình khác nhau thực sự; có thể trả về 3–4
    nếu dữ liệu không cho phép nhiều hơn mà vẫn giữ được sự khác biệt."""
    if not candidates:
        return []

    scored = sorted(
        ({"route": r, "ids": _route_id_set(r), "q": _route_quality(r, available_minutes)} for r in candidates),
        key=lambda c: -c["q"],
    )

    max_routes = max(MIN_ROUTES, min(5, max_routes))
    chosen = [scored[0]]
    used_themes = {scored[0]["route"].get("theme")}

    for limit in JACCARD_LIMITS:
        if len(chosen) >= max_routes:
            break
        # Hai lượt quét cho mỗi ngưỡng: lượt 1 chỉ nhận theme CHƯA xuất hiện, lượt 2
        # mới nhận theme trùng. Nhờ vậy 3–5 route trả về trải đều các chủ đề thay vì
        # dồn hết vào một chiến lược tình cờ cho điểm chất lượng cao.
        for prefer_new_theme in (True, False):
            for cand in scored:
                if len(chosen) >= max_routes:
                    break
                if any(cand["route"] is c["route"] for c in chosen):
                    continue
                theme = cand["route"].get("theme")
                if prefer_new_theme and theme in used_themes:
                    continue
                overlap = max(_jaccard(cand["ids"], c["ids"]) for c in chosen)
                if overlap > limit:
                    continue
                chosen.append(cand)
                used_themes.add(theme)

    # Sắp xếp hiển thị: route mạnh nhất lên đầu để Screen 2 gợi ý mặc định đúng.
    chosen.sort(key=lambda c: -c["q"])
    return [c["route"] for c in chosen]


def build_timeline(places: list) -> list:
    """
    Timeline phẳng cho Screen 2/3: xen kẽ 'visit' và 'travel'. Sinh TỪ chính
    `places` (arrive/depart/travel_to_next đã được finalize_route mô phỏng),
    nên map, timeline và route summary luôn nói cùng một câu chuyện (mục 11).
    """
    timeline = []
    for i, p in enumerate(places):
        timeline.append({
            "type": "visit",
            "order": i + 1,
            "place_id": p["id"],
            "name": p["ten"],
            "loai_hinh": p.get("loai_hinh", ""),
            "start": p.get("arrive_time"),
            "end": p.get("depart_time"),
            "duration": p.get("visit_time", 0),
            "wait_time": p.get("wait_time", 0),
            "lat": p.get("lat"),
            "lon": p.get("lon"),
        })
        if i < len(places) - 1 and (p.get("travel_to_next") or 0) > 0:
            timeline.append({
                "type": "travel",
                "from_id": p["id"],
                "to_id": places[i + 1]["id"],
                "from": p["ten"],
                "to": places[i + 1]["ten"],
                "start": p.get("depart_time"),
                "end": places[i + 1].get("arrive_time"),
                "duration": p.get("travel_to_next", 0),
                "distance_km": p.get("distance_to_next", 0),
            })
    return timeline


def format_route_object(route: dict, index: int) -> dict:
    """
    Đóng gói route thành MỘT THỰC THỂ ĐỘC LẬP theo schema mục 3.

    Giữ nguyên các khoá cũ (strategy / route_name / optimized_route /
    total_time_minutes) để app.js hiện tại không vỡ — mục 13 yêu cầu không
    sửa frontend ngoài phạm vi cần thiết.
    """
    places = route.get("optimized_route", [])
    travel_time = sum((p.get("travel_to_next") or 0) for p in places)
    visit_time = sum((p.get("visit_time") or 0) for p in places)
    wait_time = sum((p.get("wait_time") or 0) for p in places)
    distance_km = sum((p.get("distance_to_next") or 0) for p in places)
    theme = route.get("theme", "diverse")
    total = route.get("total_time_minutes", 0)

    route_points = [{"loai_hinh": p.get("loai_hinh"), "ten": p.get("ten")} for p in places]
    existing_name = route.get("route_name")
    if not existing_name or existing_name.startswith("Lộ trình ") or existing_name.startswith("Hành trình trải nghiệm "):
        name = generate_route_name(route_points, index, theme)
    else:
        name = existing_name

    route.update({
        "route_id": f"route_{index}",
        "name": name,
        "theme": theme,
        "theme_label": ROUTE_THEMES.get(theme, {}).get("label", theme),
        "description": generate_route_description(places, theme, total, travel_time),
        "places": places,
        "timeline": build_timeline(places),
        "total_duration": round(total, 1),
        "travel_time": round(travel_time, 1),
        "visit_time": round(visit_time, 1),
        "wait_time": round(wait_time, 1),
        "distance_km": round(distance_km, 1),
        "place_count": len(places),
        # ---- khoá cũ, giữ cho tương thích ngược ----
        "route_name": name,
    })
    return route
