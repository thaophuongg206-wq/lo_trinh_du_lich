"""
Cheap, OSRM-free candidate filtering BEFORE the distance matrix is requested.

Step A  filter_infeasible : drop POIs that can never be in a feasible route
          - excluded by the user / avoid-list
          - closed for the whole trip window, or open window shorter than the visit time
          - unreachable: even at an OPTIMISTIC speed on a straight line, arrival + visit
            (+ way back if return_to_start) does not fit the time budget
Step B  top_k             : keep the K most promising by (experience / time-cost), never
                            dropping protected (must-visit) POIs
Every bound is a LOWER bound on the true travel time (straight line, optimistic speed), so a POI
is only removed when it is certainly infeasible: the optimiser loses nothing.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta
from typing import Dict, Iterable, List, Optional, Tuple

# optimistic km/h (>= any realistic urban speed) => straight-line time is a true lower bound
OPT_SPEED = {"xe_may": 40.0, "o_to": 50.0, "di_bo": 6.0, "xe_dap": 18.0, "bus": 35.0}


def _km(a: dict, b: dict) -> float:
    r = 6371.0
    p1, p2 = math.radians(float(a["lat"])), math.radians(float(b["lat"]))
    dphi, dl = p2 - p1, math.radians(float(b["lon"]) - float(a["lon"]))
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def min_travel_min(a: dict, b: dict, vehicle_type: str) -> float:
    return _km(a, b) / OPT_SPEED.get((vehicle_type or "xe_may").lower(), 40.0) * 60.0


def filter_infeasible(points: List[dict], origin: dict, start: datetime, end: datetime, base_date,
                      vehicle_type: str = "xe_may", return_to_start: bool = False,
                      excluded_ids: Iterable[str] = ()) -> Tuple[List[dict], Dict[str, List[str]]]:
    excluded = {str(x) for x in excluded_ids}
    kept, dropped = [], {"excluded": [], "closed_window": [], "unreachable": []}
    for p in points:
        if str(p["id"]) in excluded:
            dropped["excluded"].append(p["ten"]); continue
        visit = float(p.get("time") or 0)
        from replan_engine import window_dt
        op, cl = window_dt(p, base_date)           # handles POIs that close after midnight
        t_go = min_travel_min(origin, p, vehicle_type)
        earliest_start = max(start + timedelta(minutes=t_go), op)
        latest_end = min(cl, end)
        if earliest_start + timedelta(minutes=visit) > latest_end:
            # distinguish "never open in window" from "too far"
            if max(start, op) + timedelta(minutes=visit) > latest_end:
                dropped["closed_window"].append(p["ten"])
            else:
                dropped["unreachable"].append(p["ten"])
            continue
        if return_to_start:
            back = min_travel_min(p, origin, vehicle_type)
            if earliest_start + timedelta(minutes=visit + back) > end:
                dropped["unreachable"].append(p["ten"]); continue
        kept.append(p)
    return kept, dropped


def top_k(points: List[dict], origin: dict, vehicle_type: str = "xe_may", k: int = 25,
          protect_ids: Iterable[str] = (), caps: Optional[dict] = None) -> Tuple[List[dict], List[str]]:
    if k <= 0 or len(points) <= k:
        return points, []
    protect = {str(x) for x in protect_ids}

    def value(p):
        cost = min_travel_min(origin, p, vehicle_type) + float(p.get("time") or 0)
        return float(p.get("experience_score") or 0) / (1.0 + cost / 60.0)

    from categories import category_of
    # a capped category (cafe, food...) can never contribute more than cap POIs to a route, so keeping
    # 3x cap candidates of it is plenty; the freed slots go to categories without a quota
    limit = {c: 3 * int(caps[c]) for c in ("cafe", "an_uong", "tttm") if caps and c in caps}
    keep = [p for p in points if str(p["id"]) in protect]
    n = {}
    for p in keep:
        n[category_of(p)] = n.get(category_of(p), 0) + 1
    # food+cafe together: at most 3x the combined quota AND at most 60% of the kept set
    food_lim = min(3 * int(caps["food_cafe_total"]), int(0.6 * k)) if caps and "food_cafe_total" in caps else 10 ** 9
    for p in sorted((p for p in points if str(p["id"]) not in protect), key=value, reverse=True):
        if len(keep) >= k:
            break
        c = category_of(p)
        if n.get(c, 0) >= limit.get(c, 10 ** 9):
            continue
        if c in ("cafe", "an_uong") and n.get("cafe", 0) + n.get("an_uong", 0) >= food_lim:
            continue
        keep.append(p); n[c] = n.get(c, 0) + 1
    ids = {str(p["id"]) for p in keep}
    return [p for p in points if str(p["id"]) in ids], [p["ten"] for p in points if str(p["id"]) not in ids]
