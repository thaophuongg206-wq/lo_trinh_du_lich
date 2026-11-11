"""
Dynamic signals used by the optimizer: traffic (TomTom), weather (Open-Meteo),
crowd (SerpApi Popular Times -> GradientBoosting fallback).

Every provider exposes the same tiny interface so the SAME algorithm code runs in
production (live APIs) and in experiments (frozen snapshot / labelled synthetic
profile). Each provider has a `.source` attribute that run_experiments.py writes
into results.json, so a table can never silently claim "TomTom" when the numbers
came from a synthetic profile.
"""
from __future__ import annotations
import json, math, os, random, hashlib
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

OUTDOOR_TYPES = {"tham quan", "checkin", "check-in", "cong vien", "outdoor"}


def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")


def is_outdoor(point: dict) -> bool:
    return _norm(point.get("loai_hinh", "")) in OUTDOOR_TYPES


def _rush_profile(hour: float) -> float:
    """Congestion in [0,1]: two rush-hour peaks (Hanoi-like)."""
    return max(math.exp(-((hour - 8) / 1.3) ** 2), math.exp(-((hour - 17.5) / 1.6) ** 2))


# ----------------------------------------------------------------- traffic ---
class SyntheticTraffic:
    """Deterministic, clearly-labelled profile: rush-hour curve x per-zone severity."""
    source = "synthetic_profile"

    def __init__(self, seed: int = 0, max_extra: float = 1.0):
        self.seed, self.max_extra = seed, max_extra

    def _zone(self, p: dict) -> float:
        cell = f"{round(float(p['lat']), 2)}:{round(float(p['lon']), 2)}:{self.seed}"
        return 0.4 + 0.6 * (int(hashlib.md5(cell.encode()).hexdigest()[:6], 16) / 0xFFFFFF)

    def factor(self, a: dict, b: dict, when: datetime) -> float:
        h = when.hour + when.minute / 60
        sev = (self._zone(a) + self._zone(b)) / 2
        return round(1.0 + self.max_extra * sev * _rush_profile(h), 3)


class FrozenTraffic:
    """TomTom measurements frozen in traffic_snapshot.json ({poi_id: {hour: factor}})."""
    source = "tomtom_snapshot"

    def __init__(self, path: str, fallback: Optional[SyntheticTraffic] = None):
        self.data: Dict[str, Dict[str, float]] = json.loads(Path(path).read_text(encoding="utf-8"))
        self.fallback = fallback or SyntheticTraffic()
        self.hit = self.miss = 0

    def _at(self, p: dict, hour: int) -> Optional[float]:
        rec = self.data.get(str(p["id"]))
        if not rec:
            return None
        h = min((int(k) for k in rec), key=lambda k: abs(k - hour))
        return float(rec[str(h)])

    def factor(self, a: dict, b: dict, when: datetime) -> float:
        fa, fb = self._at(a, when.hour), self._at(b, when.hour)
        if fa is None or fb is None:
            self.miss += 1
            return self.fallback.factor(a, b, when)
        self.hit += 1
        return round((fa + fb) / 2, 3)


class LiveTraffic:
    """TomTom live flow at the leg origin; cached per POI for `ttl` seconds.

    LIMITATION (state it in the paper): TomTom Flow gives congestion NOW only. `factor(a, b, when)`
    deliberately ignores `when` = the congestion at query time is assumed to persist over the horizon.
    A genuinely time-dependent t_ij(t) needs FrozenTraffic (hourly snapshots accumulated with
    record_snapshot / `signals.py --snapshot`) or a traffic forecast."""
    source = "tomtom_live_at_query_time"

    def __init__(self, service=None, ttl: int = 300):
        from tomtom_service import TomTomService
        self.svc = service or TomTomService()
        self.ttl, self._c = ttl, {}

    @property
    def enabled(self) -> bool:
        return self.svc.enabled

    def prefetch(self, points) -> None:
        for p in points:
            self._get(p)

    def _get(self, p: dict) -> float:
        import time
        k = str(p["id"])
        t, v = self._c.get(k, (0, None))
        if v is None or time.time() - t > self.ttl:
            v = float(self.svc.traffic_at(float(p["lat"]), float(p["lon"])).get("factor", 1.0))
            self._c[k] = (time.time(), v)
        return v

    def record_snapshot(self, points, path: str = "traffic_snapshot.json") -> None:
        """Append the CURRENT hour's factors to the snapshot file (builds the hourly profile over time)."""
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        h = str(datetime.now().hour)
        for pt in points:
            data.setdefault(str(pt["id"]), {})[h] = self._get(pt)
        p.write_text(json.dumps(data), encoding="utf-8")

    def factor(self, a: dict, b: dict, when: datetime) -> float:
        return round(self._get(a), 3)  # cached: no network call inside the GA loop after prefetch; `when` ignored


def constant_traffic(f: float):
    class _C:
        source = "constant"
        def factor(self, a, b, when): return f
    return _C()


