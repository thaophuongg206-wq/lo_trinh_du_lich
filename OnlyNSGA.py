# -*- coding: utf-8 -*-
"""
nsga2_module.py
================
THUẬT TOÁN ĐỐI CHỨNG cho main.py: NSGA-II áp dụng cho bài toán MO-TOPTW,
dùng CHUNG dữ liệu (DB, bảng DIA_DIEM/CUA_SO_THOI_GIAN), CHUNG ma trận OSRM
(get_global_osrm_matrix) và CHUNG cách xử lý ràng buộc khung giờ với thuật
toán heuristic tham lam + 2-opt đang có trong main.py — để việc so sánh 2
thuật toán (mục V trong báo cáo) là công bằng, không lệch nhau ở tầng dữ liệu.

CÁCH CHẠY
---------
File này KHÔNG định nghĩa FastAPI app mới, mà IMPORT app đã có sẵn trong
main.py rồi gắn thêm 1 route mới vào chính app đó, nên bạn chạy:

    uvicorn nsga2_module:app --reload --port 8000

(thay vì `uvicorn main:app`) — mọi endpoint cũ của main.py (bao gồm
/api/optimize-route, /api/locations) vẫn hoạt động bình thường, cộng thêm
endpoint mới:

    POST /api/optimize-route-nsga2   -> chạy NSGA-II, trả cùng format JSON
                                         như /api/optimize-route + thêm các
                                         trường "algorithm", "elapsed_ms",
                                         "pareto_objectives" cho từng route.

Muốn so sánh 2 thuật toán trên cùng 1 truy vấn: gọi lần lượt 2 endpoint với
CÙNG body request, rồi so sánh "elapsed_ms" và tập "routes" trả về (đúng
thiết kế thực nghiệm ở mục V của tài liệu).
"""

from __future__ import annotations

import time
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple, Optional

from main import (
    app,
    get_db_connection,
    get_global_osrm_matrix,
    get_weather_factor,
    get_density_factor,
    get_large_vehicle_restriction_factor,
    OptimizationRequest,
)


# =====================================================================
# 1. HÀM PHỤ TRỢ DÙNG CHUNG QUY ƯỚC DỮ LIỆU CỦA main.py
#    (point = dict với các khoá: id, ten, lat, lon, time, score, loai_hinh,
#     open_time, close_time — giống hệt all_points trong main.py)
# =====================================================================

def _calc_dist_km(p1: dict, p2: dict) -> float:
    """Ước tính khoảng cách Euclid (km) — dùng để seed quần thể ban đầu,
    cùng công thức calc_dist() trong main.py."""
    return math.sqrt((p1["lat"] - p2["lat"]) ** 2 + (p1["lon"] - p2["lon"]) ** 2) * 111


def _travel_minutes(p1: dict, p2: dict, matrix_dict: dict, k_total: float) -> float:
    """Thời gian di chuyển thật (phút) từ ma trận OSRM, đã nhân k_total
    (= k_weather * k_restriction) — CÙNG cách nhân hệ số như finalize_route()
    và two_opt_algorithm() trong main.py (chúng gọi calculate_cost_with_clock
    với tham số 'k_weather' nhưng thực chất truyền vào là k_total)."""
    return matrix_dict[p1["id"]][p2["id"]]["duration"] * k_total


# =====================================================================
# 2. REPAIR + ĐÁNH GIÁ MỤC TIÊU (mục 3.2, 3.3 trong tài liệu)
#    Repair mô phỏng lại đúng logic mô phỏng đồng hồ mà
#    calculate_cost_with_clock() / finalize_route() dùng trong main.py.
# =====================================================================

