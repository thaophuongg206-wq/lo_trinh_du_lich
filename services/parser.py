"""
LLM-as-Parser (Module 1 of Semantic Engine).

Converts free-text tourist prompts into a strict JSON constraint object
consumed by the Dual-Mode Route Engine.

Primary path: local Ollama.
Fallback: deterministic rule-based extraction so the pipeline never blocks.
"""
from __future__ import annotations
import json
import os
import re
from typing import Any, Dict, List, Optional

from services.ollama import generate as ollama_generate

# Default time budgets (minutes) inferred from common Vietnamese phrases
_TIME_PATTERNS = [
    (r"(\d+)\s*(?:tiếng|gio|giờ|h)\b", lambda m: int(m.group(1)) * 60),
    (r"(\d+)\s*(?:phút|phut|p)\b", lambda m: int(m.group(1))),
    (r"(?:nửa ngày|nua ngay|half day)", lambda m: 240),
    (r"(?:cả ngày|ca ngay|full day)", lambda m: 480),
    (r"(?:buổi sáng|buoi sang|morning)", lambda m: 180),
    (r"(?:buổi chiều|buoi chieu|afternoon)", lambda m: 180),
    (r"(?:buổi tối|buoi toi|evening)", lambda m: 150),
]

_MUST_VISIT_HINTS = [
    "bắt buộc", "must visit", "nhất định", "không thể bỏ", "phải đến",
    "ưu tiên", "ưu tiên ghé", "muốn đến", "muốn ghé",
]

_AVOID_HINTS = [
    "tránh", "không muốn", "đừng", "skip", "avoid", "bỏ qua", "không thích",
]

_PREF_KEYWORDS = {
    "photo": ["chụp ảnh", "check-in", "view đẹp", "sống ảo", "instagram", "photo"],
    "food": ["ẩm thực", "ăn uống", "đồ ăn", "quán ăn", "cafe", "cà phê", "food"],
    "quiet": ["yên tĩnh", "vắng", "ít người", "thư giãn", "relax", "quiet"],
    "culture": ["văn hóa", "lịch sử", "di tích", "bảo tàng", "đền", "chùa", "culture"],
    "shopping": ["mua sắm", "shopping", "trung tâm thương mại", "mall"],
    "nature": ["thiên nhiên", "công viên", "hồ", "vườn", "nature", "park"],
    "family": ["gia đình", "trẻ em", "family", "kids"],
    "nightlife": ["về đêm", "bar", "nightlife", "đêm"],
}


def _rule_based_parse(prompt: str) -> Dict[str, Any]:
    """Deterministic fallback when Ollama is unavailable."""
    text = (prompt or "").lower().strip()
    time_limit = 360  # default 6h
    for pat, fn in _TIME_PATTERNS:
        m = re.search(pat, text, re.I)
        if m:
            try:
                time_limit = max(60, min(720, int(fn(m))))
                break
            except Exception:
                pass

    preferences: List[str] = []
    weights = {"experience": 0.6, "time": 0.25, "crowd": 0.15}
    for tag, kws in _PREF_KEYWORDS.items():
        if any(k in text for k in kws):
            preferences.append(tag)
    if "yên tĩnh" in text or "ít người" in text or "tránh đông" in text or "tránh chỗ" in text or "quá đông" in text:
        weights["crowd"] = 0.35
        weights["experience"] = 0.45
        weights["time"] = 0.20
    if "gần" in text or "nhanh" in text or "tiết kiệm thời gian" in text:
        weights["time"] = 0.45
        weights["experience"] = 0.40
        weights["crowd"] = 0.15

    must_visit: List[str] = []
    avoid: List[str] = []
    # crude extraction of quoted or capitalised place names is left to LLM path
    return {
        "time_limit_minutes": time_limit,
        "preferences": preferences or ["general"],
        "preference_weights": weights,
        "must_visit": must_visit,
        "avoid": avoid,
        "vehicle_hint": "xe_may" if "xe máy" in text or "xe_may" in text else (
            "di_bo" if "đi bộ" in text or "walk" in text else "xe_may"
        ),
        "raw_prompt": prompt,
        "source": "rule_based",
    }


def _parse_prompt_impl(prompt: str, timeout: float = 60.0) -> Dict[str, Any]:
    """
    Parse free-text user prompt → structured constraints.

    Output schema (always present):
      time_limit_minutes : int
      preferences        : list[str]
      preference_weights : {experience, time, crowd} summing ~1
      must_visit         : list[str]  (place name hints)
      avoid              : list[str]
      vehicle_hint       : str
      source             : "ollama" | "rule_based"
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return _rule_based_parse("")

    system = (
        "You are a strict JSON parser for a tourist trip planner. "
        "Extract constraints from the Vietnamese or English user prompt. "
        "Return ONLY a JSON object with keys:\n"
        "  time_limit_minutes (int, default 360),\n"
        "  preferences (array of short tags: photo, food, quiet, culture, shopping, nature, family, nightlife),\n"
        "  preference_weights (object experience/time/crowd, floats summing to 1),\n"
        "  must_visit (array of place-name strings the user insists on),\n"
        "  avoid (array of place-name or category strings to skip),\n"
        "  vehicle_hint (xe_may | o_to | di_bo | xe_dap).\n"
        "Do not invent places. If unsure about a field, use sensible defaults."
    )
    full_prompt = system + "\n\nUser prompt:\n" + prompt[:2000]

    try:
        result = ollama_generate(full_prompt, timeout=timeout, json_mode=True)
        if not result.get("ok"):
            raise ValueError(result.get("error") or "Ollama failed")
        raw = result.get("text") or ""
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError("No JSON in Ollama response")
        obj = json.loads(m.group(0))

        # Normalise & clamp
        tl = int(obj.get("time_limit_minutes") or 360)
        tl = max(60, min(720, tl))
        prefs = obj.get("preferences") or ["general"]
        if isinstance(prefs, str):
            prefs = [prefs]
        weights = obj.get("preference_weights") or {}
        exp = float(weights.get("experience", 0.55))
        tim = float(weights.get("time", 0.30))
        crd = float(weights.get("crowd", 0.15))
        s = exp + tim + crd or 1.0
        weights = {
            "experience": round(exp / s, 3),
            "time": round(tim / s, 3),
            "crowd": round(crd / s, 3),
        }
        return {
            "time_limit_minutes": tl,
            "preferences": list(prefs)[:8],
            "preference_weights": weights,
            "must_visit": list(obj.get("must_visit") or [])[:10],
            "avoid": list(obj.get("avoid") or [])[:10],
            "vehicle_hint": str(obj.get("vehicle_hint") or "xe_may"),
            "raw_prompt": prompt,
            "source": "ollama",
        }
    except Exception:
        return _rule_based_parse(prompt)


def parse_prompt(prompt: str) -> Dict[str, Any]:
    """Parser output + `time_limit_explicit` (True only if the user actually stated a duration;
    otherwise time_limit_minutes is just a default and must NOT override the request's end_time)."""
    out = _parse_prompt_impl(prompt)
    text = (prompt or "").lower()
    out["time_limit_explicit"] = any(re.search(pat, text, re.I) for pat, _ in _TIME_PATTERNS)
    return out
