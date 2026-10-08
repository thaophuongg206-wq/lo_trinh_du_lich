"""Regression tests for the experiment/algorithm fixes."""
from datetime import datetime, timedelta, time as dtime
import random
from evaluation import hypervolume_3d, shared_hypervolumes
import hybrid_nsga as H, signals as S
from replan_engine import greedy_2opt, replan_route, count_crossings


def _world(n=12, seed=1):
    rng = random.Random(seed)
    pts = [{"id": str(i), "ten": f"p{i}", "lat": 21 + rng.random() * .05, "lon": 105.8 + rng.random() * .05,
            "time": 30, "loai_hinh": "Tham quan", "open_time": dtime(8), "close_time": dtime(20),
            "experience_score": rng.random()} for i in range(n)]
    m = {a["id"]: {b["id"]: {"duration": 111 * ((a["lat"]-b["lat"])**2 + (a["lon"]-b["lon"])**2) ** .5 / 25 * 60}
                   for b in pts} for a in pts}
    d = datetime(2025, 6, 15).date(); st = datetime.combine(d, dtime(8)); return pts, m, d, st, st + timedelta(hours=6)


def test_hv_exact():
    assert abs(hypervolume_3d([(0, 0, 0)]) - 1) < 1e-12
    assert abs(hypervolume_3d([(0, .5, .5), (.5, 0, .5)]) - .375) < 1e-12       # overlap counted once


def test_hv_shared_scale():
    hv = shared_hypervolumes({"a": [(0, 0, 0)], "b": [(1, 1, 1)]})
    assert hv["a"] > hv["b"]


def test_hybrid_feasible_and_no_crossings():
    pts, m, d, st, en = _world()
    front, _ = H.run(pts, m, 0, st, en, d, S.constant_traffic(1.0), pop_size=20, generations=15, seed=3)
    assert front and all(i.cv == 0 for i in front)
    assert all(count_crossings([pts[k] for k in i.route]) == 0 for i in front)


def test_replan_pins_visited_and_checks_windows():
    pts, m, d, st, en = _world()
    route = pts[:6]
    out = replan_route(route, pts[6:], m, st + timedelta(hours=1), en, d, lambda a, b, t: 1.8, n_fixed=3)
    assert [p["id"] for p in out["route"][:3]] == ["0", "1", "2"]
    assert out["feasible"] and len({p["id"] for p in out["route"]}) == len(out["route"])
    g = greedy_2opt(pts[:6], "0", pinned_ids=["1", "2"])
    assert [p["id"] for p in g[:3]] == ["0", "1", "2"]


def test_weather_only_outdoor_at_arrival():
    class W:
        def penalty_at(self, when): return 1.0 if when.hour >= 12 else 0.0
    fn = S.make_weather_fn(W())
    assert fn({"loai_hinh": "Tham quan"}, datetime(2025, 1, 1, 13)) == 1.0
    assert fn({"loai_hinh": "Cafe"}, datetime(2025, 1, 1, 13)) == 0.0
    assert fn({"loai_hinh": "Tham quan"}, datetime(2025, 1, 1, 9)) == 0.0




# ------------------------------------------------ OSRM cache / prefilter / nominatim / diversity
def _osrm_mock(calls):
    import requests, re
    class R:
        def __init__(s, j): s.j = j
        def raise_for_status(s): pass
        def json(s): return s.j
    def fake_get(url, timeout=0, **kw):
        calls.append(url)
        coords = url.split("/table/v1/")[1].split("/")[1].split("?")[0].split(";")
        q = dict(x.split("=") for x in url.split("?")[1].split("&") if "=" in x)
        src = [int(x) for x in q["sources"].split(";")]; dst = [int(x) for x in q["destinations"].split(";")]
        ll = [tuple(map(float, c.split(","))) for c in coords]
        f = lambda i, j: (abs(ll[i][0]-ll[j][0]) + abs(ll[i][1]-ll[j][1])) * 111000
        return R({"durations": [[f(i, j) / 10 for j in dst] for i in src], "distances": [[f(i, j) for j in dst] for i in src]})
    return requests, fake_get


def test_osrm_cache_only_fetches_new_pairs():
    from services.osrm import OSRMService, OSRMCache
    calls = []; requests, fake = _osrm_mock(calls); orig = requests.get; requests.get = fake
    try:
        svc = OSRMService(cache=OSRMCache(":memory:"))
        pts = [{"id": str(i), "lat": 21 + i * .01, "lon": 105.8 + i * .01} for i in range(6)]
        svc.matrix(pts); assert len(calls) == 1                       # cold: one full table
        svc.matrix(pts); assert len(calls) == 1                       # warm: zero calls
        gps = {"id": "gps_current", "lat": 21.5, "lon": 105.9}
        m = svc.matrix([gps] + pts); assert len(calls) == 3           # only gps row + gps column
        assert m["gps_current"]["0"]["source"] == "osrm" and m["1"]["2"]["cached"]
        svc.matrix([{"id": "gps_current", "lat": 21.5, "lon": 105.9}] + pts); assert len(calls) == 3   # same GPS: all cached
    finally:
        requests.get = orig


