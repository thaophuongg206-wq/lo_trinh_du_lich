# Smart Travel Itinerary Decision Support System

Hybrid AI framework for the Tourist Trip Design Problem (TTDP):
Local LLM (Ollama) for ABSA / parsing / narrative + Dual-Mode mathematical optimizer.

## Architecture (4 layers)

1. **Presentation** – `index.html` / `app.js`
2. **Semantic & LLM** – Ollama (Parser, ABSA, Narrative)
3. **Optimization** – Hybrid NSGA-II (pre-tour) + Greedy 2-opt (in-tour)
4. **Data & API** – SerpApi, OSRM, TomTom, Open-Meteo, Crowd predictor

## Algorithms

| File | Role |
|------|------|
| `main.py` | Production FastAPI app + research endpoints |
| `hybrid_nsga.py` | Hybrid NSGA-II (Greedy seeds + 2-opt + NSGA-II, 3 objectives) |
| `OnlyNSGA.py` | Standard NSGA-II baseline |
| `replan_engine.py` | In-tour Greedy + 2-opt, crossing count, constraint checks |
| `absa_service.py` | Review → SABSA (Ollama + lexical fallback) |
| `services/parser.py` | **LLM-as-Parser**: free-text prompt → JSON constraints |
| `services/serpapi.py` | Reviews, Popular Times, opening hours |
| `services/osrm.py` | Distance / duration matrix (OSRM + Haversine fallback) |
| `services/narrative.py` | LLM-as-Storyteller itinerary narrative |
| `services/ollama.py` | Shared Ollama client |
| `tomtom_service.py` | Real-time traffic factor |
| `weather_service.py` | Timestamp-aware Open-Meteo hourly weather |
| `crowd_prediction.py` | Gradient Boosting crowd forecast (or temporal baseline) |
| `evaluation.py` | Hypervolume, OL, flow conservation, batch summary |
| `run_experiments.py` | **Research experiment runner** → Table 1 & Table 2 |

## Main research endpoints

- `POST /api/optimize-route-hybrid` – 3-objective Pareto (experience, time, crowd+weather)
- `POST /api/replan` – In-tour Greedy 2-opt (< 0.1 s target), reports OL before/after
- `POST /api/parse-prompt` – LLM-as-Parser (prompt → JSON constraints)
- `POST /api/narrative` – Natural-language itinerary guide
- `POST /api/absa` – Single-review ABSA
- `POST /api/serpapi-enrich` – Debug SerpApi enrichment
- `GET  /api/dynamic-signals` – TomTom + Open-Meteo snapshot
- `GET  /api/crowd-forecast` – Crowd index at place/time

## Objectives (Hybrid NSGA-II)

1. **Max** experience = `w_pref * SABSA + (1 - w_pref) * Rating`
2. **Min** total trip time (travel + visit + waiting)
3. **Min** crowd + weather penalty

## Environment

Copy `.env.example` and fill keys as needed:

```text
TOMTOM_API_KEY=...
SERPAPI_KEY=...          # optional; synthetic fallback is labelled
ABSA_OLLAMA_ENABLED=1
HYBRID_POP_SIZE=40
HYBRID_GENERATIONS=60
```

## Crowd data

`crowd_history.csv` ships with synthetic observations so GradientBoosting can train.
Replace with real `(place_id, timestamp, crowd_index)` rows for production research.

## Run the API

```bash
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

For the pure NSGA-II baseline:

```bash
uvicorn OnlyNSGA:app --reload --port 8000
```

## Run research experiments (Table 1 & Table 2)

```bash
# Full 30 test cases (pop=40, gen=50) – several minutes
python run_experiments.py

# Quick smoke test
python run_experiments.py --quick --cases 6
```

Outputs:

- `experiments/results.json` – raw per-case metrics
- `experiments/TABLE1.md` – Main Results comparison
- `experiments/TABLE2.md` – Ablation Study

Smoke tests for research modules:

```bash
python test_research_modules.py
```

## Cấu trúc sau refactor
`main.py` chỉ lắp ráp app. `api/` = tầng HTTP (db, schemas, factors, scheduling, generation, routers/core|research). `core/` = thuật toán dùng chung: `timeline` (một bộ mô phỏng đi→chờ→thăm), `objectives` (3 mục tiêu + `ObjectiveScaler`), `nsga2` (engine), `audit` (ghi nhận mọi lần dùng fallback, trả về trong `data_quality`). `config.py` = mọi tham số (ghi đè bằng env). `OnlyNSGA.py` = baseline NSGA-II gọi cùng `hybrid_nsga.run` với seeds/2-opt tắt.
