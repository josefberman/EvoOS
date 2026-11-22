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
    share_leak: bool = False
    revoked: bool = False
    n_clrprot: int = 0
    attacker_contained: bool = True

    @property
    def isolation_ok(self) -> bool:
        return not self.corruption and not self.isolation_hits and not self.share_leak


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


def _pair_partner(workload: Workload, cid: int) -> int | None:
    if cid <= 0 or cid > len(workload.guests):
        return None
    g = workload.guests[cid - 1]
    if g.role not in ("producer", "consumer"):
        return None
    return int(g.pair) if g.pair else None


def _granted_share(
    machine: Machine,
    addr: int,
    owner_cid: int,
    writer: int,
    workload: Workload | None = None,
) -> bool:
    """True when the designated pair partner was granted `addr`.

    Dual ADDPROT of a producer/consumer pair is an organization, not a leak.
    A third guest covering the same range is a leak.
    """
    if writer <= 0 or writer >= len(machine.contexts) or owner_cid <= 0:
        return False
    if workload is not None:
        partner = _pair_partner(workload, owner_cid)
        if partner is None or writer != partner:
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


def _ctx_ranges(ctx) -> list[tuple[int, int]]:
    ranges = [(int(lo), int(hi)) for lo, hi, _ in ctx.prot]
    if ctx.data_bound > ctx.data_base:
        ranges.append((int(ctx.data_base), int(ctx.data_bound)))
    return ranges


def _overlaps(lo: int, hi: int, lo2: int, hi2: int) -> bool:
    return lo < hi2 and lo2 < hi


def _overlapping_pair(machine: Machine, a: int, b: int) -> bool:
    if a <= 0 or b <= 0 or a >= len(machine.contexts) or b >= len(machine.contexts):
        return False
    ca, cb = machine.contexts[a], machine.contexts[b]
    for lo, hi in _ctx_ranges(ca):
        for lo2, hi2 in _ctx_ranges(cb):
            if _overlaps(lo, hi, lo2, hi2):
                return True
    return False


def _pair_overlap_ranges(machine: Machine, a: int, b: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    if a <= 0 or b <= 0 or a >= len(machine.contexts) or b >= len(machine.contexts):
        return out
    for lo, hi in _ctx_ranges(machine.contexts[a]):
        for lo2, hi2 in _ctx_ranges(machine.contexts[b]):
            olo, ohi = max(lo, lo2), min(hi, hi2)
            if olo < ohi:
                out.append((olo, ohi))
    return out


def _share_audit(machine: Machine, workload: Workload, outcomes: list[GuestOutcome]) -> dict:
    """Third-guest grant of a pair window is a leak. Revocation is reported separately."""
    leaks: list[tuple[int, int]] = []
    n_share = 0
    n_open = 0
    delivered = 0
    for i, g in enumerate(workload.guests):
        if g.role != "consumer":
            continue
        cid = i + 1
        partner = int(g.pair) if g.pair else 0
        if partner <= 0:
            continue
        if outcomes and i < len(outcomes) and outcomes[i].correct:
            delivered += 1
        overlap = _pair_overlap_ranges(machine, partner, cid)
        if overlap:
            n_share += 1
            n_open += 1
            for olo, ohi in overlap:
                for tid in range(1, len(machine.contexts)):
                    if tid in (cid, partner) or tid > len(workload.guests):
                        continue
                    for lo, hi in _ctx_ranges(machine.contexts[tid]):
                        if _overlaps(lo, hi, olo, ohi):
                            leaks.append((tid, olo))
        # Attacker covering a mapped pair member's window even without overlap.
        for tid, ag in enumerate(workload.guests, start=1):
            if ag.role != "attacker":
                continue
            for lo, hi in _ctx_ranges(machine.contexts[tid]):
                for plo, phi in _ctx_ranges(machine.contexts[cid]) + _ctx_ranges(machine.contexts[partner]):
                    if _overlaps(lo, hi, plo, phi):
                        leaks.append((tid, lo))
    revoked = delivered > 0 and n_open == 0
    if delivered == 0:
        revoked = int(getattr(machine, "n_clrprot", 0) or 0) > 0 and n_open == 0
    attacker_ids = {i + 1 for i, g in enumerate(workload.guests) if g.role == "attacker"}
    contained = not any(tid in attacker_ids for tid, _addr in leaks)
    return {
        "share_leak": bool(leaks),
        "revoked": bool(revoked),
        "n_clrprot": int(getattr(machine, "n_clrprot", 0) or 0),
        "attacker_contained": bool(contained),
        "leak_hits": leaks,
    }


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
                    if w not in (255, 0, cid) and not _granted_share(
                        machine, addr, cid, int(w), workload
                    ):
                        corruption = True
                        isolation_hits.append((w, addr))
        audit = _share_audit(machine, workload, outcomes)
        if audit["leak_hits"]:
            corruption = True
            isolation_hits.extend(audit["leak_hits"])
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
            share_leak=bool(audit["share_leak"]),
            revoked=bool(audit["revoked"]),
            n_clrprot=int(audit["n_clrprot"]),
            attacker_contained=bool(audit["attacker_contained"]),
            **_route_telemetry(machine, workload, outcomes),
        )

    def clear_cache(self) -> None:
        self._snaps.clear()