def repair_route(
    route_indices: List[int],
    points_data: List[dict],
    matrix_dict: dict,
    k_total: float,
    k_density: float,
    clock_start: datetime,
    clock_end: datetime,
    base_date,
) -> List[int]:
    """
    Duyệt tuần tự route, loại bỏ điểm nào vi phạm khung giờ mở cửa hoặc vượt
    quá clock_end. Điểm đầu tiên (origin, index 0 của points_data cục bộ)
    luôn được giữ; nếu bản thân origin đã vi phạm thì trả về route chỉ có
    origin (route không hợp lệ, sẽ bị loại ở bước đánh giá).
    """
    if not route_indices:
        return route_indices

    origin_idx = route_indices[0]
    origin = points_data[origin_idx]
    o_open = datetime.combine(base_date, origin["open_time"])
    o_close = datetime.combine(base_date, origin["close_time"])
    clock = max(clock_start, o_open)
    departure = clock + timedelta(minutes=origin["time"] * k_density)

    if departure > o_close or departure > clock_end:
        return [origin_idx]

    valid = [origin_idx]
    clock = departure

    for idx in route_indices[1:]:
        point = points_data[idx]
        prev_point = points_data[valid[-1]]
        travel = _travel_minutes(prev_point, point, matrix_dict, k_total)
        arrival = clock + timedelta(minutes=travel)

        p_open = datetime.combine(base_date, point["open_time"])
        p_close = datetime.combine(base_date, point["close_time"])
        visit_start = max(arrival, p_open)
        visit_end = visit_start + timedelta(minutes=point["time"] * k_density)

        if visit_end <= p_close and visit_end <= clock_end:
            valid.append(idx)
            clock = visit_end
        # else: bỏ điểm vi phạm, thử điểm kế tiếp trong route (đúng REPAIR)

    return valid


OBJECTIVE_IS_MAX = (True, False, True, True)  # (f1 điểm, f2 tgian di chuyển, f3 số điểm, f4 đa dạng)


def evaluate_route(
    route_indices: List[int],
    points_data: List[dict],
    matrix_dict: dict,
    k_total: float,
) -> Tuple[float, float, float, float]:
    visited = route_indices[1:]
    f1 = sum(points_data[i].get("score") or 0 for i in visited)          # maximize
    f2 = 0.0
    for i in range(len(route_indices) - 1):
        p1 = points_data[route_indices[i]]
        p2 = points_data[route_indices[i + 1]]
        f2 += _travel_minutes(p1, p2, matrix_dict, k_total)               # minimize
    f3 = float(len(visited))                                              # maximize
    if visited:
        loai_hinh_set = {points_data[i]["loai_hinh"] for i in visited}
        f4 = len(loai_hinh_set) / len(visited)                            # maximize
    else:
        f4 = 0.0
    return (f1, f2, f3, f4)


# =====================================================================
# 3. CÁ THỂ + NSGA-II CORE 
# =====================================================================

@dataclass
class Individual:
    route: List[int]
    raw: Tuple[float, ...] = field(default_factory=tuple)
    minimized: Tuple[float, ...] = field(default_factory=tuple)
    rank: int = 0
    crowding_distance: float = 0.0

    def evaluate(self, ctx: "NSGA2Context") -> None:
        self.route = repair_route(
            self.route, ctx.points_data, ctx.matrix_dict, ctx.k_total,
            ctx.k_density, ctx.clock_start, ctx.clock_end, ctx.base_date,
        )
        self.raw = evaluate_route(self.route, ctx.points_data, ctx.matrix_dict, ctx.k_total)
        self.minimized = tuple(
            (-v if is_max else v) for v, is_max in zip(self.raw, OBJECTIVE_IS_MAX)
        )


@dataclass
class NSGA2Context:
    points_data: List[dict]          # points_data[0] = origin
    matrix_dict: dict
    k_total: float                   # = k_weather * k_restriction
    k_density: float
    clock_start: datetime
    clock_end: datetime
    base_date: Any


def dominates(a: Individual, b: Individual) -> bool:
    not_worse = all(x <= y for x, y in zip(a.minimized, b.minimized))
    better = any(x < y for x, y in zip(a.minimized, b.minimized))
    return not_worse and better


