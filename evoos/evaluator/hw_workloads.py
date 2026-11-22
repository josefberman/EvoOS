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


def guest_producer(i: int, token: int, consumer_ctx: int, messages: tuple[int, ...] | None = None) -> GuestImage:
    toks = messages or (token,)
    lines = []
    for off, tok in enumerate(toks):
        lines.append(f"        MOVI r3, {int(tok)}")
        lines.append(f"        STORE r1, r3, {off}")
        lines.append("        MOVI r4, 1")
        lines.append("        SVC r4")
    lines.append("        HALT")
    src = "\n".join(lines)
    return GuestImage(
        name=f"put_{i}",
        code=assemble(src),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        needed_cycles=8 * len(toks),
        home_block=i,
        role="producer",
        token=toks[0],
        pair=consumer_ctx,
        messages=tuple(int(t) for t in toks),
    )


def guest_consumer(i: int, token: int, producer_ctx: int, messages: tuple[int, ...] | None = None) -> GuestImage:
    toks = messages or (token,)
    lines = []
    for _ in toks:
        lines.append("        MOVI r4, 2")
        lines.append("        SVC r4")
    lines.append("        HALT")
    return GuestImage(
        name=f"get_{i}",
        code=assemble("\n".join(lines)),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        expected_off={off: int(t) for off, t in enumerate(toks)},
        needed_cycles=8 * len(toks),
        home_block=i,
        role="consumer",
        token=toks[0],
        pair=producer_ctx,
        messages=tuple(int(t) for t in toks),
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


def route_workload(
    split: str = "train",
    pairs: int = N_ROUTE_PAIRS,
    messages: tuple[int, ...] | None = None,
    name: str | None = None,
) -> Workload:
    guests = []
    pair_tokens = (11, 22, 33, 44, 55, 66)
    for p in range(pairs):
        prod_i = 2 * p
        cons_i = 2 * p + 1
        if messages is not None:
            toks = tuple(int(t) + 10 * p for t in messages)
        else:
            toks = (int(pair_tokens[p]),)
        guests.append(guest_producer(prod_i, toks[0], cons_i + 1, toks))
        guests.append(guest_consumer(cons_i, toks[0], prod_i + 1, toks))
    nmsg = len(messages) if messages is not None else 1
    return Workload(
        name=name or f"route_{pairs}x{nmsg}_{split}",
        guests=tuple(guests),
        split=split,
        kind="route",
    )


def mixed_pressure_route(split: str = "train") -> Workload:
    """Stage 4: 4 compute guests + 2 route pairs, 8 guests into 4 frames."""
    ns = (3, 4, 5, 6)
    guests = [guest_pressure(i, ns[i]) for i in range(4)]
    toks_a = (11,)
    toks_b = (22,)
    guests.append(guest_producer(4, 11, 6, toks_a))
    guests.append(guest_consumer(5, 11, 5, toks_a))
    guests.append(guest_producer(6, 22, 8, toks_b))
    guests.append(guest_consumer(7, 22, 7, toks_b))
    return Workload(
        name=f"mix_4c_2p_{split}",
        guests=tuple(guests),
        split=split,
        kind="mix",
    )


def routing_stages() -> tuple[dict, ...]:
    """Curriculum: 1 pair → ordered payload → several pairs → pressure+route."""
    return (
        {
            "name": "r1_pair",
            "n_guests": 2,
            "promote_delivered": 1,
            "train": (route_workload("train", 1, name="r1_train"),),
            "heldout": (route_workload("heldout", 1, name="r1_held"),),
        },
        {
            "name": "r2_order",
            "n_guests": 2,
            "promote_delivered": 1,
            "train": (route_workload("train", 1, (7, 8, 9), name="r2_train"),),
            "heldout": (route_workload("heldout", 1, (3, 5, 6), name="r2_held"),),
        },
        {
            "name": "r3_pairs",
            "n_guests": 6,
            "promote_delivered": 1,
            "train": (route_workload("train", 3, name="r3_train"),),
            "heldout": (route_workload("heldout", 2, name="r3_held"),),
        },
        {
            "name": "r4_pressure",
            "n_guests": 8,
            "promote_delivered": 1,
            "train": (route_workload("train", 4), mixed_pressure_route("train")),
            "heldout": (route_workload("heldout", 3), mixed_pressure_route("heldout")),
        },
    )


def hw_training() -> tuple[Workload, ...]:
    return (pressure_workload("train", 8), route_workload("train", 4))


def hw_heldout() -> tuple[Workload, ...]:
    return (pressure_workload("heldout", 8), route_workload("heldout", 3))