# ----------------------------------------------------------------- weather ---
class SyntheticWeather:
    """Seeded rain window per (date, seed); labelled synthetic."""
    source = "synthetic_rain_window"

    def __init__(self, seed: int = 0, rain_prob_day: float = 0.5):
        self.seed, self.p = seed, rain_prob_day

    def penalty_at(self, when: datetime) -> float:
        rng = random.Random(f"{self.seed}:{when.date()}")
        if rng.random() > self.p:
            return 0.0
        start = rng.randint(10, 16); dur = rng.randint(2, 4)
        return 0.9 if start <= when.hour < start + dur else 0.0


class FrozenWeather:
    """Open-Meteo hourly penalty frozen in weather_snapshot.json ({'YYYY-MM-DDTHH:00': penalty})."""
    source = "open_meteo_snapshot"

    def __init__(self, path: str, fallback: Optional[SyntheticWeather] = None):
        self.data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.fallback = fallback or SyntheticWeather()

    def penalty_at(self, when: datetime) -> float:
        k = when.strftime("%Y-%m-%dT%H:00")
        return float(self.data[k]) if k in self.data else self.fallback.penalty_at(when)


class LiveWeather:
    """Open-Meteo hourly forecast (WeatherService caches the whole day per ~1km cell)."""
    source = "open_meteo_live"

    def __init__(self, service=None):
        from weather_service import WeatherService
        self.svc = service or WeatherService()
        self.center = (21.0285, 105.8542)

    def penalty_at(self, when: datetime, lat=None, lon=None) -> float:
        lat, lon = (lat, lon) if lat is not None else self.center
        return float(self.svc.at(lat, lon, when).get("penalty", 0.0))


def make_weather_fn(provider) -> Callable[[dict, datetime], float]:
    """WeatherPenalty(j, y_j): only for OUTDOOR POIs, evaluated at the ARRIVAL time y_j."""
    def fn(point: dict, when: datetime) -> float:
        if not is_outdoor(point):
            return 0.0
        try:
            if isinstance(provider, LiveWeather):
                return provider.penalty_at(when, float(point["lat"]), float(point["lon"]))
            return provider.penalty_at(when)
        except Exception:
            return 0.0
    return fn


# ------------------------------------------------------------------- crowd ---
def make_crowd_fn(predictor=None) -> Callable[[dict, datetime], dict]:
    """Prefer SerpApi Popular Times when the POI carries it; else the predictor."""
    def fn(point: dict, when: datetime) -> dict:
        pt = point.get("popular_times")
        if pt:
            from services.serpapi import crowd_from_popular_times
            return {"crowd_index": float(crowd_from_popular_times(pt, when)), "source": "serpapi_popular_times"}
        if predictor is not None:
            return predictor.predict(str(point["id"]), when)
        return {"crowd_index": 0.35, "source": "default"}
    return fn


# --------------------------------------------- snapshot CLI (freeze real data) ---
def _snapshot(pois_path: str = "dulich.db") -> None:
    """python signals.py --snapshot : append the CURRENT hour of TomTom + Open-Meteo to the snapshot files.
    Run it at several hours/days; run_experiments.py then uses the frozen files."""
    import sqlite3
    from tomtom_service import TomTomService
    from weather_service import WeatherService
    svc, ws = TomTomService(), WeatherService()
    if not svc.enabled:
        raise SystemExit("TOMTOM_API_KEY chưa cấu hình – không thể snapshot traffic.")
    conn = sqlite3.connect(pois_path)
    rows = conn.execute("SELECT id, vi_do, kinh_do FROM DIA_DIEM").fetchall()
    tpath, wpath = Path("traffic_snapshot.json"), Path("weather_snapshot.json")
    t = json.loads(tpath.read_text()) if tpath.exists() else {}
    w = json.loads(wpath.read_text()) if wpath.exists() else {}
    now = datetime.now()
    for pid, lat, lon in rows:
        r = svc.traffic_at(float(lat), float(lon))
        if r.get("available"):
            t.setdefault(str(pid), {})[str(now.hour)] = r["factor"]
    day = ws._day(21.0285, 105.8542, now.strftime("%Y-%m-%d"))
    for i, ts in enumerate(day.get("time", [])):
        p = float((day["precipitation_probability"] or [0])[i] or 0); mm = float((day["precipitation"] or [0])[i] or 0)
        w[ts] = round(min(1.0, p / 100 * 0.7 + min(1.0, mm) * 0.3), 3)
    tpath.write_text(json.dumps(t)); wpath.write_text(json.dumps(w))
    print(f"snapshot ok: {len(t)} POI traffic, {len(w)} weather hours")


if __name__ == "__main__":
    import sys
    if "--snapshot" in sys.argv:
        _snapshot()


class Scaled:
    """vehicle speed factor x traffic factor, so f2 uses matrix * vehicle * traffic(t)."""
    def __init__(self, base, k: float):
        self.base, self.k = base, float(k)
        self.source = getattr(base, "source", "?")

    def factor(self, a, b, when):
        return self.k * float(self.base.factor(a, b, when))
