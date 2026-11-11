"""
Hybrid NSGA-II for MO-OPTW-DC (3 objectives, all minimised internally):
  f1 = -sum_j Experience(j)                      Experience = w*SABSA + (1-w)*Rating
  f2 = total time = travel(t) + visit + wait [+ leg to end node N+1]
  f3 = sum_j Crowd(j, y_j) + WeatherPenalty(j, y_j)      (y_j = ARRIVAL time at j)

Hybrid components (each can be switched off -> the SAME code gives the baselines/ablations):
  use_seeds : diverse constructive seeds (nearest / best-score / score-per-minute / roulette)
  use_2opt  : crossing-aware 2-opt on seeds AND on a fraction of children every generation
Constraint handling: repair operator (drops the infeasible tail, keeps order) + Deb's
constrained-domination, so a feasible solution always beats an infeasible one.
Travel time: matrix duration x traffic.factor(prev, next, clock) -> TomTom enters f2.
"""
from __future__ import annotations
import math, random, time
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional, Tuple

from config import CFG
from core import nsga2 as N
from core.nsga2 import Individual                      # re-exported (run_experiments / tests use H.Individual)
from core.objectives import experience_of, objective_vector, ObjectiveScaler
from core.timeline import walk, violation, window_dt, visit_minutes
from replan_engine import count_crossings
from categories import category_of, excess_cats, resolve_caps

CAP_PENALTY = CFG.penalty.category_cap   # kept as a module attribute for backward compatibility
exp_of = experience_of                    # one definition of per-POI experience (core.objectives)


class Ctx:
    """Everything an evaluation needs; avoids 9-argument signatures."""
    def __init__(self, pts, matrix, start, end, base_date, traffic, crowd_fn, weather_fn, end_idx=None, caps="auto"):
        self.caps = resolve_caps(caps, (end - start).total_seconds() / 3600.0)
        self.cat = [category_of(p) for p in pts]
        self.pts, self.m, self.start, self.end, self.date = pts, matrix, start, end, base_date
        self.traffic, self.crowd_fn, self.weather_fn, self.end_idx = traffic, crowd_fn, weather_fn, end_idx
        w = [window_dt(p, base_date) for p in pts]
        self.open, self.close = [a for a, _ in w], [b for _, b in w]
        self.xy = [(float(p["lat"]), float(p["lon"])) for p in pts]
        self.visit = [visit_minutes(p) for p in pts]

    def leg(self, i: int, j: int, clock: datetime) -> float:
        a, b = self.pts[i], self.pts[j]
        return float(self.m[a["id"]][b["id"]]["duration"]) * float(self.traffic.factor(a, b, clock))

    def scaler(self) -> ObjectiveScaler:
        """Instance-adaptive objective bounds (see core.objectives)."""
        return ObjectiveScaler.for_instance(self.pts[1:] if len(self.pts) > 1 else self.pts,
                                            (self.end - self.start).total_seconds() / 60.0)


def schedule(route, c: Ctx):
    """-> (total_minutes, wait, violation_minutes, arrival_times). Built on core.timeline.walk."""
    tl = walk(route, c.start, leg=c.leg, window=lambda i: (c.open[i], c.close[i]),
              visit=lambda i: c.visit[i])
    total = tl.total_minutes()
    cv = violation(tl, c.end)          # origin's own window is not a constraint; trip end counted once
    cv += CAP_PENALTY * excess_cats((c.cat[i] for i in route[1:]), c.caps)
    if c.end_idx is not None and route:
        d = c.leg(route[-1], c.end_idx, tl.finish)
        total += d
        cv += max(0.0, (tl.finish + timedelta(minutes=d) - c.end).total_seconds() / 60)
    return total, tl.wait_minutes, cv, tl.arrivals


def feasible(route, c: Ctx) -> bool:
    return schedule(route, c)[2] <= 1e-9


def repair(route, c: Ctx):
    """Keep the origin, keep the order, skip every POI that would break a hard constraint."""
    if not route:
        return route
    cur = [route[0]]
    for idx in route[1:]:
        if idx in cur:
            continue
        if feasible(cur + [idx], c):
            cur.append(idx)
    return cur


def evaluate(route, c: Ctx) -> Individual:
    total, wait, cv, arr = schedule(route, c)
    obj = objective_vector([c.pts[i] for i in route[1:]], arr[1:], total, c.crowd_fn, c.weather_fn)
    return Individual(route, obj, cv)


def route_objectives(route_idx, c: Ctx):
    """Same objective vector for ANY method's route (used for fair, shared-scale HV)."""
    return evaluate(route_idx, c).obj


# ------------------------------------------------------------ 2-opt (indices) ---
def _crossings_idx(route, xy) -> int:
    return count_crossings([{"lat": xy[i][0], "lon": xy[i][1]} for i in route])


