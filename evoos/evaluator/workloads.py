"""Guest programs and train/held-out workload splits.

Guests are ordinary bytecode. They share finite data memory and an instruction
budget; they are not processes in an OS sense. Layout is a hardware/firmware
convention the supervisor can inspect via CTXGET / the info block.
"""

from __future__ import annotations

from evoos.vm.instruction_set import assemble
from evoos.vm.sandbox import GuestImage, Workload


# Four disjoint data windows. Supervisor is not given "process" objects —
# only these addresses, via firmware or CTXGET DATA_BASE / DATA_BOUND.
GUEST_SLOTS: tuple[tuple[int, int], ...] = (
    (256, 384),
    (384, 512),
    (512, 640),
    (640, 768),
)


def _sum_code(base: int) -> tuple:
    src = f"""
        MOVI r1, {base}
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


def guest_sum(slot: int, n: int) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    code = _sum_code(lo)
    return GuestImage(
        name=f"sum_{n}",
        code=code,
        data_base=lo,
        data_bound=hi,
        init={lo: n},
        expected={lo + 2: n * (n + 1) // 2},
        needed_cycles=12 * n + 8,
    )


def _fill_code(base: int) -> tuple:
    src = f"""
        MOVI r1, {base}
        LOAD r3, r1, 0
        MOVI r5, 0
    loop:
        BGE r5, r3, done
        ADDI r6, r5, 10
        ADD r6, r1, r6
        ADDI r4, r5, 1
        STORE r6, r4, 0
        ADDI r5, r5, 1
        LOAD r7, r1, 1
        ADDI r7, r7, 1
        STORE r1, r7, 1
        JMP loop
    done:
        HALT
    """
    return assemble(src)


def guest_fill(slot: int, k: int) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    expected = {lo + 10 + i: i + 1 for i in range(k)}
    return GuestImage(
        name=f"fill_{k}",
        code=_fill_code(lo),
        data_base=lo,
        data_bound=hi,
        init={lo: k},
        expected=expected,
        needed_cycles=14 * k + 8,
    )


def _xor_code(base: int) -> tuple:
    src = f"""
        MOVI r1, {base}
        LOAD r3, r1, 0
        MOVI r4, 0
        MOVI r5, 0
    loop:
        BGE r5, r3, done
        ADDI r6, r5, 10
        ADD r6, r1, r6
        LOAD r7, r6, 0
        XOR r4, r4, r7
        ADDI r5, r5, 1
        LOAD r2, r1, 1
        ADDI r2, r2, 1
        STORE r1, r2, 1
        JMP loop
    done:
        STORE r1, r4, 2
        HALT
    """
    return assemble(src)


def guest_xor(slot: int, values: tuple[int, ...]) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    k = len(values)
    init = {lo: k}
    acc = 0
    for i, v in enumerate(values):
        init[lo + 10 + i] = v
        acc ^= v
    return GuestImage(
        name=f"xor_{k}",
        code=_xor_code(lo),
        data_base=lo,
        data_bound=hi,
        init=init,
        expected={lo + 2: acc},
        needed_cycles=16 * k + 8,
    )


def _copyinc_code(base: int) -> tuple:
    src = f"""
        MOVI r1, {base}
        LOAD r3, r1, 0
        MOVI r5, 0
    loop:
        BGE r5, r3, done
        ADDI r6, r5, 10
        ADD r6, r1, r6
        LOAD r4, r6, 0
        ADDI r4, r4, 1
        ADD r7, r6, r3
        STORE r7, r4, 0
        ADDI r5, r5, 1
        LOAD r2, r1, 1
        ADDI r2, r2, 1
        STORE r1, r2, 1
        JMP loop
    done:
        HALT
    """
    return assemble(src)


def guest_copyinc(slot: int, values: tuple[int, ...]) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    k = len(values)
    init = {lo: k}
    expected: dict[int, int] = {}
    for i, v in enumerate(values):
        init[lo + 10 + i] = v
        expected[lo + 10 + k + i] = v + 1
    return GuestImage(
        name=f"copyinc_{k}",
        code=_copyinc_code(lo),
        data_base=lo,
        data_bound=hi,
        init=init,
        expected=expected,
        needed_cycles=16 * k + 8,
    )


def _max_code(base: int) -> tuple:
    src = f"""
        MOVI r1, {base}
        LOAD r3, r1, 0
        MOVI r4, 0
        MOVI r5, 0
    loop:
        BGE r5, r3, done
        ADDI r6, r5, 10
        ADD r6, r1, r6
        LOAD r7, r6, 0
        BGE r4, r7, skip
        MOV r4, r7
    skip:
        ADDI r5, r5, 1
        LOAD r2, r1, 1
        ADDI r2, r2, 1
        STORE r1, r2, 1
        JMP loop
    done:
        STORE r1, r4, 2
        HALT
    """
    return assemble(src)


def guest_max(slot: int, values: tuple[int, ...]) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    k = len(values)
    init = {lo: k}
    for i, v in enumerate(values):
        init[lo + 10 + i] = v
    return GuestImage(
        name=f"max_{k}",
        code=_max_code(lo),
        data_base=lo,
        data_bound=hi,
        init=init,
        expected={lo + 2: max(values) if values else 0},
        needed_cycles=16 * k + 8,
    )


def _mulacc_code(base: int) -> tuple:
    # acc = sum_{i=1..n} i * m
    src = f"""
        MOVI r1, {base}
        LOAD r3, r1, 0
        LOAD r6, r1, 3
        MOVI r4, 0
        MOVI r5, 1
    loop:
        BLT r3, r5, done
        MUL r7, r5, r6
        ADD r4, r4, r7
        ADDI r5, r5, 1
        LOAD r2, r1, 1
        ADDI r2, r2, 1
        STORE r1, r2, 1
        JMP loop
    done:
        STORE r1, r4, 2
        HALT
    """
    return assemble(src)


def guest_mulacc(slot: int, n: int, m: int) -> GuestImage:
    lo, hi = GUEST_SLOTS[slot]
    return GuestImage(
        name=f"mulacc_{n}_{m}",
        code=_mulacc_code(lo),
        data_base=lo,
        data_bound=hi,
        init={lo: n, lo + 3: m},
        expected={lo + 2: m * n * (n + 1) // 2},
        needed_cycles=14 * n + 10,
    )


def guest_pressure(slot: int, k: int = 40) -> GuestImage:
    """Memory-pressure fill: write a long prefix of the guest window."""
    g = guest_fill(slot, k)
    return GuestImage(
        name=f"pressure_{k}",
        code=g.code,
        data_base=g.data_base,
        data_bound=g.data_bound,
        init=g.init,
        expected=g.expected,
        needed_cycles=g.needed_cycles,
    )


def guest_route(slot: int, dest_ctx: int = 2, payload: int = 77) -> GuestImage:
    """Routing trap: SVC 3 with dest ctx in window[0] and payload in window[2]."""
    lo, hi = GUEST_SLOTS[slot]
    src = f"""
        MOVI r1, {lo}
        MOVI r2, 3
        SVC r2
        HALT
    """
    return GuestImage(
        name=f"route_{dest_ctx}",
        code=assemble(src),
        data_base=lo,
        data_bound=hi,
        init={lo: dest_ctx, lo + 2: payload},
        expected={lo + 2: payload},
        needed_cycles=8,
        role="compute",
    )


def guest_route_prod(slot: int, payload: int = 77) -> GuestImage:
    """Producer: STORE payload at window[2], then HALT. Live analogue writes 8(%rdi)."""
    lo, hi = GUEST_SLOTS[slot]
    src = f"""
        MOVI r1, {lo}
        MOVI r2, {payload}
        STORE r1, r2, 2
        HALT
    """
    return GuestImage(
        name="route_prod",
        code=assemble(src),
        data_base=lo,
        data_bound=hi,
        init={lo: 0, lo + 2: 0},
        expected={lo + 2: payload},
        needed_cycles=8,
        role="producer",
        token=payload,
    )


def guest_route_cons(slot: int, src_slot: int = 0) -> GuestImage:
    """Consumer: LOAD window[2] of the producer slot. Needs a shared ADDPROT grant."""
    lo, hi = GUEST_SLOTS[slot]
    src_lo = GUEST_SLOTS[src_slot][0]
    src = f"""
        MOVI r1, {src_lo}
        LOAD r2, r1, 2
        HALT
    """
    return GuestImage(
        name="route_cons",
        code=assemble(src),
        data_base=lo,
        data_bound=hi,
        init={lo: 0, lo + 2: 0},
        expected={},
        needed_cycles=8,
        role="consumer",
        pair=src_slot + 1,
    )


def guest_malicious_store(slot: int, target_addr: int) -> GuestImage:
    """Writes outside its slot. Used to test isolation detection."""
    lo, hi = GUEST_SLOTS[slot]
    src = f"""
        MOVI r1, {target_addr}
        MOVI r2, 12345
        STORE r1, r2, 0
        HALT
    """
    return GuestImage(
        name="malicious_store",
        code=assemble(src),
        data_base=lo,
        data_bound=hi,
        init={lo: 1},
        expected={lo + 2: 0},
        needed_cycles=8,
    )


def training_workloads() -> tuple[Workload, ...]:
    return (
        Workload(
            name="train_small",
            split="train",
            guests=(
                guest_sum(0, 8),
                guest_fill(1, 5),
                guest_xor(2, (3, 5, 7, 9, 1)),
                guest_copyinc(3, (4, 8, 2, 6)),
            ),
        ),
        Workload(
            name="train_medium",
            split="train",
            guests=(
                guest_sum(0, 14),
                guest_fill(1, 8),
                guest_xor(2, (1, 2, 4, 8, 16, 32)),
                guest_copyinc(3, (9, 1, 4, 3, 7)),
            ),
        ),
    )


def heldout_workloads() -> tuple[Workload, ...]:
    return (
        Workload(
            name="heldout_mixed",
            split="heldout",
            guests=(
                guest_sum(0, 11),
                guest_max(1, (4, 17, 2, 9, 9)),
                guest_mulacc(2, 7, 3),
                guest_fill(3, 6),
            ),
        ),
        Workload(
            name="heldout_longer",
            split="heldout",
            guests=(
                guest_sum(0, 20),
                guest_xor(1, (11, 22, 33, 44, 55, 66, 77)),
                guest_copyinc(2, (2, 3, 5, 7, 11, 13)),
                guest_max(3, (1, 8, 3, 8, 2, 10, 4)),
            ),
        ),
    )


def _slice_workload(name: str, split: str, guests: tuple) -> Workload:
    return Workload(name=name, split=split, guests=guests)


def curriculum_stages() -> tuple[dict, ...]:
    """1 → 2 → 3 → 4 guests, then a harder 4-guest mix.

    Each stage is a smaller *machine* (n_contexts = n_guests+1), not a
    scheduler hint. Held-out for the stage uses the same guest count.
    """
    g1 = (guest_sum(0, 8),)
    g2 = (guest_sum(0, 8), guest_fill(1, 5))
    g3 = (guest_sum(0, 8), guest_fill(1, 5), guest_xor(2, (3, 5, 7, 9, 1)))
    g4 = training_workloads()[0].guests
    hard = training_workloads()[1].guests
    h1 = (guest_sum(0, 11),)
    h2 = (guest_sum(0, 11), guest_max(1, (4, 17, 2, 9, 9)))
    h3 = (guest_sum(0, 11), guest_max(1, (4, 17, 2, 9, 9)), guest_mulacc(2, 7, 3))
    h4 = heldout_workloads()[0].guests
    return (
        {
            "name": "k1",
            "n_guests": 1,
            "train": [_slice_workload("curr_k1_train", "train", g1)],
            "heldout": [_slice_workload("curr_k1_held", "heldout", h1)],
        },
        {
            "name": "k2",
            "n_guests": 2,
            "train": [_slice_workload("curr_k2_train", "train", g2)],
            "heldout": [_slice_workload("curr_k2_held", "heldout", h2)],
        },
        {
            "name": "k3",
            "n_guests": 3,
            "train": [_slice_workload("curr_k3_train", "train", g3)],
            "heldout": [_slice_workload("curr_k3_held", "heldout", h3)],
        },
        {
            "name": "k4",
            "n_guests": 4,
            "train": [_slice_workload("curr_k4_train", "train", g4)],
            "heldout": [_slice_workload("curr_k4_held", "heldout", h4)],
        },
        {
            "name": "k4_hard",
            "n_guests": 4,
            "train": [_slice_workload("curr_k4h_train", "train", hard)],
            "heldout": list(heldout_workloads()),
        },
    )


def isolation_workload() -> Workload:
    victim = GUEST_SLOTS[1][0] + 2
    return Workload(
        name="isolation_probe",
        split="train",
        guests=(
            guest_malicious_store(0, victim),
            guest_sum(1, 4),
            guest_fill(2, 3),
            guest_xor(3, (1, 2, 3)),
        ),
    )
