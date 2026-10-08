"""
Fast in-tour dynamic re-planning using Greedy + 2-opt.

The engine accepts the already selected itinerary and a set of dynamic
penalties (traffic congestion, weather), then preserves pinned/visited
points where possible and guarantees zero route crossings (OL = 0)
via 2-opt local search.

Also exposes helpers for mathematical constraint checks used by the
research evaluation (flow conservation, time-window feasibility).
"""
from __future__ import annotations
from typing import Callable, List, Dict, Any, Optional, Tuple
import math

from config import CFG
from core.timeline import (window_dt, optional_window, visit_minutes, approx_km, walk, Timeline)


def _seg_intersect(a, b, c, d) -> bool:
    """Proper segment intersection test (excluding shared endpoints)."""
    def orient(p, q, r):
        val = (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])
        if abs(val) < 1e-12:
            return 0
        return 1 if val > 0 else 2

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    if o1 != o2 and o3 != o4:
        if a == c or a == d or b == c or b == d:
            return False
        return True
    return False


def count_crossings(order: List[dict]) -> int:
    """Count geometric edge crossings (Overlap Crossings / OL) of an OPEN path (first and last edge
    are NOT adjacent, so they are tested too) on a locally-planar projection
    (longitude scaled by cos(latitude): ~7% shorter than latitude at Hanoi)."""
    if len(order) < 4:
        return 0
    import math
    lat0 = sum(float(p.get("lat", 0)) for p in order) / len(order)
    kx = math.cos(math.radians(lat0))
    pts = [(float(p.get("lat", 0)), float(p.get("lon", 0)) * kx) for p in order]
    n = len(pts)
    crosses = 0
    for i in range(n - 1):
        for j in range(i + 2, n - 1):
            if _seg_intersect(pts[i], pts[i + 1], pts[j], pts[j + 1]):
                crosses += 1
    return crosses


def two_opt(
    order: List[dict],
    cost_fn: Callable[[dict, dict], float],
    fixed: int = 1,
    feasible_fn: Optional[Callable[[List[dict]], bool]] = None,
    max_rounds: int = 50,
    total_cost_fn: Optional[Callable[[List[dict]], float]] = None,
) -> List[dict]:
    """Crossing-aware 2-opt.

    - the first `fixed` elements never move (origin / already visited POIs);
    - a candidate is accepted only if feasible_fn(candidate) is True (time windows
      are re-checked after every reversal, so reordering can never silently break them);
    - criterion: fewer crossings first, then lower total cost.
    """
    if len(order) < 4:
        return order[:]
    best = order[:]
    for _ in range(max_rounds):
        improved = False
        _cost = total_cost_fn or (lambda r: sum(cost_fn(r[k], r[k + 1]) for k in range(len(r) - 1)))
        best_cost = _cost(best)
        best_cross = count_crossings(best)
        for i in range(max(1, fixed), len(best) - 1):
            for j in range(i + 1, len(best)):
                cand = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                if feasible_fn is not None and not feasible_fn(cand):
                    continue
                c = _cost(cand)
                cr = count_crossings(cand)
                if cr < best_cross or (cr == best_cross and c + 1e-9 < best_cost):
                    best, best_cost, best_cross, improved = cand, c, cr, True
                    break
            if improved:
                break
        if not improved:
            break
    return best


def greedy_2opt(
    points: List[dict],
    start_id: str,
    penalty_fn: Callable[[dict], float] = lambda p: 0.0,
    pinned_ids: Optional[List[str]] = None,
) -> List[dict]:
    """
    Build a feasible order starting from start_id, optionally keeping
    pinned points fixed in relative order, then polish with 2-opt.
    """
    if not points:
        return []
    by_id = {str(p["id"]): p for p in points}
    current = by_id.get(str(start_id), points[0])
    remaining = [p for p in points if p is not current]
    order = [current]

    pinned = set(str(x) for x in (pinned_ids or []))
    n_fixed = 1
    if pinned:
        # visited / pinned POIs keep their ORIGINAL relative order and are never moved
        pinned_ordered = [p for p in points if str(p["id"]) in pinned and p is not current]
        order.extend(pinned_ordered)
        n_fixed = len(order)
        remaining = [p for p in remaining if str(p["id"]) not in pinned]
        current = order[-1]

    while remaining:
        nxt = min(remaining, key=lambda p: approx_km(current, p) + penalty_fn(p))
        order.append(nxt)
        remaining.remove(nxt)
        current = nxt

    def cost(a, b):
        return approx_km(a, b) + penalty_fn(b)

    return two_opt(order, cost, fixed=n_fixed)


