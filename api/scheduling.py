"""Legacy cost function, 2-opt, Or-opt and exact TSPTW DP used by the classic generator.

Time arithmetic is delegated to core.timeline (walk/step) — no private copy of the simulator here."""
from config import CFG
from core import audit
from core.timeline import approx_km, step, walk, window_dt
from api.factors import get_density_factor, get_large_vehicle_restriction_factor

calc_dist = approx_km   # one distance formula for the whole code base (core.timeline)


def get_travel_minutes(origin_id, dest_id, dist_km, matrix_dict, k_weather, vehicle_type, departure_dt):
    """
    Thời gian di chuyển (phút), tính ĐỘNG theo thời điểm khởi hành thực tế của
    CHẶNG ĐÓ (BUG 5 fix) — không dùng một hệ số cố định tính từ start_time cho
    toàn bộ chuyến đi.

    Congestion (k_density) và hạn chế xe lớn theo giờ (k_restriction) CHỈ tác
    động đến travel_time, KHÔNG được áp dụng cho visit_time (BUG 2 fix).
    """
    time_str = departure_dt.strftime("%H:%M")
    k_density_dynamic = get_density_factor(time_str)
    k_restriction_dynamic = get_large_vehicle_restriction_factor(vehicle_type, time_str)

    try:
        base_duration = matrix_dict[origin_id][dest_id]["duration"]
    except (KeyError, TypeError):
        audit.record("osrm", "pair_missing_in_matrix", fallback="straight-line/%gkmh" % CFG.routing.fallback_leg_speed_kmh)
        base_duration = (dist_km / CFG.routing.fallback_leg_speed_kmh) * 60

    return base_duration * k_weather * k_density_dynamic * k_restriction_dynamic

