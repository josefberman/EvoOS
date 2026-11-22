"""Condition C-hw workloads: memory pressure and guest-to-guest routing.

Guests are relocatable: they use r1 (hardware DATA_BASE) as the window base.
The supervisor must place them with ADDPROT / CTXSET / BLKRD. No paging opcode.
"""

from __future__ import annotations

from evoos.vm.instruction_set import assemble
from evoos.vm.sandbox import GuestImage, Workload

FRAME = 32
FRAME0 = 256
N_FRAMES = 4
N_PRESSURE_GUESTS = 8
N_ROUTE_PAIRS = 4


def _reloc_sum(n: int) -> tuple:
    src = f"""
        LOAD r3, r1, 0
        MOVI r4, 0
        MOVI r5, 1
    loop:
        BLT r3, r5, done
        ADD r4, r4, r5
        ADDI r5, r5, 1
        LOAD r6, r1, 1
        ADDI r6, r6, 1
        STORE r1, r6, 1
        JMP loop
    done:
        STORE r1, r4, 2
        HALT
    """
    return assemble(src)


def guest_pressure(i: int, n: int) -> GuestImage:
    return GuestImage(
        name=f"psum_{i}_{n}",
        code=_reloc_sum(n),
        data_base=0,
        data_bound=0,
        init={0: n},
        expected={},
        expected_off={2: n * (n + 1) // 2},
        needed_cycles=12 * n + 10,
        home_block=i,
        role="compute",
    )


def guest_producer(i: int, token: int, consumer_ctx: int) -> GuestImage:
    src = f"""
        MOVI r3, {token}
        STORE r1, r3, 0
        MOVI r4, 1
        SVC r4
        HALT
    """
    return GuestImage(
        name=f"put_{i}",
        code=assemble(src),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        needed_cycles=8,
        home_block=i,
        role="producer",
        token=token,
        pair=consumer_ctx,
    )


def guest_consumer(i: int, token: int, producer_ctx: int) -> GuestImage:
    src = """
        MOVI r4, 2
        SVC r4
        HALT
    """
    return GuestImage(
        name=f"get_{i}",
        code=assemble(src),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        expected_off={0: token},
        needed_cycles=8,
        home_block=i,
        role="consumer",
        token=token,
        pair=producer_ctx,
    )


def pressure_workload(split: str = "train", n: int = N_PRESSURE_GUESTS) -> Workload:
    ns = (3, 4, 5, 6, 3, 4, 5, 6, 7, 8, 4, 5)
    guests = tuple(guest_pressure(i, ns[i]) for i in range(n))
    return Workload(
        name=f"pressure_{n}_{split}",
        guests=guests,
        split=split,
        kind="pressure",
    )


def route_workload(split: str = "train", pairs: int = N_ROUTE_PAIRS) -> Workload:
    guests = []
    tokens = (11, 22, 33, 44, 55, 66)
    for p in range(pairs):
        prod_i = 2 * p
        cons_i = 2 * p + 1
        tok = tokens[p]
        guests.append(guest_producer(prod_i, tok, cons_i + 1))
        guests.append(guest_consumer(cons_i, tok, prod_i + 1))
    return Workload(
        name=f"route_{pairs}_{split}",
        guests=tuple(guests),
        split=split,
        kind="route",
    )


def hw_training() -> tuple[Workload, ...]:
    return (pressure_workload("train", 8), route_workload("train", 4))


def hw_heldout() -> tuple[Workload, ...]:
    return (pressure_workload("heldout", 8), route_workload("heldout", 3))