def check_flow_conservation(route: List[dict], start_id: Optional[str] = None,
                            end_id: Optional[str] = None, return_to_start: bool = False) -> bool:
    """Flow conservation of the path 0 -> ... -> N+1 (x_ij binary):
       - exactly one arc leaves the start and (if an end node exists) exactly one arc enters it;
       - every intermediate node has in-degree = out-degree = 1 (each POI visited at most once);
       - the start is the declared origin (start_id) and, if given, the end is end_id.
       With return_to_start the closing arc goes back to the origin (origin counted once)."""
    if not route:
        return False
    ids = [str(p["id"]) for p in route]
    if start_id is not None and ids[0] != str(start_id):
        return False
    body = ids[1:]
    if return_to_start:
        if len(set(body)) != len(body) or ids[0] in body:
            return False
    elif len(ids) != len(set(ids)):
        return False
    if end_id is not None and ids[-1] != str(end_id):
        return False
    arcs = list(zip(ids, ids[1:])) + ([(ids[-1], ids[0])] if return_to_start and len(ids) > 1 else [])
    out_deg, in_deg = {}, {}
    for a, b in arcs:
        out_deg[a] = out_deg.get(a, 0) + 1; in_deg[b] = in_deg.get(b, 0) + 1
    for node in set(ids):
        if out_deg.get(node, 0) > 1 or in_deg.get(node, 0) > 1:
            return False
    if len(ids) > 1 and out_deg.get(ids[0], 0) != 1:
        return False
    return True


def check_time_windows(
    route: List[dict],
    matrix: Dict[str, Dict[str, Dict[str, float]]],
    start,
    end,
    base_date,
    travel_factor: float = 1.0,
    factor_fn: Optional[Callable] = None,
) -> Tuple[bool, float]:
    """
    Return (feasible, total_minutes). Soft wait is allowed before open time;
    hard violation if depart after close or after trip end.
    """
    if not route:
        return False, 0.0

    def leg(prev, p, clock):
        d = matrix.get(str(prev["id"]), {}).get(str(p["id"]), {})
        f = factor_fn(prev, p, clock) if factor_fn else travel_factor
        return float(d.get("duration", 15)) * f

    tl = walk(route, start, leg=leg, window=lambda p: optional_window(p, base_date))
    total = 0.0
    for s in tl.stops:                       # stop at the first violation, like the old loop
        total += s.travel + s.wait + s.visit
        if s.late > 1e-9 or s.depart > end:
            return False, total
    return True, total


# ----------------------------------------------------------------------------
# In-tour re-planning (Mode 2)
# ----------------------------------------------------------------------------
def _leg_min(a, b, clock, matrix, factor_fn):
    return float(matrix[str(a["id"])][str(b["id"])]["duration"]) * float(factor_fn(a, b, clock))


def simulate_tail(prefix_last, tail, now, end, base_date, matrix, factor_fn, end_pt=None):
    """Walk prefix_last -> tail (-> end_pt) starting at `now`. Returns (feasible, finish_clock, arrivals).
    Built on core.timeline.walk; on the first violation returns the clock at that point."""
    from datetime import timedelta
    tl = walk([prefix_last] + list(tail), now,
              leg=lambda a, b, clk: _leg_min(a, b, clk, matrix, factor_fn),
              window=lambda p: optional_window(p, base_date), anchor_first=True)
    arrivals = []
    for s in tl.stops[1:]:
        arrivals.append(s.start)
        if s.late > 1e-9 or s.depart > end:
            return False, s.depart, arrivals
    clock, prev = tl.finish, (tail[-1] if tail else prefix_last)
    if end_pt is not None:
        clock += timedelta(minutes=_leg_min(prev, end_pt, clock, matrix, factor_fn))
        if clock > end:
            return False, clock, arrivals
    return True, clock, arrivals


