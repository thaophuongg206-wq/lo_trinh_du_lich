"""Evaluation helpers for the research experiments.

Metrics aligned with the paper:
- Hypervolume (2-D / 3-D approximation)
- Overlap Crossings (OL)
- Constraint Violations (%)
- Latency
"""
from __future__ import annotations
import math
from typing import Iterable, Sequence, List, Dict, Any

from replan_engine import count_crossings, check_flow_conservation


def dominates(a: Sequence[float], b: Sequence[float]) -> bool:
    return all(x <= y for x, y in zip(a, b)) and any(x < y for x, y in zip(a, b))


def pareto_front(points: Iterable[Sequence[float]]) -> list:
    pts = [tuple(p) for p in points]
    return [p for i, p in enumerate(pts) if not any(j != i and dominates(q, p) for j, q in enumerate(pts))]


def hypervolume_2d(front: Iterable[Sequence[float]], reference=(1.0, 1.0)) -> float:
    """2-D minimization hypervolume; useful for controlled ablations."""
    pts = sorted((float(x), float(y)) for x, y, *_ in front)
    if not pts:
        return 0.0
    hv = 0.0
    prev_y = reference[1]
    for x, y in pts:
        if x >= reference[0] or y >= reference[1]:
            continue
        hv += (reference[0] - x) * max(0, prev_y - y)
        prev_y = min(prev_y, y)
    return max(0.0, hv)


def hypervolume_3d(
    front: Iterable[Sequence[float]],
    reference=(1.0, 1.0, 1.0),
) -> float:
    """EXACT 3-D hypervolume (minimisation) by slicing along the 3rd objective.

    Overlapping boxes are counted once: for each z-slab we take the exact 2-D
    hypervolume of all points whose z is below the slab. Points must already be
    expressed on a COMMON scale (see joint_bounds / normalise) and `reference`
    must be the same for every method being compared.
    """
    pts = [tuple(float(v) for v in p[:3]) for p in front]
    pts = sorted(p for p in pts if all(p[i] < reference[i] for i in range(3)))
    if not pts:
        return 0.0
    hv = 0.0
    for i, p in enumerate(pts):
        z_next = pts[i + 1][2] if i + 1 < len(pts) else reference[2]
        depth = z_next - p[2]
        if depth > 0:
            hv += hypervolume_2d([q[:2] for q in pts[: i + 1]], reference[:2]) * depth
    return max(0.0, hv)


def joint_bounds(fronts: Iterable[Iterable[Sequence[float]]]):
    """Per-objective (min, max) over the UNION of every method's front in one test case."""
    allp = [tuple(float(v) for v in p[:3]) for f in fronts for p in f]
    if not allp:
        return None
    return [(min(p[d] for p in allp), max(p[d] for p in allp)) for d in range(3)]


def normalise(front: Iterable[Sequence[float]], bounds) -> list:
    """Map objectives to [0,1] with the shared bounds (0 = best seen by any method)."""
    out = []
    for p in front:
        out.append(tuple(
            0.5 if bounds[d][1] <= bounds[d][0] else (float(p[d]) - bounds[d][0]) / (bounds[d][1] - bounds[d][0])
            for d in range(3)
        ))
    return out


def shared_hypervolumes(fronts_by_method: Dict[str, list], ref: float = 1.1) -> Dict[str, float]:
    """HV of every method on the same scale and the same reference point (ref,ref,ref)."""
    bounds = joint_bounds(fronts_by_method.values())
    if bounds is None:
        return {m: 0.0 for m in fronts_by_method}
    return {
        m: round(hypervolume_3d(normalise(f, bounds), (ref, ref, ref)), 5)
        for m, f in fronts_by_method.items()
    }


def evaluate_route(
    places: List[dict],
    objectives: Sequence[float] | None = None,
) -> Dict[str, Any]:
    """Compute OL, flow conservation, and optional objective snapshot."""
    ol = count_crossings(places)
    flow_ok = check_flow_conservation(places)
    return {
        "overlap_crossings": ol,
        "flow_conservation": flow_ok,
        "n_pois": max(0, len(places) - 1),
        "objectives": list(objectives) if objectives is not None else None,
    }


def summarise_batch(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics over a list of evaluate_route outputs."""
    if not results:
        return {"n": 0}
    ols = [r["overlap_crossings"] for r in results]
    flows = [1.0 if r["flow_conservation"] else 0.0 for r in results]
    return {
        "n": len(results),
        "avg_ol": sum(ols) / len(ols),
        "zero_ol_rate": sum(1 for x in ols if x == 0) / len(ols),
        "flow_ok_rate": sum(flows) / len(flows),
    }
