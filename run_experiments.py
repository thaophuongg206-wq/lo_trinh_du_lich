#!/usr/bin/env python3
"""
Research experiment runner for the Hybrid AI TTDP system.

Produces filled Table 1 (Main Results) and Table 2 (Ablation Study)
aligned with the paper structure, using the real Hybrid NSGA-II,
Greedy 2-opt, evaluation metrics, and offline-friendly synthetic signals.

Usage:
    python run_experiments.py
    python run_experiments.py --quick          # smaller pop/gen for smoke test
    python run_experiments.py --cases 10       # fewer test cases

Output:
    experiments/results.json
    experiments/TABLE1.md
    experiments/TABLE2.md
"""
from __future__ import annotations
import argparse
import json
import math
import os
import random
import sqlite3
import time
from copy import deepcopy
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
import hashlib
import statistics as st
import hybrid_nsga as H
from replan_engine import count_crossings, replan_route, simulate_tail
from evaluation import shared_hypervolumes
from absa_service import analyze as absa_analyze, _lexical
from crowd_prediction import CrowdPredictor
import signals as S

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "dulich.db"
OUT_DIR = ROOT / "experiments"
OUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

ABSA_CACHE = ROOT / "absa_cache.json"


def _absa(text: str, mode: str, cache: dict) -> dict:
    """mode 'lexical' (fast, offline) or 'ollama' (real LLM, cached on disk by text hash)."""
    if mode == "lexical":
        return _lexical(text)
    key = hashlib.md5(text.encode("utf-8")).hexdigest()
    if key not in cache:
        cache[key] = absa_analyze(text, timeout=60.0)
        ABSA_CACHE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache[key]


REVIEWS_JSON = ROOT / "places_output.json"
NAME_ALIAS = {"lotte center hanoi": "lotte center lieu giai"}   # tên khác nhau giữa crawl và DB


