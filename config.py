"""Central configuration — every tunable that used to be a magic number lives here.

Each value can be overridden by an environment variable (see `.env.example`), so an experiment
can be reproduced from its environment alone. Frozen dataclasses: a run cannot silently mutate
its own settings half-way through.

    from config import CFG
    CFG.nsga.pop_size, CFG.penalty.category_cap, CFG.db.server ...
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Tuple


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return float(default)


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, default)))
    except ValueError:
        return int(default)


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _tuple(name: str, default: Tuple[float, ...]) -> Tuple[float, ...]:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return tuple(float(x) for x in raw.split(","))
    except ValueError:
        return default


# --------------------------------------------------------------------------- NSGA-II
@dataclass(frozen=True)
class NSGAConfig:
    pop_size: int = field(default_factory=lambda: _i("HYBRID_POP_SIZE", 40))
    generations: int = field(default_factory=lambda: _i("HYBRID_GENERATIONS", 60))
    seed: int = field(default_factory=lambda: _i("NSGA_SEED", 42))
    n_seeds: int = field(default_factory=lambda: _i("NSGA_N_SEEDS", 10))          # constructive seeds
    p_local: float = field(default_factory=lambda: _f("NSGA_P_LOCAL", 0.25))      # 2-opt on children
    mutation_rate: float = field(default_factory=lambda: _f("NSGA_MUTATION_RATE", 0.30))
    init_min_pois: int = 2                                                        # random individuals
    init_max_pois: int = field(default_factory=lambda: _i("NSGA_INIT_MAX_POIS", 8))
    two_opt_max_rounds: int = field(default_factory=lambda: _i("NSGA_2OPT_ROUNDS", 30))


# ---------------------------------------------------------------- constraint penalties
@dataclass(frozen=True)
class PenaltyConfig:
    # hybrid NSGA-II: "minutes of violation" charged per POI over a category quota
    category_cap: float = field(default_factory=lambda: _f("CAP_PENALTY", 1000.0))
    # main.py greedy/2-opt/DP cost function (legacy, same units: minutes)
    same_category_adjacent: float = field(default_factory=lambda: _f("PEN_SAME_CAT_ADJ", 1000.0))
    same_category_skip_one: float = field(default_factory=lambda: _f("PEN_SAME_CAT_SKIP", 500.0))
    time_window_violation: float = field(default_factory=lambda: _f("PEN_TIME_WINDOW", 10000.0))


# -------------------------------------------------------------- objective normalisation
@dataclass(frozen=True)
class ObjectiveConfig:
    # normalise the 3 objectives to [0,1] with instance-adaptive reference bounds
    normalise: bool = field(default_factory=lambda: _b("OBJ_NORMALISE", True))
    # a POI's crowd index and weather penalty are both in [0,1] -> per-POI worst case
    crowd_weather_per_poi_max: float = 2.0
    # shared reference point for hypervolume (in normalised space, > 1 so boundary points count)
    hv_reference: float = field(default_factory=lambda: _f("HV_REF", 1.1))
    # default weights used when ranking routes after the search (experience, time, crowd)
    rank_weights: Tuple[float, float, float] = field(
        default_factory=lambda: _tuple("RANK_WEIGHTS", (0.4, 0.4, 0.2)))


# ------------------------------------------------------------------- route generation
@dataclass(frozen=True)
class RoutingConfig:
    dp_max_candidates: int = field(default_factory=lambda: _i("DP_MAX_CANDIDATES", 11))
    min_routes: int = field(default_factory=lambda: _i("MIN_ROUTES", 3))
    max_routes: int = field(default_factory=lambda: _i("MAX_ROUTES", 5))
    jaccard_limits: Tuple[float, ...] = field(
        default_factory=lambda: _tuple("JACCARD_LIMITS", (0.45, 0.60, 0.75, 1.01)))
    hybrid_jaccard_limits: Tuple[float, ...] = field(
        default_factory=lambda: _tuple("HYBRID_JACCARD_LIMITS", (0.5, 0.65, 0.8, 0.95, 1.01)))
    min_route_fraction: float = field(default_factory=lambda: _f("MIN_ROUTE_FRACTION", 0.6))
    prefilter_max: int = field(default_factory=lambda: _i("PREFILTER_MAX", 25))
    replan_pool_limit: int = field(default_factory=lambda: _i("REPLAN_POOL_LIMIT", 8))
    fallback_leg_speed_kmh: float = field(default_factory=lambda: _f("FALLBACK_SPEED_KMH", 20.0))


# --------------------------------------------------------------------------- database
@dataclass(frozen=True)
class DBConfig:
    # NO machine name in code: set DB_SERVER in .env. Empty -> straight to the bundled SQLite.
    server: str = field(default_factory=lambda: os.getenv("DB_SERVER", "").strip())
    name: str = field(default_factory=lambda: os.getenv("DB_NAME", "DuLichThongMinh"))
    odbc_driver: str = field(default_factory=lambda: os.getenv("DB_ODBC_DRIVER", "ODBC Driver 17 for SQL Server"))
    timeout_s: int = field(default_factory=lambda: _i("DB_TIMEOUT_S", 2))
    sqlite_path: str = field(default_factory=lambda: os.getenv(
        "SQLITE_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dulich.db")))


@dataclass(frozen=True)
class Settings:
    nsga: NSGAConfig = field(default_factory=NSGAConfig)
    penalty: PenaltyConfig = field(default_factory=PenaltyConfig)
    obj: ObjectiveConfig = field(default_factory=ObjectiveConfig)
    routing: RoutingConfig = field(default_factory=RoutingConfig)
    db: DBConfig = field(default_factory=DBConfig)
    crowd_csv: str = field(default_factory=lambda: os.getenv(
        "CROWD_CSV", os.path.join(os.path.dirname(os.path.abspath(__file__)), "crowd_history.csv")))


def load() -> Settings:
    """Re-read the environment (tests / experiment scripts call this after changing env vars)."""
    return Settings()


CFG = load()
