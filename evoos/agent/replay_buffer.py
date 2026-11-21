"""Replay of (parent, operator, child, reward) for the RL mutation hook."""

from __future__ import annotations

from dataclasses import dataclass, field

from evoos.vm.instruction_set import Instr, instrs_to_jsonable


@dataclass
class Transition:
    parent: tuple[Instr, ...]
    operator: str
    child: tuple[Instr, ...]
    reward: float
    descriptor: tuple[float, ...]
    generation: int


@dataclass
class ReplayBuffer:
    capacity: int = 4096
    items: list[Transition] = field(default_factory=list)

    def add(self, t: Transition) -> None:
        self.items.append(t)
        if len(self.items) > self.capacity:
            self.items = self.items[-self.capacity :]

    def sample(self, k: int, rng) -> list[Transition]:
        if not self.items:
            return []
        k = min(k, len(self.items))
        return rng.sample(self.items, k)

    def to_jsonable(self, limit: int = 64) -> list[dict]:
        out = []
        for t in self.items[-limit:]:
            out.append(
                {
                    "operator": t.operator,
                    "reward": t.reward,
                    "generation": t.generation,
                    "parent_len": len(t.parent),
                    "child_len": len(t.child),
                    "child": instrs_to_jsonable(t.child)[:8],
                }
            )
        return out
