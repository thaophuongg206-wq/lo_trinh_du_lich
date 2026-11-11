"""
Narrative Generator (LLM-as-Storyteller).

Takes an already optimised POI sequence from the math engine and produces a
natural-language Vietnamese itinerary guide (thuyết minh lịch trình).

Uses local Ollama when available; falls back to a deterministic template so the
pipeline never blocks on LLM availability.
"""
from __future__ import annotations
import os
import json
import re
from typing import Any, Dict, List, Optional
import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = float(os.getenv("NARRATIVE_TIMEOUT", "60"))


def _template_narrative(places: List[dict], start_time: str = "", end_time: str = "") -> str:
    """Deterministic fallback when Ollama is offline."""
    if not places:
        return "Chưa có điểm đến trong lịch trình."
    lines = [
        f"Lịch trình gợi ý ({start_time or '?'} – {end_time or '?'}):",
        "",
    ]
    for i, p in enumerate(places, 1):
        name = p.get("ten") or p.get("name") or f"Điểm {i}"
        arrive = p.get("arrive_time") or ""
        visit = p.get("visit_time") or p.get("time") or ""
        sabsa = p.get("sabsa_score")
        reason = p.get("mo_ta") or p.get("review") or ""
        reason = (reason[:120] + "…") if len(str(reason)) > 120 else reason
        sabsa_txt = f" (cảm xúc {sabsa:.2f})" if isinstance(sabsa, (int, float)) else ""
        lines.append(f"{i}. {arrive} – {name}{sabsa_txt}")
        if visit:
            lines.append(f"   Thời lượng tham quan: ~{visit} phút.")
        if reason:
            lines.append(f"   Gợi ý: {reason}")
        if p.get("travel_to_next"):
            lines.append(f"   Di chuyển tiếp theo: ~{p['travel_to_next']} phút.")
        lines.append("")
    lines.append("Chúc bạn có một chuyến đi thú vị và suôn sẻ!")
    return "\n".join(lines)


def generate(
    places: List[dict],
    user_preference: str = "",
    start_time: str = "",
    end_time: str = "",
    use_ollama: bool = True,
) -> Dict[str, Any]:
    """
    Return {narrative, source, places_count}.
    """
    if not places:
        return {"narrative": "Chưa có điểm đến.", "source": "empty", "places_count": 0}

    if not use_ollama:
        return {
            "narrative": _template_narrative(places, start_time, end_time),
            "source": "template",
            "places_count": len(places),
        }

    # Compact context for small local models
    items = []
    for i, p in enumerate(places, 1):
        items.append({
            "stt": i,
            "ten": p.get("ten") or p.get("name"),
            "arrive": p.get("arrive_time"),
            "visit_min": p.get("visit_time") or p.get("time"),
            "sabsa": p.get("sabsa_score"),
            "loai": p.get("loai_hinh"),
            "mo_ta": (p.get("mo_ta") or "")[:80],
        })
    prompt = (
        "Bạn là hướng dẫn viên du lịch Việt Nam. Viết bài thuyết minh lịch trình "
        "ngắn gọn, tự nhiên, thân thiện (khoảng 150-250 từ) dựa trên chuỗi điểm sau. "
        "Giữ đúng thứ tự, nhắc giờ đến nếu có, và đưa lời khuyên thực tế (tránh đông, "
        "chụp ảnh, ẩm thực...). Không bịa địa điểm mới.\n"
        f"Sở thích du khách: {user_preference or 'không nêu rõ'}\n"
        f"Khung giờ: {start_time} – {end_time}\n"
        f"Chuỗi điểm (JSON): {json.dumps(items, ensure_ascii=False)}\n"
        "Chỉ trả về đoạn văn thuyết minh, không JSON, không markdown."
    )
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT,
        )
        r.raise_for_status()
        text = (r.json().get("response") or "").strip()
        # Strip accidental code fences
        text = re.sub(r"^```.*?```$", "", text, flags=re.S).strip()
        if len(text) < 40:
            raise ValueError("response quá ngắn")
        return {"narrative": text, "source": "ollama", "places_count": len(places)}
    except Exception:
        return {
            "narrative": _template_narrative(places, start_time, end_time),
            "source": "template_fallback",
            "places_count": len(places),
        }
