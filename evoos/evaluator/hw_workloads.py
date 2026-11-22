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


def guest_producer(
    i: int,
    token: int,
    consumer_ctx: int,
    messages: tuple[int, ...] | None = None,
    store_off: int = 0,
) -> GuestImage:
    toks = messages or (token,)
    lines = []
    for j, tok in enumerate(toks):
        off = int(store_off) + j
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


def guest_consumer(
    i: int,
    token: int,
    producer_ctx: int,
    messages: tuple[int, ...] | None = None,
    store_off: int = 0,
) -> GuestImage:
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
        expected_off={int(store_off) + j: int(t) for j, t in enumerate(toks)},
        needed_cycles=8 * len(toks),
        home_block=i,
        role="consumer",
        token=toks[0],
        pair=producer_ctx,
        messages=tuple(int(t) for t in toks),
    )


def guest_attacker(i: int, kind: str = "store_frames") -> GuestImage:
    """Malicious guest: poke likely RAM frames. Must fault unless granted a leak."""
    if kind == "load_frames":
        lines = []
        for base in (256, 288, 320, 352, 384):
            lines.append(f"        MOVI r1, {base}")
            lines.append("        LOAD r3, r1, 0")
        lines.append("        HALT")
    else:
        lines = [
            "        MOVI r3, 99",
        ]
        for base in (256, 288, 320, 352, 384):
            lines.append(f"        MOVI r1, {base}")
            lines.append("        STORE r1, r3, 0")
        lines.append("        HALT")
    return GuestImage(
        name=f"atk_{i}_{kind}",
        code=assemble("\n".join(lines)),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        needed_cycles=40,
        home_block=i,
        role="attacker",
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


def route_variant(
    split: str,
    *,
    pairs: int,
    messages: tuple[int, ...] | None = None,
    perm: tuple[int, ...] | None = None,
    store_off: int = 0,
    name: str = "",
) -> Workload:
    """Route pairs with shuffled guest order and/or payload offsets.

    Context ids follow list order (ctx = index+1), so a permutation changes
    which hardware ids are producer vs consumer. Pair pointers are rewritten
    after placement so the evaluator still knows the intended partner.
    """
    n = pairs * 2
    order = list(perm) if perm is not None else list(range(n))
    if sorted(order) != list(range(n)):
        raise ValueError("perm must be a permutation of 0..n-1")
    raw: list[tuple[str, int, tuple[int, ...]]] = []
    base = messages or (11,)
    for p in range(pairs):
        toks = tuple(int(t) + 17 * p for t in base)
        raw.append(("prod", p, toks))
        raw.append(("cons", p, toks))
    placed = [raw[i] for i in order]
    loc: dict[int, dict[str, int]] = {}
    for i, (role, p, _toks) in enumerate(placed):
        loc.setdefault(p, {})[role] = i + 1
    guests = []
    for i, (role, p, toks) in enumerate(placed):
        if role == "prod":
            guests.append(guest_producer(i, toks[0], loc[p]["cons"], toks, store_off=store_off))
        else:
            guests.append(guest_consumer(i, toks[0], loc[p]["prod"], toks, store_off=store_off))
    nmsg = len(messages) if messages is not None else 1
    return Workload(
        name=name or f"routev_{pairs}x{nmsg}_off{store_off}_{split}",
        guests=tuple(guests),
        split=split,
        kind="route",
    )


def mix_variant(split: str, *, route_first: bool = False, name: str = "") -> Workload:
    """4 compute + 2 pairs, optionally with route guests occupying ctx 1..4."""
    ns = (3, 4, 5, 6)
    compute = [guest_pressure(i, ns[i % len(ns)]) for i in range(4)]
    if route_first:
        guests: list[GuestImage] = []
        guests.append(guest_producer(0, 31, 2, (31,)))
        guests.append(guest_consumer(1, 31, 1, (31,)))
        guests.append(guest_producer(2, 41, 4, (41,)))
        guests.append(guest_consumer(3, 41, 3, (41,)))
        for i, g in enumerate(compute):
            guests.append(
                GuestImage(
                    name=g.name,
                    code=g.code,
                    data_base=0,
                    data_bound=0,
                    init=g.init,
                    expected={},
                    expected_off=g.expected_off,
                    needed_cycles=g.needed_cycles,
                    home_block=4 + i,
                    role="compute",
                )
            )
    else:
        guests = list(mixed_pressure_route(split).guests)
    return Workload(
        name=name or f"mixv_{'rf' if route_first else 'cf'}_{split}",
        guests=tuple(guests),
        split=split,
        kind="mix",
    )


def audit_workloads() -> tuple[Workload, ...]:
    """Adversarial guests sitting next to a live pair."""
    pair = list(route_workload("heldout", 1, name="aud_pair").guests)
    atk_store = guest_attacker(2, "store_frames")
    atk_load = guest_attacker(2, "load_frames")
    two = list(route_workload("heldout", 2, name="aud_two").guests)
    return (
        Workload("audit_pair_store", tuple(pair + [atk_store]), split="heldout", kind="route"),
        Workload("audit_pair_load", tuple(pair + [atk_load]), split="heldout", kind="route"),
        Workload(
            "audit_two_store",
            tuple(two + [guest_attacker(4, "store_frames")]),
            split="heldout",
            kind="route",
        ),
        Workload(
            "audit_mix_store",
            tuple(list(mixed_pressure_route("heldout").guests)[:6] + [guest_attacker(6, "store_frames"), guest_attacker(7, "load_frames")]),
            split="heldout",
            kind="mix",
        ),
    )


def generalization_train() -> tuple[Workload, ...]:
    """Lexicase train: official 4/4 split plus held-like variants."""
    # Reverse each pair (consumer before producer) so ctx ids are not 1,2,3,4,...
    rev4 = tuple(i ^ 1 for i in range(8))
    rot3 = tuple((i + 2) % 6 for i in range(6))
    return (
        route_workload("train", 4),
        mixed_pressure_route("train"),
        route_variant("train", pairs=4, perm=rev4, name="gen_rev4_train"),
        route_variant("train", pairs=3, messages=(4, 5, 6), store_off=1, perm=rot3, name="gen_msg_off_train"),
        pressure_workload("train", 8),
    )


def generalization_held() -> tuple[Workload, ...]:
    rot = tuple((i + 2) % 6 for i in range(6))
    return (
        route_workload("heldout", 3),
        mixed_pressure_route("heldout"),
        route_variant("heldout", pairs=3, perm=rot, name="gen_rot3_held"),
        route_variant("heldout", pairs=2, messages=(8, 9), store_off=2, name="gen_off2_held"),
        mix_variant("heldout", route_first=True, name="gen_mix_rf_held"),
        pressure_workload("heldout", 8),
    )


def official_split() -> tuple[list, list]:
    """The round-8 4/4 split: route_4 + mix, train and held, all guests complete."""
    train = [route_workload("train", 4), mixed_pressure_route("train")]
    held = [route_workload("heldout", 3), mixed_pressure_route("heldout")]
    return train, held
