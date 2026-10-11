"""SerpApi reviews cache & enrichment service.

Loads crawled Google Maps reviews from places_output.json and enriches
points with real Vietnamese user reviews for keyword matching and LLM prompts.
"""
from __future__ import annotations
import json
import os
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parent.parent
_JSON_PATH = _ROOT / "places_output.json"

_NAME_ALIAS = {
    "lotte center hanoi": "lotte center lieu giai",
}

_CACHE: Optional[Dict[str, Dict[str, Any]]] = None


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    folded = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").strip()
    return _NAME_ALIAS.get(folded, folded)


def load_serpapi_data() -> Dict[str, Dict[str, Any]]:
    """Loads crawled Google Maps places and reviews into memory cache."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE

    _CACHE = {}
    if not _JSON_PATH.exists():
        return _CACHE

    try:
        raw_list = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        for item in raw_list:
            raw_name = item.get("ten_dia_diem") or ""
            key = _fold(raw_name)
            reviews = [
                r.get("noi_dung", "").strip()
                for r in item.get("reviews", [])
                if (r.get("noi_dung") or "").strip()
            ]
            _CACHE[key] = {
                "ten_dia_diem": raw_name,
                "dia_chi_goc": item.get("dia_chi_goc", ""),
                "rating": item.get("rating"),
                "so_luot_danh_gia": item.get("so_luot_danh_gia", len(reviews)),
                "reviews": reviews,
                "review_text": " ".join(reviews),
            }
    except Exception as e:
        print(f"[WARN] Could not load places_output.json: {e}")

    return _CACHE


def get_serpapi_info(place_name: str) -> Optional[Dict[str, Any]]:
    """Look up SerpApi crawled data by place name (supports exact folded or partial match)."""
    data = load_serpapi_data()
    if not data or not place_name:
        return None

    target = _fold(place_name)
    if target in data:
        return data[target]

    # Partial / substring match fallback
    for k, v in data.items():
        if target in k or k in target:
            return v

    return None


def enrich_point_with_serpapi(point: dict) -> dict:
    """Enriches a POI dict with real SerpApi reviews.
    
    Preserves original DB fields while appending SerpApi reviews to 'review'
    so both preference scoring and LLM prompt context have rich real review text.
    """
    info = get_serpapi_info(point.get("ten", ""))
    if not info:
        return point

    point["serpapi_reviews"] = info["reviews"]
    point["serpapi_rating"] = info.get("rating")
    
    # Enrich review text: combine original short DB note with real crawled reviews
    db_review = (point.get("review") or "").strip()
    serp_review_text = info.get("review_text", "").strip()
    
    if serp_review_text:
        point["serpapi_review_text"] = serp_review_text
        if db_review:
            point["review"] = f"{db_review}. Đánh giá thực tế từ khách hàng: {serp_review_text}"
        else:
            point["review"] = serp_review_text

    return point
