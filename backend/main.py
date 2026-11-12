"""Application entry point — assembly only. Logic lives in api/ (HTTP layer) and core/ (algorithms).

Run:  uvicorn backend.main:app --reload --port 8000
Or:   cd backend && uvicorn main:app --reload --port 8000
"""
import logging
import os
import sys

# Ensure backend directory is in sys.path
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

try:                                   # optional: load .env if python-dotenv is installed
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BACKEND_DIR, "..", ".env"))
except ImportError:
    pass

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s | %(message)s")

app = FastAPI(title="Routing Optimization API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

from api.routers.core import router as core_router          # noqa: E402
from api.routers.research import router as research_router  # noqa: E402
from api.routers.itinerary import router as itinerary_router  # noqa: E402
from ai_advisor import router as ai_router                  # noqa: E402

app.include_router(core_router)
app.include_router(itinerary_router)
app.include_router(ai_router)
app.include_router(research_router)


@app.get("/api/health")
def health():
    from core import audit
    return {"status": "ok", "data_quality": audit.data_quality()}


from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

FRONTEND_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "frontend"))
DATA_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "data"))

if os.path.exists(os.path.join(FRONTEND_DIR, "images")):
    app.mount("/images", StaticFiles(directory=os.path.join(FRONTEND_DIR, "images")), name="images")
if os.path.exists(os.path.join(FRONTEND_DIR, "css")):
    app.mount("/css", StaticFiles(directory=os.path.join(FRONTEND_DIR, "css")), name="css")
if os.path.exists(os.path.join(FRONTEND_DIR, "js")):
    app.mount("/js", StaticFiles(directory=os.path.join(FRONTEND_DIR, "js")), name="js")
if os.path.exists(DATA_DIR):
    app.mount("/data", StaticFiles(directory=DATA_DIR), name="data")


@app.get("/")
def serve_index():
    index_path = os.path.join(FRONTEND_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()
    if "<!-- INCLUDE_MODALS -->" in html:
        modals_dir = os.path.join(FRONTEND_DIR, "components", "modals")
        modal_files = [
            "place_detail.html", "add_place.html", "replace_place.html",
            "change_time.html", "profile.html", "favorites.html", "image_preview.html"
        ]
        modals_html = []
        for mf in modal_files:
            p = os.path.join(modals_dir, mf)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as mf_f:
                    modals_html.append(mf_f.read())
        html = html.replace("<!-- INCLUDE_MODALS -->", "\n".join(modals_html))
        from fastapi.responses import HTMLResponse
        return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return FileResponse(index_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/app.js")
def serve_js():
    return FileResponse(os.path.join(FRONTEND_DIR, "app.js"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/style.css")
def serve_css():
    return FileResponse(os.path.join(FRONTEND_DIR, "style.css"), headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# ---- backward-compatible names (old code did `from main import ...`) -------------------------
from api.db import get_db_connection, fetch_all_dict, fetch_all_points            # noqa: E402,F401
from api.schemas import OptimizationRequest                                        # noqa: E402,F401
from api.preferences import _strip_diacritics, _extract_keywords, calculate_preference_score  # noqa: E402,F401
from api.factors import get_vehicle_osrm_profile, get_density_factor, get_large_vehicle_restriction_factor  # noqa: E402,F401
from api.scheduling import calc_dist, calculate_cost_with_clock, two_opt_algorithm  # noqa: E402,F401
from api.generation import run_route_generation                                    # noqa: E402,F401
