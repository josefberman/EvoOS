"""Behavioral shaping terms. None of these matches an instruction sequence.

Each term is an *observable* of the machine after a run. A supervisor can
satisfy them with any bytecode that produces that machine state. They are
not `schedule_process`, not `ADDPROT` opcode bonuses, not run-queue hints.

| Term | Observation | Why it is not an architectural prior |
| --- | --- | --- |
| entered | Fraction of guests that retired ≥1 instruction | Any context-switch primitive posts this; we do not care *how* RUNCTX was reached |
| prot_cover | Guest's assigned data window is inside some installed MMU range | Hardware protection *state*, not “emit ADDPROT in this order” |
| progress | Guest executed >0 cycles (same as entered here, kept distinct for heartbeats later) | Work happened |
| complete | Guest halted with expected outputs | The original behavioral objective |

Weights sum to 1 and replace the sparse F = 0.8·complete + 0.2·progress
when shaping is on. Isolation remains a hard constraint.

Round 4 gated mode (isolation-gated / lexicographic):
entered, progress, and complete for a guest count **only if** that guest
retired at least one instruction while its data window was already covered.
`prot_cover` is therefore on the path, not a competing term. Ungated RUNCTX
(1–2 instructions then PROT fault) scores 0 on those terms — that was the
R=0.52 local optimum in round 3.

This gate is still behavioral: it inspects MPU state at the moment a guest
instruction retires, not the opcode sequence that installed the range.
Evaluator telemetry (`ran_protected`) is **not** an ISA CSR; a host stub
does not need to expose it.
"""

from __future__ import annotations

from dataclasses import dataclass

from evoos.vm.sandbox import EvalResult

SHAPE_WEIGHTS = {
    "entered": 0.15,
    "prot_cover": 0.20,
    "progress": 0.25,
    "complete": 0.40,
}

SHAPING_DOCS = __doc__


@dataclass
class ShapeTerms:
    entered: float
    prot_cover: float
    progress: float
    complete: float
    gated: bool = False

    def blended(self, weights: dict[str, float] | None = None) -> float:
        w = weights or SHAPE_WEIGHTS
        return (
            w["entered"] * self.entered
            + w["prot_cover"] * self.prot_cover
            + w["progress"] * self.progress
            + w["complete"] * self.complete
        )

    def as_dict(self) -> dict:
        return {
            "entered": self.entered,
            "prot_cover": self.prot_cover,
            "progress": self.progress,
            "complete": self.complete,
            "blended": self.blended(),
            "gated": self.gated,
        }


def shape_terms(result: EvalResult, gated: bool = False) -> ShapeTerms:
    n = max(result.n_guests, 1)
    cover = 0.0
    if result.prot_cover:
        cover = sum(1 for c in result.prot_cover if c) / n
    if gated:
        entered = result.entered_protected / n
        progress = result.progressed_protected / n
        complete = result.completed_protected / n
    else:
        entered = result.entered / n if result.entered else (result.progressed / n)
        progress = result.progressed / n
        complete = result.completed / n
    return ShapeTerms(
        entered=entered,
        prot_cover=cover,
        progress=progress,
        complete=complete,
        gated=gated,
    )


def mean_shape(results: list[EvalResult], gated: bool = False) -> ShapeTerms:
    if not results:
        return ShapeTerms(0, 0, 0, 0, gated=gated)
    parts = [shape_terms(r, gated=gated) for r in results]
    k = len(parts)
    return ShapeTerms(
        entered=sum(p.entered for p in parts) / k,
        prot_cover=sum(p.prot_cover for p in parts) / k,
        progress=sum(p.progress for p in parts) / k,
        complete=sum(p.complete for p in parts) / k,
        gated=gated,
    )
