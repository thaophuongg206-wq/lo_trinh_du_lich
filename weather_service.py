"""
Timestamp-aware Open-Meteo service.
Unlike the old current_weather call, this module queries hourly weather
around the requested trip date/time.
"""
from __future__ import annotations
from datetime import datetime
from typing import Any, Dict, Optional
import requests

URL = "https://api.open-meteo.com/v1/forecast"

class WeatherService:
    def __init__(self, timeout: float = 5.0):
        self.timeout = timeout
        self._cache: Dict[tuple, Dict[str, Any]] = {}

    def _day(self, lat: float, lon: float, date: str) -> Dict[str, Any]:
        key=(round(float(lat),2),round(float(lon),2),date)   # ~1 km cell: one call per cell/day
        if key in self._cache:
            return self._cache[key]
        try:
            r = requests.get(
            URL,
            params={
                "latitude": lat, "longitude": lon,
                "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code",
                "start_date": date, "end_date": date,
                "timezone": "Asia/Bangkok",
            },
            timeout=self.timeout,
            )
            r.raise_for_status()
        except Exception:
            self._cache[key] = {}      # negative cache: never retry the network inside the GA / replan loop
            raise
        h = r.json().get("hourly", {})
        self._cache[key]=h
        return h

    def at(self, lat: float, lon: float, when: datetime) -> Dict[str, Any]:
        try:
            date = when.strftime("%Y-%m-%d")
            h = self._day(lat, lon, date)
            times = h.get("time", [])
            if not times:
                raise ValueError("Open-Meteo không trả dữ liệu hourly")
            target = when.strftime("%Y-%m-%dT%H:00")
            idx = min(range(len(times)), key=lambda i: abs(
                datetime.fromisoformat(times[i]).replace(tzinfo=None) -
                when.replace(tzinfo=None)
            ))
            code = int((h.get("weather_code") or [0])[idx] or 0)
            rain_prob = float((h.get("precipitation_probability") or [0])[idx] or 0)
            precip = float((h.get("precipitation") or [0])[idx] or 0)
            penalty = min(1.0, rain_prob / 100.0 * 0.7 + min(1.0, precip) * 0.3)
            return {
                "available": True, "source": "open-meteo",
                "time": times[idx], "weather_code": code,
                "rain_probability": round(rain_prob, 1),
                "precipitation_mm": round(precip, 2),
                "penalty": round(penalty, 3),
                "factor": round(1.0 + 0.25 * penalty, 3),
            }
        except Exception as exc:
            return {"available": False, "source": "fallback", "penalty": 0.0,
                    "factor": 1.0, "message": str(exc)}