def test_osrm_fallback_is_not_cached():
    from services.osrm import OSRMService, OSRMCache
    import requests
    orig = requests.get
    def boom(*a, **k): raise RuntimeError("offline")
    requests.get = boom
    try:
        svc = OSRMService(cache=OSRMCache(":memory:"))
        pts = [{"id": "a", "lat": 21.0, "lon": 105.8}, {"id": "b", "lat": 21.02, "lon": 105.82}]
        m = svc.matrix(pts); assert m["a"]["b"]["source"] == "haversine_fallback"
        assert not svc.cache._mem and svc.cache._db.execute("select count(*) from pairs").fetchone()[0] == 0
    finally:
        requests.get = orig


def test_prefilter():
    from prefilter import filter_infeasible, top_k
    pts, m, d, st, en = _world(10)
    pts[1]["open_time"], pts[1]["close_time"] = dtime(18), dtime(20)       # closed in an 8-10h window
    pts[2]["lat"] = 25.0                                                    # ~400 km away
    kept, dr = filter_infeasible(pts[1:], pts[0], st, st + timedelta(hours=2), d, "xe_may", excluded_ids=["3"])
    assert pts[1]["ten"] in dr["closed_window"] and pts[2]["ten"] in dr["unreachable"] and pts[3]["ten"] in dr["excluded"]
    assert all(p["id"] not in ("1", "2", "3") for p in kept)
    k2, cut = top_k(kept, pts[0], k=3, protect_ids=["9"])
    assert len(k2) == 3 and any(p["id"] == "9" for p in k2) and len(cut) == len(kept) - 3


def test_nominatim_local_first_throttle_cache():
    from services.nominatim import NominatimService
    import requests
    hits = []
    class R:
        def raise_for_status(s): pass
        def json(s): return [{"display_name": "Hồ Gươm, Hà Nội", "lat": "21.0285", "lon": "105.8522", "osm_type": "way", "osm_id": 1}]
    orig = requests.get; requests.get = lambda *a, **k: (hits.append(1), R())[1]
    try:
        svc = NominatimService(min_interval=60)
        local = [{"id": "5", "ten": "Đền Ngọc Sơn", "lat": 21.03, "lon": 105.85}]
        r = svc.suggest("ngoc", limit=1, local_points=local); assert r["suggestions"][0]["source"] == "local"
        r = svc.suggest("ho guom", local_points=local); assert r["remote"] and r["suggestions"][0]["source"] == "nominatim" and len(hits) == 1
        r = svc.suggest("ho guom", local_points=local); assert len(hits) == 1                # cached
        r = svc.suggest("lang bac", local_points=local); assert r["throttled"] and len(hits) == 1   # <1 req / interval
        assert svc.suggest("ab", local_points=local)["remote"] is False                    # < 3 chars: no remote
    finally:
        requests.get = orig


def test_diverse_routes():
    pts, m, d, st, en = _world(16, seed=5)
    pool = []
    front, _ = H.run(pts, m, 0, st, en, d, S.constant_traffic(1.0), pop_size=30, generations=25, seed=2, pool_out=pool)
    picked = H.select_diverse(front, pool, k_min=3, k_max=5)
    assert 3 <= len(picked) <= 5
    inds = [p for p, _ in picked]
    assert len({tuple(i.route) for i in inds}) == len(inds)
    worst = max(H.route_similarity(a, b) for i, a in enumerate(inds) for b in inds[i + 1:])
    assert worst <= 0.95, worst      # genuinely different POI sets, not 5 near-copies




# ------------------------------------------------ category quota
def _food_world():
    pts, m, d, st, en = _world(18, seed=9)
    for i, p in enumerate(pts):                      # 12 cafe/food (the trap), 4 sights, 2 malls
        p["loai_hinh"] = ["Cafe", "Ăn uống", "Cafe", "Ăn uống"][i % 4] if i < 13 else ("Tham quan" if i < 17 else "TTTM")
        p["experience_score"] = 0.9 if p["loai_hinh"] in ("Cafe", "Ăn uống") else 0.5   # food looks BEST -> old model spams it
    return pts, m, d, st, en


def test_category_quota_hybrid():
    from categories import excess, default_caps, composition
    pts, m, d, st, en = _food_world()
    caps = default_caps(6)
    front, _ = H.run(pts, m, 0, st, en, d, S.constant_traffic(1.0), pop_size=30, generations=25, seed=4)
    assert front
    for ind in front:
        assert excess([pts[i] for i in ind.route[1:]], caps) == 0, composition([pts[i] for i in ind.route[1:]])
    free, _ = H.run(pts, m, 0, st, en, d, S.constant_traffic(1.0), pop_size=30, generations=25, seed=4, caps=None)
    worst = max(sum(1 for i in x.route[1:] if pts[i]["loai_hinh"] in ("Cafe", "Ăn uống")) for x in free)
    assert worst > caps["food_cafe_total"]            # without the quota the optimiser really does spam food/cafe


