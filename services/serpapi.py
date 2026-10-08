"""
SerpApi client for POI enrichment: reviews, popular times, opening hours.

- Uses SERPAPI_KEY from environment when available.
- Falls back to deterministic synthetic enrichment so the research pipeline
  remains runnable without an API key (explicitly labelled source="fallback").
- Designed for Aspect-Based Sentiment Analysis (ABSA) and crowd signals.
"""
from __future__ import annotations
import os
import re
import hashlib
from datetime import datetime, time as dtime
from typing import Any, Dict, List, Optional
import requests

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SERPAPI_URL = "https://serpapi.com/search"
DEFAULT_TIMEOUT = float(os.getenv("SERPAPI_TIMEOUT", "12"))

# Lightweight Vietnamese/English review snippets used only for offline fallback
_FALLBACK_REVIEWS = [
    "Không gian đẹp, view tuyệt, nhân viên thân thiện. Giá cả hợp lý.",
    "Đồ ăn ngon nhưng hơi đông vào cuối tuần. Phục vụ nhanh.",
    "Cảnh quan ấn tượng, sạch sẽ. Nên đến vào buổi sáng để tránh đông.",
    "Giá hơi đắt so với chất lượng. Không gian yên tĩnh, phù hợp chụp ảnh.",
    "Vị trí thuận tiện, dễ tìm. Trải nghiệm tổng thể tốt.",
    "Đông khách, chờ hơi lâu. View đẹp nhưng dịch vụ trung bình.",
    "Rất thích hợp cho citywalk. Không khí thoáng đãng, sạch sẽ.",
    "Nên tránh giờ cao điểm. Chất lượng ổn định, nhân viên nhiệt tình.",
]


def _stable_seed(text: str) -> int:
    return int(hashlib.md5((text or "").encode("utf-8")).hexdigest()[:8], 16)


class SerpApiService:
    def __init__(self, api_key: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT):
        self.api_key = api_key if api_key is not None else SERPAPI_KEY
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def search_place(self, query: str, lat: Optional[float] = None, lon: Optional[float] = None) -> Dict[str, Any]:
        """Search a single place and return normalised fields used by the optimizer."""
        if not self.enabled:
            return self._fallback(query, lat, lon)

        params: Dict[str, Any] = {
            "engine": "google_maps",
            "q": query,
            "type": "search",
            "hl": "vi",
            "api_key": self.api_key,
        }
        if lat is not None and lon is not None:
            params["ll"] = f"@{lat},{lon},14z"

        try:
            r = requests.get(SERPAPI_URL, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            local = (data.get("local_results") or data.get("place_results") or {})
            if isinstance(local, list):
                local = local[0] if local else {}
            return self._normalise(local, source="serpapi")
        except Exception as exc:
            out = self._fallback(query, lat, lon)
            out["message"] = str(exc)
            return out

    def enrich_points(self, points: List[dict], max_calls: int = 8) -> List[dict]:
        """
        Enrich a list of POI dicts with reviews / popular_times / opening hours.
        Limits network calls to keep latency acceptable for research runs.
        """
        out = []
        for i, p in enumerate(points):
            q = dict(p)
            if i >= max_calls or not self.enabled:
                fb = self._fallback(p.get("ten") or p.get("id") or "poi", p.get("lat"), p.get("lon"))
                q.update({k: v for k, v in fb.items() if k not in ("id", "ten", "lat", "lon")})
                out.append(q)
                continue
            info = self.search_place(p.get("ten") or "", p.get("lat"), p.get("lon"))
            if info.get("review"):
                q["review"] = (q.get("review") or "") + " " + info["review"]
            if info.get("popular_times"):
                q["popular_times"] = info["popular_times"]
            if info.get("open_time") and info.get("close_time"):
                q["open_time"] = info["open_time"]
                q["close_time"] = info["close_time"]
            if info.get("rating") is not None:
                q["serpapi_rating"] = info["rating"]
            q["serpapi_source"] = info.get("source", "serpapi")
            out.append(q)
        return out

    def _normalise(self, raw: Dict[str, Any], source: str = "serpapi") -> Dict[str, Any]:
        reviews = raw.get("user_reviews") or raw.get("reviews") or []
        texts = []
        if isinstance(reviews, list):
            for r in reviews[:5]:
                if isinstance(r, dict):
                    texts.append(str(r.get("snippet") or r.get("description") or ""))
                else:
                    texts.append(str(r))
        review_text = " ".join(t for t in texts if t).strip()

        popular = raw.get("popular_times") or raw.get("popular_times_histogram") or {}
        # Normalise to hour -> 0..1 intensity
        popular_norm: Dict[int, float] = {}
        if isinstance(popular, dict):
            for k, v in popular.items():
                try:
                    h = int(re.sub(r"\D", "", str(k)) or 0)
                    popular_norm[h % 24] = max(0.0, min(1.0, float(v) / 100.0 if float(v) > 1 else float(v)))
                except Exception:
                    continue

        open_t, close_t = dtime(8, 0), dtime(21, 0)
        hours = raw.get("hours") or raw.get("operating_hours") or {}
        if isinstance(hours, dict):
            # best-effort parse of first day
            for day_val in hours.values():
                if isinstance(day_val, str) and "-" in day_val:
                    parts = re.findall(r"(\d{1,2}):(\d{2})", day_val)
                    if len(parts) >= 2:
                        open_t = dtime(int(parts[0][0]) % 24, int(parts[0][1]) % 60)
                        close_t = dtime(int(parts[1][0]) % 24, int(parts[1][1]) % 60)
                    break

        rating = raw.get("rating")
        try:
            rating = float(rating) if rating is not None else None
        except Exception:
            rating = None

        return {
            "review": review_text,
            "popular_times": popular_norm,
            "open_time": open_t,
            "close_time": close_t,
            "rating": rating,
            "source": source,
            "available": True,
        }

    def _fallback(self, query: str, lat: Optional[float], lon: Optional[float]) -> Dict[str, Any]:
        seed = _stable_seed(f"{query}|{lat}|{lon}")
        review = _FALLBACK_REVIEWS[seed % len(_FALLBACK_REVIEWS)]
        # Synthetic popular-times curve (morning + evening peaks)
        popular = {}
        for h in range(24):
            morning = max(0.0, 1.0 - abs(h - 10) / 3.0)
            evening = max(0.0, 1.0 - abs(h - 18) / 3.5)
            popular[h] = round(min(1.0, 0.25 + 0.55 * max(morning, evening)), 3)
        return {
            "review": review,
            "popular_times": popular,
            "open_time": dtime(8, 0),
            "close_time": dtime(21, 30),
            "rating": round(3.5 + (seed % 15) / 10.0, 1),
            "source": "fallback",
            "available": False,
            "message": "SERPAPI_KEY chưa cấu hình — dùng dữ liệu synthetic có nhãn rõ ràng.",
        }


def crowd_from_popular_times(popular_times: Dict[int, float], when: datetime) -> float:
    """Map Popular Times histogram to a 0..1 crowd index at a given timestamp."""
    if not popular_times:
        return 0.35
    h = when.hour
    return float(popular_times.get(h, popular_times.get(str(h), 0.35)))
