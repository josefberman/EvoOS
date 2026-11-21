"""Condition B: tune knobs on a *human-designed* round-robin skeleton.

The organization (protect each guest window, then rotate RUNCTXI) is fixed.
The search only chooses the RUNCTXI immediate (quantum). That is not Condition C.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from evoos.agent.policy import UCB1Bandit
from evoos.agent.program import Candidate
from evoos.evaluator.reward import RewardWeights, score
from evoos.experiments.scheduling.baselines import round_robin
from evoos.vm.sandbox import Sandbox, Workload

# Discrete quanta. Human structure stays round-robin; only this immediate changes.
QUANTA = (
    1,
    2,
    4,
    8,
    12,
    16,
    24,
    32,
    48,
    64,
    96,
    128,
    192,
    256,
    384,
    512,
    1024,
    2048,
    4000,
)


def skeleton(quantum: int) -> Candidate:
    return round_robin(int(quantum))


def _eval(sandbox: Sandbox, quantum: int, train: list[Workload], heldout: list[Workload]) -> dict:
    cand = skeleton(quantum)
    train_res = [sandbox.evaluate(cand.instrs, w) for w in train]
    held_res = [sandbox.evaluate(cand.instrs, w) for w in heldout]
    train_bd = score(train_res, novelty=0.0, weights=RewardWeights.correctness_only())
    held_bd = score(held_res, novelty=0.0, weights=RewardWeights.correctness_only())
    four = all(r.completed == r.n_guests and r.isolation_ok for r in train_res + held_res)
    return {
        "quantum": quantum,
        "train_reward": train_bd.total,
        "held_reward": held_bd.total,
        "train_completed": [r.completed for r in train_res],
        "held_completed": [r.completed for r in held_res],
        "train_isolation": all(r.isolation_ok for r in train_res),
        "held_isolation": all(r.isolation_ok for r in held_res),
        "four_of_four": four,
        "train_switches": [r.switch_count for r in train_res],
        "held_switches": [r.switch_count for r in held_res],
    }


@dataclass
class ConditionBResult:
    best_quantum: int
    best: dict
    grid: list[dict]
    bandit_history: list[dict]
    pulls: int


def grid_search(
    sandbox: Sandbox,
    train: list[Workload],
    heldout: list[Workload],
    quanta: tuple[int, ...] = QUANTA,
) -> list[dict]:
    return [_eval(sandbox, q, train, heldout) for q in quanta]


def bandit_search(
    sandbox: Sandbox,
    train: list[Workload],
    heldout: list[Workload],
    *,
    seed: int,
    pulls: int = 40,
    quanta: tuple[int, ...] = QUANTA,
) -> ConditionBResult:
    """UCB1 over quantum arms. Eval is deterministic, so this is mostly grid+exploit."""
    rng = random.Random(seed)
    arms = tuple(str(q) for q in quanta)
    bandit = UCB1Bandit(operators=arms, c=1.2)
    cache: dict[int, dict] = {}
    history: list[dict] = []
    for i in range(pulls):
        arm = bandit.select_operator(rng)
        q = int(arm)
        if q not in cache:
            cache[q] = _eval(sandbox, q, train, heldout)
        row = cache[q]
        bandit.update(arm, row["train_reward"])
        history.append({"pull": i, "quantum": q, "train_reward": row["train_reward"]})
    grid = [cache[q] if q in cache else _eval(sandbox, q, train, heldout) for q in quanta]
    best = max(grid, key=lambda r: (r["train_reward"], r["held_reward"], -r["quantum"]))
    return ConditionBResult(
        best_quantum=int(best["quantum"]),
        best=best,
        grid=grid,
        bandit_history=history,
        pulls=pulls,
    )
