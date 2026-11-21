"""Composite reward with hard isolation constraints.

R = 0.45 F + 0.20 S + 0.15 E + 0.10 G + 0.10 N

Catastrophic isolation / memory corruption cannot be compensated by
throughput (E), generalization (G), or novelty (N). Invalid bytecode is
scored separately as -1 by the search loop, not here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evoos.evaluator.correctness import summarize_correctness
from evoos.evaluator.shaping import mean_shape
from evoos.vm.sandbox import EvalResult


@dataclass
class RewardWeights:
    f: float = 0.45
    s: float = 0.20
    e: float = 0.15
    g: float = 0.10
    n: float = 0.10

    @classmethod
    def correctness_only(cls) -> "RewardWeights":
        return cls(f=0.80, s=0.20, e=0.0, g=0.0, n=0.0)

    @classmethod
    def quality_diversity(cls) -> "RewardWeights":
        return cls()

    @classmethod
    def condition_b(cls) -> "RewardWeights":
        """Correctness plus efficiency so quantum is identifiable."""
        return cls(f=0.45, s=0.20, e=0.35, g=0.00, n=0.00)

    @classmethod
    def shaped_correctness(cls) -> "RewardWeights":
        return cls(f=0.80, s=0.20, e=0.0, g=0.0, n=0.0)

    def normalized(self) -> "RewardWeights":
        tot = self.f + self.s + self.e + self.g + self.n
        if tot <= 0:
            return RewardWeights(1.0, 0, 0, 0, 0)
        return RewardWeights(self.f / tot, self.s / tot, self.e / tot, self.g / tot, self.n / tot)


@dataclass
class RewardBreakdown:
    total: float
    f: float
    s: float
    e: float
    g: float
    n: float
    hard_fail: bool
    isolation_ok: bool
    completed: int
    n_guests: int
    details: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "F": self.f,
            "S": self.s,
            "E": self.e,
            "G": self.g,
            "N": self.n,
            "hard_fail": self.hard_fail,
            "isolation_ok": self.isolation_ok,
            "completed": self.completed,
            "n_guests": self.n_guests,
        }


def score(
    train_results: list[EvalResult],
    *,
    heldout_results: list[EvalResult] | None = None,
    novelty: float = 0.0,
    weights: RewardWeights | None = None,
    needed_cycles: list[int] | None = None,
    shaping: bool = False,
    rich_efficiency: bool = False,
    gated: bool = False,
) -> RewardBreakdown:
    w = (weights or RewardWeights()).normalized()
    if not train_results:
        return RewardBreakdown(0.0, 0, 0, 0, 0, 0, False, True, 0, 0)

    hard_fail = any((not r.isolation_ok) or r.corruption for r in train_results)
    if heldout_results:
        # Held-out isolation failures are also catastrophic: a policy that
        # only isolates the training mix has not discovered isolation.
        hard_fail = hard_fail or any((not r.isolation_ok) or r.corruption for r in heldout_results)

    f_parts = []
    s_parts = []
    e_parts = []
    completed = 0
    n_guests = 0
    for r in train_results:
        c = summarize_correctness(r, needed_cycles)
        f_parts.append(c.functional)
        s_parts.append(c.stability)
        e_parts.append(c.rich_efficiency if rich_efficiency else c.efficiency)
        completed += r.completed
        n_guests += r.n_guests
    f = sum(f_parts) / len(f_parts)
    s = sum(s_parts) / len(s_parts)
    e = sum(e_parts) / len(e_parts)
    shape = mean_shape(train_results, gated=gated)
    if shaping:
        f = shape.blended()
        if gated:
            # Isolation-gated stability: unprotected 1–2-cycle RUNCTX is not "tested isolation".
            protected_progress = any(r.progressed_protected > 0 for r in train_results)
            if not protected_progress:
                s = 0.0
            else:
                s = 1.0 if all(r.isolation_ok for r in train_results) else 0.0
                if any(r.supervisor_fault for r in train_results):
                    s *= 0.5

    g = 0.0
    if heldout_results:
        g_parts = []
        for r in heldout_results:
            c = summarize_correctness(r, needed_cycles)
            g_parts.append(c.functional if r.isolation_ok else 0.0)
        g = sum(g_parts) / len(g_parts)

    n = max(0.0, min(1.0, novelty))

    if hard_fail:
        # Hard constraint: throughput / novelty cannot buy a passing score.
        return RewardBreakdown(
            total=0.0,
            f=f,
            s=0.0,
            e=0.0,
            g=0.0,
            n=0.0,
            hard_fail=True,
            isolation_ok=False,
            completed=completed,
            n_guests=n_guests,
            details={"reason": "isolation_or_corruption", "shape": shape.as_dict()},
        )

    total = w.f * f + w.s * s + w.e * e + w.g * g + w.n * n
    return RewardBreakdown(
        total=total,
        f=f,
        s=s,
        e=e,
        g=g,
        n=n,
        hard_fail=False,
        isolation_ok=True,
        completed=completed,
        n_guests=n_guests,
        details={"shape": shape.as_dict()},
    )
