"""Static checks on typed bytecode. Rejects ill-formed programs before evaluation."""

from __future__ import annotations

from typing import Sequence

from evoos.vm.instruction_set import IMM_MAX, IMM_MIN, N_REGS, Instr, Op, PRIVILEGED
from evoos.vm.machine import MachineConfig


class VerifyError(ValueError):
    def __init__(self, message: str, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index


def _check_instr(ins: Instr, index: int, allow_privileged: bool) -> None:
    if ins.op not in Op._value2member_map_:
        raise VerifyError(f"unknown opcode {ins.op} at {index}", index)
    op = Op(ins.op)
    for name, val in (("a", ins.a), ("b", ins.b), ("c", ins.c)):
        if not (0 <= val < N_REGS):
            raise VerifyError(f"register {name}={val} out of range at {index}", index)
    if not (IMM_MIN <= ins.imm <= IMM_MAX):
        raise VerifyError(f"immediate {ins.imm} out of range at {index}", index)
    if op in PRIVILEGED and not allow_privileged:
        raise VerifyError(f"privileged opcode {op.name} at {index}", index)


def verify_program(
    instrs: Sequence[Instr],
    *,
    allow_privileged: bool,
    max_len: int,
    role: str,
) -> None:
    if len(instrs) == 0:
        raise VerifyError(f"{role} program is empty")
    if len(instrs) > max_len:
        raise VerifyError(f"{role} program length {len(instrs)} exceeds {max_len}")
    for i, ins in enumerate(instrs):
        if not isinstance(ins, Instr):
            try:
                ins = Instr(*ins)  # type: ignore[misc]
            except Exception as exc:
                raise VerifyError(f"malformed instruction at {i}", i) from exc
        _check_instr(ins, i, allow_privileged)


def verify_supervisor(instrs: Sequence[Instr], config: MachineConfig | None = None) -> None:
    cfg = config or MachineConfig()
    verify_program(
        instrs,
        allow_privileged=True,
        max_len=cfg.max_supervisor_len,
        role="supervisor",
    )


def verify_guest(instrs: Sequence[Instr], config: MachineConfig | None = None) -> None:
    cfg = config or MachineConfig()
    verify_program(
        instrs,
        allow_privileged=False,
        max_len=cfg.max_guest_len,
        role="guest",
    )
