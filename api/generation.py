"""Classic route generation (greedy strategies + DP/2-opt finalisation)."""
from api.scheduling import DP_MAX_CANDIDATES, calc_dist, get_travel_minutes, optimize_sequence, solve_tsptw_exact
from fastapi import HTTPException
from api.schemas import OptimizationRequest
from api.naming import ROUTE_THEMES, _CULTURE_CATEGORIES, _FOOD_CATEGORIES, _fmt_minutes, generate_route_name
from api.factors import VEHICLE_ACCESS_LEVEL, VEHICLE_ACCESS_NOTE
from api.session_helpers import _ensure_unique_names
from api.preferences import _extract_keywords, calculate_preference_score
from datetime import datetime, timedelta
from api.db import fetch_all_points
from api.diversity import format_route_object, select_diverse_routes
from api import osrm_matrix, factors
import random

def run_route_generation(request: OptimizationRequest,
                         excluded_ids: set = None,
                         must_visit_ids: set = None,
                         force_visit_ids: set = None,
                         mode: str = "multi"):
    """
    LÕI SINH LỘ TRÌNH — dùng chung cho mọi endpoint (mục 10, 11).

    Trước đây toàn bộ logic này nằm trực tiếp trong @app.post("/api/optimize-route"),
    nên không endpoint nào khác (chọn route, cập nhật itinerary, chatbot) tái sử
    dụng được — mỗi luồng lại tự gọi lại HTTP và sinh lại route mới. Tách ra thành
    hàm thuần để:
      * /api/routes            → mode="multi"  : sinh 3–5 route candidate
      * /api/itinerary/update  → mode="single" : tính lại đúng MỘT lịch trình
      * /api/optimize-route    → giữ nguyên hành vi cũ cho frontend hiện tại

    excluded_ids : RÀNG BUỘC CỨNG. Áp ngay tại nguồn candidate pool nên mọi nhánh
                   phía sau (greedy, fill-up, DP, 2-opt) đều tự động tôn trọng
                   (mục 6, 7). KHÔNG hề đụng tới database.
    must_visit_ids: điểm phải giữ (từ AI hoặc từ itinerary hiện tại) — chỉ là ưu
                   tiên tuyệt đối trong greedy, KHÔNG giới hạn candidate pool (mục 5).
    force_visit_ids: điểm người dùng VỪA CHỦ ĐỘNG yêu cầu thêm. Ưu tiên cao hơn
                   must_visit một bậc, để khi quỹ thời gian đã gần đầy thì điểm
                   mới vẫn chen được vào và điểm auto-fill bị đẩy ra — đúng tinh
                   thần "A → B → X → D → E" của mục 9.
    """
    excluded_ids = set(excluded_ids or ())
    force_visit_ids = set(force_visit_ids or ()) - excluded_ids
    must_visit_ids = set(must_visit_ids or ()) | force_visit_ids
    # Điểm vừa bị loại thì không thể đồng thời là must-visit.
    must_visit_ids -= excluded_ids

    # 1. XỬ LÝ NGÀY KHỞI HÀNH (trip_date)
    try:
        if request.trip_date:
            base_date = datetime.strptime(request.trip_date, "%Y-%m-%d").date()
        else:
            base_date = datetime.today().date()
    except:
        base_date = datetime.today().date()

    # 2. XỬ LÝ THỜI GIAN BẮT ĐẦU / KẾT THÚC
    try:
        clock_start = datetime.strptime(request.start_time, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        clock_end = datetime.strptime(request.end_time, "%H:%M").replace(
            year=base_date.year, month=base_date.month, day=base_date.day
        )
        if clock_end <= clock_start:
            clock_end += timedelta(days=1)
        available_minutes = (clock_end - clock_start).total_seconds() / 60
    except:
        raise HTTPException(status_code=400, detail="Lỗi định dạng thời gian (start_time/end_time phải theo dạng HH:MM)")

    # 3. LẤY DANH SÁCH ĐỊA ĐIỂM TỪ DATABASE
    all_points = fetch_all_points()

    # 3b. LỌC ĐỊA ĐIỂM THEO KHẢ NĂNG TIẾP CẬN CỦA PHƯƠNG TIỆN
    max_access = VEHICLE_ACCESS_LEVEL.get(request.vehicle_type, 3)
    all_points = [p for p in all_points if p["cap_do_tiep_can"] <= max_access]
    vehicle_note = VEHICLE_ACCESS_NOTE.get(request.vehicle_type, "")

    # 3b'. ÁP RÀNG BUỘC LOẠI TRỪ NGAY TẠI NGUỒN (mục 6, 7)
    # Đây là điểm mấu chốt: lọc ở ĐÂY nghĩa là excluded_ids trở thành ràng buộc
    # thật của TOÀN BỘ pipeline phía sau — ma trận OSRM, greedy, fill-up, DP,
    # 2-opt đều không bao giờ nhìn thấy điểm đã bị loại. Không phải "ẩn ở
    # frontend", không phải "xoá khỏi một mảng tạm", và tuyệt đối không xoá dữ
    # liệu khỏi database: bản ghi vẫn nguyên trong DIA_DIEM, chỉ là phiên này
    # không được dùng nó.
    if excluded_ids:
        all_points = [p for p in all_points if p["id"] not in excluded_ids]
        if not all_points:
            return {
                "status": "error",
                "message": "Bạn đã loại quá nhiều địa điểm, không còn lựa chọn nào phù hợp.",
                "excluded_ids": sorted(excluded_ids),
            }

    # 3c. TÍNH PREFERENCE SCORE CHO TỪNG ĐIỂM (BUG 1 FIX)
    # user_preference + weight phải thực sự tham gia scoring/route generation,
    # không chỉ được nhận vào rồi bỏ qua.
    preference_keywords = _extract_keywords(request.user_preference)
    pref_weight = request.weight / 100.0  # 0 = ưu tiên khoảng cách, 1 = ưu tiên đúng sở thích
    for p in all_points:
        p["preference_score"] = calculate_preference_score(p, preference_keywords)

    # 4. LẤY MA TRẬN KHOẢNG CÁCH THEO PHƯƠNG TIỆN (vehicle_type)
    global_matrix = osrm_matrix.get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)

    # 5. XÁC ĐỊNH ĐIỂM XUẤT PHÁT (start_point)
    all_points.sort(key=lambda x: (x["score"] if x["score"] is not None else 0), reverse=True)

    if request.start_lat is not None and request.start_lon is not None:
        # TRƯỜNG HỢP 1: Người dùng dùng GPS → tạo điểm ảo "Vị trí hiện tại"

        # Khai báo thời gian hoạt động mặc định cho điểm GPS
        default_open = datetime.strptime("00:00", "%H:%M").time()
        default_close = datetime.strptime("23:59", "%H:%M").time()

        # Điểm này không có trong DB, thời gian tham quan = 0 phút
        gps_point = {
            "id": "gps_current",
            "ten": "📍 Vị trí của bạn",
            "lat": request.start_lat,
            "lon": request.start_lon,
            "time": 0,              
            "loai_hinh": "diem_xuat_phat",
            "open_time": default_open,
            "close_time": default_close,
            "mo_ta": "", "thong_tin_chi_tiet": "", "review": "", "phu_hop": "",
            "preference_score": 0.5,
        }

        all_points_with_gps = [gps_point] + all_points
        global_matrix = osrm_matrix.get_global_osrm_matrix(all_points_with_gps, vehicle_type=request.vehicle_type)
        all_points = all_points_with_gps   # Cập nhật danh sách để route dùng đúng
        starting_points = [gps_point]      # Chỉ xuất phát từ vị trí GPS

    elif request.start_point and request.start_point.strip():
        # TRƯỜNG HỢP 2: Người dùng nhập tên điểm → tìm trong DB
        keyword = request.start_point.strip().lower()
        matched_points = [p for p in all_points if keyword in p["ten"].lower()]
        if matched_points:
            starting_points = matched_points[:1]  # Chỉ xuất phát từ điểm tìm được
        else:
            # BUG 7 FIX: KHÔNG được âm thầm fallback sang all_points[:1] (điểm bất kỳ).
            # Trả lỗi rõ ràng để Frontend thông báo cho người dùng, thay vì tự ý
            # chọn một điểm xuất phát không liên quan đến địa chỉ họ đã nhập.
            raise HTTPException(
                status_code=400,
                detail=f"Không tìm thấy địa điểm xuất phát '{request.start_point}'. "
                       f"Vui lòng kiểm tra lại địa chỉ hoặc sử dụng định vị GPS."
            )

    else:
        # TRƯỜNG HỢP 3: Không nhập gì (không GPS, không tên) → KHÔNG được tự ý
        # chọn đại điểm xuất phát. Đây cũng là fallback nguy hiểm giống BUG 7,
        # nên áp dụng cùng nguyên tắc: phải có GPS hoặc địa chỉ hợp lệ mới chạy.
        raise HTTPException(
            status_code=400,
            detail="Vui lòng nhập điểm xuất phát hoặc sử dụng định vị GPS."
        )
    # 6. TÍNH TOÁN LỘ TRÌNH TỐI ƯU
    # LƯU Ý: k_density (mật độ giao thông) và k_restriction (hạn chế xe lớn theo giờ)
    # KHÔNG còn được tính một lần từ request.start_time rồi dùng cho toàn bộ chuyến đi
    # (đó chính là BUG 5). Hai hệ số này giờ được get_travel_minutes() tính LẠI động,
    # theo đúng thời điểm khởi hành thực tế của TỪNG CHẶNG di chuyển.

    # (Đã loại bỏ hàm build_route_greedy() vì là dead code — không được gọi ở bất kỳ
    #  đâu trong flow hiện tại, chỉ build_route_greedy_custom() mới thực sự sinh route.
    #  Hàm cũ còn giữ nguyên lỗi visit_time*k_dens nên loại bỏ để tránh gây nhầm lẫn
    #  hoặc bị dùng nhầm trong tương lai. Xem phần báo cáo audit.)

    def finalize_route(selected_points, k_weather, route_label, route_index, route_theme="diverse"):
        """
        Sắp xếp thứ tự thăm điểm tối ưu, xử lý drop-point khi route vi phạm giờ
        đóng cửa, rồi tạo chi tiết lộ trình cuối cùng (kèm route_name theo nội
        dung thực tế). Trả về dict lộ trình hoặc None nếu không hợp lệ.

        Chọn thuật toán sắp xếp theo số lượng điểm:
        - Số điểm nhỏ (<= DP_MAX_CANDIDATES): dùng solve_tsptw_exact — quy hoạch
          động, đảm bảo tối ưu THẬT SỰ (không chỉ "cải thiện dần" như 2-opt), và
          tự nhiên xử lý luôn việc "bỏ bớt điểm nếu không kịp giờ" mà không cần
          vòng lặp drop-point kiểu cũ.
        - Số điểm lớn hơn: fallback về optimize_sequence (2-opt + Or-opt luân
          phiên), kèm vòng lặp drop-point dựa trên violation_index (BUG-6 fix).
        """
        # Chốt chặn thứ ba: không route nào được finalize nếu còn chứa điểm bị loại.
        if excluded_ids:
            selected_points = [p for p in selected_points if p["id"] not in excluded_ids]
        if len(selected_points) < 2:
            return None

        n_candidates = len(selected_points) - 1
        dropped_point = False

        if n_candidates <= DP_MAX_CANDIDATES:
            dp_result = solve_tsptw_exact(
                selected_points, global_matrix, k_weather, request.vehicle_type,
                clock_start, clock_end, base_date
            )
            best_route_indices, best_cost, violation_idx = dp_result
            if len(best_route_indices) < len(selected_points):
                dropped_point = True   # DP tự loại bớt điểm không kịp giờ, không phải bug
        else:
            initial_route = list(range(len(selected_points)))
            best_route_indices, best_cost, violation_idx = optimize_sequence(
                initial_route, global_matrix, selected_points,
                k_weather, request.vehicle_type, clock_start, clock_end, base_date
            )

            # Vòng lặp drop-point (chỉ cần cho nhánh fallback lớn — DP ở trên đã tự
            # chọn tập điểm khả thi nhiều nhất nên không cần bước này).
            # BUG 6 FIX: xoá đúng ĐIỂM GÂY VI PHẠM (violation_idx do calculate_cost_with_clock
            # xác định), KHÔNG xoá điểm xa nhất so với điểm xuất phát (furthest_idx).
            max_drop_attempts = len(best_route_indices)
            attempts = 0
            while best_cost >= 10000 and len(best_route_indices) > 2 and attempts < max_drop_attempts:
                attempts += 1
                if violation_idx is None or not (0 <= violation_idx < len(best_route_indices)):
                    break
                point_to_drop = best_route_indices[violation_idx]
                if point_to_drop == best_route_indices[0]:
                    break
                best_route_indices = [x for x in best_route_indices if x != point_to_drop]
                dropped_point = True
                best_route_indices, best_cost, violation_idx = optimize_sequence(
                    best_route_indices, global_matrix, selected_points,
                    k_weather, request.vehicle_type, clock_start, clock_end, base_date
                )

        if len(best_route_indices) < 2 or best_cost >= 10000:
            return None

        final_route_details = []
        simulated_clock = clock_start

        for i in range(len(best_route_indices)):
            idx = best_route_indices[i]
            point = selected_points[idx]
            p_open = datetime.combine(base_date, point["open_time"])

            travel_time = 0
            if i > 0:
                prev_point = selected_points[best_route_indices[i - 1]]
                dist_km = calc_dist(prev_point, point)
                travel_time = get_travel_minutes(prev_point["id"], point["id"], dist_km, global_matrix, k_weather, request.vehicle_type, simulated_clock)
                simulated_clock += timedelta(minutes=travel_time)

            wait_time = 0
            if simulated_clock < p_open:
                wait_time = (p_open - simulated_clock).total_seconds() / 60
                simulated_clock = p_open

            arrive_time_str = simulated_clock.strftime("%H:%M")   # Giờ đến
            visit_time = point.get("time") or 0   # Visit time KHÔNG nhân hệ số traffic (BUG 2 fix)
            simulated_clock += timedelta(minutes=visit_time)
            depart_time_str = simulated_clock.strftime("%H:%M")   # Giờ rời

            final_route_details.append({
                "id": point["id"], "ten": point["ten"],
                "lat": point["lat"], "lon": point["lon"],
                "loai_hinh": point.get("loai_hinh", ""),
                "mo_ta": point.get("mo_ta", ""),
                "thong_tin_chi_tiet": point.get("thong_tin_chi_tiet", ""),
                "review": point.get("review", ""),
                "phu_hop": point.get("phu_hop", ""), "url_hinh_anh": point.get("url_hinh_anh", ""),
               
                "visit_time": round(visit_time, 1),
                "wait_time": round(wait_time, 1),
                "arrive_time": arrive_time_str,
                "depart_time": depart_time_str,
                "travel_to_next": 0,
                "distance_to_next": 0
            })

        # travel_to_next hiển thị cho UI: dùng lại đúng thời điểm khởi hành thực tế
        # (arrive/depart đã mô phỏng ở trên) để nhất quán với travel_time đã dùng khi
        # tính lịch trình, thay vì tính lại bằng một công thức khác (tránh double logic).
        for i in range(len(final_route_details) - 1):
            idx1 = best_route_indices[i]
            idx2 = best_route_indices[i + 1]
            p1, p2 = selected_points[idx1], selected_points[idx2]
            departure_dt = datetime.combine(base_date, datetime.strptime(final_route_details[i]["depart_time"], "%H:%M").time())
            dist_km = calc_dist(p1, p2)
            travel_dur = get_travel_minutes(p1["id"], p2["id"], dist_km, global_matrix, k_weather, request.vehicle_type, departure_dt)
            try:
                travel_dist = global_matrix[p1["id"]][p2["id"]]["distance"]
            except (KeyError, TypeError):
                travel_dist = dist_km
            final_route_details[i]["travel_to_next"] = round(travel_dur, 1)
            final_route_details[i]["distance_to_next"] = round(travel_dist, 1)

        total_actual = (simulated_clock - clock_start).total_seconds() / 60
        route_pts = [selected_points[i] for i in best_route_indices]
        avg_preference = sum(p.get("preference_score", 0.5) for p in route_pts) / len(route_pts)

        covered_must = len([p for p in route_pts if p["id"] in must_visit_ids])

        return {
            "dropped_point": dropped_point,
            "total_time_minutes": round(total_actual, 1),
            "vehicle_type": request.vehicle_type,
            "trip_date": str(base_date),
            "strategy": route_label,
            "theme": route_theme,
            "route_name": generate_route_name(route_pts, route_index, route_theme, request.user_preference),
            "avg_preference_score": round(avg_preference, 3),
            "must_visit_covered": covered_must,
            "must_visit_total": len(must_visit_ids),
            "optimized_route": final_route_details
        }

    def build_route_greedy_custom(origin, candidate_pool, k_weather, end_clock, score_fn,
                                   must_visit_ids=None):
        """
        Greedy builder với scorer tùy chỉnh.
        score_fn(last_point, candidate, dist_km) -> float  (nhỏ hơn = ưu tiên hơn)
        end_clock: thời điểm kết thúc tối đa (clock_end hoặc fake ngắn hơn).
        must_visit_ids: tập ID điểm bắt buộc ghé (từ AI), được ưu tiên tuyệt đối.

        travel_time dùng get_travel_minutes() (tính động theo thời điểm khởi hành
        thực tế của từng chặng — BUG 5 fix). visit_time KHÔNG nhân hệ số traffic
        (BUG 2 fix).
        """
        must_visit_ids = must_visit_ids or set()
        origin_open  = datetime.combine(base_date, origin["open_time"])
        origin_close = datetime.combine(base_date, origin["close_time"])
        current_clock = max(clock_start, origin_open)
        departure = current_clock + timedelta(minutes=(origin.get("time") or 0))
        if departure > origin_close or departure > end_clock:
            return None
        selected  = [origin]

        # ── CANDIDATE POOL CỦA GREEDY (mục 7) ──
        # Chốt chặn thứ hai, cố ý trùng với bộ lọc ở bước 3b': kể cả khi sau này
        # có ai đó truyền thẳng một pool chưa lọc vào hàm này, điểm đã bị loại
        # vẫn không thể lọt vào lộ trình. `used_ids` đảm bảo không ghé trùng điểm.
        used_ids  = {origin["id"]}
        unvisited = [
            p for p in candidate_pool
            if p["id"] not in used_ids
            and p["id"] not in excluded_ids
        ]
        while True:
            best_next  = None
            best_score = float('inf')
            best_dep   = departure
            for p in unvisited:
                if p["id"] in used_ids or p["id"] in excluded_ids:
                    continue
                dist = calc_dist(selected[-1], p)
                est_travel = get_travel_minutes(selected[-1]["id"], p["id"], dist, global_matrix, k_weather, request.vehicle_type, departure)
                est_visit  = p.get("time") or 0
                arrival    = departure + timedelta(minutes=est_travel)
                p_open     = datetime.combine(base_date, p["open_time"])
                p_close    = datetime.combine(base_date, p["close_time"])
                sv         = max(arrival, p_open)
                nd         = sv + timedelta(minutes=est_visit)
                if nd <= p_close and nd <= end_clock:
                    sc = score_fn(selected[-1], p, dist)
                    # ƯU TIÊN PHÂN TẦNG:
                    #   force_visit (user vừa yêu cầu thêm) > must_visit (đang giữ) > phần còn lại.
                    # Nếu để force và must cùng một mức, khi quỹ giờ gần đầy Greedy sẽ
                    # nhồi hết các điểm đang giữ trước rồi hết chỗ cho điểm mới.
                    if p["id"] in force_visit_ids:
                        sc = -9999999 + sc * 0.001
                    elif p["id"] in must_visit_ids:
                        sc = -99999 + sc * 0.001
                    if sc < best_score:
                        best_score = sc
                        best_next  = p
                        best_dep   = nd
            if best_next:
                selected.append(best_next)
                used_ids.add(best_next["id"])
                unvisited.remove(best_next)
                departure = best_dep
            else:
                break
        return selected if len(selected) >= 2 else None

    # Biên độ (km-tương-đương) tối đa mà mức độ KHÔNG phù hợp preference có thể
    # "phạt" vào điểm số greedy. Dùng để hoà trộn preference_score (thang 0..1)
    # với distance (thang km) theo đúng pref_weight người dùng chọn.
    PREF_PENALTY_SCALE_KM = 6.0

    def _preference_penalty(p):
        return (1.0 - p.get("preference_score", 0.5)) * PREF_PENALTY_SCALE_KM

    def generate_routes_with_factor():
        """
        Sinh KHO route candidate theo nhiều CHIẾN LƯỢC KHÁC NHAU THỰC SỰ (mục 4).

        Mỗi chiến lược khác nhau ở ít nhất một trong: candidate pool được cấp,
        hàm chấm điểm greedy, và ngân sách thời gian. Nhờ vậy các route khác
        nhau về tập địa điểm, thứ tự, tổng thời gian, mức độ di chuyển và đặc
        điểm trải nghiệm — không phải cùng một lộ trình mang 5 cái tên.

        Trả về danh sách candidate thô; việc chọn ra 3–5 lộ trình cuối cùng do
        select_diverse_routes() đảm nhiệm.
        """
        generated    = []
        seen_id_sets = {}   # frozenset(id) -> vị trí trong `generated`

        def try_add(pts, k_weather, label, theme):
            if not pts or len(pts) < 2:
                return
            route_index = len(generated) + 1
            r = finalize_route(pts, k_weather, label, route_index, route_theme=theme)
            if not r:
                return

            # KHỬ TRÙNG LẶP THEO TẬP ĐIỂM, KHÔNG THEO NHÃN (mục 4).
            # Cùng một tập địa điểm thì dù nhãn chiến lược khác nhau vẫn chỉ là
            # MỘT lộ trình — trước đây đưa nhãn vào fingerprint chính là nguyên
            # nhân sinh ra hàng chục "route" trùng nhau.
            fp = frozenset(p["id"] for p in r["optimized_route"] if p["id"] != "gps_current")
            if not fp:
                return
            if fp in seen_id_sets:
                # Giữ bản tốt hơn: phủ được nhiều must-visit hơn, rồi tới nhanh hơn.
                old = generated[seen_id_sets[fp]]
                better = (r.get("must_visit_covered", 0), -r["total_time_minutes"]) > \
                         (old.get("must_visit_covered", 0), -old["total_time_minutes"])
                if better:
                    generated[seen_id_sets[fp]] = r
                return
            seen_id_sets[fp] = len(generated)
            generated.append(r)

        for origin in starting_points:
            k_weather      = factors.get_weather_factor(origin["lat"], origin["lon"])
            # Toàn bộ candidate pool (đã trừ excluded_ids ở bước 3b') — AI chỉ là
            # ƯU TIÊN, không phải danh sách duy nhất, nên Greedy vẫn được quyền
            # fill-up từ đây khi còn dư thời gian (mục 5).
            base_unvisited = [p for p in all_points if p["id"] != origin["id"]]
            if not base_unvisited:
                continue

            pool_near  = sorted(base_unvisited, key=lambda p: calc_dist(origin, p))
            pool_value = sorted(base_unvisited, key=lambda p: -(p.get("score") or 0))
            pool_food  = [p for p in base_unvisited if p.get("loai_hinh") in _FOOD_CATEGORIES]
            pool_cult  = [p for p in base_unvisited if p.get("loai_hinh") in _CULTURE_CATEGORIES]
            pool_long  = sorted(base_unvisited, key=lambda p: -(p.get("time") or 0))
            pool_far   = list(reversed(pool_near))

            # ── SCORER THEO CHỦ ĐỀ ──
            # (theme, nhãn hiển thị, pool, score_fn, hệ số ngân sách thời gian)
            # Lưu ý ngân sách khác nhau → tổng thời gian khác nhau, đúng yêu cầu
            # "các route cần khác nhau về tổng thời gian" (mục 4).
            strategies = [
                ("time_optimized", base_unvisited,
                 lambda last, p, d: d, 1.0),

                ("preference", base_unvisited,
                 lambda last, p, d: d * (1 - pref_weight) + _preference_penalty(p) * pref_weight, 1.0),

                # Thong thả: quỹ thời gian ngắn hơn + ưu tiên điểm ở được lâu
                # → ít điểm hơn, mỗi điểm lâu hơn, ít di chuyển hơn.
                ("relax", pool_long,
                 lambda last, p, d: d * 0.6 - (p.get("time") or 0) * 0.05, 0.7),

                ("food", pool_food or base_unvisited,
                 lambda last, p, d: d - (3.0 if p.get("loai_hinh") in _FOOD_CATEGORIES else 0), 1.0),

                ("culture", pool_cult or base_unvisited,
                 lambda last, p, d: d - (3.0 if p.get("loai_hinh") in _CULTURE_CATEGORIES else 0), 1.0),

                # Khám phá: bắt đầu từ nửa xa của bản đồ → tập điểm lệch hẳn.
                ("exploration", pool_far[: max(8, len(pool_far) // 2)],
                 lambda last, p, d: d / ((p.get("score") or 1) + 1), 1.0),

                # Đa dạng: phạt nặng nếu trùng loại hình với điểm vừa ghé.
                ("diverse", base_unvisited,
                 lambda last, p, d: d + (150 if p.get("loai_hinh") == last.get("loai_hinh") else 0), 1.0),

                ("highlight", pool_value[: max(8, len(pool_value) // 2)],
                 lambda last, p, d: -(p.get("score") or 0) + d * 0.1, 1.0),
            ]

            for theme, pool, score_fn, ratio in strategies:
                if not pool:
                    continue
                end_t = clock_start + timedelta(minutes=available_minutes * ratio)
                label = ROUTE_THEMES.get(theme, {}).get("label", theme)
                try_add(
                    build_route_greedy_custom(origin, pool, k_weather, end_t, score_fn,
                                              must_visit_ids=must_visit_ids),
                    k_weather, label, theme
                )

            # ── BIẾN THỂ ĐỘ DÀI: cùng chủ đề nhưng quỹ thời gian khác nhau ──
            # Cho người dùng lựa chọn "đi nhẹ nhàng nửa ngày" so với "đi trọn ngày".
            for ratio in (0.5, 0.75):
                mins = available_minutes * ratio
                if mins < 60:
                    continue
                end_t = clock_start + timedelta(minutes=mins)
                label = f"{ROUTE_THEMES['time_optimized']['label']} · {_fmt_minutes(mins)}"
                try_add(
                    build_route_greedy_custom(origin, base_unvisited, k_weather, end_t,
                                              lambda last, p, d: d,
                                              must_visit_ids=must_visit_ids),
                    k_weather, label, "time_optimized"
                )
                label = f"{ROUTE_THEMES['relax']['label']} · {_fmt_minutes(mins)}"
                try_add(
                    build_route_greedy_custom(origin, pool_long, k_weather, end_t,
                                              lambda last, p, d: d * 0.6 - (p.get("time") or 0) * 0.05,
                                              must_visit_ids=must_visit_ids),
                    k_weather, label, "relax"
                )

            # ── LOẠI TRỪ ĐIỂM TOP → ép ra tập địa điểm lệch hẳn ──
            # Lưu ý: đây là biến thể TẠO SỰ ĐA DẠNG trong lúc sinh candidate,
            # hoàn toàn khác với excluded_ids (ràng buộc của người dùng).
            for skip in range(min(4, len(pool_value))):
                pool_excl = [p for p in base_unvisited if p["id"] != pool_value[skip]["id"]]
                try_add(
                    build_route_greedy_custom(origin, pool_excl, k_weather, clock_end,
                                              lambda last, p, d: d / ((p.get("score") or 1) + 1),
                                              must_visit_ids=must_visit_ids),
                    k_weather, f"{ROUTE_THEMES['exploration']['label']} #{skip + 1}", "exploration"
                )

            # ── Random sample nhiều seed: mở rộng kho candidate cho bước chọn đa dạng ──
            for seed in (7, 13, 42, 77, 99, 111):
                rng    = random.Random(seed)
                n      = max(6, int(len(base_unvisited) * (0.3 + (seed % 5) * 0.1)))
                n      = min(n, len(base_unvisited))
                sample = rng.sample(base_unvisited, n)
                try_add(
                    build_route_greedy_custom(origin, sample, k_weather, clock_end,
                                              lambda last, p, d: d / ((p.get("score") or 1) + 1),
                                              must_visit_ids=must_visit_ids),
                    k_weather, f"{ROUTE_THEMES['exploration']['label']} #{seed}", "exploration"
                )

        return generated


    # ============================================================
    # MUST-VISIT (AI + itinerary hiện tại) — FILL-UP LOGIC (mục 5)
    # ------------------------------------------------------------
    # ai_selected_ids CHỈ LÀ ƯU TIÊN, không phải danh sách duy nhất. Cố ý KHÔNG
    # ghi đè all_points bằng (starting_points + ai_points): làm thế sẽ cắt mất
    # toàn bộ candidate còn lại và Greedy hết đường fill-up khi còn dư thời gian.
    # Toàn bộ candidate pool (đã trừ excluded_ids) vẫn nằm trong all_points.
    # ============================================================
    if request.ai_selected_ids:
        valid_ids = {p["id"] for p in all_points}
        ai_ids = {str(i) for i in request.ai_selected_ids} & valid_ids
        # Điểm AI gợi ý nhưng người dùng đã loại thì KHÔNG được quay lại (mục 8):
        # excluded_ids đã bị loại khỏi all_points nên phép giao ở trên tự lọc sạch.
        if not ai_ids and not must_visit_ids:
            return {"status": "error", "message": "Không có ID điểm đến hợp lệ nào."}
        must_visit_ids = (must_visit_ids | ai_ids) - excluded_ids

    generated_routes = generate_routes_with_factor()

    if len(generated_routes) == 0:
        return {
            "status": "error",
            "message": "Quỹ thời gian quá ngắn hoặc các địa điểm đều chưa mở cửa vào khung giờ này!",
            "excluded_ids": sorted(excluded_ids),
        }

    # ============================================================
    # MODE "single": tính lại ĐÚNG MỘT lịch trình (mục 9)
    # Dùng cho /api/itinerary/update sau khi người dùng xoá điểm. Chọn candidate
    # giữ được nhiều điểm-phải-giữ nhất, rồi mới tới nhiều điểm / dùng hết quỹ
    # thời gian. Điểm đã xoá không thể quay lại vì đã bị loại khỏi all_points.
    # ============================================================
    if mode == "single":
        def _rank(r):
            covered_force = len([p for p in r["optimized_route"] if p["id"] in force_visit_ids])
            return (
                covered_force,                      # điểm user vừa thêm phải vào được trước
                r.get("must_visit_covered", 0),     # rồi mới tới giữ lại nhiều nhất
                len(r["optimized_route"]),
                -abs(available_minutes - r["total_time_minutes"]),
            )

        best = max(generated_routes, key=_rank)
        itinerary = format_route_object(best, 1)
        return {
            "status": "success",
            "available_minutes": available_minutes,
            "trip_date": str(base_date),
            "vehicle_type": request.vehicle_type,
            "vehicle_note": vehicle_note,
            "accessible_locations_count": len(all_points),
            "user_preference": request.user_preference,
            "weight": request.weight,
            "excluded_ids": sorted(excluded_ids),
            "itinerary": itinerary,
            # Khoá cũ để app.js hiện tại render được ngay không cần sửa.
            "routes": [itinerary],
        }

    # ============================================================
    # MODE "multi": chọn 3–5 lộ trình KHÁC NHAU THỰC SỰ (mục 4)
    # Không còn sort theo (-số điểm, thời gian) rồi cắt [:5] — cách đó luôn trả
    # về 5 biến thể của cùng một lộ trình dài nhất.
    # ============================================================
    selected = select_diverse_routes(generated_routes, available_minutes, request.num_routes)
    routes = _ensure_unique_names([format_route_object(r, i + 1) for i, r in enumerate(selected)])

    return {
        "status": "success",
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "vehicle_note": vehicle_note,
        "accessible_locations_count": len(all_points),
        "user_preference": request.user_preference,
        "weight": request.weight,
        "excluded_ids": sorted(excluded_ids),
        "candidates_generated": len(generated_routes),
        "routes": routes,
    }
