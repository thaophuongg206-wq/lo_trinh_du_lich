"""
Place suggestions for the search box: local POIs first, then Nominatim.

!! OSM usage policy: the PUBLIC nominatim.openstreetmap.org forbids auto-complete-as-you-type
   (max 1 request/second, identifying User-Agent, cache results). This service therefore
     - answers from local POIs first (instant, no network),
     - only queries the remote server for >= MIN_CHARS characters, at most 1 request/MIN_INTERVAL,
       and returns {"throttled": true} instead of queueing when called faster,
     - caches every remote answer (LRU),
     - lets you point NOMINATIM_URL to a self-hosted Nominatim or a Photon server, which DO
       support search-as-you-type, with NOMINATIM_MIN_INTERVAL=0.
   Set NOMINATIM_USER_AGENT (with a contact e-mail) in .env.
"""
from __future__ import annotations
import os
import threading
import time
import unicodedata
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional

import requests

BASE = os.getenv("NOMINATIM_URL", "https://nominatim.openstreetmap.org").rstrip("/")
UA = os.getenv("NOMINATIM_USER_AGENT", "DuLichThongMinh/1.0 (set NOMINATIM_USER_AGENT with your e-mail)")
MIN_INTERVAL = float(os.getenv("NOMINATIM_MIN_INTERVAL", "1.0"))
MIN_CHARS = int(os.getenv("NOMINATIM_MIN_CHARS", "3"))
# Hanoi bounding box: left, top, right, bottom
VIEWBOX = os.getenv("NOMINATIM_VIEWBOX", "105.60,21.25,106.05,20.85")


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").strip()


class NominatimService:
    def __init__(self, base: str = BASE, min_interval: float = MIN_INTERVAL, timeout: float = 4.0, cache_size: int = 512):
        self.base, self.min_interval, self.timeout = base.rstrip("/"), min_interval, timeout
        self._cache: "OrderedDict[str, List[dict]]" = OrderedDict()
        self._cache_size = cache_size
        self._last = float("-inf")   # NOT 0.0: time.monotonic() can be < min_interval right after boot
        self._lock = threading.Lock()

    @staticmethod
    def local(q: str, points: Iterable[dict], limit: int) -> List[dict]:
        fq = fold(q)
        if not fq:
            return []
        scored = []
        for p in points:
            name = fold(p.get("ten", ""))
            if name.startswith(fq): rank = 0
            elif any(w.startswith(fq) for w in name.split()): rank = 1
            elif fq in name: rank = 2
            else: continue
            scored.append((rank, name, p))
        scored.sort(key=lambda x: (x[0], x[1]))
        return [{"label": p["ten"], "lat": float(p["lat"]), "lon": float(p["lon"]), "id": str(p["id"]),
                 "source": "local"} for _, _, p in scored[:limit]]

    def _remote(self, q: str, limit: int, lat: Optional[float], lon: Optional[float]) -> List[dict]:
        params: Dict[str, Any] = {"q": q, "format": "jsonv2", "limit": limit, "countrycodes": "vn",
                                  "accept-language": "vi", "viewbox": VIEWBOX, "bounded": 0, "addressdetails": 0}
        r = requests.get(f"{self.base}/search", params=params, headers={"User-Agent": UA}, timeout=self.timeout)
        r.raise_for_status()
        return [{"label": x.get("display_name", ""), "lat": float(x["lat"]), "lon": float(x["lon"]),
                 "source": "nominatim", "osm_type": x.get("osm_type"), "osm_id": x.get("osm_id")} for x in r.json()]

    def suggest(self, q: str, limit: int = 5, lat: Optional[float] = None, lon: Optional[float] = None,
                local_points: Iterable[dict] = ()) -> Dict[str, Any]:
        q = (q or "").strip()
        limit = max(1, min(10, int(limit)))
        out = {"query": q, "suggestions": [], "throttled": False, "remote": False}
        out["suggestions"] = self.local(q, local_points, limit)
        if len(q) < MIN_CHARS or len(out["suggestions"]) >= limit:
            return out
        key = fold(q)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                remote = self._cache[key]; out["remote"] = True
            elif time.monotonic() - self._last < self.min_interval:
                out["throttled"] = True; return out          # never queue: caller retries after the debounce
            else:
                self._last = time.monotonic()
                remote = None
        if remote is None:
            try:
                remote = self._remote(q, limit, lat, lon); out["remote"] = True
            except Exception as exc:
                out["error"] = str(exc); return out
            with self._lock:
                self._cache[key] = remote
                while len(self._cache) > self._cache_size:
                    self._cache.popitem(last=False)
        seen = {(round(s["lat"], 4), round(s["lon"], 4)) for s in out["suggestions"]}
        for s in remote:
            if (round(s["lat"], 4), round(s["lon"], 4)) not in seen and len(out["suggestions"]) < limit:
                out["suggestions"].append(s)
        return out


_SVC: Optional[NominatimService] = None


def get_nominatim() -> NominatimService:
    global _SVC
    if _SVC is None:
        _SVC = NominatimService()
    return _SVC
