"""Application entry point — assembly only. Logic lives in api/ (HTTP layer) and core/ (algorithms).

Run:  uvicorn main:app --reload --port 8000
"""
import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:                                   # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

app = FastAPI(title="Routing Optimization API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

from api.routers.core import router as core_router          # noqa: E402
from api.routers.research import router as research_router  # noqa: E402
from ai_advisor import router as ai_router                  # noqa: E402

app.include_router(core_router)
app.include_router(ai_router)
app.include_router(research_router)


@app.get("/api/health")
def health():
    from core import audit
    return {"status": "ok", "data_quality": audit.data_quality()}


# ---- backward-compatible names (old code did `from main import ...`) -------------------------
from api.db import get_db_connection, fetch_all_dict, fetch_all_points            # noqa: E402,F401
from api.schemas import OptimizationRequest                                        # noqa: E402,F401
from api.preferences import _strip_diacritics, _extract_keywords, calculate_preference_score  # noqa: E402,F401
from api.factors import get_vehicle_osrm_profile, get_density_factor, get_large_vehicle_restriction_factor  # noqa: E402,F401
from api.scheduling import calc_dist, calculate_cost_with_clock, two_opt_algorithm  # noqa: E402,F401
from api.generation import run_route_generation                                    # noqa: E402,F401
