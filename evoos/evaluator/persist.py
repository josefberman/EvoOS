"""Persistence evaluation: store/retrieve across power cuts and bad blocks.

Behavioral contract only: a value stored under a name by a guest is returned
to that guest after RAM is wiped. Namespaces and layout are the supervisor's.
Cross-guest leaks are a hard fail.

Gated GET: a matching payload counts only if that value was on the device
after PUT (before GET) *and* the GET phase issued at least one BLKRD.
Leaving the word in a guest window, or MOVIing it after wipe, is not credit.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence

from evoos.evaluator.workloads import GUEST_SLOTS
from evoos.vm.instruction_set import Instr, assemble
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import GuestImage, Workload, load_firmware

STAGES = ("A", "B", "C", "D")
LAYOUTS = ("empty", "append-log-like", "dense-table", "sparse-hash-like", "other")


def guest_put(slot: int, key: int, value: int) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    code = assemble(
        """
        MOVI r1, 1
        SVC r1
        HALT
        """
    )
    return GuestImage(
        name=f"put_{key}_{value}",
        code=code,
        data_base=lo,
        data_bound=hi,
        init={lo: key, lo + 1: 1, lo + 2: value},
        expected={lo + 2: value},
        needed_cycles=8,
    )


def guest_get(slot: int, key: int, expect: int) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    code = assemble(
        """
        MOVI r1, 2
        SVC r1
        HALT
        """
    )
    return GuestImage(
        name=f"get_{key}",
        code=code,
        data_base=lo,
        data_bound=hi,
        init={lo: key, lo + 1: 1, lo + 2: 0},
        expected={lo + 2: expect},
        needed_cycles=8,
    )


def _install(m: Machine, guest: GuestImage, slot: int) -> None:
    cid = slot + 1
    ctx = m.contexts[cid]
    ctx.code = list(guest.code)
    ctx.pc = 0
    ctx.regs = [0] * m.config.n_regs
    ctx.regs[1] = guest.data_base
    ctx.regs[2] = guest.data_bound
    ctx.halted = False
    ctx.fault = 0
    ctx.cycles = 0
    ctx.svc = 0
    ctx.data_base = guest.data_base
    ctx.data_bound = guest.data_bound
    m.legal_store[cid] = (guest.data_base, guest.data_bound)
    for addr in range(guest.data_base, guest.data_bound):
        m.mem[addr] = 0
        m.last_writer[addr] = 255
    for addr, val in guest.init.items():
        m.mem[addr] = val
        m.last_writer[addr] = 0


def _run(m: Machine, steps: int) -> None:
    limit = m.cycles + steps
    while not m.halted and m.cycles < limit:
        sup = m.contexts[0]
        if sup.halted or sup.fault:
            break
        m._execute(0)


def _on_device(rows: list[list[int]], value: int) -> bool:
    v = int(value)
    for block in rows:
        if v in block:
            return True
    return False


def _poison_mem(m: Machine) -> None:
    for i in range(len(m.mem)):
        m.mem[i] = 0x5A5A5A5A
        m.last_writer[i] = 255


def classify_disk(rows: list[list[int]]) -> str:
    """Heuristic label of whatever landed on the device. Not a filesystem name."""
    nonempty = [(i, b) for i, b in enumerate(rows) if any(w != 0 for w in b)]
    if not nonempty:
        return "empty"
    idxs = [i for i, _ in nonempty]
    vals = [b[0] for _, b in nonempty]
    if idxs == list(range(min(idxs), max(idxs) + 1)) and max(idxs) - min(idxs) + 1 == len(idxs):
        if vals == sorted(vals) or vals == list(reversed(sorted(vals))):
            return "append-log-like"
        return "dense-table"
    if len(idxs) <= 4 and max(idxs) - min(idxs) >= 8:
        return "sparse-hash-like"
    return "other"


def _eval_items(
    supervisor: tuple[Instr, ...] | list[Instr],
    items: Sequence[tuple[int, int, int]],
    *,
    config: MachineConfig | None = None,
    slice_steps: int = 4000,
    restart: bool = True,
    crash_at: int | None = None,
    corrupt_blocks: Sequence[int] = (),
) -> dict:
    """PUT each (slot, key, value), optional restart, then GET.

    `crash_at` is the PUT index after which the next write is dropped and a
    power cut happens (remaining PUTs run after reboot).
    """
    cfg = config or MachineConfig(max_steps=80_000, wall_steps=100_000)
    puts = tuple(guest_put(s, k, v) for s, k, v in items)
    unique_slots = sorted({s for s, _, _ in items})
    dummy = tuple(guest_put(s, 0, 0) for s in unique_slots) or (guest_put(0, 0, 0),)
    m = Machine(cfg)
    load_firmware(m, Workload(name="persist_put", guests=dummy, split="train"))
    m.install_supervisor(supervisor)

    isolation_hits: list = []
    disk_after_put: list[list[int]] = []
    put_halted: list[int] = []

    sequential = len(unique_slots) < len(items)
    if sequential:
        for i, (slot, key, value) in enumerate(items):
            g = guest_put(slot, key, value)
            _install(m, g, slot)
            if crash_at is not None and i == crash_at:
                m.disk.drop_next_write = True
            _run(m, slice_steps)
            put_halted.append(int(m.contexts[slot + 1].halted))
            isolation_hits.extend(m.isolation_hits)
            if crash_at is not None and i == crash_at:
                m.power_cycle(drop_inflight=True)
                m.install_supervisor(supervisor)
        n_write = m.disk.n_write
        disk_after_put = m.disk.as_rows()
    else:
        load_firmware(m, Workload(name="persist_put", guests=puts, split="train"))
        m.install_supervisor(supervisor)
        if crash_at is not None:
            m.disk.drop_next_write = True
        _run(m, slice_steps)
        put_halted = [int(m.contexts[s + 1].halted) for s, _, _ in items]
        isolation_hits = list(m.isolation_hits)
        n_write = m.disk.n_write
        disk_after_put = m.disk.as_rows()

    if restart:
        m.power_cycle(drop_inflight=crash_at is not None)
        m.install_supervisor(supervisor)
    else:
        _poison_mem(m)
        m.last_svc = 0
        m.last_svc_ctx = 0
        for ctx in m.contexts:
            ctx.svc = 0

    if corrupt_blocks:
        for b in corrupt_blocks:
            if 0 <= b < m.disk.n_blocks:
                m.disk.bad.add(int(b))

    gets = tuple(guest_get(s, k, v) for s, k, v in items)
    n_read_before = m.disk.n_read
    if sequential:
        got: list[int] = []
        get_halted: list[int] = []
        for slot, key, value in items:
            g = guest_get(slot, key, value)
            _install(m, g, slot)
            _run(m, slice_steps)
            got.append(int(m.mem[g.data_base + 2]))
            get_halted.append(int(m.contexts[slot + 1].halted))
            isolation_hits.extend(m.isolation_hits)
    else:
        load_firmware(m, Workload(name="persist_get", guests=gets, split="train"))
        m.install_supervisor(supervisor)
        _run(m, slice_steps)
        got = [int(m.mem[g.data_base + 2]) for g in gets]
        get_halted = [int(m.contexts[s + 1].halted) for s, _, _ in items]
        isolation_hits.extend(m.isolation_hits)

    n_read_get = m.disk.n_read - n_read_before
    expect = [v for _, _, v in items]
    raw = [got[i] == expect[i] for i in range(len(items))]
    gated = [
        raw[i] and _on_device(disk_after_put, expect[i]) and n_read_get > 0
        for i in range(len(items))
    ]
    slots = [s for s, _, _ in items]
    leak = False
    for i, (s_i, k_i, v_i) in enumerate(items):
        for j, (s_j, k_j, v_j) in enumerate(items):
            if s_i == s_j or i == j:
                continue
            if got[i] == v_j and v_i != v_j:
                leak = True
    isolation_ok = not isolation_hits and not leak
    return {
        "n": len(items),
        "correct": sum(gated),
        "raw_match": sum(raw),
        "gated": gated,
        "got": got,
        "expect": expect,
        "leak": leak,
        "isolation_ok": isolation_ok,
        "isolation_hits": isolation_hits,
        "put_halted": put_halted,
        "get_halted": get_halted,
        "n_write": n_write,
        "n_read": m.disk.n_read,
        "n_read_get": n_read_get,
        "supervisor_fault": int(m.supervisor_fault or m.contexts[0].fault),
        "disk": m.disk.as_rows(),
        "disk_after_put": disk_after_put,
        "layout": classify_disk(disk_after_put),
        "restart": restart,
        "slots": slots,
    }


def evaluate_persist(
    supervisor: tuple[Instr, ...] | list[Instr],
    *,
    config: MachineConfig | None = None,
    slice_steps: int = 8000,
    corrupt: bool = False,
    crash_write: bool = False,
) -> dict:
    """Stage D-shaped default: two guests, same key, restart, optional crash/corrupt."""
    r = _eval_items(
        supervisor,
        ((0, 1, 42), (1, 1, 99)),
        config=config,
        slice_steps=slice_steps,
        restart=True,
        crash_at=0 if crash_write else None,
        corrupt_blocks=(0,) if corrupt else (),
    )
    r["crash_write"] = crash_write
    r["corrupt"] = corrupt
    return r


def evaluate_stage(
    supervisor: tuple[Instr, ...] | list[Instr],
    stage: str,
    *,
    config: MachineConfig | None = None,
    slice_steps: int = 4000,
    rng: random.Random | None = None,
) -> dict:
    stage = stage.upper()
    rng = rng or random.Random(0)
    if stage == "A":
        r = _eval_items(
            supervisor, ((0, 3, 77),), config=config, slice_steps=slice_steps, restart=False
        )
    elif stage == "B":
        items = ((0, 1, 11), (0, 2, 22), (0, 3, 33))
        cut = rng.randint(0, 2)
        r = _eval_items(
            supervisor,
            items,
            config=config,
            slice_steps=slice_steps,
            restart=True,
            crash_at=cut,
        )
        r["crash_at"] = cut
    elif stage == "C":
        items = ((0, 1, 11), (0, 2, 22), (0, 3, 33))
        r = _eval_items(
            supervisor,
            items,
            config=config,
            slice_steps=slice_steps,
            restart=True,
            corrupt_blocks=(0, 31),
        )
    elif stage == "D":
        r = _eval_items(
            supervisor,
            ((0, 1, 42), (1, 1, 99)),
            config=config,
            slice_steps=slice_steps,
            restart=True,
        )
    else:
        raise ValueError(f"unknown stage {stage}")
    r["stage"] = stage
    return r


def score_persist(r: dict) -> float:
    if not r["isolation_ok"] or r["leak"]:
        return -1.0
    if r["supervisor_fault"]:
        return -0.5
    n = max(int(r.get("n", 2)), 1)
    c = r["correct"] / float(n)
    writes = r["n_write"]
    need = n
    efficiency = max(0.0, 1.0 - min(1.0, (writes - need) / 16.0)) if writes else 0.0
    return 0.75 * c + 0.25 * efficiency


def promote(r: dict) -> bool:
    """Partial success → next curriculum stage."""
    if not r.get("isolation_ok") or r.get("leak") or r.get("supervisor_fault"):
        return False
    stage = r.get("stage", "D")
    c = r["correct"]
    n = r.get("n", 2)
    if stage == "A":
        return c >= 1 and r["n_write"] >= 1
    if stage == "B":
        return c >= 2
    if stage == "C":
        return c >= 2
    return c >= n and n >= 2


def persist_cell(r: dict) -> tuple[int, int]:
    """MAP-Elites: layout × gated-correct (milestone-aligned)."""
    layout = r.get("layout", "empty")
    x = LAYOUTS.index(layout) if layout in LAYOUTS else len(LAYOUTS) - 1
    y = min(int(r.get("correct", 0)), 4)
    return (x, y)


def program_rng(rows: Sequence[Sequence[int]]) -> random.Random:
    h = hashlib.sha256(repr([list(r) for r in rows]).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))