def _fold(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").strip()
    return NAME_ALIAS.get(s, s)


def load_real_reviews() -> Dict[str, List[str]]:
    """{folded place name: [review text, ...]} from the crawled SerpApi file."""
    if not REVIEWS_JSON.exists():
        return {}
    out = {}
    for p in json.loads(REVIEWS_JSON.read_text(encoding="utf-8")):
        out[_fold(p["ten_dia_diem"])] = [r["noi_dung"] for r in p.get("reviews", []) if (r.get("noi_dung") or "").strip()]
    return out


def load_pois(absa_mode: str = "lexical") -> List[dict]:
    cache = json.loads(ABSA_CACHE.read_text(encoding="utf-8")) if ABSA_CACHE.exists() else {}
    REAL_REVIEWS = load_real_reviews()
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT d.id, d.ten, d.vi_do AS lat, d.kinh_do AS lon,
               d.loai_hinh, d.diem_gia_tri, d.thoi_gian_tham_quan_phut AS visit_min,
               d.review, d.thong_tin_chi_tiet, d.phu_hop,
               c.gio_mo_cua, c.gio_dong_cua
        FROM DIA_DIEM d
        JOIN CUA_SO_THOI_GIAN c ON d.id = c.dia_diem_id
        """
    ).fetchall()
    conn.close()
    pois = []
    for r in rows:
        open_t = dtime.fromisoformat(str(r["gio_mo_cua"])[:8])
        close_t = dtime.fromisoformat(str(r["gio_dong_cua"])[:8])
        rating = float(r["diem_gia_tri"] or 7.0) / 2.0  # scale ~1-5
        review_text = " ".join(
            str(r[k] or "") for k in ("review", "thong_tin_chi_tiet", "phu_hop")
        )
        real = REAL_REVIEWS.get(_fold(r["ten"]), [])
        if real:   # SABSA = mean of per-review ABSA over the real crawled reviews
            per = [_absa(t, absa_mode, cache) for t in real]
            sabsa = {"sabsa_score": sum(x["sabsa_score"] for x in per) / len(per),
                     "source": per[0].get("source", "?") + f"+{len(real)}reviews"}
            review_text = " ".join(real)[:400]
        else:
            sabsa = _absa(review_text, absa_mode, cache)
        # Cap visit duration so short budgets (3h) remain feasible after repair.
        raw_visit = float(r["visit_min"] or 45)
        visit = max(20.0, min(60.0, raw_visit * 0.45))
        pois.append({
            "id": str(r["id"]),
            "ten": r["ten"],
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "loai_hinh": r["loai_hinh"],
            "rating": round(rating, 2),
            "time": visit,
            "open_time": open_t,
            "close_time": close_t,
            "sabsa_score": float(sabsa["sabsa_score"]),
            "absa_source": sabsa.get("source"),
            "review": review_text[:200],
            "experience_score": 0.0,  # filled later with w_pref
        })
    return pois


def build_haversine_matrix(pois: List[dict], vehicle: str = "xe_may") -> dict:
    speeds = {"xe_may": 25, "o_to": 30, "di_bo": 4.5, "xe_dap": 12}
    speed = speeds.get(vehicle, 25)
    matrix: Dict[str, Dict[str, dict]] = {}
    for a in pois:
        matrix[a["id"]] = {}
        for b in pois:
            if a["id"] == b["id"]:
                matrix[a["id"]][b["id"]] = {"duration": 0.0, "distance": 0.0}
                continue
            dlat = (a["lat"] - b["lat"]) * 111
            dlon = (a["lon"] - b["lon"]) * 111
            dist = math.hypot(dlat, dlon)
            dur = (dist / speed) * 60
            matrix[a["id"]][b["id"]] = {"duration": round(dur, 2), "distance": round(dist, 3)}
    return matrix


# ---------------------------------------------------------------------------
# Baselines (all operate on index routes, are evaluated by the SAME scheduler)
# ---------------------------------------------------------------------------
def set_experience(pois, w_pref, use_absa=True):
    for p in pois:
        r = float(p["rating"]) / 5.0
        p["experience_score"] = round(w_pref * float(p["sabsa_score"]) + (1 - w_pref) * r, 4) if use_absa else round(r, 4)


def b_greepop(c: H.Ctx, start_idx):
    order = sorted((i for i in range(len(c.pts)) if i != start_idx), key=lambda i: -H.exp_of(c.pts[i]))
    r = [start_idx]
    for i in order:                      # take hottest POIs while they still fit
        if H.feasible(r + [i], c):
            r.append(i)
    return r


def b_greenear(c: H.Ctx, start_idx):
    rem, r = [i for i in range(len(c.pts)) if i != start_idx], [start_idx]
    while rem:
        clock = H.schedule(r, c)[3][-1] + timedelta(minutes=float(c.pts[r[-1]].get("time") or 0))
        nxt = min(rem, key=lambda i: c.leg(r[-1], i, clock))
        rem.remove(nxt)
        if H.feasible(r + [nxt], c):
            r.append(nxt)
    return r


def b_ollama(pois, c: H.Ctx, start_idx, budget_h, cot: bool):
    """REAL Ollama baseline: the LLM orders POIs from a text list; no mock. None if Ollama is down."""
    from services.ollama import generate, extract_json
    lst = "\n".join(f'{i}: {p["ten"]} ({p["loai_hinh"]}, mở {p["open_time"]:%H:%M}-{p["close_time"]:%H:%M}, '
                    f'thăm {int(p["time"])} phút, lat {p["lat"]:.4f}, lon {p["lon"]:.4f})' for i, p in enumerate(pois))
    q = (f"Lập lịch tham quan Hà Nội {budget_h:g} giờ, bắt đầu 08:00 tại POI {start_idx}. Danh sách:\n{lst}\n"
         + ("Hãy suy nghĩ từng bước về khoảng cách và giờ mở cửa. " if cot else "")
         + 'Trả về DUY NHẤT JSON {"route":[id,...]} bắt đầu bằng ' + str(start_idx) + ".")
    t0 = time.perf_counter()
    res = generate(q, json_mode=True, timeout=180)
    lat = (time.perf_counter() - t0) * 1000
    if not res["ok"]:
        return None, lat
    obj = extract_json(res["text"]) or {}
    r = [x for x in obj.get("route", []) if isinstance(x, int) and 0 <= x < len(pois)]
    seen, out = set(), []
    for x in [start_idx] + r:
        if x not in seen:
            seen.add(x); out.append(x)
    return out, lat          # NOT repaired: violations are measured as-is


def polish(route, c):  # optional, applied to EVERY method when --polish is set
    return H.repair(H.two_opt_idx(route, c), c)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
def metrics_of(route, c: H.Ctx, lat_ms):
    ind = H.evaluate(route, c)
    total, _w, cv, arr = H.schedule(route, c)
    n = max(1, len(route) - 1)
    return {
        "hours": round(total / 60, 3), "sentiment": round(-ind.obj[0] / n, 4), "f1_sum": round(-ind.obj[0], 4),
        "ol": H._crossings_idx(route, c.xy), "violation": cv > 1e-9, "latency_ms": lat_ms,
        "n_pois": len(route) - 1, "obj": ind.obj,
        "n_food_cafe": sum(1 for i in route[1:] if c.cat[i] in ("cafe", "an_uong")),
    }


def jam_exposure(route, c: H.Ctx, thr=1.3):
    """Share of travel minutes spent where the TRUE traffic factor >= thr."""
    clock, bad, tot = c.start, 0.0, 0.0
    for pos in range(1, len(route)):
        prev = route[pos - 1]
        clock_leave = H.schedule(route[:pos], c)[3][-1] + timedelta(minutes=float(c.pts[prev].get("time") or 0))
        a, b = c.pts[prev], c.pts[route[pos]]
        base = float(c.m[a["id"]][b["id"]]["duration"])
        f = float(c.traffic.factor(a, b, clock_leave))
        tot += base * f
        if f >= thr:
            bad += base * f
    return bad / tot if tot else 0.0


class Disrupted:
    """True traffic x jam after `t_event` (incident) -- used for in-tour experiments."""
    source = "disruption"
    def __init__(self, base, t_event, jam): self.base, self.t, self.jam = base, t_event, jam
    def factor(self, a, b, when): return self.base.factor(a, b, when) * (self.jam if when >= self.t else 1.0)


def disruption_test(route, pois, c: H.Ctx, jam, replan: bool, pool_idx, rain_fn):
    """Incident after the 2nd POI. Returns violation (bool), latency, sentiment of remaining plan."""
    if len(route) < 4:
        return None
    k = 2                                                    # visited prefix = route[:2]
    arr = H.schedule(route, c)[3]
    t_event = arr[k - 1] + timedelta(minutes=float(pois[route[k - 1]].get("time") or 0))
    dis = Disrupted(c.traffic, t_event, jam)
    plan = [pois[i] for i in route]
    if not replan:
        feas, _fin, _a = simulate_tail(plan[k - 1], plan[k:], t_event, c.end, c.date, c.m, dis.factor, None)
        return {"violation": not feas, "latency_ms": 0.0, "n_pois": len(plan) - 1,
                "sentiment": round(sum(H.exp_of(p) for p in plan[1:]) / max(1, len(plan) - 1), 4), "ol": count_crossings(plan)}
    pen = lambda p, t: 10.0 * rain_fn(p, t)                  # rain at ARRIVAL time => outdoor POIs cost more (minutes-equivalent)
    out = replan_route(plan, [pois[i] for i in pool_idx], c.m, t_event, c.end, c.date, dis.factor, pen, n_fixed=k, caps=c.caps)
    r = out["route"]
    return {"violation": not out["feasible"], "latency_ms": out["latency_ms"], "n_pois": len(r) - 1,
            "sentiment": round(sum(H.exp_of(p) for p in r[1:]) / max(1, len(r) - 1), 4), "ol": out["overlap_crossings"],
            "dropped": len(out["dropped"]), "added": len(out["added"])}


# ---------------------------------------------------------------------------
# One test case x one seed
# ---------------------------------------------------------------------------
def best_by_exp(front):
    return max(front, key=lambda i: -i.obj[0]) if front else None


def run_case(case_id, pois, matrix, budget_h, w_pref, seed, pop, gens, traffic, weather_fn, crowd_fn, args):
    pois = deepcopy(pois)
    set_experience(pois, w_pref)
    base = datetime(2025, 6, 15).date()
    start = datetime.combine(base, dtime(8, 0)); end = start + timedelta(hours=budget_h)
    c = H.Ctx(pois, matrix, start, end, base, traffic, crowd_fn, weather_fn)
    pol = (lambda r: polish(r, c)) if args.polish else (lambda r: r)
    res: Dict[str, Any] = {"case_id": case_id, "seed": seed, "budget_h": budget_h, "w_pref": w_pref}
    fronts: Dict[str, list] = {}

    def single(name, route, lat):
        route = pol(route)
        res[name] = metrics_of(route, c, lat); fronts[name] = [res[name]["obj"]]

    t = time.perf_counter(); r = b_greepop(c, 0); single("GREEPOP", r, (time.perf_counter() - t) * 1000)
    t = time.perf_counter(); r = b_greenear(c, 0); single("GREENEAR", r, (time.perf_counter() - t) * 1000)

    if args.ollama:
        for name, cot in (("Pure_Ollama", False), ("Pure_Ollama_CoT", True)):
            r, lat = b_ollama(pois, c, 0, budget_h, cot)
            if r:
                single(name, r, lat)

    def nsga(name, **kw):
        front, lat = H.run(pois, matrix, 0, start, end, base, traffic, crowd_fn, weather_fn,
                           pop_size=pop, generations=gens, seed=seed, **kw)
        if args.polish:
            front = [H.evaluate(pol(i.route), c) for i in front]
        b = best_by_exp(front)
        m = metrics_of(b.route, c, lat)
        m["pareto_size"] = len(front)
        m["ol_front"] = round(sum(H._crossings_idx(i.route, c.xy) for i in front) / len(front), 3)
        m["viol_front_pct"] = round(100 * sum(1 for i in front if i.cv > 1e-9) / len(front), 1)
        res[name] = m; fronts[name] = [i.obj for i in front]
        return front

    nsga("Standard_NSGAII", use_seeds=False, use_2opt=False)
    front_h = nsga("Hybrid_NSGAII")
    hv = shared_hypervolumes(fronts)                         # one scale + one reference point for everyone
    for k, v in hv.items():
        res[k]["hv"] = v

    # Greedy+2-opt as a stand-alone planner (from scratch) ...
    t = time.perf_counter()
    t_leave = start + timedelta(minutes=float(pois[0].get('time') or 0))   # same clock as H.schedule
    out = replan_route([pois[0]], pois[1:], matrix, t_leave, end, base, traffic.factor, n_fixed=1, pool_limit=len(pois), caps=c.caps)
    lat = (time.perf_counter() - t) * 1000
    idx = {p["id"]: i for i, p in enumerate(pois)}
    single("Greedy_2opt", [idx[p["id"]] for p in out["route"]], lat)
    res["Greedy_2opt"].pop("hv", None)

    # ... and as in-tour re-planner under a real disruption, vs. NOT re-planning
    rng = random.Random(seed)
    jam = rng.uniform(1.5, 2.0)
    b = best_by_exp(front_h)
    pool = [i for i in range(1, len(pois)) if i not in b.route]
    rain = weather_fn
    res["Disruption"] = {
        "replan": disruption_test(b.route, pois, c, jam, True, pool, rain),
        "no_replan": disruption_test(b.route, pois, c, jam, False, pool, rain),
        "jam": round(jam, 2),
    }
    res["_front_hybrid"] = b.route
    res["_traffic_exposure"] = round(jam_exposure(b.route, c), 4)
    return res


def run_ablation_case(case_id, pois, matrix, budget_h, w_pref, seed, pop, gens, traffic, weather_fn, crowd_fn):
    """Every variant is EVALUATED with the full system's scorer, never with its own objective."""
    pois = deepcopy(pois)
    base = datetime(2025, 6, 15).date()
    start = datetime.combine(base, dtime(8, 0)); end = start + timedelta(hours=budget_h)
    set_experience(pois, w_pref, True)
    truth = H.Ctx(pois, matrix, start, end, base, traffic, crowd_fn, weather_fn)       # full score + TRUE traffic
    rng = random.Random(seed); jam = rng.uniform(1.5, 2.0)

    def variant(use_absa=True, use_2opt=True, use_traffic=True, use_dynamic=True):
        opt = deepcopy(pois)
        set_experience(opt, w_pref, use_absa)                       # optimiser may only see rating
        tr = traffic if use_traffic else S.constant_traffic(1.0)    # optimiser blind to traffic
        front, lat = H.run(opt, matrix, 0, start, end, base, tr, crowd_fn, weather_fn,
                           pop_size=pop, generations=gens, seed=seed, use_2opt=use_2opt)
        b = max(front, key=lambda i: sum(H.exp_of(pois[k]) for k in i.route[1:]))   # pick by TRUE experience
        m = metrics_of(b.route, truth, lat)
        pool = [i for i in range(1, len(pois)) if i not in b.route]
        d = disruption_test(b.route, pois, truth, jam, use_dynamic, pool, weather_fn)
        return {"sentiment": m["sentiment"], "ol": m["ol"], "jam_pct": round(100 * jam_exposure(b.route, truth), 2),
                "violation": m["violation"], "post_incident_violation": (d or {}).get("violation", False),
                "latency_ms": lat}
    return {"Full_System": variant(), "w_o_ABSA": variant(use_absa=False), "w_o_2opt": variant(use_2opt=False),
            "w_o_TomTom": variant(use_traffic=False), "w_o_Dynamic": variant(use_dynamic=False)}


# ---------------------------------------------------------------------------
# Aggregation / stats / tables
# ---------------------------------------------------------------------------
METHODS = ["GREEPOP", "GREENEAR", "Pure_Ollama", "Pure_Ollama_CoT", "Standard_NSGAII", "Greedy_2opt", "Hybrid_NSGAII"]
LABELS = {"GREEPOP": "GREEPOP", "GREENEAR": "GREENEAR", "Pure_Ollama": "Pure Ollama (Zero-shot)",
          "Pure_Ollama_CoT": "Pure Ollama + CoT", "Standard_NSGAII": "Standard NSGA-II",
          "Greedy_2opt": "Ours: Greedy 2-opt (from scratch)", "Hybrid_NSGAII": "Ours: Hybrid NSGA-II (Pareto)"}


def per_case_mean(rows, method, key):
    by = {}
    for r in rows:
        if method in r and key in r[method]:
            by.setdefault(r["case_id"], []).append(float(r[method][key]))
    return {k: sum(v) / len(v) for k, v in by.items()}


def ms(vals):
    vals = list(vals)
    if not vals: return "N/A"
    return f"{st.mean(vals):.3f} ± {st.pstdev(vals):.3f}" if len(vals) > 1 else f"{vals[0]:.3f}"


def wilcoxon(rows, a, b, key):
    from scipy.stats import wilcoxon as w
    x, y = per_case_mean(rows, a, key), per_case_mean(rows, b, key)
    ks = sorted(set(x) & set(y))
    try:
        stat, p = w([x[k] for k in ks], [y[k] for k in ks])
    except ValueError:
        p = 1.0
    return {"n_pairs": len(ks), "mean_a": round(st.mean(x[k] for k in ks), 4) if ks else None,
            "mean_b": round(st.mean(y[k] for k in ks), 4) if ks else None, "p": round(float(p), 5)}


def render_table1(rows):
    L = ["| Phương pháp | Tổng thời gian (h) | Sentiment/POI | #POI | #Cafe+Ăn | OL (route tốt nhất) | OL (cả front) | Vi phạm (%) | Latency (s) | HV (chung thang) |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for m in METHODS:
        if not any(m in r for r in rows):
            L.append(f"| {LABELS[m]} | N/A (chưa chạy: dùng --ollama) | | | | | | | | |"); continue
        g = lambda k: per_case_mean(rows, m, k).values()
        viol = 100 * st.mean(per_case_mean(rows, m, "violation").values())
        hv = ms(g("hv")) if any("hv" in r.get(m, {}) for r in rows) else "N/A"
        olf = ms(g("ol_front")) if any("ol_front" in r.get(m, {}) for r in rows) else "–"
        L.append(f"| {LABELS[m]} | {ms(g('hours'))} | {ms(g('sentiment'))} | {ms(g('n_pois'))} | {ms(g('n_food_cafe'))} | {ms(g('ol'))} | {olf} | "
                 f"{viol:.1f}% | {st.mean(g('latency_ms'))/1000:.3f} | {hv} |")
    return "\n".join(L)


def render_table2(abl_rows):
    order = [("Full_System", "Full System (Ours)"), ("w_o_ABSA", "(a) w/o ABSA (rating thô)"),
             ("w_o_2opt", "(b) w/o 2-opt"), ("w_o_TomTom", "(c) w/o TomTom traffic"), ("w_o_Dynamic", "(d) w/o Dynamic re-planning")]
    L = ["| Biến thể | Sentiment/POI (thang đầy đủ) | OL | Kẹt xe (% thời gian di chuyển) | Vi phạm trước sự cố (%) | Vi phạm sau sự cố (%) | Latency (s) |",
         "| --- | --- | --- | --- | --- | --- | --- |"]
    for k, lab in order:
        g = lambda f: [r[k][f] for r in abl_rows]
        L.append(f"| {lab} | {ms(g('sentiment'))} | {ms(g('ol'))} | {ms(g('jam_pct'))} | {100*st.mean(map(float,g('violation'))):.1f}% | "
                 f"{100*st.mean(map(float,g('post_incident_violation'))):.1f}% | {st.mean(g('latency_ms'))/1000:.3f} |")
    return "\n".join(L)


def render_table3(rows):
    L = ["| Sau sự cố kẹt xe | Vi phạm (%) | Sentiment/POI | OL | Latency (ms) |", "| --- | --- | --- | --- | --- |"]
    for k, lab in (("no_replan", "Giữ nguyên lộ trình"), ("replan", "Greedy 2-opt re-plan")):
        v = [r["Disruption"][k] for r in rows if r["Disruption"][k]]
        if not v: continue
        L.append(f"| {lab} | {100*st.mean(float(x['violation']) for x in v):.1f}% | {ms(x['sentiment'] for x in v)} | "
                 f"{ms(x['ol'] for x in v)} | {ms(x['latency_ms'] for x in v)} (max {max(x['latency_ms'] for x in v):.1f}) |")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Hybrid AI TTDP experiment runner (no mocks)")
    ap.add_argument("--cases", type=int, default=30)
    ap.add_argument("--seeds", type=int, default=3, help="independent GA seeds per case")
    ap.add_argument("--abl-cases", type=int, default=10)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--absa", choices=["lexical", "ollama"], default="lexical")
    ap.add_argument("--ollama", action="store_true", help="run REAL Pure-Ollama baselines (needs Ollama)")
    ap.add_argument("--polish", action="store_true", help="apply 2-opt+repair to EVERY method (reported as such)")
    args = ap.parse_args()
    pop, gens = (20, 25) if args.quick else (40, 50)

    pois = load_pois(args.absa)
    matrix = build_haversine_matrix(pois)
    tpath, wpath = ROOT / "traffic_snapshot.json", ROOT / "weather_snapshot.json"
    traffic = S.FrozenTraffic(str(tpath)) if tpath.exists() else S.SyntheticTraffic(seed=args.seed)
    weather = S.FrozenWeather(str(wpath)) if wpath.exists() else S.SyntheticWeather(seed=args.seed)
    weather_fn = S.make_weather_fn(weather)
    crowd_fn = S.make_crowd_fn(CrowdPredictor(str(ROOT / "crowd_history.csv")))
    sources = {"pois": len(pois), "absa": sorted({p["absa_source"] for p in pois}), "traffic": traffic.source,
               "weather": weather.source, "time_matrix": "haversine (not OSRM)", "crowd": "CrowdPredictor (crowd_history.csv)"}
    print("DATA SOURCES:", sources)

    budgets = [3.0, 6.0, 12.0]
    rows = []
    for i in range(min(args.cases, 30)):
        bh, wp = budgets[i % 3], 0.4 + 0.2 * ((i // 3) % 3)
        for s in range(args.seeds):
            r = run_case(i + 1, pois, matrix, bh, wp, args.seed + 1000 * s + i, pop, gens, traffic, weather_fn, crowd_fn, args)
            rows.append(r)
        h = rows[-1]["Hybrid_NSGAII"]; sd = rows[-1]["Standard_NSGAII"]
        print(f"case {i+1:02d} {bh:g}h  Hybrid OL={h['ol']} HV={h['hv']}  Std OL={sd['ol']} HV={sd['hv']}", flush=True)

    abl_rows = []
    for i in range(min(args.abl_cases, args.cases)):
        for s in range(args.seeds):
            abl_rows.append(run_ablation_case(i + 1, pois, matrix, budgets[i % 3], 0.4 + 0.2 * ((i // 3) % 3),
                                              args.seed + 1000 * s + i, pop, gens, traffic, weather_fn, crowd_fn))

    tests = {k: wilcoxon(rows, "Hybrid_NSGAII", "Standard_NSGAII", k) for k in ("hv", "sentiment", "ol_front", "hours")}
    t1, t2, t3 = render_table1(rows), render_table2(abl_rows), render_table3(rows)
    print("\nTABLE 1\n" + t1 + "\n\nTABLE 2\n" + t2 + "\n\nTABLE 3 (re-planning)\n" + t3 + "\n\nWilcoxon Hybrid vs Standard:", json.dumps(tests, indent=1))

    clean = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    for r in clean:
        for m in METHODS:
            r.get(m, {}).pop("obj", None)
    meta = {"n_cases": min(args.cases, 30), "seeds": args.seeds, "pop": pop, "gens": gens, "polish": args.polish,
            "data_sources": sources, "timestamp": datetime.now().isoformat()}
    (OUT_DIR / "results.json").write_text(json.dumps({"meta": meta, "wilcoxon": tests, "cases": clean, "ablation": abl_rows},
                                                     ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    note = f"\n\n_Nguồn dữ liệu: {json.dumps(sources, ensure_ascii=False)}; polish={args.polish}; {args.seeds} seed/case._\n"
    (OUT_DIR / "TABLE1.md").write_text("# Bảng 1\n\n" + t1 + note, encoding="utf-8")
    (OUT_DIR / "TABLE2.md").write_text("# Bảng 2 – Ablation\n\n" + t2 + note, encoding="utf-8")
    (OUT_DIR / "TABLE3.md").write_text("# Bảng 3 – Re-planning\n\n" + t3 + note, encoding="utf-8")


if __name__ == "__main__":
    main()