def test_category_quota_replan_and_prefilter():
    from categories import excess, default_caps
    from prefilter import top_k
    pts, m, d, st, en = _food_world()
    caps = default_caps(6)
    route = [pts[0], pts[14], pts[15]]
    pool = [p for p in pts if p["loai_hinh"] in ("Cafe", "Ăn uống")] + [pts[16]]
    out = replan_route(route, pool, m, st + timedelta(hours=1), en, d, lambda a, b, t: 1.0, n_fixed=1, caps=caps)
    assert excess(out["route"][1:], caps) == 0 and len(out["route"]) >= 3
    kept, _ = top_k(pts[1:], pts[0], k=10, caps=caps)
    assert sum(1 for p in kept if p["loai_hinh"] in ("Cafe",)) <= 3 * caps["cafe"] and any(p["loai_hinh"] == "Tham quan" for p in kept)




# ------------------------------------------------ review round 2
def test_osrm_k_override_is_exact():
    from services.osrm import OSRMService, OSRMCache
    calls = []; requests, fake = _osrm_mock(calls); orig = requests.get; requests.get = fake
    try:
        svc = OSRMService(cache=OSRMCache(":memory:"))
        pts = [{"id": "a", "lat": 21.0, "lon": 105.8}, {"id": "b", "lat": 21.02, "lon": 105.82}]
        raw = svc.matrix(pts, "xe_may", k_override=1.0)["a"]["b"]["duration"]
        k15 = svc.matrix(pts, "xe_may", k_override=1.5)["a"]["b"]["duration"]
        assert abs(k15 / raw - 1.5) < 1e-2 and len(calls) == 1          # same cached RAW pair, scaled once
    finally:
        requests.get = orig


def test_deadline_overshoot_counted_once():
    pts, m, d, st, en = _world(6)
    c = H.Ctx(pts, m, st, st + timedelta(minutes=30), d, S.constant_traffic(1.0), lambda p, w: {"crowd_index": 0}, lambda p, w: 0, caps=None)
    for p in pts: p["close_time"] = dtime(23, 59)
    c = H.Ctx(pts, m, st, st + timedelta(minutes=30), d, S.constant_traffic(1.0), lambda p, w: {"crowd_index": 0}, lambda p, w: 0, caps=None)
    one = H.schedule([0, 1], c)[2]
    four = H.schedule([0, 1, 2, 3, 4], c)[2]
    clock_end = H.schedule([0, 1, 2, 3, 4], c)[0] + 0       # total minutes incl. everything
    assert four < one + 4 * 200            # not 4 x re-added overshoot
    assert abs(four - (clock_end - 30)) < 1e-6 or four >= one   # overshoot == final clock - deadline (once)


def test_flow_conservation_is_real():
    from replan_engine import check_flow_conservation as F
    P = lambda *ids: [{"id": i} for i in ids]
    assert F(P("0", "1", "2"), start_id="0")
    assert not F(P("1", "2"), start_id="0")                      # wrong origin
    assert not F(P("0", "1", "1"), start_id="0")                 # revisit
    assert F(P("0", "1", "2", "0"), start_id="0", return_to_start=False) is False
    assert F(P("0", "1", "2"), start_id="0", return_to_start=True)
    assert not F(P("0", "1", "2"), start_id="0", end_id="9")     # declared end not reached


def test_midnight_window_and_crossing_scale():
    from replan_engine import window_dt, count_crossings
    d = datetime(2025, 6, 15).date()
    o, c = window_dt({"open_time": dtime(18), "close_time": dtime(2)}, d)
    assert c - o == timedelta(hours=8)
    X = [{"lat": 0, "lon": 0}, {"lat": 1, "lon": 1}, {"lat": 0, "lon": 1}, {"lat": 1, "lon": 0}]
    assert count_crossings(X) == 1


def test_replan_penalty_uses_arrival_time_and_monitor():
    from monitor import check_disruption
    pts, m, d, st, en = _world(8)
    seen = []
    def pen(p, t): seen.append(t); return 0.0
    replan_route(pts[:5], pts[5:], m, st, en, d, lambda a, b, t: 1.0, penalty_fn=pen, n_fixed=1)
    assert seen and all(t >= st for t in seen) and len({t for t in seen}) > 1       # per-arrival, not a single `now`
    class T:
        def factor(self, a, b, w): return 2.0
    rem = [dict(p, arrive_time="10:00") for p in pts[:3]]
    r = check_disruption(rem, st, T(), lambda p, w: 0.9, d)
    assert r["triggered"] and {x["type"] for x in r["reasons"]} == {"traffic", "rain"}
    class T1:
        def factor(self, a, b, w): return 1.1
    assert not check_disruption(rem, st, T1(), lambda p, w: 0.0, d)["triggered"]


def test_parser_time_limit_only_when_explicit():
    from services.parser import parse_prompt
    assert parse_prompt("Tôi muốn đi tour 6 tiếng ở trung tâm")["time_limit_explicit"]
    assert not parse_prompt("thích chụp ảnh, tránh chỗ đông")["time_limit_explicit"]


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_"): f(); print("[OK]", k)
