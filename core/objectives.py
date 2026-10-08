"""ONE definition of the three research objectives + instance-adaptive normalisation.

All objectives are expressed as MINIMISATION internally:
    f1 = -sum_j Experience(j)                    (j = every visited POI, origin excluded)
    f2 = total time = travel + visit + wait [+ leg to the end node]
    f3 = sum_j [ Crowd(j, y_j) + WeatherPenalty(j, y_j) ]   (y_j = ARRIVAL time at j)

hybrid_nsga and the NSGA-II baseline (OnlyNSGA) both import these, so the two algorithms
optimise *exactly* the same vector. (Before: the baseline used per-POI MEANS for f1/f3 and left the
waiting time out of f2, so a Hypervolume comparison between them was not comparing like with like.)

The three components live on very different scales (experience ~0..10, time ~0..hundreds of
minutes, crowd+weather ~0..20). `ObjectiveScaler` maps each to [0,1] using bounds derived from the
INSTANCE (candidate POIs + time window) — not from whatever happens to be on the current front — so
weighted rankings and hypervolumes are not dominated by whichever objective has the biggest unit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from config import CFG
from core.timeline import visit_minutes

Vec3 = Tuple[float, float, float]


# ----------------------------------------------------------------------------- experience
def experience_of(p: dict) -> float:
    """Experience of one POI in [0,1]. Prefers the pre-computed blend of SABSA and rating;
    falls back to the raw 0-10 rating."""
    v = p.get("experience_score")
    if v is not None:
        return float(v)
    return float(p.get("score") or 0.0) / 10.0


def blend_experience(sabsa: float, rating10: float, w_pref: float) -> float:
    """Experience = w*SABSA + (1-w)*Rating, both on [0,1]. ONE formula for every endpoint."""
    rating = max(0.0, min(1.0, float(rating10 or 0.0) / 10.0))
    return round(w_pref * float(sabsa) + (1.0 - w_pref) * rating, 4)


# ----------------------------------------------------------------------------- vector
def crowd_weather(p: dict, when: datetime, crowd_fn: Callable, weather_fn: Callable) -> float:
    return float(crowd_fn(p, when).get("crowd_index", 0.0)) + float(weather_fn(p, when))


def objective_vector(pois: Sequence[dict], arrivals: Sequence[datetime], total_minutes: float,
                     crowd_fn: Callable, weather_fn: Callable) -> Vec3:
    """(f1, f2, f3) for the VISITED pois (origin excluded) given their arrival times."""
    exp = sum(experience_of(p) for p in pois)
    pen = sum(crowd_weather(p, t, crowd_fn, weather_fn) for p, t in zip(pois, arrivals))
    return (-exp, float(total_minutes), pen)


# ----------------------------------------------------------------------------- scaling
@dataclass(frozen=True)
class ObjectiveScaler:
    """Instance-adaptive bounds. lo/hi are per objective in the internal (minimisation) form."""
    lo: Vec3
    hi: Vec3

    @classmethod
    def for_instance(cls, pois: Iterable[dict], window_minutes: float,
                     crowd_weather_per_poi_max: Optional[float] = None) -> "ObjectiveScaler":
        """Bounds that every FEASIBLE route must respect:

        * max #POIs K: even with zero travel, visit times alone must fit the window, so
          K = how many of the shortest visits fit;
        * f1 >= -(sum of the K best experiences);  f1 <= 0
        * f2 in [0, window_minutes]
        * f3 in [0, K * per_poi_max]   (crowd index and weather penalty are each in [0,1])
        """
        cw = crowd_weather_per_poi_max if crowd_weather_per_poi_max is not None \
            else CFG.obj.crowd_weather_per_poi_max
        pois = list(pois)
        durations = sorted(visit_minutes(p) for p in pois)
        k, used = 0, 0.0
        for d in durations:
            if used + d > window_minutes:
                break
            used += d
            k += 1
        k = max(1, min(k, len(pois))) if pois else 1
        best = sorted((experience_of(p) for p in pois), reverse=True)[:k]
        return cls(lo=(-sum(best), 0.0, 0.0), hi=(0.0, max(1.0, float(window_minutes)), k * cw))

    def normalise(self, obj: Sequence[float]) -> Vec3:
        """Map to [0,1] (0 = ideal, 1 = worst conceivable); values are clipped so a route that
        slightly overshoots a bound cannot distort a ranking."""
        out: List[float] = []
        for i in range(3):
            span = self.hi[i] - self.lo[i]
            x = 0.0 if span <= 1e-12 else (float(obj[i]) - self.lo[i]) / span
            out.append(min(1.0, max(0.0, x)))
        return (out[0], out[1], out[2])

    def weighted(self, obj: Sequence[float], weights: Sequence[float]) -> float:
        """Weighted sum of NORMALISED objectives (lower = better). Weights are re-scaled to sum 1."""
        z = self.normalise(obj)
        s = sum(max(0.0, w) for w in weights) or 1.0
        return sum(max(0.0, w) / s * zi for w, zi in zip(weights, z))

    def as_bounds(self) -> List[Tuple[float, float]]:
        """[(lo,hi)]*3 in the format evaluation.normalise()/shared_hypervolumes() expect."""
        return [(self.lo[i], self.hi[i]) for i in range(3)]