def fast_non_dominated_sort(population: List[Individual]) -> List[List[Individual]]:
    fronts: List[List[Individual]] = [[]]
    dom_count: Dict[int, int] = {}
    dom_sols: Dict[int, List[Individual]] = {}

    for i, p in enumerate(population):
        dom_count[i] = 0
        dom_sols[i] = []
        for q in population:
            if p is q:
                continue
            if dominates(p, q):
                dom_sols[i].append(q)
            elif dominates(q, p):
                dom_count[i] += 1
        if dom_count[i] == 0:
            p.rank = 1
            fronts[0].append(p)

    idx_of = {id(ind): i for i, ind in enumerate(population)}
    k = 0
    while fronts[k]:
        nxt: List[Individual] = []
        for p in fronts[k]:
            for q in dom_sols[idx_of[id(p)]]:
                j = idx_of[id(q)]
                dom_count[j] -= 1
                if dom_count[j] == 0:
                    q.rank = k + 2
                    nxt.append(q)
        k += 1
        fronts.append(nxt)

    return [f for f in fronts if f]


def crowding_distance_assignment(front: List[Individual]) -> None:
    n = len(front)
    if n == 0:
        return
    for ind in front:
        ind.crowding_distance = 0.0
    n_obj = len(front[0].minimized)
    for m in range(n_obj):
        front.sort(key=lambda ind: ind.minimized[m])
        front[0].crowding_distance = float("inf")
        front[-1].crowding_distance = float("inf")
        f_min, f_max = front[0].minimized[m], front[-1].minimized[m]
        if f_max == f_min:
            continue
        for i in range(1, n - 1):
            front[i].crowding_distance += (
                front[i + 1].minimized[m] - front[i - 1].minimized[m]
            ) / (f_max - f_min)


def crowded_better(a: Individual, b: Individual) -> bool:
    if a.rank != b.rank:
        return a.rank < b.rank
    return a.crowding_distance > b.crowding_distance


def tournament_select(pop: List[Individual]) -> Individual:
    a, b = random.sample(pop, 2)
    return a if crowded_better(a, b) else b


def order_crossover(parent1: List[int], parent2: List[int]) -> Tuple[List[int], List[int]]:
    origin = parent1[0]
    p1, p2 = parent1[1:], parent2[1:]
    if not p1 or not p2:
        return list(parent1), list(parent2)

    def ox(a: List[int], b: List[int]) -> List[int]:
        size = len(a)
        i, j = sorted(random.sample(range(size), 2)) if size >= 2 else (0, 0)
        child = [None] * size
        child[i:j + 1] = a[i:j + 1]
        fill = [g for g in b if g not in child]
        pos = 0
        for k in range(size):
            if child[k] is None and pos < len(fill):
                child[k] = fill[pos]
                pos += 1
        return [g for g in child if g is not None]

    return [origin] + ox(p1, p2), [origin] + ox(p2, p1)


def swap_mutation(route: List[int]) -> List[int]:
    route = list(route)
    if len(route) < 3:
        return route
    i, j = random.sample(range(1, len(route)), 2)
    route[i], route[j] = route[j], route[i]
    return route


def insert_mutation(route: List[int], n_points: int) -> List[int]:
    route = list(route)
    remaining = [i for i in range(1, n_points) if i not in route]
    if not remaining:
        return route
    route.insert(random.randint(1, len(route)), random.choice(remaining))
    return route


def remove_mutation(route: List[int]) -> List[int]:
    route = list(route)
    if len(route) < 2:
        return route
    del route[random.randint(1, len(route) - 1)]
    return route


def mutate(route: List[int], n_points: int) -> List[int]:
    op = random.choice(["swap", "insert", "remove"])
    if op == "swap":
        return swap_mutation(route)
    if op == "insert":
        return insert_mutation(route, n_points)
    return remove_mutation(route)


def _greedy_seed_routes(points_data: List[dict], ctx: NSGA2Context, n_seeds: int) -> List[List[int]]:
    """
    Seeding đơn giản kiểu nearest-neighbor (dùng khoảng cách Euclid, cùng ý
    tưởng calc_dist() trong main.py) để tăng tốc hội tụ ban đầu — KHÔNG thay
    thế cho greedy 2-opt của main.py, chỉ giúp NSGA-II xuất phát tốt hơn.
    """
    seeds = []
    n_points = len(points_data)
    for _ in range(n_seeds):
        remaining = list(range(1, n_points))
        random.shuffle(remaining)
        cur = 0
        route = [0]
        # nearest-neighbor trên một tập con ngẫu nhiên để đa dạng seed
        k = random.randint(3, max(3, min(len(remaining), n_points - 1)))
        pool = remaining[:k]
        while pool:
            nxt = min(pool, key=lambda i: _calc_dist_km(points_data[cur], points_data[i]))
            route.append(nxt)
            pool.remove(nxt)
            cur = nxt
        seeds.append(route)
    return seeds


