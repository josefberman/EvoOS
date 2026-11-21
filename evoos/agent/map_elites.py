"""MAP-Elites quality-diversity archive over supervisor programs."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from evoos.agent.program import Candidate
from evoos.evaluator.reward import RewardBreakdown


@dataclass
class Elite:
    candidate: Candidate
    reward: float
    cell: tuple
    breakdown: RewardBreakdown | None = None
    generation: int = 0


class MapElites:
    def __init__(self, x_bins: int = 8, y_bins: int = 5) -> None:
        self.x_bins = x_bins
        self.y_bins = y_bins
        self.cells: dict[tuple, Elite] = {}
        self.visits: dict[tuple, int] = {}
        self._t = 0

    def add(
        self,
        candidate: Candidate,
        cell: tuple,
        reward: float,
        generation: int,
        breakdown: RewardBreakdown | None = None,
    ) -> bool:
        if len(cell) == 2:
            x = min(max(int(cell[0]), 0), self.x_bins - 1)
            y = min(max(int(cell[1]), 0), self.y_bins - 1)
            key: tuple = (x, y)
        else:
            key = tuple(int(x) for x in cell)
        old = self.cells.get(key)
        if old is None or reward > old.reward:
            self.cells[key] = Elite(candidate, reward, key, breakdown, generation)
            self.visits.setdefault(key, 0)
            return True
        return False

    def sample(self, rng: random.Random) -> Candidate | None:
        if not self.cells:
            return None
        elite = rng.choice(list(self.cells.values()))
        self._touch(elite.cell)
        return elite.candidate

    def sample_curious(self, rng: random.Random, c: float = 1.4) -> Candidate | None:
        """Favor under-visited cells (UCB1 over the archive)."""
        if not self.cells:
            return None
        self._t += 1
        logt = math.log(self._t + 1.0)
        best_e: Elite | None = None
        best_s = -1e9
        for key, elite in self.cells.items():
            n = self.visits.get(key, 0)
            bonus = c * math.sqrt(logt / (n + 1.0))
            # Inverse-visit curiosity plus quality, so a cover-only cell is not starved.
            s = elite.reward + bonus + 1.0 / (n + 1.0)
            if s > best_s:
                best_s = s
                best_e = elite
        if best_e is None:
            return None
        self._touch(best_e.cell)
        return best_e.candidate

    def sample_other(self, rng: random.Random, than: Candidate | None) -> Candidate | None:
        pool = [e.candidate for e in self.cells.values() if e.candidate is not than]
        if not pool:
            return self.sample(rng)
        return rng.choice(pool)

    def _touch(self, key: tuple) -> None:
        self.visits[key] = self.visits.get(key, 0) + 1

    def elites(self) -> list[Candidate]:
        return [e.candidate for e in self.cells.values()]

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
                    "visits": self.visits.get(key, 0),
                }
            )
        return rows