def calculate_cost_with_clock(route_indices, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Mô phỏng tuần tự Current Time -> Travel -> Arrival -> Visit -> Next Departure
    cho toàn bộ route, trả về (cost, violation_index).
    violation_index là vị trí (trong route_indices) của điểm ĐẦU TIÊN gây vi phạm
    giờ đóng cửa / vượt khung giờ cho phép — dùng để xác định CHÍNH XÁC điểm cần
    xử lý khi route không khả thi (BUG 6 fix), thay vì đoán mù theo khoảng cách.
    """
    pen = CFG.penalty
    pts = [points_data[i] for i in route_indices]
    tl = walk(pts, clock_start_dt, window=lambda p: window_dt(p, base_date),
              leg=lambda a, b, clk: get_travel_minutes(a["id"], b["id"], calc_dist(a, b), matrix_dict,
                                                       k_weather, vehicle_type, clk))
    penalty, violation_index = 0.0, None
    for i, st in enumerate(tl.stops):
        if i > 0 and st.node["loai_hinh"] == tl.stops[i - 1].node["loai_hinh"]:
            penalty += pen.same_category_adjacent
        if i > 1 and st.node["loai_hinh"] == tl.stops[i - 2].node["loai_hinh"]:
            penalty += pen.same_category_skip_one
        if st.late > 1e-9 or st.depart > clock_end_dt:
            penalty += pen.time_window_violation
            if violation_index is None:
                violation_index = i
    total_minutes = (tl.finish - clock_start_dt).total_seconds() / 60
    return total_minutes + penalty, violation_index

def or_opt_pass(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Cải tiến bổ sung cho 2-opt: 2-opt chỉ đảo NGƯỢC một đoạn liên tiếp, nên có những
    cách sắp xếp tốt hơn (dời MỘT điểm sang vị trí khác trong route) mà 2-opt không
    bao giờ thử tới. Or-opt bù đắp đúng chỗ yếu này — đặc biệt hữu ích với route có
    ràng buộc giờ mở/đóng cửa, nơi việc "nhấc" một điểm hay bị time-window chặn ra
    khỏi vị trí ban đầu thường dễ khả thi hơn là đảo ngược cả một đoạn dài.
    """
    best_route = route[:]
    best_cost, best_violation = calculate_cost_with_clock(best_route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        for i in range(1, len(best_route)):  # không dời điểm xuất phát (index 0)
            node = best_route[i]
            remaining = best_route[:i] + best_route[i + 1:]
            for j in range(1, len(remaining) + 1):
                if j == i:
                    continue
                candidate = remaining[:j] + [node] + remaining[j:]
                cost, viol = calculate_cost_with_clock(candidate, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
                if cost < best_cost:
                    best_route, best_cost, best_violation = candidate, cost, viol
                    improved = True
                    break
            if improved:
                break
    return best_route, best_cost, best_violation

def optimize_sequence(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Kết hợp 2-opt + Or-opt luân phiên (heuristic cổ điển, chất lượng tốt hơn hẳn dùng
    một mình 2-opt — 2-opt dễ bị kẹt ở local optimum mà Or-opt gỡ ra được, và ngược
    lại). Dùng cho tập điểm LỚN, nơi bitmask DP (solve_tsptw_exact) không còn khả thi
    về mặt hiệu năng.
    """
    r, c, v = two_opt_algorithm(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
    for _ in range(3):  # vài vòng luân phiên là đủ hội tụ trong thực tế, tránh loop vô hạn
        r2, c2, v2 = or_opt_pass(r, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
        r3, c3, v3 = two_opt_algorithm(r2, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
        if c3 >= c - 1e-6:  # không còn cải thiện đáng kể -> dừng
            r, c, v = r3, c3, v3
            break
        r, c, v = r3, c3, v3
    return r, c, v

# Ngưỡng số điểm candidate cho exact DP: config.CFG.routing.dp_max_candidates (env DP_MAX_CANDIDATES).
# Benchmark gốc: n=10 ~0.2s, n=11 ~0.5s, n=12 ~1.2s, n=13 ~2.9s — nên benchmark lại trên máy thật.
DP_MAX_CANDIDATES = CFG.routing.dp_max_candidates

def solve_tsptw_exact(points, matrix_dict, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    """
    Giải CHÍNH XÁC (không phải heuristic) bài toán sắp xếp thứ tự thăm điểm có ràng
    buộc giờ mở/đóng cửa (TSP with Time Windows), bằng quy hoạch động bitmask kiểu
    Held-Karp — chất lượng cao hơn 2-opt/Or-opt vì 2-opt chỉ CẢI THIỆN dần từ 1 lời
    giải khởi tạo (có thể kẹt ở tối ưu cục bộ), còn DP này duyệt HẾT không gian trạng
    thái khả thi nên đảm bảo tìm được: (1) tập điểm ghé được NHIỀU NHẤT trong khung
    giờ cho phép, và (2) trong các cách đạt được số điểm đó, cách hoàn thành SỚM NHẤT.

    Giả định (FIFO property): xuất phát/đến sớm hơn ở một điểm không bao giờ khiến
    kết quả về sau tệ hơn xuất phát muộn — hợp lý với mô hình giao thông có tính chu
    kỳ theo giờ trong ngày ở đây, nhưng đây là giả định kỹ thuật cần lưu ý nếu sau
    này mô hình hoá giao thông phức tạp hơn (VD: có sự kiện đột xuất chỉ xảy ra ở
    một mốc giờ cụ thể chứ không theo khung).

    points[0] LUÔN là điểm xuất phát (origin), cố định. points[1:] là các điểm candidate.
    Trả về (order_indices, total_cost, violation_index) để tương thích với chữ ký của
    two_opt_algorithm/or_opt_pass — nhưng ở đây violation_index luôn None vì DP chỉ giữ
    lại những trạng thái ĐÃ được xác nhận khả thi (không có điểm nào bị "gắn cờ vi phạm"
    còn sót lại trong route trả về).
    Trả về None nếu n > DP_MAX_CANDIDATES (caller cần fallback sang optimize_sequence).
    """
    n = len(points) - 1
    if n <= 0:
        return [0], 0.0, None
    if n > DP_MAX_CANDIDATES:
        return None

    origin = points[0]
    cands = points[1:]

    _, _, origin_finish, _, o_late = step(clock_start_dt, 0.0, window_dt(origin, base_date), origin.get("time") or 0)
    if o_late > 1e-9 or origin_finish > clock_end_dt:
        return [0], 999999.0, 0  # điểm xuất phát tự nó đã vi phạm giờ giấc (hiếm, gần như không xảy ra)

    size = 1 << n
    dp = [[None] * n for _ in range(size)]
    parent = [[-1] * n for _ in range(size)]

    for j in range(n):
        p = cands[j]
        dist = calc_dist(origin, p)
        travel = get_travel_minutes(origin["id"], p["id"], dist, matrix_dict, k_weather, vehicle_type, origin_finish)
        _, _, finish, _, late = step(origin_finish, travel, window_dt(p, base_date), p.get("time") or 0)
        if late <= 1e-9 and finish <= clock_end_dt:
            dp[1 << j][j] = finish

    for mask in range(size):
        for last in range(n):
            if not (mask & (1 << last)) or dp[mask][last] is None:
                continue
            cur_time = dp[mask][last]
            p_last = cands[last]
            for nxt in range(n):
                if mask & (1 << nxt):
                    continue
                p_next = cands[nxt]
                dist = calc_dist(p_last, p_next)
                travel = get_travel_minutes(p_last["id"], p_next["id"], dist, matrix_dict, k_weather, vehicle_type, cur_time)
                _, _, finish, _, late = step(cur_time, travel, window_dt(p_next, base_date), p_next.get("time") or 0)
                if late <= 1e-9 and finish <= clock_end_dt:
                    new_mask = mask | (1 << nxt)
                    if dp[new_mask][nxt] is None or finish < dp[new_mask][nxt]:
                        dp[new_mask][nxt] = finish
                        parent[new_mask][nxt] = last

    # Chọn trạng thái ghé được NHIỀU điểm nhất; hoà thì chọn hoàn thành sớm nhất
    best_mask, best_last, best_finish, best_count = 0, -1, None, -1
    for mask in range(size):
        cnt = bin(mask).count("1")
        for j in range(n):
            if dp[mask][j] is None:
                continue
            if cnt > best_count or (cnt == best_count and dp[mask][j] < best_finish):
                best_mask, best_last, best_finish, best_count = mask, j, dp[mask][j], cnt

    if best_last == -1:
        # Không candidate nào khả thi trong khung giờ -> chỉ còn điểm xuất phát
        return [0], (origin_finish - clock_start_dt).total_seconds() / 60, None

    order = []
    mask, last = best_mask, best_last
    while last != -1:
        order.append(last)
        prev = parent[mask][last]
        mask ^= (1 << last)
        last = prev
    order.reverse()

    full_order = [0] + [idx + 1 for idx in order]
    total_cost = (best_finish - clock_start_dt).total_seconds() / 60
    return full_order, total_cost, None

def two_opt_algorithm(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date):
    best_route = route
    best_cost, best_violation_idx = calculate_cost_with_clock(route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
    improved = True
    while improved:
        improved = False
        # Bắt đầu từ i=1 để KHÔNG đảo điểm xuất phát (index 0 luôn cố định)
        for i in range(1, len(best_route) - 1):
            for j in range(i + 1, len(best_route) + 1):
                if j - i <= 1: continue
                new_route = best_route[:]
                new_route[i:j] = best_route[i:j][::-1]
                new_cost, new_violation_idx = calculate_cost_with_clock(new_route, matrix_dict, points_data, k_weather, vehicle_type, clock_start_dt, clock_end_dt, base_date)
                if new_cost < best_cost:
                    best_route, best_cost, best_violation_idx = new_route, new_cost, new_violation_idx
                    improved = True
    return best_route, best_cost, best_violation_idx
