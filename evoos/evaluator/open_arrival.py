"""Open-arrival evaluation: guests appear after the machine is already running.

Firmware (this module, in the research VM) installs code into a context.
The supervisor bytecode still decides ADDPROT / RUNCTX. No new opcodes.

Under open arrivals, supervisor HALT or supervisor fault before the
episode horizon is failure: firmware does not re-enter the blob.
"""

from __future__ import annotations

from evoos.evaluator.workloads import guest_fill, guest_sum
from evoos.vm.instruction_set import Instr
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import EvalResult, GuestImage, Workload, load_firmware


def install_guest(machine: Machine, guest: GuestImage, slot: int) -> None:
    """Firmware loader: write a guest into an existing context slot (0..3)."""
    cid = slot + 1
    if cid <= 0 or cid >= machine.config.n_contexts:
        raise ValueError(f"slot {slot} out of range")
    cfg = machine.config
    ctx = machine.contexts[cid]
    ctx.code = list(guest.code)
    ctx.pc = 0
    ctx.regs = [0] * cfg.n_regs
    ctx.regs[1] = guest.data_base
    ctx.regs[2] = guest.data_bound
    ctx.halted = False
    ctx.fault = 0
    ctx.cycles = 0
    ctx.halt_cycle = None
    ctx.data_base = guest.data_base
    ctx.data_bound = guest.data_bound
    machine.legal_store[cid] = (guest.data_base, guest.data_bound)
    for addr in range(guest.data_base, guest.data_bound):
        machine.mem[addr] = 0
        machine.last_writer[addr] = 255
    for addr, val in guest.init.items():
        machine.mem[addr] = val
        machine.last_writer[addr] = 0


def reenter_supervisor(machine: Machine) -> None:
    """Legacy firmware watchdog (not used by live / HITL scoring).

    Does not choose which guest to run. Kept so one-shot demos can still
    be re-entered; open-arrival search treats HALT as failure instead.
    """
    sup = machine.contexts[0]
    sup.halted = False
    sup.fault = 0
    sup.pc = 0
    machine.halted = False
    machine.supervisor_fault = 0


def guest_correct(machine: Machine, guest: GuestImage, cid: int) -> bool:
    ctx = machine.contexts[cid]
    if not ctx.halted or ctx.fault:
        return False
    for addr, exp in guest.expected.items():
        if machine.mem[addr] != exp:
            return False
    return True


def _std(xs: list[float]) -> float:
    if not xs:
        return 0.0
    mean = sum(xs) / len(xs)
    var = sum((x - mean) ** 2 for x in xs) / len(xs)
    return var ** 0.5


def evaluate_open_arrival(
    supervisor: tuple[Instr, ...] | list[Instr],
    *,
    config: MachineConfig | None = None,
    slice_steps: int = 400,
    max_slices: int = 80,
    reenter_on_halt: bool = False,
) -> dict:
    """Two guests at t=0, two more injected later, then a replacement.

    Default: HALT / supervisor fault before the horizon is failure
    (`halted_early`). Isolation hits are a hard constraint.
    """
    cfg = config or MachineConfig(max_steps=200_000, wall_steps=250_000)
    first = (guest_sum(0, 8), guest_fill(1, 5))
    later = (guest_sum(2, 8), guest_fill(3, 5))
    replacement = guest_sum(0, 11)
    m = Machine(cfg)
    load_firmware(m, Workload(name="open_k2", guests=first, split="train"))
    # Empty slots 2,3 start halted so RUNCTX is a nop until firmware installs.
    for cid in (3, 4):
        m.contexts[cid].halted = True
        m.contexts[cid].code = list(later[cid - 3].code)
        m.contexts[cid].data_base = later[cid - 3].data_base
        m.contexts[cid].data_bound = later[cid - 3].data_bound
        m.legal_store[cid] = (later[cid - 3].data_base, later[cid - 3].data_bound)
    m.install_supervisor(supervisor)

    injected = False
    replaced = False
    halted_early = False
    for s in range(max_slices):
        if m.contexts[0].halted or m.contexts[0].fault or m.halted:
            if reenter_on_halt:
                reenter_supervisor(m)
            else:
                halted_early = True
                break
        limit = m.cycles + slice_steps
        while not m.halted and m.cycles < limit:
            sup = m.contexts[0]
            if sup.halted or sup.fault:
                m.halted = True
                halted_early = True
                break
            m._execute(0)
        if halted_early and not reenter_on_halt:
            break
        if not injected and s >= 8:
            install_guest(m, later[0], 2)
            install_guest(m, later[1], 3)
            injected = True
        if not replaced and injected and guest_correct(m, first[0], 1):
            install_guest(m, replacement, 0)
            replaced = True

    if m.contexts[0].halted or m.contexts[0].fault or m.supervisor_fault:
        halted_early = True

    ok_first = guest_correct(m, first[0], 1) or guest_correct(m, replacement, 1)
    ok_fill = guest_correct(m, first[1], 2)
    ok_late0 = guest_correct(m, later[0], 3)
    ok_late1 = guest_correct(m, later[1], 4)
    completed = sum([ok_first, ok_fill, ok_late0, ok_late1])
    guest_cycles = [int(m.contexts[i].cycles) for i in range(1, 5)]
    halt_cycles = [m.contexts[i].halt_cycle for i in range(1, 5)]
    progress = [min(1.0, c / 32.0) for c in guest_cycles]
    fairness = max(0.0, 1.0 - _std(progress)) if any(guest_cycles) else 0.0
    done_halts = [h for h in halt_cycles if h is not None]
    horizon = max(m.cycles, slice_steps * max_slices, 1)
    if completed and done_halts:
        latency = max(0.0, 1.0 - (max(done_halts) / horizon))
    else:
        latency = 0.0
    isolation_ok = not m.isolation_hits
    return {
        "completed": completed,
        "injected": injected,
        "replaced": replaced,
        "isolation_hits": list(m.isolation_hits),
        "isolation_ok": isolation_ok,
        "cycles": m.cycles,
        "switch_count": m.switch_count,
        "halted_flags": [int(c.halted) for c in m.contexts],
        "faults": [int(c.fault) for c in m.contexts],
        "halted_early": halted_early,
        "supervisor_fault": int(m.supervisor_fault or m.contexts[0].fault),
        "guest_cycles": guest_cycles,
        "halt_cycles": halt_cycles,
        "fairness": fairness,
        "latency": latency,
        "horizon_survived": (not halted_early) and isolation_ok,
    }


def score_open_live(oa: dict, train: EvalResult | None = None) -> float:
    """Scalar used by open-arrival search. Isolation is a hard constraint.

    HALT / supervisor fault before the horizon is failure; a small gradient
    on completed guests keeps the landscape from going flat.
    """
    if not oa.get("isolation_ok", oa.get("isolation_hits") == []):
        return -1.0
    if train is not None and (not train.isolation_ok or train.corruption):
        return -1.0
    if oa.get("supervisor_fault"):
        return -0.5
    completed = oa["completed"] / 4.0
    train_c = (train.completed / max(train.n_guests, 1)) if train else 0.0
    if oa["halted_early"]:
        return 0.08 * completed + 0.02 * train_c
    return (
        0.50 * completed
        + 0.15 * train_c
        + 0.20 * float(oa.get("fairness") or 0.0)
        + 0.15 * float(oa.get("latency") or 0.0)
    )
