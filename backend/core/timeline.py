"""Single travel -> wait -> visit simulator.

Before this module the same loop was written four times (main.calculate_cost_with_clock,
hybrid_nsga.schedule, replan_engine.simulate_tail / check_time_windows, OnlyNSGA.repair_route)
with small, silent differences (midnight roll-over, waiting at the origin, visit time scaled by a
traffic factor in one of them, ...). Every caller now builds on `walk()` and only decides how to
*score* the resulting stops, so a fix lands everywhere at once.

The module is dependency-free (stdlib only) so it can be unit-tested in isolation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, List, Optional, Sequence, Tuple

Window = Tuple[datetime, datetime]


# ----------------------------------------------------------------------------- windows
def window_dt(p: dict, base_date: date) -> Window:
    """(open, close) datetimes of a POI. close <= open means it closes after midnight."""
    op = datetime.combine(base_date, p["open_time"])
    cl = datetime.combine(base_date, p["close_time"])
    if cl <= op:
        cl += timedelta(days=1)
    return op, cl


def optional_window(p: dict, base_date: date) -> Optional[Window]:
    """Like window_dt but tolerates a POI that has no opening hours (always open)."""
    if p.get("open_time") is None or p.get("close_time") is None:
        return None
    op, cl = p["open_time"], p["close_time"]
    if isinstance(op, datetime) and isinstance(cl, datetime):     # already absolute datetimes
        return op, cl
    return window_dt(p, base_date)


def visit_minutes(p: dict) -> float:
    """Visit duration in minutes. NEVER scaled by traffic/density (traffic only affects travel)."""
    return float(p.get("time") or p.get("visit_time") or 0.0)


# ----------------------------------------------------------------------------- distance
def approx_km(a: dict, b: dict) -> float:
    """Equirectangular distance (km). Longitude is scaled by cos(lat): ~7% shorter than the
    naive lat/lon x 111 at Hanoi's latitude, which the old copies ignored."""
    kx = math.cos(math.radians((float(a["lat"]) + float(b["lat"])) / 2.0))
    return math.hypot(float(a["lat"]) - float(b["lat"]),
                      (float(a["lon"]) - float(b["lon"])) * kx) * 111.0


def matrix_minutes(matrix: dict, a: dict, b: dict, fallback_kmh: float = 20.0,
                   on_fallback: Optional[Callable[[dict, dict], None]] = None) -> float:
    """Base duration (min) from the OSRM matrix; straight-line / fallback_kmh if the pair is
    missing. `on_fallback` lets the caller record that the matrix did NOT cover the pair
    (see core.audit) instead of the fallback happening silently."""
    try:
        return float(matrix[str(a["id"])][str(b["id"])]["duration"])
    except (KeyError, TypeError):
        if on_fallback:
            on_fallback(a, b)
        return approx_km(a, b) / max(1e-6, fallback_kmh) * 60.0


# ----------------------------------------------------------------------------- one step
def step(clock: datetime, travel_min: float, win: Optional[Window], visit_min: float):
    """Advance ONE leg: drive, wait for opening, visit.
    -> (arrive, visit_start, depart, wait_min, late_min). The only place this arithmetic lives:
    `walk()` and the exact DP solver both call it."""
    arrive = clock + timedelta(minutes=float(travel_min))
    began, wait = arrive, 0.0
    if win is not None and arrive < win[0]:
        wait = (win[0] - arrive).total_seconds() / 60.0
        began = win[0]
    depart = began + timedelta(minutes=float(visit_min))
    late = max(0.0, (depart - win[1]).total_seconds() / 60.0) if win is not None else 0.0
    return arrive, began, depart, wait, late


# ----------------------------------------------------------------------------- the walk
@dataclass
class Stop:
    pos: int
    node: Any
    travel: float          # minutes driven to reach this node (0 for the first)
    arrive: datetime       # moment the vehicle reaches the node
    wait: float            # minutes waiting for it to open
    start: datetime        # visit starts (arrive + wait)
    depart: datetime       # visit ends
    late: float            # minutes the visit ends AFTER the node closes (0 = ok)
    visit: float = 0.0     # minutes spent at the node


@dataclass
class Timeline:
    stops: List[Stop] = field(default_factory=list)
    finish: Optional[datetime] = None

    @property
    def arrivals(self) -> List[datetime]:
        """Visit-start times — the 'y_j' the crowd/weather objectives are evaluated at."""
        return [s.start for s in self.stops]

    @property
    def travel_minutes(self) -> float:
        return sum(s.travel for s in self.stops)

    @property
    def wait_minutes(self) -> float:
        return sum(s.wait for s in self.stops)

    @property
    def visit_minutes(self) -> float:
        return sum(s.visit for s in self.stops)

    def total_minutes(self, t0: Optional[datetime] = None) -> float:
        """travel + wait + visit. Summed from the parts (not finish - t0) so it is free of the
        microsecond rounding datetime arithmetic adds."""
        return self.travel_minutes + self.wait_minutes + self.visit_minutes

    @property
    def first_late_pos(self) -> Optional[int]:
        for s in self.stops:
            if s.late > 1e-9:
                return s.pos
        return None


def walk(nodes: Sequence[Any], start: datetime, *,
         leg: Callable[[Any, Any, datetime], float],
         window: Callable[[Any], Optional[Window]],
         visit: Callable[[Any], float] = visit_minutes,
         anchor_first: bool = False) -> Timeline:
    """Simulate nodes[0] -> nodes[1] -> ... starting at `start`.

    leg(prev, node, clock)  travel minutes of that leg when leaving at `clock`
    window(node)            (open, close) or None
    visit(node)             visit minutes (never traffic-scaled)

    The origin (pos 0) is not travelled to, but it still waits for its own opening time and is
    visited, exactly like every other node. `late` is the overshoot of the visit END past close;
    callers decide whether to count the origin's lateness and which deadline applies to the trip.

    anchor_first=True: nodes[0] is only the CURRENT POSITION (re-planning: the place we are
    standing at, already visited) — no waiting, no visit, `start` is the moment we leave it.
    """
    tl = Timeline()
    clock = start
    for pos, node in enumerate(nodes):
        if anchor_first and pos == 0:
            tl.stops.append(Stop(0, node, 0.0, clock, 0.0, clock, clock, 0.0, 0.0))
            continue
        travel = float(leg(nodes[pos - 1], node, clock)) if pos else 0.0
        v = float(visit(node))
        arrive, began, clock, wait, late = step(clock, travel, window(node), v)
        tl.stops.append(Stop(pos, node, travel, arrive, wait, began, clock, late, v))
    tl.finish = clock
    return tl


def violation(tl: Timeline, deadline: datetime, *, count_origin: bool = False) -> float:
    """Total constraint violation in minutes: each POI's own overshoot past its closing time plus
    the trip-level overshoot past `deadline`, counted ONCE (final clock) and only for real trips."""
    stops = tl.stops if count_origin else tl.stops[1:]
    cv = sum(s.late for s in stops)
    if len(tl.stops) > 1 and tl.finish is not None:
        cv += max(0.0, (tl.finish - deadline).total_seconds() / 60.0)
    return cv
