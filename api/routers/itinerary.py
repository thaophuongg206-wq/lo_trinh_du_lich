"""
Itinerary manipulation endpoints:
- Quick actions (add lunch, add cafe, remove one, slow down) WITHOUT calling LLM.
- Manual route adjustments: reorder, replace, candidate suggestions, change time, add, remove.
Always operates on the currently selected route (selected_route_id) and preserves all other routes.
"""
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from api.db import fetch_all_points
from api.diversity import build_timeline
from api.factors import VEHICLE_ACCESS_LEVEL, get_weather_factor
from api.naming import ROUTE_THEMES
from api.osrm_matrix import get_global_osrm_matrix
from api.preferences import _strip_diacritics
from api.scheduling import calc_dist, get_travel_minutes
from itinerary_store import store

router = APIRouter()

QUICK_ACTIONS = {"add_lunch", "add_cafe", "remove_one", "slow_down"}
_QUICK_CATEGORY = {"add_lunch": "an uong", "add_cafe": "cafe"}


class QuickActionRequest(BaseModel):
    session_id: str
    action: str


class ItineraryReorderRequest(BaseModel):
    session_id: str
    ordered_place_ids: List[str]
    ai_advice: Optional[str] = None


class ItineraryReplaceRequest(BaseModel):
    session_id: str
    old_place_id: str
    new_place_id: Optional[str] = None
    ai_advice: Optional[str] = None


class ItineraryChangeTimeRequest(BaseModel):
    session_id: str
    place_id: Optional[str] = None
    custom_visit_time: Optional[float] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    ai_advice: Optional[str] = None


class ItineraryAddPlaceRequest(BaseModel):
    session_id: str
    place_id: str
    insert_after_id: Optional[str] = None
    custom_visit_time: Optional[float] = None
    ai_advice: Optional[str] = None


class ItineraryRemovePlaceRequest(BaseModel):
    session_id: str
    place_id: str
    exclude_from_session: bool = True
    ai_advice: Optional[str] = None


def _is_open_during(p: dict, start_h: float, end_h: float) -> bool:
    """Điểm có mở cửa trọn trong khung [start_h, end_h] (giờ, dạng số thực) hay không."""
    try:
        o, c = p["open_time"], p["close_time"]
        return (o.hour + o.minute / 60) <= start_h and (c.hour + c.minute / 60) >= end_h
    except Exception:
        return True


def _score_of(p: dict) -> float:
    try:
        return float(p.get("score") or p.get("diem_gia_tri") or 0)
    except (TypeError, ValueError):
        return 0.0


def _pick_place_to_add(session, category: str, lunch: bool):
    """Chọn 1 điểm đúng loại hình, chưa có trong lịch trình, chưa bị loại.
    Xếp hạng: điểm giá trị trừ dần theo khoảng cách tới điểm gần nhất trong lịch trình."""
    vehicle = session.params.get("vehicle_type", "xe_may")
    current_ids = set(session.current_place_ids())
    current_points = (session.current_itinerary or {}).get("places", [])
    anchors = [p for p in current_points if p.get("id") != "gps_current"]

    candidates = []
    for p in fetch_all_points(vehicle_type=vehicle):
        if p["id"] in current_ids or p["id"] in session.excluded_ids:
            continue
        if _strip_diacritics(p.get("loai_hinh") or "") != category:
            continue
        if lunch and not _is_open_during(p, 11.5, 13.0):
            continue
        candidates.append(p)
    if not candidates:
        return None

    def rank(p):
        near = min((calc_dist(p, a) for a in anchors), default=0.0)
        return _score_of(p) - 0.5 * near

    return max(candidates, key=rank)


