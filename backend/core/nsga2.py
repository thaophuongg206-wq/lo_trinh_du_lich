"""ONE NSGA-II engine: Individual, Deb's constrained dominance, fast non-dominated sort,
crowding distance, binary tournament and the permutation operators.

Used by hybrid_nsga (seeds + 2-opt switched on) AND by the NSGA-II baseline (both switched off),
so the baseline can no longer drift away from the method it is compared against.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Sequence, Tuple

EPS = 1e-9


@dataclass
class Individual:
    route: List[int]
    obj: Tuple[float, ...] = (0.0, 0.0, 0.0)   # all minimised
    cv: float = 0.0                            # constraint violation (minutes over); 0 = feasible
    rank: int = 0
    crowd: float = 0.0


def dominates(a: Individual, b: Individual) -> bool:
    """Deb's constrained domination: feasible beats infeasible, smaller violation beats larger,
    otherwise ordinary Pareto domination."""
    if a.cv != b.cv and (a.cv > EPS or b.cv > EPS):
        return a.cv < b.cv
    return all(x <= y for x, y in zip(a.obj, b.obj)) and any(x < y for x, y in zip(a.obj, b.obj))


def fast_sort(pop: Sequence[Individual]) -> List[List[Individual]]:
    fronts, ds, n = [[]], {}, {}
    for p in pop:
        ds[id(p)], n[id(p)] = [], 0
        for q in pop:
            if p is q:
                continue
            if dominates(p, q):
                ds[id(p)].append(q)
            elif dominates(q, p):
                n[id(p)] += 1
        if n[id(p)] == 0:
            p.rank = 0
            fronts[0].append(p)
    k = 0
    while fronts[k]:
        nxt = []
        for p in fronts[k]:
            for q in ds[id(p)]:
                n[id(q)] -= 1
                if n[id(q)] == 0:
                    q.rank = k + 1
                    nxt.append(q)
        k += 1
        fronts.append(nxt)
    return fronts[:-1]


def crowding(front: List[Individual]) -> None:
    """Crowding distance with PER-FRONT range normalisation (scale-free by construction)."""
    if not front:
        return
    for p in front:
        p.crowd = 0.0
    n_obj = len(front[0].obj)
    for m in range(n_obj):
        front.sort(key=lambda x: x.obj[m])
        front[0].crowd = front[-1].crowd = float("inf")
        lo, hi = front[0].obj[m], front[-1].obj[m]
        if hi == lo:
            continue
        for i in range(1, len(front) - 1):
            front[i].crowd += (front[i + 1].obj[m] - front[i - 1].obj[m]) / (hi - lo)


def tournament(pop: Sequence[Individual], rng: random.Random) -> Individual:
    a, b = rng.sample(list(pop), 2)
    if a.rank != b.rank:
        return a if a.rank < b.rank else b
    return a if a.crowd > b.crowd else b


def order_crossover(a: List[int], b: List[int], rng: random.Random) -> List[int]:
    """OX on the visiting order; gene 0 (the origin) is fixed."""
    ga, gb = a[1:], b[1:]
    if len(ga) < 2:
        return a[:]
    i, j = sorted(rng.sample(range(len(ga)), 2))
    mid = ga[i:j + 1]
    rest = [x for x in gb + ga if x not in mid]
    seen, tail = set(), []
    for x in rest:
        if x not in seen:
            seen.add(x)
            tail.append(x)
    child = tail[:i] + mid + tail[i:]
    return [a[0]] + child[: max(len(ga), len(gb))]


def mutate(route: List[int], rng: random.Random, n_all: int, rate: float) -> List[int]:
    """swap | drop | insert, probabilities rate / 0.7*rate / 0.7*rate (one of them per call)."""
    r = route[:]
    u = rng.random()
    if len(r) > 3 and u < rate:
        i, j = sorted(rng.sample(range(1, len(r)), 2))
        r[i], r[j] = r[j], r[i]
    elif u < rate * 1.7 and len(r) > 2:
        r.pop(rng.randrange(1, len(r)))
    elif u < rate * 2.4:
        free = [x for x in range(n_all) if x not in r]
        if free:
            r.insert(rng.randrange(1, len(r) + 1), rng.choice(free))
    return r


def environmental_selection(pop: List[Individual], children: List[Individual], size: int) -> List[Individual]:
    """(mu + lambda) survivor selection by rank, then crowding."""
    new: List[Individual] = []
    for f in fast_sort(pop + children):
        crowding(f)
        if len(new) + len(f) <= size:
            new.extend(f)
        else:
            f.sort(key=lambda x: x.crowd, reverse=True)
            new.extend(f[: size - len(new)])
            break
    return new