def init_population(ctx: NSGA2Context, pop_size: int, n_seeds: int = 10) -> List[Individual]:
    n_points = len(ctx.points_data)
    population: List[Individual] = []

    for seed_route in _greedy_seed_routes(ctx.points_data, ctx, min(n_seeds, pop_size)):
        population.append(Individual(route=seed_route))

    while len(population) < pop_size:
        candidates = list(range(1, n_points))
        k = random.randint(1, max(1, len(candidates)))
        subset = random.sample(candidates, k)
        random.shuffle(subset)
        population.append(Individual(route=[0] + subset))

    for ind in population:
        ind.evaluate(ctx)
    return population


@dataclass
class NSGA2Config:
    pop_size: int = 50
    n_generations: int = 60
    pc: float = 0.9
    pm: float = 0.25


def run_nsga2(ctx: NSGA2Context, config: NSGA2Config) -> List[Individual]:
    n_points = len(ctx.points_data)
    P_t = init_population(ctx, config.pop_size)
    fronts = fast_non_dominated_sort(P_t)
    for f in fronts:
        crowding_distance_assignment(f)

    for _ in range(config.n_generations):
        Q_t: List[Individual] = []
        while len(Q_t) < config.pop_size:
            parent1, parent2 = tournament_select(P_t), tournament_select(P_t)
            if random.random() < config.pc:
                c1, c2 = order_crossover(parent1.route, parent2.route)
            else:
                c1, c2 = list(parent1.route), list(parent2.route)
            if random.random() < config.pm:
                c1 = mutate(c1, n_points)
            if random.random() < config.pm:
                c2 = mutate(c2, n_points)
            ind1, ind2 = Individual(route=c1), Individual(route=c2)
            ind1.evaluate(ctx)
            ind2.evaluate(ctx)
            Q_t.append(ind1)
            if len(Q_t) < config.pop_size:
                Q_t.append(ind2)

        R_t = P_t + Q_t
        fronts = fast_non_dominated_sort(R_t)

        P_next: List[Individual] = []
        i = 0
        while i < len(fronts) and len(P_next) + len(fronts[i]) <= config.pop_size:
            crowding_distance_assignment(fronts[i])
            P_next.extend(fronts[i])
            i += 1
        if len(P_next) < config.pop_size and i < len(fronts):
            crowding_distance_assignment(fronts[i])
            slots = config.pop_size - len(P_next)
            fronts[i].sort(key=lambda ind: ind.crowding_distance, reverse=True)
            P_next.extend(fronts[i][:slots])

        P_t = P_next

    final_fronts = fast_non_dominated_sort(P_t)
    return final_fronts[0] if final_fronts else []


# =====================================================================
# 4. HYPERVOLUME 2D (mục V — chỉ số so sánh định lượng)
# =====================================================================

def hypervolume_2d(front: List[Individual], obj_idx=(0, 1)) -> float:
    if not front:
        return 0.0
    pts = [(ind.minimized[obj_idx[0]], ind.minimized[obj_idx[1]]) for ind in front]
    ref_x = max(p[0] for p in pts) * 1.1 + 1e-6
    ref_y = max(p[1] for p in pts) * 1.1 + 1e-6
    pts.sort(key=lambda p: p[0])
    hv, prev_x, min_y = 0.0, pts[0][0], ref_y
    for x, y in pts:
        if y < min_y:
            if x > prev_x:
                hv += (x - prev_x) * (min_y - y)
            min_y = y
            prev_x = x
    return hv


# =====================================================================
# 5. XÂY DỰNG OUTPUT ROUTE — CÙNG FORMAT với finalize_route() trong main.py
#    để frontend (route_selector trong index.html) dùng lại được ngay.
# =====================================================================

