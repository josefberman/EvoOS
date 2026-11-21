"""MAP-Elites quality-diversity archive over supervisor programs."""

from __future__ import annotations

import random
from dataclasses import dataclass

from evoos.agent.program import Candidate
from evoos.evaluator.reward import RewardBreakdown


@dataclass
class Elite:
    candidate: Candidate
    reward: float
    cell: tuple[int, int]
    breakdown: RewardBreakdown | None = None
    generation: int = 0


class MapElites:
    def __init__(self, x_bins: int = 8, y_bins: int = 5) -> None:
        self.x_bins = x_bins
        self.y_bins = y_bins
        self.cells: dict[tuple[int, int], Elite] = {}

    def add(
        self,
        candidate: Candidate,
        cell: tuple[int, int],
        reward: float,
        generation: int,
        breakdown: RewardBreakdown | None = None,
    ) -> bool:
        x = min(max(cell[0], 0), self.x_bins - 1)
        y = min(max(cell[1], 0), self.y_bins - 1)
        key = (x, y)
        old = self.cells.get(key)
        if old is None or reward > old.reward:
            self.cells[key] = Elite(candidate, reward, key, breakdown, generation)
            return True
        return False

    def sample(self, rng: random.Random) -> Candidate | None:
        if not self.cells:
            return None
        elite = rng.choice(list(self.cells.values()))
        return elite.candidate

    def best(self) -> Elite | None:
        if not self.cells:
            return None
        return max(self.cells.values(), key=lambda e: e.reward)

    def size(self) -> int:
        return len(self.cells)

    def dump(self) -> list[dict]:
        rows = []
        for key, e in sorted(self.cells.items()):
            rows.append(
                {
                    "cell": key,
                    "reward": e.reward,
                    "generation": e.generation,
                    "origin": e.candidate.origin,
                    "length": len(e.candidate),
                    "id": e.candidate.id,
                }
            )
        return rows
