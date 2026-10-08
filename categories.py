"""
Category quota constraint (K_c): a trip must not be 6 cafes in a row.

  sum over visited POIs of category c  <=  K_c          (cafe, an_uong, tttm)
  #cafe + #an_uong                     <=  K_food_cafe  (food/drink stops combined)
  #cafe + #an_uong                     <=  ceil(food_share_max * #POIs)   (never more than half the stops)

Defaults scale with trip length (assumption: ~1 meal-or-cafe stop every 3 h, one meal + one cafe per 4 h) and can be
overridden per request. The ORIGIN is not counted. Categories that are not listed
(Tham quan, Checkin, ...) are unlimited: they are what the quota pushes the route towards.
"""
from __future__ import annotations
import math
import unicodedata
from typing import Dict, Iterable, List, Optional

_MAP = {"cafe": "cafe", "an uong": "an_uong", "tttm": "tttm"}
CAPPED = ("an_uong", "cafe", "tttm")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").strip()


def category_of(p: dict) -> str:
    return _MAP.get(_fold(p.get("loai_hinh", "")), "other")


def default_caps(hours: float) -> Dict[str, int]:
    each = max(1, round(hours / 4))          # 4h:1  6h:2  8h:2  12h:3
    return {"an_uong": each, "cafe": each, "tttm": 1 if hours < 8 else 2,
            "food_cafe_total": max(1, math.ceil(hours / 3)),   # 3h:1  6h:2  8h:3  12h:4
            "food_share_max": 0.5}


def resolve_caps(caps, hours: float) -> Optional[Dict[str, int]]:
    """'auto' -> defaults by duration; None -> no quota; dict -> defaults overridden by the dict."""
    if caps is None:
        return None
    base = default_caps(hours)
    if isinstance(caps, dict):
        base.update({k: (float(v) if k == "food_share_max" else int(v)) for k, v in caps.items() if v is not None})
    return base


def excess_cats(cats: Iterable[str], caps: Optional[Dict[str, int]]) -> int:
    if not caps:
        return 0
    n: Dict[str, int] = {}
    for c in cats:
        n[c] = n.get(c, 0) + 1
    ex = sum(max(0, n.get(k, 0) - caps[k]) for k in CAPPED if k in caps)
    food = n.get("cafe", 0) + n.get("an_uong", 0)
    if "food_cafe_total" in caps:
        ex += max(0, food - caps["food_cafe_total"])
    if caps.get("food_share_max") is not None:
        ex += max(0, food - math.ceil(float(caps["food_share_max"]) * sum(n.values())))
    return ex


def excess(places: Iterable[dict], caps: Optional[Dict[str, int]]) -> int:
    return excess_cats([category_of(p) for p in places], caps)


def composition(places: Iterable[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in places:
        c = category_of(p); out[c] = out.get(c, 0) + 1
    return out