def recalculate_route_from_places(
    session,
    ordered_places: List[dict],
    start_time_str: str = None,
    end_time_str: str = None,
    ai_advice: str = None,
    custom_visit_times: dict = None,
) -> tuple[dict, List[str]]:
    """
    Tính lại toàn bộ timeline, khoảng cách, giờ đến, giờ đi cho một danh sách
    địa điểm theo thứ tự định sẵn.
    Chỉ cập nhật route đang được chọn trong session.
    """
    trip_date_str = session.params.get("trip_date")
    try:
        if trip_date_str:
            base_date = datetime.strptime(str(trip_date_str)[:10], "%Y-%m-%d").date()
        else:
            base_date = datetime.today().date()
    except Exception:
        base_date = datetime.today().date()

    st = start_time_str or session.params.get("start_time", "08:00")
    et = end_time_str or session.params.get("end_time", "20:00")
    try:
        clock_start = datetime.strptime(st, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        clock_end = datetime.strptime(et, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        if clock_end <= clock_start:
            clock_end += timedelta(days=1)
    except Exception:
        clock_start = datetime.strptime("08:00", "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        clock_end = datetime.strptime("20:00", "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )

    vehicle_type = session.params.get("vehicle_type", "xe_may")
    ref_lat = session.params.get("start_lat") or (ordered_places[0].get("lat") if ordered_places else 21.0285)
    ref_lon = session.params.get("start_lon") or (ordered_places[0].get("lon") if ordered_places else 105.8542)
    k_weather = get_weather_factor(ref_lat, ref_lon)
    global_matrix = get_global_osrm_matrix(ordered_places, vehicle_type=vehicle_type)

    db_points_list = fetch_all_points(vehicle_type=vehicle_type)
    db_points = {p["id"]: p for p in db_points_list}

    simulated_clock = clock_start
    final_places = []
    warnings = []
    custom_times = custom_visit_times or {}

    for i, p in enumerate(ordered_places):
        pid = str(p.get("id"))
        db_p = db_points.get(pid, {})

        if pid == "gps_current":
            p_open_time = datetime.strptime("00:00", "%H:%M").time()
            p_close_time = datetime.strptime("23:59", "%H:%M").time()
            def_visit = 0
            ten = "📍 Vị trí của bạn"
            loai_hinh = "diem_xuat_phat"
        else:
            p_open_time = db_p.get("open_time") or p.get("open_time") or datetime.strptime("00:00", "%H:%M").time()
            p_close_time = db_p.get("close_time") or p.get("close_time") or datetime.strptime("23:59", "%H:%M").time()
            def_visit = db_p.get("time") or db_p.get("thoi_gian_tham_quan_phut") or p.get("visit_time") or 45
            ten = p.get("ten") or db_p.get("ten", "")
            loai_hinh = p.get("loai_hinh") or db_p.get("loai_hinh", "")

        if i > 0:
            prev = final_places[i - 1]
            dist_km = calc_dist(prev, p)
            travel_time = get_travel_minutes(prev["id"], pid, dist_km, global_matrix, k_weather, vehicle_type, simulated_clock)
            simulated_clock += timedelta(minutes=travel_time)
            prev["travel_to_next"] = round(travel_time, 1)
            prev["distance_to_next"] = round(dist_km, 1)

        p_open = datetime.combine(base_date, p_open_time)
        p_close = datetime.combine(base_date, p_close_time)
        wait_time = 0
        if simulated_clock < p_open:
            wait_time = (p_open - simulated_clock).total_seconds() / 60
            simulated_clock = p_open

        arrive_time_str = simulated_clock.strftime("%H:%M")
        if simulated_clock > p_close and pid != "gps_current":
            warnings.append(f"Địa điểm '{ten}' đã qua giờ đóng cửa ({p_close_time.strftime('%H:%M')}) khi bạn đến ({arrive_time_str}).")

        visit_time = custom_times.get(pid, p.get("visit_time", def_visit))
        simulated_clock += timedelta(minutes=visit_time)
        depart_time_str = simulated_clock.strftime("%H:%M")

        if simulated_clock > p_close and pid != "gps_current":
            warnings.append(f"Thời gian tham quan '{ten}' kéo dài tới {depart_time_str}, sau giờ đóng cửa ({p_close_time.strftime('%H:%M')}).")

        final_places.append({
            "id": pid,
            "ten": ten,
            "lat": p.get("lat") or db_p.get("vi_do") or db_p.get("lat", 0),
            "lon": p.get("lon") or db_p.get("kinh_do") or db_p.get("lon", 0),
            "loai_hinh": loai_hinh,
            "mo_ta": p.get("mo_ta") or db_p.get("mo_ta", ""),
            "thong_tin_chi_tiet": p.get("thong_tin_chi_tiet") or db_p.get("thong_tin_chi_tiet", ""),
            "review": p.get("review") or db_p.get("review", ""),
            "phu_hop": p.get("phu_hop") or db_p.get("phu_hop", ""),
            "url_hinh_anh": p.get("url_hinh_anh") or db_p.get("url_hinh_anh", ""),
            "diem_gia_tri": p.get("diem_gia_tri") or db_p.get("diem_gia_tri"),
            "rating": p.get("rating") or db_p.get("rating") or db_p.get("diem_gia_tri"),
            "open_time": p_open_time,
            "close_time": p_close_time,
            "visit_time": round(visit_time, 1),
            "wait_time": round(wait_time, 1),
            "arrive_time": arrive_time_str,
            "depart_time": depart_time_str,
            "travel_to_next": 0,
            "distance_to_next": 0,
        })

    if simulated_clock > clock_end:
        warnings.append(f"Lộ trình kết thúc lúc {simulated_clock.strftime('%H:%M')}, muộn hơn giờ kết thúc dự kiến ({et}).")

    total_actual = (simulated_clock - clock_start).total_seconds() / 60
    travel_total = sum(p["travel_to_next"] for p in final_places)
    visit_total = sum(p["visit_time"] for p in final_places)
    wait_total = sum(p["wait_time"] for p in final_places)
    dist_total = sum(p["distance_to_next"] for p in final_places)

    current_route = session.current_itinerary or {}
    route_id = session.selected_route_id or current_route.get("route_id", "route_1")
    theme = current_route.get("theme", "diverse")
    name = current_route.get("name") or current_route.get("route_name", "Lộ trình tuỳ chỉnh")

    new_itinerary = {
        "route_id": route_id,
        "name": name,
        "route_name": name,
        "theme": theme,
        "theme_label": ROUTE_THEMES.get(theme, {}).get("label", theme),
        "description": ai_advice or current_route.get("description", "Lộ trình đã được điều chỉnh theo yêu cầu của bạn."),
        "places": final_places,
        "timeline": build_timeline(final_places),
        "total_duration": round(total_actual, 1),
        "total_time_minutes": round(total_actual, 1),
        "travel_time": round(travel_total, 1),
        "visit_time": round(visit_total, 1),
        "wait_time": round(wait_total, 1),
        "distance_km": round(dist_total, 1),
        "place_count": len(final_places),
        "optimized_route": final_places,
        "is_customized": True,
    }
    session.set_current_itinerary(new_itinerary)
    return new_itinerary, warnings


def find_replacement_candidates(session, place_id: str, limit: int = 5) -> List[dict]:
    """Tìm ứng viên thay thế phù hợp: cùng loại hình, gần đó, còn mở cửa, điểm đánh giá tốt."""
    vehicle_type = session.params.get("vehicle_type", "xe_may")
    max_access = VEHICLE_ACCESS_LEVEL.get(vehicle_type, 3)

    all_db_points = fetch_all_points(vehicle_type=vehicle_type)
    current_places = (session.current_itinerary or {}).get("places", [])
    current_ids = set(session.current_place_ids())

    target_idx = -1
    target_place = None
    for idx, p in enumerate(current_places):
        if str(p.get("id")) == str(place_id):
            target_idx = idx
            target_place = p
            break

    if not target_place:
        for p in all_db_points:
            if str(p["id"]) == str(place_id):
                target_place = p
                break

    if not target_place:
        return []

    target_category = _strip_diacritics(target_place.get("loai_hinh") or "")
    prev_anchor = current_places[target_idx - 1] if target_idx > 0 else None
    next_anchor = current_places[target_idx + 1] if (0 <= target_idx < len(current_places) - 1) else None

    arr_str = target_place.get("arrive_time")
    arr_h = 12.0
    if arr_str:
        try:
            ah, am = map(int, arr_str.split(":"))
            arr_h = ah + am / 60.0
        except Exception:
            pass

    candidates = []
    for p in all_db_points:
        pid = str(p["id"])
        if pid == str(place_id) or pid in current_ids or pid in session.excluded_ids:
            continue
        if p.get("cap_do_tiep_can", 3) > max_access:
            continue

        p_cat = _strip_diacritics(p.get("loai_hinh") or "")
        cat_match = (p_cat == target_category)
        is_open = _is_open_during(p, arr_h, arr_h + 1.0)

        dist = 0.0
        anchors_count = 0
        if prev_anchor and prev_anchor.get("lat"):
            dist += calc_dist(prev_anchor, p)
            anchors_count += 1
        if next_anchor and next_anchor.get("lat"):
            dist += calc_dist(p, next_anchor)
            anchors_count += 1
        if anchors_count > 0:
            dist /= anchors_count

        score = _score_of(p)
        rank_val = (10.0 if cat_match else 2.0) + score * 2.0 + (5.0 if is_open else -10.0) - dist * 0.8

        candidates.append({
            "id": str(p["id"]),
            "ten": p["ten"],
            "loai_hinh": p.get("loai_hinh", ""),
            "lat": p["lat"],
            "lon": p["lon"],
            "rating": p.get("rating") or p.get("diem_gia_tri"),
            "diem_gia_tri": p.get("diem_gia_tri"),
            "review": p.get("review", ""),
            "url_hinh_anh": p.get("url_hinh_anh", ""),
            "distance_km": round(dist, 1),
            "open_time": p.get("open_time").strftime("%H:%M") if p.get("open_time") else None,
            "close_time": p.get("close_time").strftime("%H:%M") if p.get("close_time") else None,
            "same_category": cat_match,
            "is_open": is_open,
            "_rank": rank_val,
        })

    candidates.sort(key=lambda c: -c["_rank"])
    for c in candidates:
        c.pop("_rank", None)
    return candidates[:limit]


@router.post("/api/itinerary/quick-action")
async def quick_action(req: QuickActionRequest):
    """4 nút nhanh trên màn hình lộ trình — xử lý trực tiếp không qua LLM, kết quả chuẩn xác và ổn định."""
    if req.action not in QUICK_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Hành động không hợp lệ: {req.action}")

    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")

    places = list((session.current_itinerary or {}).get("places", []))
    non_gps = [p for p in places if p.get("id") != "gps_current"]
    if not non_gps:
        return {
            "status": "no_change",
            "session_id": session.session_id,
            "message": "Chưa có lộ trình để chỉnh sửa. Bạn hãy chọn một lộ trình trước nhé."
        }

    if req.action in _QUICK_CATEGORY:
        chosen = _pick_place_to_add(session, _QUICK_CATEGORY[req.action], lunch=(req.action == "add_lunch"))
        if chosen is None:
            what = "quán ăn trưa" if req.action == "add_lunch" else "quán cà phê"
            return {
                "status": "no_change",
                "session_id": session.session_id,
                "message": f"Mình chưa tìm được {what} phù hợp trong khu vực để thêm vào lộ trình này."
            }

        # Tìm vị trí chèn tối ưu
        best_idx = len(places)
        min_extra = float("inf")
        start_idx = 1 if (places and places[0].get("id") == "gps_current") else 0
        for i in range(start_idx, len(places)):
            p_prev = places[i - 1] if i > 0 else places[0]
            p_next = places[i]
            d = calc_dist(p_prev, chosen) + calc_dist(chosen, p_next) - calc_dist(p_prev, p_next)
            if d < min_extra:
                min_extra = d
                best_idx = i
        places.insert(best_idx, chosen)
        message = f"Mình đã thêm {chosen['ten']} vào lịch trình của bạn."
    else:
        if len(non_gps) <= 2:
            return {
                "status": "no_change",
                "session_id": session.session_id,
                "message": "Lịch trình chỉ còn 2 địa điểm, mình không bớt thêm nữa để chuyến đi không quá trống nhé."
            }
        # Bớt điểm có giá trị thấp nhất
        victim = min(reversed(non_gps), key=_score_of)
        places = [p for p in places if str(p.get("id")) != str(victim["id"])]
        session.exclude([victim["id"]])
        if req.action == "slow_down":
            message = f"Để chuyến đi thong thả hơn, mình đã bớt {victim['ten']}."
        else:
            message = f"Mình đã bớt {victim['ten']} cho lịch trình nhẹ hơn."

    updated_route, warnings = recalculate_route_from_places(session, places, ai_advice=message)
    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "message": message,
        "warnings": warnings,
        "itinerary": updated_route,
        "routes": [updated_route],
    }


@router.post("/api/itinerary/reorder")
@router.post("/api/itinerary/places/reorder")
async def reorder_places(req: ItineraryReorderRequest):
    """ĐỔI THỨ TỰ ĐỊA ĐIỂM (kéo thả). Chỉ cập nhật route đang được chọn."""
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not session.current_itinerary:
        raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn.")

    current_places = (session.current_itinerary or {}).get("places", [])
    place_map = {str(p["id"]): p for p in current_places}
    ordered_ids = [str(i) for i in req.ordered_place_ids]
    new_places = []

    if current_places and current_places[0].get("id") == "gps_current" and "gps_current" not in ordered_ids:
        new_places.append(current_places[0])

    for pid in ordered_ids:
        if pid in place_map and pid != "gps_current":
            new_places.append(place_map[pid])

    for p in current_places:
        pid = str(p.get("id"))
        if pid != "gps_current" and pid not in ordered_ids:
            new_places.append(p)

    if len(new_places) < 2:
        raise HTTPException(status_code=400, detail="Lộ trình phải có ít nhất 2 địa điểm.")

    advice = req.ai_advice or "Đã cập nhật lại thứ tự các địa điểm theo yêu cầu của bạn."
    updated_route, warnings = recalculate_route_from_places(session, new_places, ai_advice=advice)

    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "message": advice,
        "warnings": warnings,
        "itinerary": updated_route,
        "routes": [updated_route],
    }


