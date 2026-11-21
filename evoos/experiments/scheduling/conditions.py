"""Experimental conditions for later comparison.

A — conventional: human-written supervisors in the bytecode (baselines).
B — RL tunes a human-designed structure (skeleton + learned knobs).
C — emergent: search from random bytecode with no OS-shaped skeleton.
"""

from __future__ import annotations

from enum import Enum

from evoos.agent.program import Candidate
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion


class Condition(str, Enum):
    A_CONVENTIONAL = "A"
    B_RL_TUNES_STRUCTURE = "B"
    C_EMERGENT = "C"


def condition_a_candidates() -> dict[str, Candidate]:
    return {
        "run_to_completion": run_to_completion(),
        "round_robin": round_robin(16),
        "round_robin_q4": round_robin(4),
        "round_robin_q64": round_robin(64),
    }


def condition_b_skeleton(quantum: int) -> Candidate:
    """Human-designed round-robin skeleton with a tunable quantum.

    Milestone 1 stores the skeleton and the knob. A later RL loop (Condition B)
    should treat `quantum` (and similar immediates) as the action, not invent
    a new ISA.
    """
    return round_robin(quantum)


def condition_c_seeds() -> list[Candidate]:
    """Condition C starts from random programs; no conventional seeds."""
    return []
