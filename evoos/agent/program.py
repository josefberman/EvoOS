"""The evolving artifact: a supervisor program in EvoOS bytecode."""

from __future__ import annotations

from dataclasses import dataclass

from evoos.vm.instruction_set import Instr, instrs_from_jsonable, instrs_to_jsonable


@dataclass(frozen=True)
class Candidate:
    instrs: tuple[Instr, ...]
    origin: str = "random"
    parent_id: int | None = None
    id: int = 0

    def __len__(self) -> int:
        return len(self.instrs)

    def to_jsonable(self) -> dict:
        return {
            "id": self.id,
            "origin": self.origin,
            "parent_id": self.parent_id,
            "instrs": instrs_to_jsonable(self.instrs),
        }

    @classmethod
    def from_jsonable(cls, data: dict) -> "Candidate":
        return cls(
            instrs=instrs_from_jsonable(data["instrs"]),
            origin=data.get("origin", ""),
            parent_id=data.get("parent_id"),
            id=int(data.get("id", 0)),
        )