def replan_route(
    route: List[dict],
    pool: List[dict],
    matrix,
    now,
    end,
    base_date,
    factor_fn: Callable,
    penalty_fn: Callable[[dict, Any], float] = lambda p, t: 0.0,   # (poi, ARRIVAL time) -> minutes-equivalent
    n_fixed: int = 1,
    end_pt: Optional[dict] = None,
    pool_limit: Optional[int] = None,
    caps: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Greedy rebuild + crossing-aware 2-opt + backup-POI fill, all time-window checked.

    route[:n_fixed] are visited/pinned and NEVER touched; `now` is the clock when leaving
    route[n_fixed-1]. POIs that no longer fit (closed, over budget under the new traffic)
    are dropped and reported; spare time is refilled from `pool` (backup POIs).
    """
    import time as _t
    t0 = _t.perf_counter()
    n_fixed = max(1, min(n_fixed, len(route)))
    prefix, last = route[:n_fixed], route[n_fixed - 1]
    remaining = route[n_fixed:]
    tail: List[dict] = []
    dropped: List[dict] = []

    from categories import excess
    base_ex = excess(prefix[1:], caps)       # never make the quota situation worse than the visited prefix

    def ok(tl):
        if caps and excess((prefix + tl)[1:], caps) > base_ex:
            return False
        return simulate_tail(last, tl, now, end, base_date, matrix, factor_fn, end_pt)[0]

    cur = last
    while remaining:
        _, clk, _a = simulate_tail(last, tail, now, end, base_date, matrix, factor_fn)
        cand = [p for p in remaining if ok(tail + [p])]
        if not cand:
            dropped.extend(remaining)
            break
        from datetime import timedelta as _td
        def _score(p):
            leg = _leg_min(cur, p, clk, matrix, factor_fn)
            return leg + penalty_fn(p, clk + _td(minutes=leg))      # penalty at the ARRIVAL time
        nxt = min(cand, key=_score)
        tail.append(nxt); remaining.remove(nxt); cur = nxt

    def cost(a, b):          # only used if total_cost_fn is absent
        return float(matrix[str(a["id"])][str(b["id"])]["duration"])

    # 2-opt minimises the SAME quantity the feasibility check simulates (finish clock incl. traffic
    # factor and waiting). The weather penalty is NOT in this cost: it is constant under reordering.
    def finish(cand):
        return simulate_tail(last, cand[n_fixed:], now, end, base_date, matrix, factor_fn, end_pt)[1].timestamp()

    full = two_opt(prefix + tail, cost, fixed=n_fixed, total_cost_fn=finish,
                   feasible_fn=lambda cand: ok(cand[n_fixed:]))
    tail = full[n_fixed:]

    # backup POIs: best experience gain per extra minute, only if still feasible
    # only the most promising backups are tried (keeps latency ~tens of ms)
    pool_limit = CFG.routing.replan_pool_limit if pool_limit is None else pool_limit
    pool = sorted(pool, key=lambda p: -float(p.get("experience_score") or 0))[:pool_limit]
    added: List[dict] = []
    used = {str(p["id"]) for p in route} | {str(p["id"]) for p in tail}
    improved = True
    while improved:
        improved = False
        _, fin0, _a = simulate_tail(last, tail, now, end, base_date, matrix, factor_fn, end_pt)
        best = None
        for p in pool:
            if str(p["id"]) in used:
                continue
            for pos in range(len(tail) + 1):
                trial = tail[:pos] + [p] + tail[pos:]
                feas, fin, _a = simulate_tail(last, trial, now, end, base_date, matrix, factor_fn, end_pt)
                if not feas or (caps and excess((prefix + trial)[1:], caps) > base_ex):
                    continue
                extra = (fin - fin0).total_seconds() / 60 + penalty_fn(p, _a[pos])
                gain = float(p.get("experience_score") or 0) / max(1.0, extra)
                if best is None or gain > best[0]:
                    best = (gain, p, pos)
        if best:
            _, p, pos = best
            tail.insert(pos, p); added.append(p); used.add(str(p["id"])); improved = True
    final = prefix + tail
    feas, fin, arr = simulate_tail(last, tail, now, end, base_date, matrix, factor_fn, end_pt)
    return {
        "route": final, "dropped": dropped, "added": added, "feasible": feas,
        "finish": fin, "arrivals": arr,
        "overlap_crossings": count_crossings(final),
        "latency_ms": round((_t.perf_counter() - t0) * 1000, 2),
    }
