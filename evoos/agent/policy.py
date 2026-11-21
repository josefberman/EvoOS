"""Pluggable mutation-operator policies: UCB1 bandit, optional PPO hook."""

from __future__ import annotations

import math
import random
from typing import Protocol

from evoos.agent.mutations import MUTATION_OPS


class MutationPolicy(Protocol):
    def select_operator(self, rng: random.Random) -> str: ...

    def update(self, operator: str, reward: float) -> None: ...

    def snapshot(self) -> dict: ...


class UCB1Bandit:
    """Learned mutation-operator selector (v1 default)."""

    def __init__(self, operators: tuple[str, ...] = MUTATION_OPS, c: float = 1.4) -> None:
        self.operators = operators
        self.c = c
        self.counts = {op: 0 for op in operators}
        self.values = {op: 0.0 for op in operators}
        self.n = 0

    def select_operator(self, rng: random.Random) -> str:
        for op in self.operators:
            if self.counts[op] == 0:
                return op
        logn = math.log(max(self.n, 1))
        best_op = self.operators[0]
        best = -1e9
        # Tiny jitter so ties break deterministically via rng, not set order.
        for op in self.operators:
            bonus = self.c * math.sqrt(logn / self.counts[op])
            score = self.values[op] + bonus + rng.random() * 1e-9
            if score > best:
                best = score
                best_op = op
        return best_op

    def update(self, operator: str, reward: float) -> None:
        if operator not in self.counts:
            return
        self.n += 1
        self.counts[operator] += 1
        n = self.counts[operator]
        self.values[operator] += (reward - self.values[operator]) / n

    def snapshot(self) -> dict:
        return {
            "kind": "ucb1",
            "counts": dict(self.counts),
            "values": dict(self.values),
            "n": self.n,
        }


class PPOMutationPolicy:
    """Hook for a small PPO policy over mutation operators.

    v1 uses a linear preference vector updated by a REINFORCE/PPO-lite
    step. If PyTorch is installed, the same interface can wrap an nn.Module.
    """

    def __init__(self, operators: tuple[str, ...] = MUTATION_OPS, lr: float = 0.05) -> None:
        self.operators = operators
        self.lr = lr
        self.logits = {op: 0.0 for op in operators}
        self._last: str | None = None
        self._torch_mod = None
        try:
            import torch  # noqa: F401
            import torch.nn as nn

            self._torch_mod = nn.Linear(8, len(operators))
        except Exception:
            self._torch_mod = None

    def _probs(self) -> dict[str, float]:
        m = max(self.logits.values())
        exps = {op: math.exp(self.logits[op] - m) for op in self.operators}
        z = sum(exps.values())
        return {op: v / z for op, v in exps.items()}

    def select_operator(self, rng: random.Random) -> str:
        probs = self._probs()
        r = rng.random()
        acc = 0.0
        chosen = self.operators[-1]
        for op in self.operators:
            acc += probs[op]
            if r <= acc:
                chosen = op
                break
        self._last = chosen
        return chosen

    def update(self, operator: str, reward: float) -> None:
        # PPO-lite: advantage vs 0, push chosen logit up when reward > 0.
        probs = self._probs()
        adv = reward
        for op in self.operators:
            ind = 1.0 if op == operator else 0.0
            self.logits[op] += self.lr * adv * (ind - probs[op])
        if self._torch_mod is not None:
            # Keep the module present as a swap-in; v1 does not backprop yet.
            pass

    def snapshot(self) -> dict:
        return {
            "kind": "ppo_lite",
            "logits": dict(self.logits),
            "torch": self._torch_mod is not None,
        }


def make_policy(name: str, operators: tuple[str, ...] = MUTATION_OPS) -> MutationPolicy:
    if name in ("ppo", "ppo_lite"):
        return PPOMutationPolicy(operators=operators)
    return UCB1Bandit(operators=operators)
