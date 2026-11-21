"""Open-arrival evaluation: guests appear after the machine is already running.

Firmware (this module, in the research VM) installs code into a context.
The supervisor bytecode still decides ADDPROT / RUNCTX. No new opcodes.
"""

from __future__ import annotations

from evoos.evaluator.workloads import guest_fill, guest_sum, training_workloads
from evoos.vm.instruction_set import Instr
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import GuestImage, load_firmware
from evoos.vm.sandbox import Workload


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
    """Firmware watchdog: supervisor HALT returns to firmware; re-enter PC=0.

    Does not choose which guest to run.
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


def evaluate_open_arrival(
    supervisor: tuple[Instr, ...] | list[Instr],
    *,
    config: MachineConfig | None = None,
    slice_steps: int = 400,
    max_slices: int = 80,
) -> dict:
    """Two guests at t=0, two more injected later, then a replacement.

    Returns counts the live firmware cares about.
    """
    cfg = config or MachineConfig(max_steps=200_000, wall_steps=250_000)
    first = (guest_sum(0, 8), guest_fill(1, 5))
    later = (guest_sum(2, 8), guest_fill(3, 5))
    replacement = guest_sum(0, 11)
    wl = Workload(name="open_arrival", guests=first + later, split="train")
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
    for s in range(max_slices):
        if m.contexts[0].halted or m.halted:
            reenter_supervisor(m)
        limit = m.cycles + slice_steps
        while not m.halted and m.cycles < limit:
            sup = m.contexts[0]
            if sup.halted or sup.fault:
                m.halted = True
                break
            m._execute(0)
        if not injected and s >= 8:
            install_guest(m, later[0], 2)
            install_guest(m, later[1], 3)
            injected = True
        if not replaced and injected and guest_correct(m, first[0], 1):
            install_guest(m, replacement, 0)
            replaced = True

    ok_first = guest_correct(m, first[0], 1) or guest_correct(m, replacement, 1)
    ok_fill = guest_correct(m, first[1], 2)
    ok_late0 = guest_correct(m, later[0], 3)
    ok_late1 = guest_correct(m, later[1], 4)
    completed = sum([ok_first, ok_fill, ok_late0, ok_late1])
    return {
        "completed": completed,
        "injected": injected,
        "replaced": replaced,
        "isolation_hits": list(m.isolation_hits),
        "cycles": m.cycles,
        "switch_count": m.switch_count,
        "halted_flags": [int(c.halted) for c in m.contexts],
        "faults": [int(c.fault) for c in m.contexts],
    }
