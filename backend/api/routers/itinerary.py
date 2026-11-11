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

from api.db import fetch_all_points, V2_PUBLIC_KEYS
from api.diversity import build_timeline
from api.factors import VEHICLE_ACCESS_LEVEL, get_weather_factor
import re
from api.naming import ROUTE_THEMES, _fmt_minutes
from api.osrm_matrix import get_global_osrm_matrix
from api.preferences import _strip_diacritics
from api.scheduling import calc_dist, get_travel_minutes
from itinerary_store import store

router = APIRouter()

QUICK_ACTIONS = {"add_lunch", "add_cafe", "remove_one", "slow_down"}
_QUICK_CATEGORY = {"add_lunch": "an uong", "add_cafe": "cafe"}


class ChangeVehicleRequest(BaseModel):
    session_id: str
    vehicle_type: str


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
    visit_time: Optional[float] = None
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
    for p in fetch_all_points(vehicle_type=vehicle, trip_date=session.params.get("trip_date")):
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

"""ham tro giup chen vi tri toi uu"""
def _find_best_insert_index(places: List[dict], new_place: dict) -> int:
    """Tìm vị trí chèn tối ưu (đầu, giữa, hoặc cuối lộ trình) giúp tăng quãng đường ít nhất."""
    if not places:
        return 0
    has_gps = places[0].get("id") == "gps_current"
    if len(places) == 1:
        return 1

    best_idx = len(places)
    min_extra = float("inf")

    # 1. Xét chèn vào đầu lộ trình (nếu không có điểm GPS cố định ở đầu)
    if not has_gps:
        d_first = calc_dist(new_place, places[0])
        if d_first < min_extra:
            min_extra = d_first
            best_idx = 0

    # 2. Xét chèn vào giữa hai điểm (i-1) và i
    for i in range(1, len(places)):
        p_prev = places[i - 1]
        p_next = places[i]
        d_extra = calc_dist(p_prev, new_place) + calc_dist(new_place, p_next) - calc_dist(p_prev, p_next)
        if d_extra < min_extra:
            min_extra = d_extra
            best_idx = i

    # 3. Xét chèn vào cuối lộ trình (sau điểm cuối cùng)
    d_last = calc_dist(places[-1], new_place)
    if d_last < min_extra:
        min_extra = d_last
        best_idx = len(places)

    return best_idx

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

    db_points_list = fetch_all_points(vehicle_type=vehicle_type, trip_date=session.params.get("trip_date"))
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
            if wait_time >= 30 and pid != "gps_current":
                warnings.append(f"Bạn sẽ phải chờ {int(wait_time)} phút vì '{ten}' mở cửa lúc {p_open_time.strftime('%H:%M')}.")
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
            **{k: (p.get(k) if p.get(k) not in (None, "", []) else db_p.get(k)) for k in V2_PUBLIC_KEYS if k != "rating"},
            "open_time": p_open_time,
            "close_time": p_close_time,
            "gio_mo_cua": p_open_time.strftime("%H:%M") if hasattr(p_open_time, "strftime") else str(p_open_time)[:5],
            "gio_dong_cua": p_close_time.strftime("%H:%M") if hasattr(p_close_time, "strftime") else str(p_close_time)[:5],
            "visit_time": round(visit_time, 1),
            "wait_time": round(wait_time, 1),
            "arrive_time": arrive_time_str,
            "depart_time": depart_time_str,
            "travel_to_next": 0,
            "distance_to_next": 0,
        })

    available_minutes = max(0.0, (clock_end - clock_start).total_seconds() / 60.0)
    if available_minutes < 30:
        warnings.append(f"Quỹ thời gian chuyến đi quá ngắn ({round(available_minutes)} phút).")

    overtime_minutes = 0.0
    if simulated_clock > clock_end:
        overtime_minutes = round((simulated_clock - clock_end).total_seconds() / 60.0, 1)
        warnings.append(f"Lộ trình kết thúc lúc {simulated_clock.strftime('%H:%M')}, muộn hơn giờ kết thúc dự kiến ({et}) {overtime_minutes} phút.")

    total_actual = (simulated_clock - clock_start).total_seconds() / 60
    travel_total = sum(p["travel_to_next"] for p in final_places)
    visit_total = sum(p["visit_time"] for p in final_places)
    wait_total = sum(p["wait_time"] for p in final_places)
    dist_total = sum(p["distance_to_next"] for p in final_places)

    current_route = session.current_itinerary or {}
    route_id = session.selected_route_id or current_route.get("route_id", "route_1")
    theme = current_route.get("theme", "diverse")
    name = current_route.get("name") or current_route.get("route_name", "Lộ trình tuỳ chỉnh")
    if " · Thong thả " in name:
        name = re.sub(r" · Thong thả .*", "", name) + f" · Thong thả {_fmt_minutes(total_actual)}"

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
        "is_feasible": len(warnings) == 0,
        "overtime_minutes": overtime_minutes,
        "warnings": warnings,
    }
    session.set_current_itinerary(new_itinerary)
    return new_itinerary, warnings


