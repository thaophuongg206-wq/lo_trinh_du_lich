"""Tests for the shared core (timeline, objectives, audit) and baseline/hybrid parity."""
from datetime import datetime, date, time as T, timedelta
import hybrid_nsga as H, signals as S
from core import audit
from core.timeline import walk, window_dt, violation, approx_km
from core.objectives import ObjectiveScaler, experience_of
from test_fixes import _world

D = date(2026, 10, 8)
def P(i, o, c, t=30): return {"id": str(i), "lat": 21.0, "lon": 105.8, "open_time": T(*o), "close_time": T(*c), "time": t}

def test_walk_wait_and_late():
    pts = [P(0, (8, 0), (20, 0)), P(1, (9, 0), (10, 0)), P(2, (8, 0), (10, 0))]
    tl = walk(pts, datetime(2026, 10, 8, 8), leg=lambda a, b, c: 15, window=lambda p: window_dt(p, D))
    assert tl.stops[1].wait == 15 and tl.stops[1].late == 0       # arrives 8:45+..., waits for 9:00
    assert tl.stops[2].late > 0                                    # ends 10:15 > 10:00 close
    assert violation(tl, datetime(2026, 10, 8, 20)) == tl.stops[2].late

def test_midnight_window_rollover():
    o, c = window_dt(P(0, (18, 0), (2, 0)), D)
    assert c - o == timedelta(hours=8)

def test_scaler_bounds_and_clip():
    ps = [{"experience_score": .9, "time": 60}, {"experience_score": .5, "time": 30}]
    s = ObjectiveScaler.for_instance(ps, 120)
    z = s.normalise((-99, 999, 99))
    assert all(0 <= v <= 1 for v in z)
    assert s.weighted((s.lo[0], 0, 0), (1, 1, 1)) == 0.0

def test_baseline_same_objectives_as_hybrid():
    pts, m, d, st, en = _world(12, 3)
    c = H.Ctx(pts, m, st, en, d, S.constant_traffic(1.0), lambda p, w: {"crowd_index": 0.2}, lambda p, w: 0.1)
    r = H.repair([0, 3, 5, 7], c)
    assert H.route_objectives(r, c) == H.evaluate(r, c).obj
    a, _ = H.run(pts, m, 0, st, en, d, S.constant_traffic(1.0), pop_size=20, generations=5, seed=1, use_seeds=False, use_2opt=False)
    assert a and all(i.cv <= 1e-9 for i in a)

def test_audit_counts_once_per_reason():
    audit.reset()
    for _ in range(5): audit.record("osrm", "pair_missing", fallback="line")
    snap = audit.snapshot()
    assert len(snap) == 1 and snap[0]["count"] == 5 and not audit.data_quality()["live"]
    audit.reset()


def test_crowd_csv_path_exists_and_trains():
    import os
    from config import CFG
    from crowd_prediction import CrowdPredictor
    assert os.path.exists(CFG.crowd_csv)
    audit.reset()
    CrowdPredictor(CFG.crowd_csv)
    assert not [e for e in audit.snapshot() if e["reason"] == "history_csv_missing"]
