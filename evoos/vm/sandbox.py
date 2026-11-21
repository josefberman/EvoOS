"""Evaluation sandbox: clean snapshots, hard limits, repeatable runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from evoos.vm.instruction_set import (
    FW_GUEST_STRIDE,
    FW_GUEST_TABLE,
    FW_MAGIC,
    FW_MAX_STEPS,
    FW_MEM_WORDS,
    FW_N_GUESTS,
    MAGIC,
    Instr,
)
from evoos.vm.machine import Machine, MachineConfig, Snapshot
from evoos.vm.verifier import VerifyError, verify_guest, verify_supervisor


@dataclass
class GuestImage:
    name: str
    code: tuple[Instr, ...]
    data_base: int
    data_bound: int
    init: dict[int, int]  # absolute address -> value
    expected: dict[int, int]  # absolute address -> value
    needed_cycles: int = 64


@dataclass
class Workload:
    name: str
    guests: tuple[GuestImage, ...]
    split: str = "train"  # "train" | "heldout"


@dataclass
class GuestOutcome:
    name: str
    halted: bool
    fault: int
    cycles: int
    halt_cycle: int | None
    correct: bool
    progressed: bool
    mismatches: list[tuple[int, int, int]]  # addr, expected, actual


@dataclass
class EvalResult:
    workload: str
    split: str
    completed: int
    n_guests: int
    progressed: int
    isolation_hits: list[tuple[int, int]]
    corruption: bool
    supervisor_fault: int
    supervisor_halted: bool
    cycles: int
    switch_count: int
    guest_cycles: list[int]
    halt_cycles: list[int | None]
    outcomes: list[GuestOutcome]
    runctx_log: list[tuple[int, int, int]]
    mem_fingerprint: int
    invalid: bool = False
    invalid_reason: str = ""
    prot_cover: list[bool] = field(default_factory=list)
    entered: int = 0

    @property
    def isolation_ok(self) -> bool:
        return not self.corruption and not self.isolation_hits


def load_firmware(machine: Machine, workload: Workload) -> None:
    cfg = machine.config
    m = machine.mem
    m[FW_MAGIC] = MAGIC
    m[FW_N_GUESTS] = len(workload.guests)
    m[FW_MEM_WORDS] = cfg.mem_words
    m[FW_MAX_STEPS] = cfg.max_steps
    for i, g in enumerate(workload.guests):
        base = FW_GUEST_TABLE + i * FW_GUEST_STRIDE
        ctx_id = i + 1
        m[base + 0] = ctx_id
        m[base + 1] = g.data_base
        m[base + 2] = g.data_bound
        m[base + 3] = 0
        ctx = machine.contexts[ctx_id]
        ctx.code = list(g.code)
        ctx.pc = 0
        ctx.regs = [0] * cfg.n_regs
        ctx.halted = False
        ctx.fault = 0
        ctx.cycles = 0
        ctx.halt_cycle = None
        ctx.prot = []
        ctx.data_base = g.data_base
        ctx.data_bound = g.data_bound
        # Hardware reset: r1 = data_base, r2 = data_bound (not an OS ABI).
        ctx.regs[1] = g.data_base
        ctx.regs[2] = g.data_bound
        machine.legal_store[ctx_id] = (g.data_base, g.data_bound)
        for addr, val in g.init.items():
            m[addr] = val
            machine.last_writer[addr] = 0
    for extra in range(len(workload.guests) + 1, cfg.n_contexts):
        machine.legal_store[extra] = None


def _fingerprint(mem: list[int]) -> int:
    h = 2166136261
    for w in mem:
        h ^= w & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _guest_outcome(machine: Machine, guest: GuestImage, cid: int) -> GuestOutcome:
    ctx = machine.contexts[cid]
    mismatches: list[tuple[int, int, int]] = []
    for addr, exp in guest.expected.items():
        actual = machine.mem[addr]
        if actual != exp:
            mismatches.append((addr, exp, actual))
    progressed = ctx.cycles > 0
    correct = ctx.halted and ctx.fault == 0 and not mismatches
    return GuestOutcome(
        name=guest.name,
        halted=ctx.halted,
        fault=ctx.fault,
        cycles=ctx.cycles,
        halt_cycle=ctx.halt_cycle,
        correct=correct,
        progressed=progressed,
        mismatches=mismatches,
    )


class Sandbox:
    """Reuse a per-workload snapshot so evaluations start from a clean machine."""

    def __init__(self, config: MachineConfig | None = None) -> None:
        self.config = config or MachineConfig()
        self._snaps: dict[str, tuple[Machine, Snapshot, Workload]] = {}

    def _prepare(self, workload: Workload) -> tuple[Machine, Snapshot]:
        cached = self._snaps.get(workload.name)
        if cached is not None:
            return cached[0], cached[1]
        for g in workload.guests:
            verify_guest(g.code, self.config)
        m = Machine(self.config)
        load_firmware(m, workload)
        snap = m.snapshot()
        self._snaps[workload.name] = (m, snap, workload)
        return m, snap

    def evaluate(self, supervisor: tuple[Instr, ...] | list[Instr], workload: Workload) -> EvalResult:
        try:
            verify_supervisor(supervisor, self.config)
        except VerifyError as exc:
            return EvalResult(
                workload=workload.name,
                split=workload.split,
                completed=0,
                n_guests=len(workload.guests),
                progressed=0,
                isolation_hits=[],
                corruption=False,
                supervisor_fault=0,
                supervisor_halted=False,
                cycles=0,
                switch_count=0,
                guest_cycles=[0] * len(workload.guests),
                halt_cycles=[None] * len(workload.guests),
                outcomes=[],
                runctx_log=[],
                mem_fingerprint=0,
                invalid=True,
                invalid_reason=str(exc),
                prot_cover=[False] * len(workload.guests),
                entered=0,
            )
        machine, snap = self._prepare(workload)
        machine.restore(snap)
        machine.install_supervisor(supervisor)
        machine.run(self.config.max_steps)
        outcomes = [
            _guest_outcome(machine, g, i + 1) for i, g in enumerate(workload.guests)
        ]
        isolation_hits = list(machine.isolation_hits)
        # Corruption: a guest wrote another guest's region, or an isolation hit fired.
        corruption = bool(isolation_hits)
        if not corruption:
            for i, g in enumerate(workload.guests):
                cid = i + 1
                for addr in range(g.data_base, g.data_bound):
                    w = machine.last_writer[addr]
                    if w not in (255, 0, cid):
                        corruption = True
                        isolation_hits.append((w, addr))
        completed = sum(1 for o in outcomes if o.correct)
        progressed = sum(1 for o in outcomes if o.progressed)
        prot_cover = []
        for i, g in enumerate(workload.guests):
            ctx = machine.contexts[i + 1]
            covered = any(lo <= g.data_base and hi >= g.data_bound for lo, hi, _ in ctx.prot)
            prot_cover.append(bool(covered))
        entered = sum(1 for o in outcomes if o.cycles > 0)
        return EvalResult(
            workload=workload.name,
            split=workload.split,
            completed=completed,
            n_guests=len(workload.guests),
            progressed=progressed,
            isolation_hits=isolation_hits,
            corruption=corruption,
            supervisor_fault=machine.supervisor_fault,
            supervisor_halted=machine.contexts[0].halted,
            cycles=machine.cycles,
            switch_count=machine.switch_count,
            guest_cycles=[o.cycles for o in outcomes],
            halt_cycles=[o.halt_cycle for o in outcomes],
            outcomes=outcomes,
            runctx_log=list(machine.runctx_log),
            mem_fingerprint=_fingerprint(machine.mem),
            prot_cover=prot_cover,
            entered=entered,
        )

    def clear_cache(self) -> None:
        self._snaps.clear()
