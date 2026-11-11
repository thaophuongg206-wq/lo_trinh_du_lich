"""Fallback audit trail — no more silent degradation (SerpApi / TomTom / Ollama / OSRM / DB).

Every external dependency has a fallback so the pipeline never dies, but the RESULTS of a fallback
run differ a lot from a live run. Each fallback now calls `record(source, reason)`; the registry

  * logs a WARNING the first time (source, reason) is seen in a run (log.warning, structured),
  * counts repeats (so 600 OSRM-pair misses are one line with count=600, not 600 lines),
  * is returned in API responses (`data_quality`) and in experiment CSV/JSON via `snapshot()`,
    so a result table can say which of its numbers came from live data and which from fallbacks.
"""
from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Dict, List

log = logging.getLogger("audit")
_lock = threading.Lock()
_events: "OrderedDict[tuple, dict]" = OrderedDict()


def record(source: str, reason: str, *, fallback: str = "", detail: str = "") -> None:
    """source: osrm | tomtom | serpapi | ollama | weather | db | crowd | nominatim ...
    fallback: what was used instead (e.g. 'haversine/20kmh', 'synthetic', 'sqlite')."""
    key = (source, reason, fallback)
    with _lock:
        ev = _events.get(key)
        if ev is None:
            _events[key] = {"source": source, "reason": reason, "fallback": fallback,
                            "detail": detail[:200], "count": 1}
            log.warning("fallback used | source=%s reason=%s fallback=%s %s",
                        source, reason, fallback or "-", detail[:120])
        else:
            ev["count"] += 1


def snapshot() -> List[dict]:
    with _lock:
        return [dict(v) for v in _events.values()]


def reset() -> None:
    with _lock:
        _events.clear()


def data_quality() -> Dict[str, object]:
    """Compact block to attach to an API response / experiment record."""
    ev = snapshot()
    return {"live": not ev, "fallbacks": ev,
            "note": "" if not ev else "Một phần dữ liệu lấy từ phương án dự phòng — kết quả có thể "
                                      "khác môi trường chạy đủ API."}
