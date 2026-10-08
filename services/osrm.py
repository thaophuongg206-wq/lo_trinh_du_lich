"""
OSRM / Nominatim distance-matrix service.

- Primary: public OSRM table API (router.project-osrm.org).
- Fallback: Haversine approximation so the optimizer never crashes offline.
- Vehicle profiles map to OSRM profiles + a mild traffic multiplier.
"""
from __future__ import annotations
import math
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

OSRM_BASE = os.getenv("OSRM_BASE", "http://router.project-osrm.org")
OSRM_TIMEOUT = float(os.getenv("OSRM_TIMEOUT", "8"))

# vehicle_type -> (osrm_profile, base_traffic_factor)
_PROFILES = {
    "xe_may": ("motorcycle", 1.05),
    "o_to": ("driving", 1.15),
    "di_bo": ("foot", 1.0),
    "xe_dap": ("bike", 1.02),
    "bus": ("driving", 1.25),
}


def vehicle_profile(vehicle_type: str) -> Tuple[str, float]:
    return _PROFILES.get((vehicle_type or "xe_may").lower(), ("driving", 1.1))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def estimate_duration_min(dist_km: float, vehicle_type: str = "xe_may") -> float:
    """Rough free-flow speed assumptions (km/h)."""
    speeds = {"xe_may": 25, "o_to": 30, "di_bo": 4.5, "xe_dap": 12, "bus": 18}
    speed = speeds.get((vehicle_type or "xe_may").lower(), 25)
    return (dist_km / max(speed, 1)) * 60.0


class OSRMCache:
    """Directed-pair cache of RAW OSRM results (seconds, metres) per profile.

    Key = (profile, rounded coords of A, rounded coords of B) -> works for the GPS origin too
    (a new GPS position only creates new pairs; the 30 POI x POI pairs are reused forever).
    Raw values are stored, never vehicle factors, and Haversine fallbacks are NEVER stored.
    Two layers: in-process dict + SQLite file (survives restarts).
    """

    def __init__(self, path: Optional[str] = None, ttl_days: float = 30.0):
        self.path = path or os.getenv("OSRM_CACHE_PATH", "osrm_cache.sqlite")
        self.ttl = ttl_days * 86400
        self._mem: Dict[tuple, Tuple[float, float, float]] = {}
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS pairs(profile TEXT, la1 REAL, lo1 REAL, la2 REAL, lo2 REAL,"
            " dur_s REAL, dist_m REAL, ts REAL, PRIMARY KEY(profile,la1,lo1,la2,lo2))")
        self._db.commit()

    @staticmethod
    def key(profile: str, a: Tuple[float, float], b: Tuple[float, float]) -> tuple:
        return (profile, a[0], a[1], b[0], b[1])

    def get_many(self, keys) -> Dict[tuple, Tuple[float, float]]:
        out, now = {}, time.time()
        with self._lock:
            for k in keys:
                v = self._mem.get(k)
                if v is None:
                    row = self._db.execute(
                        "SELECT dur_s,dist_m,ts FROM pairs WHERE profile=? AND la1=? AND lo1=? AND la2=? AND lo2=?", k).fetchone()
                    if row:
                        v = self._mem[k] = (row[0], row[1], row[2])
                if v is not None and now - v[2] <= self.ttl:
                    out[k] = (v[0], v[1])
        return out

    def put_many(self, items: Dict[tuple, Tuple[float, float]]) -> None:
        now = time.time()
        with self._lock:
            for k, (d, m) in items.items():
                self._mem[k] = (d, m, now)
            self._db.executemany("INSERT OR REPLACE INTO pairs VALUES(?,?,?,?,?,?,?,?)",
                                 [(*k, d, m, now) for k, (d, m) in items.items()])
            self._db.commit()

    def clear(self) -> None:
        with self._lock:
            self._mem.clear(); self._db.execute("DELETE FROM pairs"); self._db.commit()


