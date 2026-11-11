"""
Thin Ollama client shared by ABSA, Narrative and AI-Advisor modules.
"""
from __future__ import annotations
import os
import json
import re
from typing import Any, Dict, Optional
import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
DEFAULT_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "90"))


def generate(
    prompt: str,
    model: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
    json_mode: bool = False,
) -> Dict[str, Any]:
    """
    Call local Ollama /api/generate.
    Returns {ok, text, raw, error}.
    """
    payload = {
        "model": model or OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    if json_mode:
        payload["format"] = "json"
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=timeout)
        r.raise_for_status()
        raw = r.json()
        text = (raw.get("response") or "").strip()
        return {"ok": True, "text": text, "raw": raw, "error": None}
    except Exception as exc:
        return {"ok": False, "text": "", "raw": None, "error": str(exc)}


def extract_json(text: str) -> Optional[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None
