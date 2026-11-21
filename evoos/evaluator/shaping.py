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
        }


def shape_terms(result: EvalResult) -> ShapeTerms:
    n = max(result.n_guests, 1)
    entered = result.entered / n if result.entered else (result.progressed / n)
    cover = 0.0
    if result.prot_cover:
        cover = sum(1 for c in result.prot_cover if c) / n
    progress = result.progressed / n
    complete = result.completed / n
    return ShapeTerms(entered=entered, prot_cover=cover, progress=progress, complete=complete)


def mean_shape(results: list[EvalResult]) -> ShapeTerms:
    if not results:
        return ShapeTerms(0, 0, 0, 0)
    parts = [shape_terms(r) for r in results]
    k = len(parts)
    return ShapeTerms(
        entered=sum(p.entered for p in parts) / k,
        prot_cover=sum(p.prot_cover for p in parts) / k,
        progress=sum(p.progress for p in parts) / k,
        complete=sum(p.complete for p in parts) / k,
    )
