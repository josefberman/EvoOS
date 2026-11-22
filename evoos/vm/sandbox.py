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
    home_block: int | None = None
    expected_off: dict[int, int] = field(default_factory=dict)
    role: str = "compute"
    token: int = 0
    pair: int | None = None
    messages: tuple[int, ...] = ()


@dataclass
class Workload:
    name: str
    guests: tuple[GuestImage, ...]
    split: str = "train"  # "train" | "heldout"
    kind: str = "compute"  # compute | pressure | route


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
    # Isolation-gated observables (window covered *before* the credited steps).
    entered_protected: int = 0
    progressed_protected: int = 0
    completed_protected: int = 0
    ran_protected: list[bool] = field(default_factory=list)
    protected_cycles: list[int] = field(default_factory=list)
    # Evaluator telemetry: runtime values the supervisor observed (not an ISA CSR).
    observed_consts: list[int] = field(default_factory=list)
    n_blk_read: int = 0
    n_blk_write: int = 0
    n_svc: int = 0
    delivered: int = 0
    n_resident: int = 0
    n_messages: int = 0
    msg_ok: list[bool] = field(default_factory=list)
    route_style: str = "none"
    shared_window: bool = False
    delivery_writers: list[int] = field(default_factory=list)

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
        ctx.protected_cycles = 0
        ctx.ran_protected = False
        ctx.svc = 0
        # Hardware reset: r1 = data_base, r2 = data_bound (not an OS ABI).
        ctx.regs[1] = g.data_base
        ctx.regs[2] = g.data_bound
        machine.legal_store[ctx_id] = (g.data_base, g.data_bound) if g.data_bound > g.data_base else None
        if g.home_block is not None and machine.disk is not None:
            words = [0] * machine.config.block_words
            for off, val in g.init.items():
                if 0 <= off < len(words):
                    words[off] = val
            machine.disk.write(int(g.home_block), words)
        else:
            for addr, val in g.init.items():
                m[addr] = val
                machine.last_writer[addr] = 0
    for extra in range(len(workload.guests) + 1, cfg.n_contexts):
        machine.legal_store[extra] = None
    if machine.disk is not None:
        machine.disk.n_read = 0
        machine.disk.n_write = 0


def _in_window(ctx, addr: int) -> bool:
    return ctx.data_bound > ctx.data_base and ctx.data_base <= addr < ctx.data_bound


def _prot_covers(ctx, addr: int) -> bool:
    return any(lo <= addr < hi for lo, hi, _ in ctx.prot)


def _granted_share(machine: Machine, addr: int, owner_cid: int, writer: int) -> bool:
    """True when ADDPROT (or matching windows) granted the writer that address.

    Shared windows are an organization, not a leak: both contexts must cover `addr`.
    """
    if writer <= 0 or writer >= len(machine.contexts) or owner_cid <= 0:
        return False
    owner = machine.contexts[owner_cid]
    wr = machine.contexts[writer]
    owner_ok = _in_window(owner, addr) or _prot_covers(owner, addr)
    wr_ok = _in_window(wr, addr) or _prot_covers(wr, addr)
    return owner_ok and wr_ok


def _fingerprint(mem: list[int]) -> int:
    h = 2166136261
    for w in mem:
        h ^= w & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def _message_slots(guest: GuestImage) -> list[tuple[int, int]]:
    if guest.expected_off:
        return sorted((int(off), int(exp)) for off, exp in guest.expected_off.items())
    if guest.messages:
        return [(i, int(t)) for i, t in enumerate(guest.messages)]
    if guest.role == "consumer" and guest.token:
        return [(0, int(guest.token))]
    return []


def _overlapping_pair(machine: Machine, a: int, b: int) -> bool:
    ca, cb = machine.contexts[a], machine.contexts[b]
    ranges_a = list(ca.prot)
    ranges_b = list(cb.prot)
    if ca.data_bound > ca.data_base:
        ranges_a.append((ca.data_base, ca.data_bound, 0))
    if cb.data_bound > cb.data_base:
        ranges_b.append((cb.data_base, cb.data_bound, 0))
    for lo, hi, _ in ranges_a:
        for lo2, hi2, _ in ranges_b:
            if lo < hi2 and lo2 < hi:
                return True
    return False