def two_opt_idx(route, c: Ctx, max_rounds=None, time_tol: Optional[float] = None):
    """Crossing-aware 2-opt; accepts a reversal only if the schedule stays feasible.
    time_tol: if set (e.g. 0.05), a crossing-removing move is rejected when it makes the route more
    than 5% slower (default None = crossings first, as in the paper)."""
    if len(route) < 4:
        return route
    max_rounds = CFG.nsga.two_opt_max_rounds if max_rounds is None else max_rounds
    best = route[:]
    best_x = _crossings_idx(best, c.xy)
    best_t = schedule(best, c)[0]
    for _ in range(max_rounds):
        improved = False
        for i in range(1, len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                total, _w, cv, _a = schedule(cand, c)
                if cv > 1e-9:
                    continue
                x = _crossings_idx(cand, c.xy)
                if time_tol is not None and total > best_t * (1 + time_tol) and x < best_x:
                    continue
                if x < best_x or (x == best_x and total + 1e-9 < best_t):
                    best, best_x, best_t, improved = cand, x, total, True
                    break
            if improved:
                break
        if not improved:
            break
    return best


# ------------------------------------------------------------------- seeds ---
def make_seeds(c: Ctx, start_idx: int, rng: random.Random, n_seeds: int):
    """Diverse constructive heuristics (NOT 10 copies of the same nearest-neighbour tour)."""
    cand = [i for i in range(len(c.pts)) if i != start_idx]
    kinds = ["nearest", "score", "ratio"] + ["wrandom"] * max(0, n_seeds - 3)
    seeds = []
    for kind in kinds[:n_seeds]:
        remain, route, clock = cand[:], [start_idx], c.start
        while remain:
            cur = route[-1]
            def travel(i): return c.leg(cur, i, clock)
            if kind == "nearest":
                nxt = min(remain, key=travel)
            elif kind == "score":
                nxt = max(remain, key=lambda i: exp_of(c.pts[i]))
            else:
                def ratio(i): return exp_of(c.pts[i]) / max(1.0, travel(i) + float(c.pts[i].get("time") or 0))
                if kind == "ratio":
                    nxt = max(remain, key=ratio)
                else:
                    ws = [max(1e-6, ratio(i)) for i in remain]
                    nxt = rng.choices(remain, weights=ws, k=1)[0]
            trial = route + [nxt]
            if feasible(trial, c):
                route = trial
                clock = schedule(route, c)[3][-1] + timedelta(minutes=float(c.pts[nxt].get("time") or 0))
            remain.remove(nxt)
        seeds.append(route)
    return seeds


# --------------------------------------------------------------- NSGA-II core ---
# dominance / sorting / crowding / operators live in core.nsga2 (shared with the baseline).
_dominates, _fast_sort, _crowding, _tournament = N.dominates, N.fast_sort, N.crowding, N.tournament
_order_crossover = N.order_crossover


def _mutate(route, rng, n_all, rate=None):
    return N.mutate(route, rng, n_all, CFG.nsga.mutation_rate if rate is None else rate)


def run(points: List[dict], matrix: dict, start_idx: int, start: datetime, end: datetime, base_date,
        traffic=None, crowd_fn=None, weather_fn=None, pop_size: Optional[int] = None,
        generations: Optional[int] = None, seed: Optional[int] = None,
        end_idx: Optional[int] = None, use_seeds=True, use_2opt=True, p_local: Optional[float] = None,
        travel_factor: float = 1.0, pool_out: Optional[list] = None, caps="auto"):
    """Returns (pareto_front: List[Individual], elapsed_ms).
    `traffic` needs .factor(a, b, when); if None a constant `travel_factor` is used.
    pop_size / generations / seed / p_local default to config.CFG.nsga (env-overridable).
    use_seeds=False, use_2opt=False  ->  the standard NSGA-II baseline (same code, same objectives)."""
    g = CFG.nsga
    pop_size = g.pop_size if pop_size is None else pop_size
    generations = g.generations if generations is None else generations
    seed = g.seed if seed is None else seed
    p_local = g.p_local if p_local is None else p_local
    if traffic is None:
        from signals import constant_traffic
        traffic = constant_traffic(travel_factor)
    crowd_fn = crowd_fn or (lambda p, w: {"crowd_index": 0.0})
    weather_fn = weather_fn or (lambda p, w: 0.0)
    rng, t0 = random.Random(seed), time.perf_counter()
    n = len(points)
    if n < 2:
        return [], 0
    c = Ctx(points, matrix, start, end, base_date, traffic, crowd_fn, weather_fn, end_idx, caps)
    archive: dict = {}
    def ev(r):
        ind = evaluate(r, c)
        if ind.cv <= 1e-9:
            archive.setdefault(tuple(r), ind)
        return ind
    cand = list(range(n)); cand.remove(start_idx)

    pop: List[Individual] = []
    if use_seeds:
        for s in make_seeds(c, start_idx, rng, min(g.n_seeds, pop_size)):
            s = repair(s, c)
            if use_2opt:
                s = two_opt_idx(s, c)
            pop.append(ev(s))
    while len(pop) < pop_size:
        k = rng.randint(min(g.init_min_pois, len(cand)), min(g.init_max_pois, len(cand)))
        r = repair([start_idx] + rng.sample(cand, k), c)
        pop.append(ev(r))

    for _ in range(generations):
        for f in _fast_sort(pop):
            _crowding(f)
        children = []
        while len(children) < pop_size:
            a, b = _tournament(pop, rng), _tournament(pop, rng)
            r = _mutate(_order_crossover(a.route, b.route, rng), rng, n)
            r = repair(r, c)
            if use_2opt and rng.random() < p_local:
                r = two_opt_idx(r, c)
            children.append(ev(r))
        pop = N.environmental_selection(pop, children, pop_size)

    front = _fast_sort(pop)[0]
    if use_2opt:   # final polish of the returned front (still feasibility-checked)
        polished = [ev(two_opt_idx(i.route, c)) for i in front]
        front = [x for x in _fast_sort(polished + front)[0]]
    _crowding(front)
    seen, out = set(), []
    for ind in sorted(front, key=lambda x: x.obj):
        sig = tuple(ind.route)
        if sig not in seen:
            seen.add(sig); out.append(ind)
    if pool_out is not None:      # every feasible route seen: material for diverse selection
        pool_out.extend(archive.values())
    return out, round((time.perf_counter() - t0) * 1000, 2)


# ---------------------------------------------------------------- diversity ---
def route_similarity(a: Individual, b: Individual) -> float:
    """Jaccard overlap of the visited POI sets (origin excluded). 1.0 = same places."""
    A, B = set(a.route[1:]), set(b.route[1:])
    return len(A & B) / max(1, len(A | B))


def select_diverse(front, pool=None, k_min: Optional[int] = None, k_max: Optional[int] = None,
                   min_pois: Optional[int] = None, limits=None):
    """Pick 3..5 genuinely different routes. Returns [(Individual, label)].

    1) candidates = Pareto front + every feasible route seen during the search (pool);
       tiny degenerate routes (< half the longest) are ignored;
    2) anchors: best experience / fastest / least crowd+weather -> the three Pareto 'corners';
    3) the rest by max-min distance (farthest from everything already chosen);
    4) a route is accepted only if its POI-set overlap with every chosen one <= limit; the limit is
       relaxed step by step (0.5 -> 1.01) ONLY if fewer than k_min routes were found. Never pads
       with duplicates: fewer than k_min distinct routes are returned as-is (caller can warn).
    """
    R = CFG.routing
    k_min = R.min_routes if k_min is None else k_min
    k_max = R.max_routes if k_max is None else k_max
    limits = R.hybrid_jaccard_limits if limits is None else limits
    uniq = {}
    for ind in list(front) + list(pool or []):
        if ind.cv <= 1e-9 and len(ind.route) >= 2:
            uniq.setdefault(tuple(ind.route), ind)
    cands = list(uniq.values())
    if not cands:
        return []
    longest = max(len(c.route) - 1 for c in cands)
    # a suggestion should be a real outing: >= 2 stops and >= 60% of the longest feasible route
    floor = min_pois if min_pois is not None else min(longest, max(2, math.ceil(R.min_route_fraction * longest)))
    pools = [[c for c in cands if len(c.route) - 1 >= floor], cands]
    anchors = [("Trải nghiệm cao nhất", lambda c: c.obj[0]), ("Nhanh nhất", lambda c: c.obj[1]),
               ("Ít đông & thời tiết xấu nhất", lambda c: c.obj[2])]
    best = []
    for good in pools:
        for lim in limits:
            chosen, labels = [], []
            ok = lambda c: all(c is not s and route_similarity(c, s) <= lim for s in chosen)
            for lab, key in anchors:
                for c in sorted(good, key=key):
                    if ok(c):
                        chosen.append(c); labels.append(lab); break
            while len(chosen) < k_max:
                rest = [c for c in good if ok(c)]
                if not rest:
                    break
                nxt = max(rest, key=lambda c: min(1 - route_similarity(c, s) for s in chosen))
                chosen.append(nxt); labels.append("Cân bằng")
            if len(chosen) >= k_min:
                return list(zip(chosen[:k_max], labels[:k_max]))
            if len(chosen) > len(best):
                best = list(zip(chosen, labels))
    return best
