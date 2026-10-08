"""Research endpoints: Hybrid NSGA-II, dynamic signals, in-tour re-planning, ABSA, narrative."""
from pydantic import BaseModel
from fastapi import HTTPException
from api.schemas import OptimizationRequest
from typing import Optional
from api.preferences import _strip_diacritics
from api.diversity import build_timeline
from datetime import datetime, timedelta
from api.db import fetch_all_points
from api import osrm_matrix
from api.osrm_matrix import vehicle_restriction
import os
from itinerary_store import store

from fastapi import APIRouter
from core import audit
from config import CFG
from core.objectives import blend_experience
router = APIRouter()

# ============================================================
# RESEARCH MODE: HYBRID NSGA-II + DYNAMIC SIGNALS
# ============================================================
@router.post("/api/optimize-route-hybrid")
async def optimize_route_hybrid(request: OptimizationRequest):
    """
    Research endpoint implementing the 3-objective Hybrid NSGA-II:
      max experience (SABSA + rating), min total time, min crowd + weather.
    Existing /api/routes remains backward-compatible for the current frontend.
    """
    try:
        from hybrid_nsga import run as run_hybrid
        from absa_service import enrich_points
        from crowd_prediction import CrowdPredictor
        from weather_service import WeatherService

        base_date = datetime.strptime(request.trip_date, "%Y-%m-%d").date() if request.trip_date else datetime.today().date()
        start_dt = datetime.strptime(request.start_time, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        end_dt = datetime.strptime(request.end_time, "%H:%M").replace(year=base_date.year, month=base_date.month, day=base_date.day)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        points = fetch_all_points(request.vehicle_type)
        if request.excluded_ids:
            points = [p for p in points if p["id"] not in set(map(str, request.excluded_ids))]

        if request.start_lat is not None and request.start_lon is not None:
            origin = {
                "id":"gps_current","ten":"📍 Vị trí của bạn",
                "lat":request.start_lat,"lon":request.start_lon,"time":0,
                "open_time":datetime.strptime("00:00","%H:%M").time(),
                "close_time":datetime.strptime("23:59","%H:%M").time(),
                "score":0,"loai_hinh":"diem_xuat_phat",
                "mo_ta":"","thong_tin_chi_tiet":"","review":"","phu_hop":"",
                "cap_do_tiep_can":3,
            }
        elif request.start_point:
            matches=[p for p in points if request.start_point.strip().lower() in p["ten"].lower()]
            if not matches:
                raise HTTPException(status_code=400, detail=f"Không tìm thấy điểm xuất phát '{request.start_point}'.")
            origin=matches[0]
        else:
            raise HTTPException(status_code=400, detail="Hybrid NSGA-II cần start_point hoặc GPS.")

        candidates=[p for p in points if p["id"] != origin["id"]]

        # LLM-as-Parser -> constraints actually consumed by the optimizer
        parsed=None
        if getattr(request,"prompt",""):
            from services.parser import parse_prompt
            parsed=parse_prompt(request.prompt)
            av=[_strip_diacritics(a) for a in parsed.get("avoid",[])]
            if av:
                candidates=[p for p in candidates if not any(a and (a in _strip_diacritics(p["ten"]) or a in _strip_diacritics(p.get("loai_hinh",""))) for a in av)]
            if not request.user_preference:
                request.user_preference=" ".join(parsed.get("preferences",[]))
            # time limit: only when the user actually stated a duration (never the parser's default)
            if parsed.get("time_limit_explicit"):
                end_dt=min(end_dt, start_dt+timedelta(minutes=int(parsed["time_limit_minutes"])))
        if not candidates:
            raise HTTPException(status_code=400, detail="Không có địa điểm ứng viên.")

        # Pre-filter BEFORE OSRM/ABSA: drop POIs that are certainly infeasible (closed, unreachable)
        from prefilter import filter_infeasible, top_k
        candidates, pf_dropped = filter_infeasible(
            candidates, origin, start_dt, end_dt, base_date, request.vehicle_type,
            return_to_start=request.return_to_start, excluded_ids=request.excluded_ids or [])
        if not candidates:
            raise HTTPException(status_code=400, detail="Không còn địa điểm khả thi trong khung giờ này.")

        # Optional SerpApi enrichment (reviews / popular times) before ABSA
        try:
            from services.serpapi import SerpApiService
            serp = SerpApiService()
            if serp.enabled:
                candidates = serp.enrich_points(candidates, max_calls=int(os.getenv("SERPAPI_MAX_CALLS", "6")))
        except Exception as e:
            audit.record("serpapi", "enrich_failed", fallback="no review/popular-times enrichment", detail=str(e))

        # ABSA is computed once per point, not once per chromosome.
        use_ollama=os.getenv("ABSA_OLLAMA_ENABLED","0").lower() in {"1","true","yes"}
        candidates=enrich_points(candidates, use_ollama=use_ollama)
        w=request.weight/100.0
        for p in candidates:
            p["experience_score"]=blend_experience(p.get("sabsa_score",0.5), p.get("score"), w)
            if parsed and any(_strip_diacritics(m) and _strip_diacritics(m) in _strip_diacritics(p["ten"]) for m in parsed.get("must_visit",[])):
                p["_must"]=True
                p["experience_score"]+=0.5   # soft must-visit: strongly preferred, still subject to time windows

        from categories import resolve_caps, composition
        caps=resolve_caps(request.category_caps or "auto",(end_dt-start_dt).total_seconds()/3600.0)
        candidates, pf_cut = top_k(candidates, origin, request.vehicle_type, caps=caps,
                                   k=CFG.routing.prefilter_max,
                                   protect_ids=[p["id"] for p in candidates if p.get("_must")])
        allp=[origin]+candidates
        import signals as SG
        live=SG.LiveTraffic()
        use_live=live.enabled      # live TomTom -> free-flow matrix x live factor; else static K only
        matrix=osrm_matrix.get_global_osrm_matrix(allp, request.vehicle_type, static_traffic=not use_live)
        predictor=CrowdPredictor(CFG.crowd_csv)
        crowd_fn=SG.make_crowd_fn(predictor)               # SerpApi Popular Times first, GBR fallback

        # TomTom enters f2: travel = OSRM duration x vehicle x traffic(prev, next, t).
        # Prefetch ONCE (n calls) so the GA loop never touches the network.
        if use_live:
            live.prefetch(allp); base_traffic=live
            if os.getenv("TRAFFIC_RECORD","0")=="1": live.record_snapshot(allp)
        else:
            base_traffic=SG.constant_traffic(1.0)
        traffic=SG.Scaled(base_traffic, vehicle_restriction(request.vehicle_type) if use_live else 1.0)

        # Weather at ARRIVAL time y_j, OUTDOOR POIs only. Warm the per-day/per-cell cache first.
        lw=SG.LiveWeather()
        for p in allp:
            lw.penalty_at(start_dt,p["lat"],p["lon"])
        weather_fn=SG.make_weather_fn(lw)

        pool=[]
        inds, elapsed=run_hybrid(
            allp,matrix,0,start_dt,end_dt,base_date,traffic,crowd_fn,weather_fn,pool_out=pool,
            use_seeds=request.nsga_seeded, use_2opt=request.nsga_local_search,
            end_idx=0 if request.return_to_start else None, caps=caps,
        )

        routes=[]
        from hybrid_nsga import select_diverse
        picked=select_diverse(inds,pool,k_min=3,k_max=max(3,min(5,request.num_routes)))
        if parsed and picked:
            from core.objectives import ObjectiveScaler
            pw=parsed["preference_weights"]
            scaler=ObjectiveScaler.for_instance(allp[1:], (end_dt-start_dt).total_seconds()/60.0)   # instance bounds, not the 3-5 picked routes
            score=lambda i:scaler.weighted(i.obj,(pw["experience"],pw["time"],pw["crowd"]))
            picked=sorted(picked,key=lambda t:score(t[0]))     # lower weighted normalised cost = recommended first
        diversity_warning=None if len(picked)>=3 else "Chỉ tìm được %d lộ trình khác biệt (ít địa điểm khả thi)."%len(picked)
        for n,(ind,label) in enumerate(picked,1):
            # Reuse the project's existing timeline schema so Screen 3 can consume
            # a research route without a second frontend data model.
            selected=[allp[i] for i in ind.route]
            details=[]
            clock=start_dt
            for i,p in enumerate(selected):
                if i:
                    prev=selected[i-1]
                    dur=matrix[prev["id"]][p["id"]]["duration"]*traffic.factor(prev,p,clock)
                    clock+=timedelta(minutes=dur)
                from replan_engine import window_dt
                open_dt,_cl=window_dt(p,base_date)
                wait_min=max(0.0,(open_dt-clock).total_seconds()/60)
                if clock<open_dt: clock=open_dt
                arrive=clock.strftime("%H:%M")
                visit=float(p.get("time") or 0); clock+=timedelta(minutes=visit)
                details.append({
                    "id":p["id"],"ten":p["ten"],"lat":p["lat"],"lon":p["lon"],
                    "loai_hinh":p.get("loai_hinh",""),"mo_ta":p.get("mo_ta",""),
                    "review":p.get("review",""),"url_hinh_anh":p.get("url_hinh_anh",""),
                    "visit_time":visit,"wait_time":round(wait_min,1),"arrive_time":arrive,
                    "depart_time":clock.strftime("%H:%M"),"travel_to_next":0,"distance_to_next":0,
                    "sabsa_score":p.get("sabsa_score"),"crowd_index":crowd_fn(p,clock).get("crowd_index"),
                })
            for i in range(len(details)-1):
                a,b=selected[i],selected[i+1]
                dep=datetime.combine(base_date,datetime.strptime(details[i]["depart_time"],"%H:%M").time())
                details[i]["travel_to_next"]=round(matrix[a["id"]][b["id"]]["duration"]*traffic.factor(a,b,dep),1)
                details[i]["distance_to_next"]=round(matrix[a["id"]][b["id"]]["distance"],1)
            return_min=0.0
            if request.return_to_start and len(selected)>1:
                last=selected[-1]; dep=datetime.combine(base_date,datetime.strptime(details[-1]["depart_time"],"%H:%M").time())
                return_min=round(matrix[last["id"]][selected[0]["id"]]["duration"]*traffic.factor(last,selected[0],dep),1)
                details[-1]["travel_to_next"]=return_min     # closing arc N+1 = origin
            total=sum(float(x["visit_time"])+float(x["wait_time"])+float(x["travel_to_next"]) for x in details)
            travel=sum(float(x["travel_to_next"]) for x in details)
            visit=sum(float(x["visit_time"]) for x in details)
            wait=sum(float(x["wait_time"]) for x in details)
            routes.append({
                "route_id":f"route_{n}","name":f"Hybrid NSGA-II #{n} – {label}",
                "theme":"hybrid_nsga2","theme_label":"Hybrid NSGA-II",
                "description":"Pareto route: trải nghiệm + thời gian + mật độ/Thời tiết.",
                "places":details,"timeline":build_timeline(details),
                "total_duration":round(total,1),"travel_time":round(travel,1),
                "visit_time":round(visit,1),"wait_time":round(wait,1),
                "distance_km":round(sum(x["distance_to_next"] for x in details),1),
                "composition":composition(selected[1:]),
                "return_to_start_minutes":return_min,
                "algorithm":"Hybrid NSGA-II","objectives":{
                    "experience":round(-ind.obj[0],4),"total_time":round(ind.obj[1],2),
                    "crowd_weather":round(ind.obj[2],4)
                }
            })
        # Narrative generator (LLM-as-Storyteller) for the first Pareto route
        narrative = None
        try:
            from services.narrative import generate as gen_narrative
            if routes:
                narrative = gen_narrative(
                    routes[0].get("places", []),
                    user_preference=getattr(request, "user_preference", "") or "",
                    start_time=request.start_time,
                    end_time=request.end_time,
                    use_ollama=use_ollama,
                )
        except Exception as _ne:
            narrative = {"narrative": str(_ne), "source": "error"}

        # Research metrics: OL = 0 target, flow conservation
        try:
            from evaluation import evaluate_route
            for r in routes:
                r["research_metrics"] = evaluate_route(r.get("places", []), r.get("objectives"))
        except Exception as e:
            audit.record("evaluation", "research_metrics_failed", fallback="metrics omitted", detail=str(e))

        return {
            "status":"success","algorithm":"Hybrid NSGA-II",
            "elapsed_ms":elapsed,"absa_ollama":use_ollama,
            "crowd_model":{"trained":predictor.trained,"samples":predictor.samples,"source":"gradient_boosting" if predictor.trained else "temporal_baseline"},
            "narrative": narrative,
            "parsed_constraints": parsed,
            "prefilter":{"dropped":pf_dropped,"cut_by_top_k":pf_cut,"candidates_to_osrm":len(candidates)},
            "diversity_warning":diversity_warning,"category_caps":caps,
            "route_overlap":[round(max([__import__("hybrid_nsga").route_similarity(a,b) for b,_ in picked if b is not a] or [0]),2) for a,_ in picked],
            "signals":{"traffic":base_traffic.source,"weather":lw.source},
            "routes":routes,
            "data_quality":audit.data_quality(),
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Hybrid NSGA-II lỗi: {exc}")

@router.get("/api/dynamic-signals")
async def dynamic_signals(lat: float, lon: float, timestamp: Optional[str] = None):
    """Expose TomTom + timestamp-aware weather for debugging and in-tour replanning."""
    from tomtom_service import TomTomService
    from weather_service import WeatherService
    when=datetime.fromisoformat(timestamp) if timestamp else datetime.now()
    return {
        "timestamp":when.isoformat(),
        "traffic":TomTomService().traffic_at(lat,lon),
        "weather":WeatherService().at(lat,lon,when)
    }

@router.get("/api/crowd-forecast")
async def crowd_forecast(place_id: str, timestamp: str):
    from crowd_prediction import CrowdPredictor
    when=datetime.fromisoformat(timestamp)
    return {"place_id":place_id,"timestamp":timestamp,
            **CrowdPredictor(CFG.crowd_csv).predict(place_id,when)}

@router.post("/api/absa")
async def absa_endpoint(review: str):
    from absa_service import analyze
    return analyze(review)


class ReplanRequest(BaseModel):
    session_id: str
    trigger: str = "traffic_or_weather"
    timestamp: Optional[str] = None

@router.post("/api/replan")
async def replan_itinerary(req: ReplanRequest):
    """In-tour re-planning (Mode 2): visited POIs are PINNED, the rest is rebuilt greedily,
    polished by crossing-aware 2-opt with time windows re-checked, and refilled from backup POIs."""
    import time as _t, signals as SG
    from replan_engine import replan_route, count_crossings, check_flow_conservation
    session=store.get(req.session_id)
    if not session or not session.current_itinerary:
        raise HTTPException(status_code=404, detail="Không tìm thấy itinerary hiện tại của session.")
    when=datetime.fromisoformat(req.timestamp) if req.timestamp else datetime.now()
    current=session.current_itinerary
    places=list(current.get("places", []))
    if len(places)<3:
        return {"status":"success","replanned":False,"reason":"itinerary quá ngắn","itinerary":current}

    prm=session.params or {}
    vehicle=prm.get("vehicle_type","xe_may")
    base_date=datetime.strptime(prm["trip_date"],"%Y-%m-%d").date() if prm.get("trip_date") else when.date()
    hhmm=lambda s_: datetime.combine(base_date, datetime.strptime(s_,"%H:%M").time())
    end_dt=hhmm(prm.get("end_time","22:00"))
    if end_dt<=hhmm(places[0].get("arrive_time","00:00")): end_dt+=timedelta(days=1)

    # hydrate with DB data (opening hours, visit time, score)
    db={p["id"]:p for p in fetch_all_points(vehicle)}
    def hydrate(p):
        q=dict(db.get(str(p["id"]),{})); q.update({k:v for k,v in p.items() if v not in (None,"")})
        q.setdefault("open_time",datetime.strptime("00:00","%H:%M").time())
        q.setdefault("close_time",datetime.strptime("23:59","%H:%M").time())
        q["time"]=float(q.get("visit_time") or q.get("time") or 0)
        q["experience_score"]=float(q.get("sabsa_score") or 0) if q.get("sabsa_score") is not None else float(q.get("score") or 0)/10
        return q
    route=[hydrate(p) for p in places]
    excluded=set(map(str,(prm.get("excluded_ids") or [])))
    used={str(p["id"]) for p in route}
    pool=[hydrate(p) for i,p in db.items() if i not in used and i not in excluded]

    # visited = departed before `when`; at least the origin
    n_fixed=1
    for i,p in enumerate(places):
        dep=p.get("depart_time")
        if dep and hhmm(dep)<=when: n_fixed=i+1
    now=max(when, hhmm(places[n_fixed-1].get("depart_time","00:00")))

    t_sig=_t.perf_counter()
    live=SG.LiveTraffic(); lw=SG.LiveWeather(); use_live=live.enabled
    scope=[p for p in route[n_fixed-1:]+pool if p["id"]!="gps_current"]
    if live.enabled: live.prefetch(scope)
    for p in scope: lw.penalty_at(now,p["lat"],p["lon"])      # warm cache
    wfn=SG.make_weather_fn(lw)
    traffic=SG.Scaled(live if use_live else SG.constant_traffic(1.0), vehicle_restriction(vehicle) if use_live else 1.0)
    matrix=osrm_matrix.get_global_osrm_matrix(route+pool, vehicle, static_traffic=not use_live)
    sig_ms=round((_t.perf_counter()-t_sig)*1000,1)

    penalty=lambda p,t: 10.0*wfn(p,t)           # weather at the ARRIVAL time of each POI
    ol_before=count_crossings(route)
    from categories import resolve_caps
    caps=resolve_caps(prm.get("category_caps") or "auto",(end_dt-hhmm(prm.get("start_time",places[0].get("arrive_time","08:00")))).total_seconds()/3600.0)
    out=replan_route(route,pool,matrix,now,end_dt,base_date,traffic.factor,penalty,n_fixed=n_fixed,caps=caps)
    new=out["route"]
    arr={str(p["id"]):a for p,a in zip(new[n_fixed:],out["arrivals"])}
    new_places=[]
    for i,p in enumerate(new):
        q=dict(places[i]) if i<n_fixed else {k:v for k,v in p.items() if k not in ("open_time","close_time")}
        if i>=n_fixed:
            a_=arr[str(p["id"])]
            q.update({"arrive_time":a_.strftime("%H:%M"),"depart_time":(a_+timedelta(minutes=float(p["time"]))).strftime("%H:%M"),"visit_time":float(p["time"])})
        new_places.append(q)

    current["places"]=new_places
    current["timeline"]=build_timeline(new_places)
    current["replan"]={
        "algorithm":"Greedy + 2-opt (pinned prefix, time-window checked)","trigger":req.trigger,
        "timestamp":when.isoformat(),"pinned_visited":n_fixed,
        "dropped":[p["ten"] for p in out["dropped"]],"added":[p["ten"] for p in out["added"]],
        "feasible":out["feasible"],"overlap_crossings_before":ol_before,"overlap_crossings_after":out["overlap_crossings"],
        "flow_conservation":check_flow_conservation(new_places,start_id=places[0]["id"]),
        "engine_latency_ms":out["latency_ms"],"signal_fetch_ms":sig_ms,
        "signals":{"traffic":traffic.source,"weather":lw.source},
    }
    session.set_current_itinerary(current, repin=True)
    return {"status":"success","replanned":True,"algorithm":"Greedy + 2-opt","itinerary":current,
            "research_metrics":{k:current["replan"][k] for k in ("overlap_crossings_before","overlap_crossings_after","flow_conservation","feasible","engine_latency_ms")}}


class ReplanCheckRequest(BaseModel):
    session_id: str
    timestamp: Optional[str] = None
    auto_replan: bool = True
    jam_threshold: float = 1.5
    rain_threshold: float = 0.5


@router.post("/api/replan/check")
async def replan_check(req: ReplanCheckRequest):
    """Poll while a tour is active: detect jam / rain ahead (monitor.check_disruption) and, if
    triggered and auto_replan, run the in-tour re-planner. The engine itself never runs in a loop."""
    import signals as SG
    from monitor import check_disruption
    session=store.get(req.session_id)
    if not session or not session.current_itinerary:
        raise HTTPException(status_code=404, detail="Không tìm thấy itinerary hiện tại của session.")
    when=datetime.fromisoformat(req.timestamp) if req.timestamp else datetime.now()
    places=list(session.current_itinerary.get("places",[]))
    prm=session.params or {}
    base_date=datetime.strptime(prm["trip_date"],"%Y-%m-%d").date() if prm.get("trip_date") else when.date()
    n_fixed=1
    for i,p in enumerate(places):
        dep=p.get("depart_time")
        if dep and datetime.combine(base_date,datetime.strptime(dep,"%H:%M").time())<=when: n_fixed=i+1
    remaining=places[n_fixed-1:]
    live=SG.LiveTraffic(); lw=SG.LiveWeather()
    if live.enabled: live.prefetch(remaining)
    res=check_disruption(remaining,when,live if live.enabled else SG.constant_traffic(1.0),SG.make_weather_fn(lw),base_date,
                         req.jam_threshold,req.rain_threshold)
    res["signals"]={"traffic":live.source if live.enabled else "none (TOMTOM_API_KEY missing)","weather":lw.source}
    if res["triggered"] and req.auto_replan:
        trig=",".join(sorted({r["type"] for r in res["reasons"]}))
        res["replan"]=await replan_itinerary(ReplanRequest(session_id=req.session_id,trigger=trig,timestamp=when.isoformat()))
    return res


@router.post("/api/narrative")
async def narrative_endpoint(payload: dict):
    """Generate natural-language itinerary narrative from an optimised POI list."""
    from services.narrative import generate as gen_narrative
    places = payload.get("places") or []
    return gen_narrative(
        places,
        user_preference=payload.get("user_preference", ""),
        start_time=payload.get("start_time", ""),
        end_time=payload.get("end_time", ""),
        use_ollama=os.getenv("ABSA_OLLAMA_ENABLED", "0").lower() in {"1", "true", "yes"},
    )


@router.post("/api/serpapi-enrich")
async def serpapi_enrich_endpoint(payload: dict):
    """Debug/enrich endpoint for SerpApi reviews & popular times."""
    from services.serpapi import SerpApiService
    points = payload.get("points") or []
    svc = SerpApiService()
    return {
        "enabled": svc.enabled,
        "points": svc.enrich_points(points, max_calls=int(payload.get("max_calls", 5))),
    }


@router.post("/api/parse-prompt")
async def parse_prompt_endpoint(payload: dict):
    """
    LLM-as-Parser (Semantic Engine Module 1).
    Free-text tourist prompt → structured JSON constraints for the route engine.
    """
    from services.parser import parse_prompt
    prompt = (payload or {}).get("prompt") or (payload or {}).get("text") or ""
    return parse_prompt(prompt)