class OSRMService:
    MAX_TABLE_COORDS = 100   # public OSRM limit for /table

    def __init__(self, base_url: str = OSRM_BASE, timeout: float = OSRM_TIMEOUT, cache: Optional[OSRMCache] = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.cache = cache if cache is not None else OSRMCache()
        self.stats = {"pairs": 0, "cache_hits": 0, "osrm_pairs": 0, "http_calls": 0, "fallback_pairs": 0}

    # ---- one HTTP call to /table for a sources x destinations block ----
    def _table(self, points, profile, srcs, dsts):
        used = sorted(set(srcs) | set(dsts))
        if len(used) > self.MAX_TABLE_COORDS:
            raise ValueError("too many coordinates for one OSRM table call")
        loc = {g: l for l, g in enumerate(used)}
        coords = ";".join(f"{points[g]['lon']},{points[g]['lat']}" for g in used)
        url = (f"{self.base_url}/table/v1/{profile}/{coords}?annotations=duration,distance"
               f"&sources={';'.join(str(loc[g]) for g in srcs)}&destinations={';'.join(str(loc[g]) for g in dsts)}")
        self.stats["http_calls"] += 1
        r = requests.get(url, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        res = {}
        for a, i in enumerate(srcs):
            for b, j in enumerate(dsts):
                d, m = data["durations"][a][b], data["distances"][a][b]
                if i != j and d is not None and m is not None:   # None = unroutable -> left for fallback
                    res[(i, j)] = (float(d), float(m))
        return res

    def matrix(
        self,
        points: List[dict],
        vehicle_type: str = "xe_may",
        k_override: Optional[float] = None,
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        """matrix[id_i][id_j] = {duration (min), distance (km), source}. Always succeeds.
        duration = RAW OSRM duration x k, where k = k_override if given (1.0 = free flow) else the
        service's mild per-vehicle factor. Only pairs that are NOT cached are requested from OSRM."""
        if not points:
            return {}
        profile, k_traffic = vehicle_profile(vehicle_type)
        if k_override is not None:
            k_traffic = float(k_override)
        n = len(points)
        ck = [(round(float(p["lat"]), 5), round(float(p["lon"]), 5)) for p in points]
        keys = {(i, j): OSRMCache.key(profile, ck[i], ck[j]) for i in range(n) for j in range(n) if i != j and ck[i] != ck[j]}
        have = self.cache.get_many(list(keys.values()))
        raw = {ij: have[k] for ij, k in keys.items() if k in have}
        self.stats["pairs"] += len(keys); self.stats["cache_hits"] += len(raw)
        cached_pairs = set(raw)
        missing = {ij for ij in keys if ij not in raw}

        try:
            if missing and len(missing) > 0.5 * len(keys):          # mostly cold -> one full table call
                fetched = self._table(points, profile, list(range(n)), list(range(n)))
                got = {ij: v for ij, v in fetched.items() if ij in missing}
                raw.update(got); missing -= set(got); self.stats["osrm_pairs"] += len(got)
            while missing:                                           # mostly warm -> only a row or a column
                rows, cols = {}, {}
                for i, j in missing:
                    rows[i] = rows.get(i, 0) + 1; cols[j] = cols.get(j, 0) + 1
                ri, rc = max(rows.items(), key=lambda x: x[1]); cj, cc = max(cols.items(), key=lambda x: x[1])
                if rc >= cc:
                    srcs, dsts = [ri], sorted({j for i, j in missing if i == ri})
                else:
                    srcs, dsts = sorted({i for i, j in missing if j == cj}), [cj]
                fetched = self._table(points, profile, srcs, dsts)
                got = {ij: v for ij, v in fetched.items() if ij in missing}
                if not got:
                    break
                raw.update(got); missing -= set(got)
                self.stats["osrm_pairs"] += len(got)
                self.cache.put_many({keys[ij]: v for ij, v in got.items()})
        except Exception:
            pass   # whatever is still missing is filled by the (uncached) fallback below
        if raw:    # persist everything fetched in the cold branch too
            new = {keys[ij]: v for ij, v in raw.items() if keys[ij] not in have}
            if new:
                self.cache.put_many(new)

        matrix: Dict[str, Dict[str, Dict[str, float]]] = {}
        for i, p1 in enumerate(points):
            matrix[p1["id"]] = {}
            for j, p2 in enumerate(points):
                if i == j or ck[i] == ck[j]:
                    matrix[p1["id"]][p2["id"]] = {"duration": 0.0, "distance": 0.0, "source": "osrm"}
                elif (i, j) in raw:
                    d, m = raw[(i, j)]
                    matrix[p1["id"]][p2["id"]] = {"duration": round(d / 60.0 * k_traffic, 3),
                                                  "distance": round(m / 1000.0, 4), "source": "osrm",
                                                  "cached": (i, j) in cached_pairs}
                else:
                    km = haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"]) * 1.3
                    self.stats["fallback_pairs"] += 1
                    matrix[p1["id"]][p2["id"]] = {"duration": round(estimate_duration_min(km, vehicle_type) * k_traffic, 3),
                                                  "distance": round(km, 4), "source": "haversine_fallback"}
        return matrix

    def _fallback_matrix(
        self,
        points: List[dict],
        vehicle_type: str,
        k_traffic: float,
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        matrix: Dict[str, Dict[str, Dict[str, float]]] = {}
        for p1 in points:
            matrix[p1["id"]] = {}
            for p2 in points:
                if p1["id"] == p2["id"]:
                    matrix[p1["id"]][p2["id"]] = {"duration": 0.0, "distance": 0.0, "source": "fallback"}
                    continue
                d = haversine_km(p1["lat"], p1["lon"], p2["lat"], p2["lon"])
                # road factor ~1.3 for urban paths
                road_km = d * 1.3
                dur = estimate_duration_min(road_km, vehicle_type) * k_traffic
                matrix[p1["id"]][p2["id"]] = {
                    "duration": round(dur, 3),
                    "distance": round(road_km, 4),
                    "source": "haversine_fallback",
                }
        return matrix

    def route_geometry(
        self,
        points: List[dict],
        vehicle_type: str = "xe_may",
    ) -> Optional[Dict[str, Any]]:
        """Optional full route geometry between ordered waypoints."""
        if len(points) < 2:
            return None
        profile, _ = vehicle_profile(vehicle_type)
        coords = ";".join(f"{p['lon']},{p['lat']}" for p in points)
        url = f"{self.base_url}/route/v1/{profile}/{coords}?overview=simplified&geometries=geojson"
        try:
            r = requests.get(url, timeout=self.timeout)
            r.raise_for_status()
            return r.json()
        except Exception:
            return None


_SERVICE: Optional[OSRMService] = None


def get_osrm_service() -> OSRMService:
    """Process-wide singleton so every request shares the same cache."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = OSRMService()
    return _SERVICE