def build_route_output(
    route_indices: List[int],
    points_data: List[dict],
    matrix_dict: dict,
    k_total: float,
    k_density: float,
    base_date,
    clock_start: datetime,
    vehicle_type: str,
    label: str,
) -> Optional[dict]:
    if len(route_indices) < 2:
        return None

    details = []
    simulated_clock = clock_start

    for i, idx in enumerate(route_indices):
        point = points_data[idx]
        p_open = datetime.combine(base_date, point["open_time"])

        travel_time = 0.0
        if i > 0:
            prev = points_data[route_indices[i - 1]]
            travel_time = _travel_minutes(prev, point, matrix_dict, k_total)
            simulated_clock += timedelta(minutes=travel_time)

        wait_time = 0.0
        if simulated_clock < p_open:
            wait_time = (p_open - simulated_clock).total_seconds() / 60
            simulated_clock = p_open

        arrive_time_str = simulated_clock.strftime("%H:%M")
        visit_time = point["time"] * k_density
        simulated_clock += timedelta(minutes=visit_time)
        depart_time_str = simulated_clock.strftime("%H:%M")

        details.append({
            "id": point["id"], "ten": point["ten"],
            "lat": point["lat"], "lon": point["lon"],
            "visit_time": round(visit_time, 1),
            "wait_time": round(wait_time, 1),
            "arrive_time": arrive_time_str,
            "depart_time": depart_time_str,
            "travel_to_next": 0,
            "distance_to_next": 0,
        })

    for i in range(len(details) - 1):
        p1 = points_data[route_indices[i]]
        p2 = points_data[route_indices[i + 1]]
        d = matrix_dict[p1["id"]][p2["id"]]
        details[i]["travel_to_next"] = round(d["duration"] * k_total, 1)
        details[i]["distance_to_next"] = round(d["distance"], 1)

    total_actual = (simulated_clock - clock_start).total_seconds() / 60

    return {
        "dropped_point": False,
        "total_time_minutes": round(total_actual, 1),
        "vehicle_type": vehicle_type,
        "trip_date": str(base_date),
        "strategy": label,
        "optimized_route": details,
    }


# =====================================================================
# 6. ENDPOINT MỚI: /api/optimize-route-nsga2
#    Bám sát luồng xử lý của /api/optimize-route trong main.py (parse ngày
#    giờ, fetch DB, chọn điểm xuất phát) — chỉ thay khối sinh lộ trình
#    (greedy + 2-opt) bằng NSGA-II.
# =====================================================================