@router.post("/api/itinerary/replace")
@router.post("/api/itinerary/places/replace")
async def replace_place(req: ItineraryReplaceRequest):
    """THAY THẾ ĐỊA ĐIỂM (cũ -> mới). Tự động tìm ứng viên nếu không truyền new_place_id."""
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not session.current_itinerary:
        raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn để thay thế.")

    places = list((session.current_itinerary or {}).get("places", []))
    old_id = str(req.old_place_id)
    target_idx = next((i for i, p in enumerate(places) if str(p.get("id")) == old_id), None)
    if target_idx is None:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy địa điểm {old_id} trong lộ trình hiện tại.")
    if old_id == "gps_current":
        raise HTTPException(status_code=400, detail="Không thể thay thế điểm xuất phát GPS.")

    new_id = str(req.new_place_id) if req.new_place_id else None
    if not new_id:
        candidates = find_replacement_candidates(session, old_id, limit=1)
        if not candidates:
            return {
                "status": "no_change",
                "session_id": session.session_id,
                "message": "Không tìm thấy địa điểm phù hợp nào khác để thay thế điểm này."
            }
        new_place_data = candidates[0]
        new_id = new_place_data["id"]
    else:
        db_pts = fetch_all_points(vehicle_type=session.params.get("vehicle_type", "xe_may"))
        new_place_data = next((p for p in db_pts if str(p["id"]) == new_id), None)
        if not new_place_data:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy thông tin địa điểm mới (id={new_id}).")

    old_place_name = places[target_idx].get("ten", old_id)
    new_place_name = new_place_data.get("ten", new_id)
    places[target_idx] = new_place_data

    session.exclude([old_id])
    session.restore([new_id])

    advice = req.ai_advice or f"Đã thay thế '{old_place_name}' bằng '{new_place_name}'."
    updated_route, warnings = recalculate_route_from_places(session, places, ai_advice=advice)

    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "replaced_old_id": old_id,
        "replaced_new_id": new_id,
        "message": advice,
        "warnings": warnings,
        "itinerary": updated_route,
        "routes": [updated_route],
    }