def _route_telemetry(machine: Machine, workload: Workload, outcomes: list[GuestOutcome]) -> dict:
    msg_ok: list[bool] = []
    writers: list[int] = []
    shared = False
    nmem = len(machine.mem)
    consumers = [(i, g) for i, g in enumerate(workload.guests) if g.role == "consumer"]
    for i, g in consumers:
        cid = i + 1
        ctx = machine.contexts[cid]
        if g.pair:
            shared = shared or _overlapping_pair(machine, int(g.pair), cid)
        slots = _message_slots(g)
        for off, exp in slots:
            addr = None
            actual = None
            writer = 255
            if ctx.data_base >= 256 and ctx.data_bound > ctx.data_base:
                addr = ctx.data_base + off
                if 0 <= addr < nmem:
                    actual = machine.mem[addr]
                    writer = int(machine.last_writer[addr])
            elif g.home_block is not None and machine.disk is not None:
                blk = int(g.home_block)
                if 0 <= blk < len(machine.disk.blocks):
                    words = machine.disk.blocks[blk]
                    actual = words[off] if 0 <= off < len(words) else 0
                    writer = -1
            ok = actual == exp
            msg_ok.append(bool(ok))
            writers.append(writer)
    n_ok = sum(1 for x in msg_ok if x)
    style = "none"
    if n_ok > 0:
        if shared:
            style = "shared-window"
        elif any(w == -1 for w in writers):
            style = "disk-buffer"
        elif (
            n_ok >= 2
            and machine.n_svc >= 2 * n_ok
            and machine.switch_count <= n_ok + 2
        ):
            style = "batch"
        else:
            style = "copy-supervisor"
    return {
        "n_messages": len(msg_ok),
        "msg_ok": msg_ok,
        "route_style": style,
        "shared_window": bool(shared),
        "delivery_writers": writers,
    }


def _guest_outcome(machine: Machine, guest: GuestImage, cid: int) -> GuestOutcome:
    ctx = machine.contexts[cid]
    mismatches: list[tuple[int, int, int]] = []
    expected = dict(guest.expected)
    if guest.expected_off:
        if ctx.data_base >= 256 and ctx.data_bound > ctx.data_base:
            for off, exp in guest.expected_off.items():
                expected[ctx.data_base + off] = exp
        elif guest.home_block is not None and machine.disk is not None:
            blk = int(guest.home_block)
            if 0 <= blk < len(machine.disk.blocks):
                words = machine.disk.blocks[blk]
                for off, exp in guest.expected_off.items():
                    actual = words[off] if 0 <= off < len(words) else 0
                    if actual != exp:
                        mismatches.append((-1000 - off, exp, actual))
    for addr, exp in expected.items():
        if addr < 0 or addr >= len(machine.mem):
            mismatches.append((addr, exp, 0))
            continue
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
                entered_protected=0,
                progressed_protected=0,
                completed_protected=0,
                ran_protected=[False] * len(workload.guests),
                protected_cycles=[0] * len(workload.guests),
                observed_consts=[],
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
        nmem = len(machine.mem)
        if not corruption:
            for i, g in enumerate(workload.guests):
                cid = i + 1
                ctx = machine.contexts[cid]
                lo, hi = ctx.data_base, ctx.data_bound
                if hi <= lo:
                    lo, hi = g.data_base, g.data_bound
                lo = max(0, int(lo))
                hi = min(nmem, int(hi))
                if hi <= lo:
                    continue
                for addr in range(lo, hi):
                    w = machine.last_writer[addr]
                    if w not in (255, 0, cid) and not _granted_share(machine, addr, cid, int(w)):
                        corruption = True
                        isolation_hits.append((w, addr))
        completed = sum(1 for o in outcomes if o.correct)
        progressed = sum(1 for o in outcomes if o.progressed)
        prot_cover = []
        n_resident = 0
        for i, g in enumerate(workload.guests):
            ctx = machine.contexts[i + 1]
            lo, hi = ctx.data_base, ctx.data_bound
            if hi <= lo:
                lo, hi = g.data_base, g.data_bound
            covered = hi > lo and any(plo <= lo and phi >= hi for plo, phi, _ in ctx.prot)
            prot_cover.append(bool(covered))
            if ctx.data_bound > ctx.data_base:
                n_resident += 1
        entered = sum(1 for o in outcomes if o.cycles > 0)
        ran_protected = [bool(machine.contexts[i + 1].ran_protected) for i in range(len(workload.guests))]
        protected_cycles = [int(machine.contexts[i + 1].protected_cycles) for i in range(len(workload.guests))]
        entered_protected = sum(1 for flag in ran_protected if flag)
        progressed_protected = sum(
            1 for o, flag in zip(outcomes, ran_protected) if o.progressed and flag
        )
        completed_protected = sum(
            1 for o, flag in zip(outcomes, ran_protected) if o.correct and flag
        )
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
            entered_protected=entered_protected,
            progressed_protected=progressed_protected,
            completed_protected=completed_protected,
            ran_protected=ran_protected,
            protected_cycles=protected_cycles,
            observed_consts=list(dict.fromkeys(machine.observed_consts))[:64],
            n_blk_read=int(machine.disk.n_read) if machine.disk is not None else 0,
            n_blk_write=int(machine.disk.n_write) if machine.disk is not None else 0,
            n_svc=int(getattr(machine, "n_svc", 0)),
            delivered=sum(
                1
                for g, o in zip(workload.guests, outcomes)
                if g.role == "consumer" and o.correct
            ),
            n_resident=n_resident,
            **_route_telemetry(machine, workload, outcomes),
        )

    def clear_cache(self) -> None:
        self._snaps.clear()