@app.post("/api/optimize-route-nsga2")
async def optimize_route_nsga2(request: OptimizationRequest):
    t0 = time.perf_counter()

    # 1. Ngày khởi hành
    try:
        base_date = (
            datetime.strptime(request.trip_date, "%Y-%m-%d").date()
            if request.trip_date else datetime.today().date()
        )
    except Exception:
        base_date = datetime.today().date()

    # 2. Giờ bắt đầu / kết thúc
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
    except Exception:
        return {"status": "error", "message": "Lỗi định dạng thời gian"}

    # 3. Lấy địa điểm từ DB (giống hệt main.py)
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT d.id, d.ten, d.vi_do, d.kinh_do, d.thoi_gian_tham_quan_phut, d.diem_gia_tri, d.loai_hinh,
               c.gio_mo_cua, c.gio_dong_cua
        FROM DIA_DIEM d
        LEFT JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
    """)

    all_points = []
    default_open = datetime.strptime("00:00", "%H:%M").time()
    default_close = datetime.strptime("23:59", "%H:%M").time()

    for r in cursor.fetchall():
        open_time = r.gio_mo_cua if r.gio_mo_cua else default_open
        close_time = r.gio_dong_cua if r.gio_dong_cua else default_close
        if isinstance(open_time, str):
            open_time = datetime.strptime(open_time[:5], "%H:%M").time()
        if isinstance(close_time, str):
            close_time = datetime.strptime(close_time[:5], "%H:%M").time()

        all_points.append({
            "id": str(r.id), "ten": r.ten, "lat": r.vi_do, "lon": r.kinh_do,
            "time": r.thoi_gian_tham_quan_phut, "score": r.diem_gia_tri, "loai_hinh": r.loai_hinh,
            "open_time": open_time, "close_time": close_time,
        })
    conn.close()

    # 4. Ma trận OSRM (dùng chung hàm với main.py)
    global_matrix = get_global_osrm_matrix(all_points, vehicle_type=request.vehicle_type)

    # 5. Chọn điểm xuất phát (CÙNG logic 3 trường hợp như main.py)
    all_points.sort(key=lambda x: x["score"], reverse=True)

    if request.start_lat is not None and request.start_lon is not None:
        gps_point = {
            "id": "gps_current", "ten": "📍 Vị trí của bạn",
            "lat": request.start_lat, "lon": request.start_lon,
            "time": 0, "loai_hinh": "diem_xuat_phat",
            "open_time": default_open, "close_time": default_close,
        }
        all_points_with_gps = [gps_point] + all_points
        global_matrix = get_global_osrm_matrix(all_points_with_gps, vehicle_type=request.vehicle_type)
        all_points = all_points_with_gps
        starting_points = [gps_point]
    elif request.start_point and request.start_point.strip():
        keyword = request.start_point.strip().lower()
        matched = [p for p in all_points if keyword in p["ten"].lower()]
        starting_points = matched[:1] if matched else all_points[:1]
    else:
        starting_points = all_points[:3]

    # 6. Chạy NSGA-II cho từng điểm xuất phát
    initial_k_density = get_density_factor(request.start_time)
    k_restriction = get_large_vehicle_restriction_factor(request.vehicle_type, request.start_time)

    generated_routes: List[dict] = []
    seen_fingerprints = set()
    config = NSGA2Config(pop_size=50, n_generations=60, pc=0.9, pm=0.25)

    for origin in starting_points:
        k_weather = get_weather_factor(origin["lat"], origin["lon"])
        k_total = k_weather * k_restriction

        local_points = [origin] + [p for p in all_points if p["id"] != origin["id"]]

        ctx = NSGA2Context(
            points_data=local_points,
            matrix_dict=global_matrix,
            k_total=k_total,
            k_density=initial_k_density,
            clock_start=clock_start,
            clock_end=clock_end,
            base_date=base_date,
        )

        pareto_front = run_nsga2(ctx, config)
        # sắp theo f1 (điểm giá trị) giảm dần để đánh số route dễ đọc
        pareto_front.sort(key=lambda ind: -ind.raw[0])

        for rank_i, ind in enumerate(pareto_front):
            if len(ind.route) < 2:
                continue
            fp = frozenset(local_points[i]["id"] for i in ind.route)
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)

            f1, f2, f3, f4 = ind.raw
            label = f"🧬 NSGA-II #{rank_i + 1} · {int(f3)} điểm · {f1:.0f}đ"
            route_out = build_route_output(
                ind.route, local_points, global_matrix, k_total, initial_k_density,
                base_date, clock_start, request.vehicle_type, label,
            )
            if route_out:
                route_out["pareto_objectives"] = {
                    "tong_diem_gia_tri": round(f1, 1),
                    "tong_thoi_gian_di_chuyen_phut": round(f2, 1),
                    "so_diem_ghe_tham": int(f3),
                    "muc_do_da_dang": round(f4, 2),
                }
                generated_routes.append(route_out)

    if not generated_routes:
        return {
            "status": "error",
            "message": "NSGA-II không tìm được lộ trình hợp lệ nào — quỹ thời gian có thể quá ngắn.",
        }

    generated_routes.sort(key=lambda r: (-len(r["optimized_route"]), r["total_time_minutes"]))
    for i, r in enumerate(generated_routes):
        r["route_id"] = i + 1

    elapsed_ms = (time.perf_counter() - t0) * 1000

    return {
        "status": "success",
        "algorithm": "NSGA-II",
        "elapsed_ms": round(elapsed_ms, 1),
        "available_minutes": available_minutes,
        "trip_date": str(base_date),
        "vehicle_type": request.vehicle_type,
        "routes": generated_routes,
    }