@router.get("/api/itinerary/replacement-candidates")
@router.get("/api/itinerary/places/candidates")
async def get_replacement_candidates(session_id: str, place_id: str, limit: int = 5):
    """LẤY DANH SÁCH ỨNG VIÊN THAY THẾ cho một địa điểm cụ thể."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    candidates = find_replacement_candidates(session, place_id, limit=limit)
    return {
        "status": "success",
        "session_id": session.session_id,
        "place_id": place_id,
        "candidates": candidates,
    }


@router.post("/api/itinerary/change-time")
@router.post("/api/itinerary/places/change-time")
async def change_time(req: ItineraryChangeTimeRequest):
    """ĐỔI THỜI GIAN: thời gian tham quan tại 1 điểm hoặc khung giờ xuất phát / kết thúc."""
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not session.current_itinerary:
        raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn.")

    current_places = list((session.current_itinerary or {}).get("places", []))
    if not current_places:
        raise HTTPException(status_code=400, detail="Lộ trình trống.")

    custom_times = {}
    if req.place_id and req.custom_visit_time is not None:
        pid = str(req.place_id)
        custom_times[pid] = max(5.0, float(req.custom_visit_time))

    if req.start_time:
        session.params["start_time"] = req.start_time
    if req.end_time:
        session.params["end_time"] = req.end_time

    advice = req.ai_advice or "Đã điều chỉnh lại thời gian và tính toán lại lịch trình."
    updated_route, warnings = recalculate_route_from_places(
        session, current_places,
        start_time_str=req.start_time,
        end_time_str=req.end_time,
        ai_advice=advice,
        custom_visit_times=custom_times
    )

    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "message": advice,
        "warnings": warnings,
        "itinerary": updated_route,
        "routes": [updated_route],
    }


@router.post("/api/itinerary/add")
@router.post("/api/itinerary/places/add")
async def add_place_to_itinerary(req: ItineraryAddPlaceRequest):
    """THÊM ĐỊA ĐIỂM vào lộ trình hiện tại."""
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not session.current_itinerary:
        raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn.")

    current_places = list((session.current_itinerary or {}).get("places", []))
    new_pid = str(req.place_id)

    if any(str(p.get("id")) == new_pid for p in current_places):
        return {
            "status": "no_change",
            "session_id": session.session_id,
            "message": "Địa điểm này đã có sẵn trong lộ trình hiện tại của bạn.",
            "itinerary": session.current_itinerary,
        }

    db_pts = fetch_all_points(vehicle_type=session.params.get("vehicle_type", "xe_may"))
    new_place_data = next((p for p in db_pts if str(p["id"]) == new_pid), None)
    if not new_place_data:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy địa điểm id={new_pid}.")

    if req.custom_visit_time is not None:
        new_place_data["visit_time"] = max(5.0, float(req.custom_visit_time))

    if req.insert_after_id:
        after_id = str(req.insert_after_id)
        ins_idx = next((i + 1 for i, p in enumerate(current_places) if str(p.get("id")) == after_id), len(current_places))
        current_places.insert(ins_idx, new_place_data)
    else:
        best_idx = len(current_places)
        min_extra_dist = float("inf")
        start_idx = 1 if (current_places and current_places[0].get("id") == "gps_current") else 0
        for i in range(start_idx, len(current_places)):
            p_prev = current_places[i - 1] if i > 0 else current_places[0]
            p_next = current_places[i]
            d_extra = calc_dist(p_prev, new_place_data) + calc_dist(new_place_data, p_next) - calc_dist(p_prev, p_next)
            if d_extra < min_extra_dist:
                min_extra_dist = d_extra
                best_idx = i
        current_places.insert(best_idx, new_place_data)

    session.restore([new_pid])
    advice = req.ai_advice or f"Đã thêm '{new_place_data.get('ten', new_pid)}' vào lộ trình."
    updated_route, warnings = recalculate_route_from_places(session, current_places, ai_advice=advice)

    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "message": advice,
        "warnings": warnings,
        "itinerary": updated_route,
        "routes": [updated_route],
    }


@router.post("/api/itinerary/remove")
@router.post("/api/itinerary/places/remove")
async def remove_place_from_itinerary(req: ItineraryRemovePlaceRequest):
    """XOÁ ĐỊA ĐIỂM khỏi lộ trình hiện tại."""
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not session.current_itinerary:
        raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn.")

    current_places = list((session.current_itinerary or {}).get("places", []))
    pid = str(req.place_id)
    if pid == "gps_current":
        raise HTTPException(status_code=400, detail="Không thể xoá điểm xuất phát.")

    target = next((p for p in current_places if str(p.get("id")) == pid), None)
    if not target:
        raise HTTPException(status_code=404, detail=f"Không tìm thấy địa điểm {pid} trong lộ trình.")

    non_gps = [p for p in current_places if p.get("id") != "gps_current"]
    if len(non_gps) <= 2:
        return {
            "status": "no_change",
            "session_id": session.session_id,
            "message": "Lộ trình chỉ còn 2 địa điểm, không thể bớt thêm.",
            "itinerary": session.current_itinerary,
        }

    current_places = [p for p in current_places if str(p.get("id")) != pid]
    if req.exclude_from_session:
        session.exclude([pid])

    advice = req.ai_advice or f"Đã bớt '{target.get('ten', pid)}' khỏi lộ trình."
    updated_route, warnings = recalculate_route_from_places(session, current_places, ai_advice=advice)

    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "message": advice,
        "warnings": warnings,
        "itinerary": updated_route,
        "routes": [updated_route],
    }
