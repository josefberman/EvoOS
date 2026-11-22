"""Behavioral correctness, isolation, fairness, and latency summaries."""

from __future__ import annotations

from dataclasses import dataclass

from evoos.vm.sandbox import EvalResult


@dataclass
class CorrectnessSummary:
    functional: float
    stability: float
    efficiency: float
    fairness: float
    latency: float
    isolation_ok: bool
    all_complete: bool
    starvation: float
    rich_efficiency: float = 0.0
    switch_score: float = 0.0


def _std(xs: list[float]) -> float:
    if not xs:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return var ** 0.5


def summarize_correctness(result: EvalResult, needed_cycles: list[int] | None = None) -> CorrectnessSummary:
    n = max(result.n_guests, 1)
    complete_frac = result.completed / n
    progress_frac = result.progressed / n
    functional = 0.8 * complete_frac + 0.2 * progress_frac

    isolation_ok = result.isolation_ok
    # Vacuous isolation (no guest ever ran) is not stability: isolation was not tested.
    if not isolation_ok:
        stability = 0.0
    elif result.progressed == 0:
        stability = 0.0
    else:
        stability = 1.0
        if result.supervisor_fault:
            stability *= 0.5

    needed = needed_cycles or [1] * n
    progress = []
    for cyc, need in zip(result.guest_cycles, needed):
        progress.append(min(1.0, cyc / max(need, 1)))
    fairness = max(0.0, 1.0 - _std(progress)) if progress else 0.0
    starved = sum(1 for c in result.guest_cycles if c == 0) / n
    starvation = 1.0 - starved

    halt_times = [h for h in result.halt_cycles if h is not None]
    if result.completed == n and halt_times:
        span = max(halt_times) - min(halt_times)
        latency = max(0.0, 1.0 - span / max(result.cycles, 1))
    else:
        latency = 0.0

    # Efficiency: guest work that completed vs total cycles spent.
    useful = 0
    for o in result.outcomes:
        if o.correct:
            useful += o.cycles
    if result.cycles <= 0 or not isolation_ok:
        useful_ratio = 0.0
    else:
        useful_ratio = min(1.0, useful / result.cycles)
    # Switch overhead: extra RUNCTX beyond one dispatch per guest.
    extra = max(0, result.switch_count - n)
    switch_score = 1.0 - min(1.0, extra / max(result.switch_count, n, 1))
    # Rich efficiency (Condition B): throughput + fairness + latency + switch cost.
    rich_efficiency = (
        0.40 * useful_ratio + 0.25 * fairness + 0.20 * latency + 0.15 * switch_score
    )
    efficiency = useful_ratio

    return CorrectnessSummary(
        functional=functional,
        stability=stability,
        efficiency=efficiency,
        fairness=fairness,
        latency=latency,
        isolation_ok=isolation_ok,
        all_complete=result.completed == n,
        starvation=starvation,
        rich_efficiency=rich_efficiency,
        switch_score=switch_score,
    )
