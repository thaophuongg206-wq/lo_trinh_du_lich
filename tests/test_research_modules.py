"""Smoke tests for research modules (SerpApi, OSRM, Narrative, Replan, Evaluation)."""
from __future__ import annotations
from datetime import datetime, time as dtime

def test_serpapi():
    from services.serpapi import SerpApiService, crowd_from_popular_times
    svc = SerpApiService()
    info = svc.search_place("Hồ Gươm", 21.0285, 105.852)
    assert "review" in info and "popular_times" in info
    assert info["source"] in ("serpapi", "fallback")
    c = crowd_from_popular_times(info["popular_times"], datetime(2025, 6, 1, 10, 0))
    assert 0 <= c <= 1
    print("[OK] SerpApiService", info["source"], "crowd@", c)

def test_osrm():
    from services.osrm import OSRMService
    pts = [
        {"id": "A", "lat": 21.0285, "lon": 105.852},
        {"id": "B", "lat": 21.0350, "lon": 105.850},
        {"id": "C", "lat": 21.0400, "lon": 105.845},
    ]
    m = OSRMService().matrix(pts, "xe_may")
    assert m["A"]["B"]["duration"] > 0
    print("[OK] OSRMService source=", m["A"]["B"].get("source"))

def test_narrative():
    from services.narrative import generate
    places = [
        {"ten": "Hồ Gươm", "arrive_time": "09:00", "visit_time": 40, "sabsa_score": 0.82},
        {"ten": "Phố cổ", "arrive_time": "10:00", "visit_time": 60, "sabsa_score": 0.75},
    ]
    out = generate(places, "thích chụp ảnh", "09:00", "12:00", use_ollama=False)
    assert out["source"] in ("template", "template_fallback", "ollama")
    assert len(out["narrative"]) > 20
    print("[OK] Narrative", out["source"])

def test_replan_and_eval():
    from replan_engine import greedy_2opt, count_crossings, check_flow_conservation
    from evaluation import evaluate_route, hypervolume_3d
    pts = [
        {"id": "0", "lat": 21.02, "lon": 105.85},
        {"id": "1", "lat": 21.04, "lon": 105.86},
        {"id": "2", "lat": 21.03, "lon": 105.84},
        {"id": "3", "lat": 21.05, "lon": 105.83},
        {"id": "4", "lat": 21.025, "lon": 105.855},
    ]
    order = greedy_2opt(pts, "0", lambda p: 0.0)
    assert order[0]["id"] == "0"
    assert check_flow_conservation(order)
    ol = count_crossings(order)
    metrics = evaluate_route(order, (-0.8, 120.0, 0.3))
    assert metrics["flow_conservation"]
    hv = hypervolume_3d([(-0.8, 0.4, 0.3), (-0.6, 0.3, 0.5)], reference=(0, 1, 1))
    # objectives are minimised; experience is stored as negative
    print("[OK] Replan OL=", ol, "HV3d=", round(hv, 4), "metrics", metrics)

def test_absa():
    from absa_service import analyze
    r = analyze("View đẹp, đồ ăn ngon nhưng hơi đông và giá đắt.")
    assert 0 <= r["sabsa_score"] <= 1
    print("[OK] ABSA", r["source"], r["sabsa_score"])

if __name__ == "__main__":
    test_serpapi()
    test_osrm()
    test_narrative()
    test_replan_and_eval()
    test_absa()
    print("\nAll research module smoke tests passed.")
