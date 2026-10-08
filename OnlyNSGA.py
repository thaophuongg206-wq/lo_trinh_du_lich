"""NSGA-II baseline — standalone app, NO duplicated algorithm.

    uvicorn OnlyNSGA:app --reload --port 8000

It calls the SAME pipeline and the SAME engine as the hybrid endpoint (hybrid_nsga.run) with the
hybrid components switched off, so baseline and method optimise exactly the same 3 objectives with
the same time-window / category / traffic handling — the only difference is the ablated part.

    POST /api/optimize-route-nsga2           random init, no 2-opt   (standard NSGA-II)
    POST /api/optimize-route-nsga2?seeded=1  greedy+2-opt seeds, no 2-opt on children
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers.research import optimize_route_hybrid
from api.schemas import OptimizationRequest
from main import app as _main_app  # noqa: F401  (reuses main's wiring; routers are NOT re-mounted)

app = _main_app


@app.post("/api/optimize-route-nsga2")
async def optimize_route_nsga2(request: OptimizationRequest, seeded: bool = False):
    req = request.model_copy(update={"nsga_seeded": bool(seeded), "nsga_local_search": False})
    out = await optimize_route_hybrid(req)
    if isinstance(out, dict):
        out["algorithm"] = "NSGA-II (seeded)" if seeded else "NSGA-II (standard)"
    return out
