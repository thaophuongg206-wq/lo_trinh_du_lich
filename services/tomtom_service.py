"""
TomTom traffic service.
- Reads TOMS/traffic data when TOMTOM_API_KEY is configured.
- Never makes the application fail when the key/API is unavailable.
- Returns a normalized congestion factor in [1.0, 3.0].
"""
from __future__ import annotations
import os
from typing import Any, Dict, Optional
import requests

BASE_URL = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
DEFAULT_TIMEOUT = float(os.getenv("TOMTOM_TIMEOUT", "5"))

class TomTomService:
    def __init__(self, api_key: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        self.api_key = api_key or os.getenv("TOMTOM_API_KEY", "")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def traffic_at(self, lat: float, lon: float) -> Dict[str, Any]:
        if not self.enabled:
            return {"available": False, "source": "fallback", "congestion": 0.0,
                    "factor": 1.0, "message": "TOMTOM_API_KEY chưa được cấu hình."}
        try:
            r = requests.get(
                BASE_URL,
                params={"key": self.api_key, "point": f"{lat},{lon}", "unit": "KMPH"},
                timeout=self.timeout,
            )
            r.raise_for_status()
            data = r.json().get("flowSegmentData", {})
            current = float(data.get("currentSpeed") or 0)
            free = float(data.get("freeFlowSpeed") or 0)
            ratio = max(0.0, min(1.0, 1.0 - current / free)) if free else 0.0
            # 0 = free flow, 1 = severe congestion
            factor = round(1.0 + 2.0 * ratio, 3)
            return {
                "available": True, "source": "tomtom",
                "current_speed_kmh": current, "free_flow_speed_kmh": free,
                "congestion": round(ratio, 3), "factor": factor,
            }
        except Exception as exc:
            return {"available": False, "source": "fallback", "congestion": 0.0,
                    "factor": 1.0, "message": str(exc)}

    def factor_for_leg(self, lat: float, lon: float) -> float:
        return float(self.traffic_at(lat, lon).get("factor", 1.0))
