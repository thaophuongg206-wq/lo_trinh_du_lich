"""
Disruption detection for in-tour re-planning (Mode 2 trigger).

check_disruption(...) looks ahead along the REMAINING legs of the itinerary and reports why a re-plan
is needed:
  - traffic : live TomTom factor on an upcoming leg >= jam_threshold
  - rain    : weather penalty at an upcoming OUTDOOR POI, at its planned arrival time, >= rain_threshold
It only detects. The caller (POST /api/replan/check) decides whether to run replan_route.
There is no background loop: the client polls (e.g. every 5 min while the tour is active).
"""
from __future__ import annotations
from datetime import datetime
from typing import Callable, Dict, List


def check_disruption(remaining: List[dict], now: datetime, traffic, weather_fn: Callable[[dict, datetime], float],
                     base_date, jam_threshold: float = 1.5, rain_threshold: float = 0.5) -> Dict:
    """remaining[0] = the POI the tourist is at / just left; the rest are upcoming. Each place needs
    id, lat, lon, loai_hinh and (optionally) arrive_time 'HH:MM'."""
    reasons: List[dict] = []
    for a, b in zip(remaining, remaining[1:]):
        f = float(traffic.factor(a, b, now))
        if f >= jam_threshold:
            reasons.append({"type": "traffic", "leg": [a.get("ten", a["id"]), b.get("ten", b["id"])], "factor": round(f, 2)})
    for p in remaining[1:]:
        try:
            when = datetime.combine(base_date, datetime.strptime(p["arrive_time"], "%H:%M").time())
        except Exception:
            when = now
        pen = float(weather_fn(p, when))
        if pen >= rain_threshold:
            reasons.append({"type": "rain", "poi": p.get("ten", p["id"]), "penalty": round(pen, 2), "at": when.strftime("%H:%M")})
    return {"triggered": bool(reasons), "reasons": reasons,
            "thresholds": {"jam_factor": jam_threshold, "rain_penalty": rain_threshold}}
