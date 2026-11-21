"""Persistence evaluation: store/retrieve across power cuts and bad blocks.

Behavioral contract only: a value stored under a name by a guest is returned
to that guest after RAM is wiped. Namespaces and layout are the supervisor's.
Cross-guest leaks are a hard fail.
"""

from __future__ import annotations

from evoos.evaluator.workloads import GUEST_SLOTS
from evoos.vm.instruction_set import Instr, Svc, assemble
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import GuestImage, load_firmware
from evoos.vm.sandbox import Workload


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


def evaluate_persist(
    supervisor: tuple[Instr, ...] | list[Instr],
    *,
    config: MachineConfig | None = None,
    slice_steps: int = 8000,
    corrupt: bool = False,
    crash_write: bool = False,
) -> dict:
    cfg = config or MachineConfig(max_steps=80_000, wall_steps=100_000)
    puts = (guest_put(0, 1, 42), guest_put(1, 1, 99))
    gets = (guest_get(0, 1, 42), guest_get(1, 1, 99))
    m = Machine(cfg)
    load_firmware(m, Workload(name="persist_put", guests=puts, split="train"))
    m.install_supervisor(supervisor)
    if crash_write:
        m.disk.drop_next_write = True
    _run(m, slice_steps)
    put_halted = [int(m.contexts[i].halted) for i in (1, 2)]
    isolation_hits = list(m.isolation_hits)
    n_write_before = m.disk.n_write
    m.power_cycle(drop_inflight=crash_write)
    if corrupt and m.disk.n_blocks:
        m.disk.bad.add(0)
    load_firmware(m, Workload(name="persist_get", guests=gets, split="train"))
    m.install_supervisor(supervisor)
    _run(m, slice_steps)
    got = [m.mem[g.data_base + 2] for g in gets]
    expect = [42, 99]
    leak = got[0] == 99 or got[1] == 42
    correct = [got[i] == expect[i] for i in range(2)]
    return {
        "correct": sum(correct),
        "got": got,
        "expect": expect,
        "leak": leak,
        "isolation_ok": not isolation_hits and not leak,
        "isolation_hits": isolation_hits,
        "put_halted": put_halted,
        "get_halted": [int(m.contexts[i].halted) for i in (1, 2)],
        "n_write": n_write_before,
        "n_read": m.disk.n_read,
        "supervisor_fault": int(m.supervisor_fault or m.contexts[0].fault),
        "disk": m.disk.as_rows(),
        "crash_write": crash_write,
        "corrupt": corrupt,
    }


def score_persist(r: dict) -> float:
    if not r["isolation_ok"] or r["leak"]:
        return -1.0
    if r["supervisor_fault"]:
        return -0.5
    c = r["correct"] / 2.0
    writes = r["n_write"]
    efficiency = max(0.0, 1.0 - min(1.0, (writes - 2) / 16.0)) if writes else 0.0
    return 0.75 * c + 0.25 * efficiency


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