def _get_semantic_cluster(cat: str) -> str:
    c = _strip_diacritics(cat or "").lower().strip()
    if any(k in c for k in ["tham quan", "checkin", "check in", "di tich", "van hoa", "lich su", "chua", "den", "lang"]):
        return "sightseeing"
    if any(k in c for k in ["an uong", "am thuc", "nha hang", "quan an", "pho", "bun", "banh"]):
        return "food"
    if any(k in c for k in ["cafe", "ca phe", "coffee", "tra"]):
        return "cafe"
    if any(k in c for k in ["tttm", "mua sam", "giai tri", "rap"]):
        return "entertainment"
    return "other"


def find_replacement_candidates(
    session,
    place_id: str,
    limit: int = 15,
    category: Optional[str] = None,
    query: Optional[str] = None,
    same_category_only: bool = False,
) -> List[dict]:
    """Tìm ứng viên thay thế phù hợp: cùng loại hình, gần đó, còn mở cửa, điểm đánh giá tốt, tính toán độ lệch lộ trình."""
    vehicle_type = session.params.get("vehicle_type", "xe_may")
    max_access = VEHICLE_ACCESS_LEVEL.get(vehicle_type, 3)

    all_db_points = fetch_all_points(vehicle_type=vehicle_type, trip_date=session.params.get("trip_date"))
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

    target_cat_raw = target_place.get("loai_hinh") or ""
    target_category = _strip_diacritics(target_cat_raw).lower().strip()
    target_cluster = _get_semantic_cluster(target_category)

    target_lat = target_place.get("vi_do") or target_place.get("lat")
    target_lon = target_place.get("kinh_do") or target_place.get("lon")
    target_pt = {"lat": target_lat, "lon": target_lon} if (target_lat and target_lon) else None

    prev_anchor = current_places[target_idx - 1] if target_idx > 0 else None
    next_anchor = current_places[target_idx + 1] if (0 <= target_idx < len(current_places) - 1) else None

    # Đoạn đường cũ nối prev_anchor -> target_place -> next_anchor
    orig_segment_dist = 0.0
    if prev_anchor and prev_anchor.get("lat") and target_pt:
        orig_segment_dist += calc_dist(prev_anchor, target_pt)
    if next_anchor and next_anchor.get("lat") and target_pt:
        orig_segment_dist += calc_dist(target_pt, next_anchor)

    arr_str = target_place.get("arrive_time")
    arr_h = 12.0
    if arr_str:
        try:
            ah, am = map(int, arr_str.split(":"))
            arr_h = ah + am / 60.0
        except Exception:
            pass

    clean_query = _strip_diacritics(query or "").lower().strip()
    filter_cat = _strip_diacritics(category or "").lower().strip() if category else None

    candidates = []
    for p in all_db_points:
        pid = str(p["id"])
        if pid == str(place_id) or pid in current_ids or pid in session.excluded_ids:
            continue
        if p.get("cap_do_tiep_can", 3) > max_access:
            continue

        p_cat_raw = p.get("loai_hinh") or ""
        p_cat = _strip_diacritics(p_cat_raw).lower().strip()
        p_cluster = _get_semantic_cluster(p_cat)

        cat_match = (p_cat == target_category)
        cluster_match = (p_cluster == target_cluster)

        # Bộ lọc cùng loại hình hoặc danh mục chỉ định
        if same_category_only and not (cat_match or cluster_match):
            continue

        if filter_cat:
            if filter_cat in ["same", "cung_loai", "cung_loai_hinh"]:
                if not (cat_match or cluster_match):
                    continue
            else:
                if filter_cat not in p_cat:
                    continue

        if clean_query:
            p_name_clean = _strip_diacritics(p.get("ten", "")).lower()
            p_addr_clean = _strip_diacritics(p.get("dia_chi", "")).lower()
            p_rev_clean = _strip_diacritics(p.get("review", "")).lower()
            if clean_query not in p_name_clean and clean_query not in p_addr_clean and clean_query not in p_rev_clean:
                continue

        cand_pt = {"lat": p.get("vi_do") or p.get("lat"), "lon": p.get("kinh_do") or p.get("lon")}
        cand_segment_dist = 0.0
        if prev_anchor and prev_anchor.get("lat") and cand_pt.get("lat"):
            cand_segment_dist += calc_dist(prev_anchor, cand_pt)
        if next_anchor and next_anchor.get("lat") and cand_pt.get("lat"):
            cand_segment_dist += calc_dist(cand_pt, next_anchor)

        delta_dist_km = round(cand_segment_dist - orig_segment_dist, 1) if (prev_anchor or next_anchor) else 0.0
        dist_from_target = round(calc_dist(target_pt, cand_pt), 1) if (target_pt and cand_pt.get("lat")) else 0.0
        delta_time_min = int(round((delta_dist_km / 20.0) * 60))

        cand_visit_time = p.get("time") or p.get("thoi_gian_tham_quan_phut") or 45
        is_open = _is_open_during(p, arr_h, arr_h + (cand_visit_time / 60.0))

        if cat_match:
            match_type = "exact"
            match_label = f"Cùng loại hình ({p_cat_raw})"
            cat_bonus = 60.0
        elif cluster_match:
            match_type = "compatible"
            match_label = f"Tương đương ({p_cat_raw})"
            cat_bonus = 40.0
        else:
            match_type = "alternative"
            match_label = f"Gợi ý khác ({p_cat_raw})"
            cat_bonus = 0.0

        score = _score_of(p)
        detour_pen = max(0.0, delta_dist_km) * 2.0
        rank_val = cat_bonus + (15.0 if is_open else -30.0) + score * 4.0 - detour_pen - dist_from_target * 1.0

        candidates.append({
            "id": str(p["id"]),
            "ten": p["ten"],
            "loai_hinh": p_cat_raw,
            "lat": p["lat"],
            "lon": p["lon"],
            "rating": p.get("rating") or p.get("diem_gia_tri"),
            **{k: p.get(k) for k in V2_PUBLIC_KEYS if k != "rating"},
            "diem_gia_tri": p.get("diem_gia_tri"),
            "review": p.get("review", ""),
            "thong_tin_chi_tiet": p.get("thong_tin_chi_tiet", ""),
            "phu_hop": p.get("phu_hop", ""),
            "url_hinh_anh": p.get("url_hinh_anh", ""),
            "distance_km": dist_from_target,
            "dist_from_target_km": dist_from_target,
            "delta_dist_km": delta_dist_km,
            "delta_time_min": delta_time_min,
            "time": cand_visit_time,
            "open_time": p.get("open_time").strftime("%H:%M") if p.get("open_time") else None,
            "close_time": p.get("close_time").strftime("%H:%M") if p.get("close_time") else None,
            "same_category": cat_match,
            "compatible_category": cluster_match,
            "match_type": match_type,
            "match_label": match_label,
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
        best_idx = _find_best_insert_index(places, chosen)
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
        if session.route_order:
            session.select_route(session.route_order[0])
        else:
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
        db_pts = fetch_all_points(vehicle_type=session.params.get("vehicle_type", "xe_may"), trip_date=session.params.get("trip_date"))
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
async def get_replacement_candidates(
    session_id: str,
    place_id: str,
    limit: int = 15,
    category: Optional[str] = None,
    query: Optional[str] = None,
    same_category_only: bool = False,
):
    """LẤY DANH SÁCH ỨNG VIÊN THAY THẾ cho một địa điểm cụ thể, hỗ trợ lọc danh mục và tìm kiếm."""
    session = store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")

    current_places = (session.current_itinerary or {}).get("places", [])
    target_place = next((p for p in current_places if str(p.get("id")) == str(place_id)), None)
    if not target_place:
        all_pts = fetch_all_points(vehicle_type=session.params.get("vehicle_type", "xe_may"), trip_date=session.params.get("trip_date"))
        target_place = next((p for p in all_pts if str(p.get("id")) == str(place_id)), None)

    candidates = find_replacement_candidates(
        session,
        place_id,
        limit=limit,
        category=category,
        query=query,
        same_category_only=same_category_only,
    )
    return {
        "status": "success",
        "session_id": session.session_id,
        "place_id": place_id,
        "target_place": {
            "id": str(target_place.get("id")) if target_place else place_id,
            "ten": target_place.get("ten") if target_place else "",
            "loai_hinh": target_place.get("loai_hinh") if target_place else "",
            "time": target_place.get("time") or target_place.get("thoi_gian_tham_quan_phut") or 45 if target_place else 45,
            "arrive_time": target_place.get("arrive_time") if target_place else None,
            "leave_time": target_place.get("leave_time") if target_place else None,
            "url_hinh_anh": target_place.get("url_hinh_anh") if target_place else None,
        } if target_place else None,
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
        if session.route_order:
            session.select_route(session.route_order[0])
        else:
            raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn.")

    current_places = list((session.current_itinerary or {}).get("places", []))
    if not current_places:
        raise HTTPException(status_code=400, detail="Lộ trình trống.")

    custom_times = {}
    v_time = req.custom_visit_time if req.custom_visit_time is not None else req.visit_time
    if req.place_id and v_time is not None:
        if v_time < 10.0 or v_time > 480.0:
            raise HTTPException(
                status_code=400,
                detail=f"Thời gian tham quan tùy chỉnh phải nằm trong khoảng từ 10 đến 480 phút (nhận {v_time} phút)."
            )
        pid = str(req.place_id)
        custom_times[pid] = float(v_time)

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
        if session.route_order:
            session.select_route(session.route_order[0])
        else:
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

    db_pts = fetch_all_points(vehicle_type=session.params.get("vehicle_type", "xe_may"), trip_date=session.params.get("trip_date"))
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
        # Sử dụng hàm _find_best_insert_index đã định nghĩa để tìm vị trí tối ưu (đầu, giữa hoặc cuối)
        best_idx = _find_best_insert_index(current_places, new_place_data)
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
        if session.route_order:
            session.select_route(session.route_order[0])
        else:
            raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn.")

    current_places = list((session.current_itinerary or {}).get("places", []))
    pid = str(req.place_id)
    if pid == "gps_current":
        raise HTTPException(status_code=400, detail="Không thể xoá điểm xuất phát.")

    target = next((p for p in current_places if str(p.get("id")) == pid), None)
    if not target:
        if pid in session.excluded_ids:
            return {
                "status": "no_change",
                "session_id": session.session_id,
                "selected_route_id": session.selected_route_id,
                "message": "Địa điểm này đã được xóa khỏi lộ trình.",
                "warnings": [],
                "itinerary": session.current_itinerary,
                "routes": [session.current_itinerary],
            }
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

class ItineraryReoptimizeRequest(BaseModel):
    session_id: str
    ai_advice: Optional[str] = None


@router.post("/api/itinerary/reoptimize")
@router.post("/api/itinerary/places/reoptimize")
async def reoptimize_itinerary(req: ItineraryReoptimizeRequest):
    """
    TỐI ƯU HOÁ LẠI LỘ TRÌNH HIỆN TẠI (Giai đoạn 4):
    Giữ nguyên các địa điểm trong route đang chọn nhưng sắp xếp lại thứ tự
    để ưu tiên kịp giờ mở cửa, giảm thời gian chờ và giảm quãng đường di chuyển.
    """
    session = store.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if not session.current_itinerary:
        raise HTTPException(status_code=400, detail="Chưa có lộ trình nào được chọn để tối ưu lại.")

    current_places = list((session.current_itinerary or {}).get("places", []))
    if len(current_places) <= 2:
        updated_route, warnings = recalculate_route_from_places(
            session, current_places, ai_advice=req.ai_advice or "Lộ trình đã ở trạng thái tối ưu."
        )
        return {
            "status": "success",
            "session_id": session.session_id,
            "selected_route_id": session.selected_route_id,
            "message": "Lộ trình ít điểm nên giữ nguyên thứ tự hiện tại.",
            "warnings": warnings,
            "itinerary": updated_route,
            "routes": [updated_route],
        }

    has_gps = current_places[0].get("id") == "gps_current"
    fixed_start = [current_places[0]] if has_gps else []
    pool = current_places[1:] if has_gps else list(current_places)

    # Giữ lại custom visit_time của từng điểm trước khi sắp xếp lại
    saved_visit_times = {
        str(p.get("id")): p.get("visit_time")
        for p in current_places
        if p.get("visit_time") is not None
    }

    # Thuật toán Greedy Time-Window + Nearest Neighbor để tìm thứ tự tối ưu
    ordered = list(fixed_start)
    remaining = list(pool)

    # Nếu không có GPS, chọn điểm mở cửa sớm nhất hoặc điểm đầu tiên hiện tại làm mốc xuất phát
    if not ordered and remaining:
         remaining.sort(
             key=lambda p: (
                 str(p.get("gio_mo_cua") or "08:00"),
                 -_score_of(p),
             )
         )
         ordered.append(remaining.pop(0))

    while remaining:
        last_p = ordered[-1]
        def _next_cost(cand):
            dist = calc_dist(last_p, cand)
            close_str = str(cand.get("gio_dong_cua") or "23:59")
            try:
                ch, cm = map(int, close_str[:5].split(":"))
                close_h = ch + cm / 60.0
            except Exception:
                close_h = 23.9
            # Ưu tiên điểm gần + đóng cửa sớm hơn để không bị lỡ giờ
            return dist * 1.5 + close_h * 0.2

        best_next = min(remaining, key=_next_cost)
        ordered.append(best_next)
        remaining.remove(best_next)

    # So sánh kết quả trước và sau khi re-optimize: chỉ áp dụng nếu tốt hơn hoặc ít cảnh báo hơn
    old_route, old_warnings = recalculate_route_from_places(
        session, current_places, custom_visit_times=saved_visit_times
    )
    new_route, new_warnings = recalculate_route_from_places(
        session, ordered, custom_visit_times=saved_visit_times
    )

    if len(new_warnings) > len(old_warnings) or (
        len(new_warnings) == len(old_warnings) and new_route["total_duration"] > old_route["total_duration"] + 5
    ):
        # Giữ nguyên thứ tự cũ nếu thứ tự mới không cải thiện tổng thời gian / số cảnh báo
        final_route, final_warnings = recalculate_route_from_places(
            session,
            current_places,
            ai_advice=req.ai_advice or "Thứ tự hiện tại đã là phương án tối ưu nhất cho các địa điểm bạn chọn.",
            custom_visit_times=saved_visit_times,
        )
        msg = req.ai_advice or "Thứ tự hiện tại đã tối ưu, không cần thay đổi."
    else:
        final_route, final_warnings = recalculate_route_from_places(
            session,
            ordered,
            ai_advice=req.ai_advice or "Đã tối ưu hóa lại thứ tự di chuyển và khung giờ cho lộ trình của bạn.",
            custom_visit_times=saved_visit_times,
        )
        msg = req.ai_advice or "Đã tối ưu hóa lại thứ tự các địa điểm để tiết kiệm thời gian di chuyển."

    return {
        "status": "success",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "message": msg,
        "warnings": final_warnings,
        "itinerary": final_route,
        "routes": [final_route],
    }

@router.post("/api/itinerary/change-vehicle")
def change_vehicle(req: ChangeVehicleRequest):
    """
    ĐỔI PHƯƠNG TIỆN DI CHUYỂN TRÊN MÀN HÌNH BẢN ĐỒ (BUG-003).
    Cập nhật session.params['vehicle_type'], tính lại toàn bộ ma trận thời gian OSRM,
    cập nhật timeline chặng và lưu lại vào session.
    """
    session = store.get(req.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Phiên không tồn tại hoặc đã hết hạn.")
    if req.vehicle_type not in ("xe_may", "o_to", "di_bo", "xe_buyt", "xe_dap"):
        raise HTTPException(status_code=400, detail="Phương tiện không hợp lệ.")

    session.params["vehicle_type"] = req.vehicle_type
    current_itin = session.current_itinerary
    if not current_itin:
        raise HTTPException(status_code=404, detail="Chưa có lộ trình hiện tại trong phiên.")

    places = current_itin.get("optimized_route") or current_itin.get("places") or []
    updated_route, warnings = recalculate_route_from_places(session, places)
    session.set_current_itinerary(updated_route)

    return {
        "status": "success",
        "message": f"Đã chuyển sang phương tiện {req.vehicle_type} và tính lại thời gian di chuyển.",
        "session_id": session.session_id,
        "selected_route_id": session.selected_route_id,
        "itinerary": updated_route,
        "routes": [updated_route],
        "warnings": warnings,
    }
