"""Lexicase and epsilon-lexicase parent selection.

Helmuth, Spector, Matheson (GECCO 2015) / La Cava et al. epsilon-lexicase.
Used here over per-guest milestone cases, not a scalar fitness. Generic GP
selection — not an OS prior.
"""

from __future__ import annotations

import random
import statistics


def _mad(xs: list[float]) -> float:
    if not xs:
        return 0.0
    med = statistics.median(xs)
    return statistics.median([abs(x - med) for x in xs])


def lexicase_index(
    scores: list[list[float]],
    rng: random.Random,
    *,
    epsilon: float | None = None,
    auto_epsilon: bool = False,
    eligible: list[int] | None = None,
) -> int:
    """Return an index into `scores` selected by (epsilon-)lexicase.

    `scores[i][k]` is individual i on case k. Higher is better.
    `epsilon is None` and `auto_epsilon False` is classic lexicase.
    `auto_epsilon True` uses the median absolute deviation on the current
    pool for each case (La Cava). Binary cases with MAD=0 reduce to lexicase.
    """
    n = len(scores)
    if n == 0:
        raise ValueError("empty lexicase pool")
    pool = list(eligible) if eligible is not None else list(range(n))
    if not pool:
        pool = list(range(n))
    n_cases = len(scores[pool[0]]) if pool else 0
    if n_cases == 0:
        return rng.choice(pool)
    order = list(range(n_cases))
    rng.shuffle(order)
    for k in order:
        if len(pool) <= 1:
            break
        vals = [scores[i][k] for i in pool]
        best = max(vals)
        eps = 0.0 if epsilon is None else float(epsilon)
        if auto_epsilon:
            eps = max(eps, _mad(vals))
        pool = [i for i, v in zip(pool, vals) if v + 1e-12 >= best - eps]
    return rng.choice(pool)


def lexicase_select(
    ids: list[int],
    bank: dict[int, list[float]],
    rng: random.Random,
    *,
    n: int = 1,
    epsilon: float | None = None,
    auto_epsilon: bool = False,
    eligible_ids: list[int] | None = None,
) -> list[int]:
    """Select `n` candidate ids. Individuals missing from `bank` are skipped."""
    use = [i for i in (eligible_ids if eligible_ids is not None else ids) if i in bank]
    if not use:
        use = [i for i in ids if i in bank]
    if not use:
        return [rng.choice(ids) for _ in range(max(1, n))]
    scores = [bank[i] for i in use]
    out: list[int] = []
    for _ in range(max(1, n)):
        idx = lexicase_index(scores, rng, epsilon=epsilon, auto_epsilon=auto_epsilon)
        out.append(use[idx])
    return out
