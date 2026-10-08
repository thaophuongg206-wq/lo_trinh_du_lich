"""
Aspect-Based Sentiment Analysis (ABSA) -> SABSA.

The primary implementation uses the local Ollama server. A deterministic
lexical fallback is provided so the routing application remains usable
without Ollama. The fallback is explicitly marked as such in the result.
"""
from __future__ import annotations
import json, os, re
from typing import Any, Dict, Iterable, List
import requests

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2")
ASPECTS = ("food", "view", "service", "cleanliness", "price", "location", "atmosphere")

_POS = {"ngon","tuyệt","đẹp","thoáng","sạch","thân thiện","nhiệt tình","hợp lý","yên tĩnh","chất lượng","great","good","nice","beautiful"}
_NEG = {"tệ","bẩn","đắt","ồn","chậm","thất vọng","kém","xấu","đông","bad","poor","expensive","dirty","crowded"}

def _fold(s: str) -> str:
    """lower-case + strip diacritics so 'đẹp' matches 'dep' (DB reviews are mostly unaccented)."""
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d")


def _fold_set(words):
    return {_fold(w) for w in words}


def _lexical(text: str) -> Dict[str, Any]:
    t = _fold(text)
    _POS_F, _NEG_F = _fold_set(_POS), _fold_set(_NEG)
    scores = []
    for asp in ASPECTS:
        hits = 0
        if asp == "food":
            hits = sum(k in t for k in _fold_set(["đồ ăn","món","ăn","food","coffee","cà phê"]))
        elif asp == "view":
            hits = sum(k in t for k in _fold_set(["view","cảnh","đẹp","khung cảnh"]))
        elif asp == "service":
            hits = sum(k in t for k in _fold_set(["phục vụ","nhân viên","dịch vụ"]))
        elif asp == "cleanliness":
            hits = sum(k in t for k in _fold_set(["sạch","bẩn","vệ sinh"]))
        elif asp == "price":
            hits = sum(k in t for k in _fold_set(["giá","đắt","rẻ"]))
        elif asp == "location":
            hits = sum(k in t for k in _fold_set(["vị trí","địa điểm","location"]))
        elif asp == "atmosphere":
            hits = sum(k in t for k in _fold_set(["không gian","yên tĩnh","ồn","atmosphere"]))
        if hits:
            local_pos = sum(k in t for k in _POS_F)
            local_neg = sum(k in t for k in _NEG_F)
            s = (local_pos - local_neg) / max(1, local_pos + local_neg)
            scores.append((asp, float(max(-1, min(1, s)))))
    if not scores:
        pos = sum(k in t for k in _POS_F); neg = sum(k in t for k in _NEG_F)
        scores = [("general", float((pos-neg)/max(1,pos+neg)))]
    avg = sum(v for _,v in scores)/len(scores)
    return {"sabsa_score": round((avg + 1) / 2, 3), "aspects": [{"aspect":a,"sentiment":round(v,3)} for a,v in scores], "source":"lexical"}

def analyze(text: str, timeout: float = 45.0) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"sabsa_score": 0.5, "aspects": [], "source": "empty"}
    prompt = (
        "Perform ABSA on the Vietnamese review below. Return ONLY JSON with keys "
        "aspects (array of {aspect,sentiment} where sentiment is -1..1) and "
        "sabsa_score (0..1). Aspects: food, view, service, cleanliness, price, "
        "location, atmosphere. Review: " + text[:5000]
    )
    try:
        r = requests.post(OLLAMA_URL, json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
                          timeout=timeout)
        r.raise_for_status()
        raw = r.json().get("response","")
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError("Ollama không trả JSON")
        obj = json.loads(m.group(0))
        score = float(obj.get("sabsa_score", 0.5))
        score = max(0.0, min(1.0, score))
        return {"sabsa_score": round(score,3), "aspects": obj.get("aspects", []), "source":"ollama"}
    except Exception:
        return _lexical(text)

def enrich_points(points: Iterable[dict], use_ollama: bool = True) -> List[dict]:
    out=[]
    for p in points:
        q=dict(p)
        text=" ".join(str(p.get(k) or "") for k in ("review","thong_tin_chi_tiet","phu_hop"))
        result=analyze(text) if use_ollama else _lexical(text)
        q["sabsa_score"]=result["sabsa_score"]
        q["absa_aspects"]=result["aspects"]
        q["absa_source"]=result["source"]
        out.append(q)
    return out
