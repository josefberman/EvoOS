"""Instruction-level mutations. No OS-level operators — only bytecode edits."""

from __future__ import annotations

import random

from evoos.vm.instruction_set import IMM_MAX, IMM_MIN, N_REGS, CtxField, Instr, Op

from evoos.agent.program import Candidate


# Bytecode edits. None of these is a scheduler: no run queue, no time-slice
# policy, no skip-halted / until-idle outer loop.
BARE_OPS = (
    "insert",
    "remove",
    "modify_op",
    "modify_operand",
    "modify_imm",
    "duplicate_block",
    "swap",
    "insert_gadget",
    "crossover",
    "control_flow",
    "revert_mutate",
)
GADGET_OPS = BARE_OPS + (
    "insert_mmu_gadget",
    "insert_runctx_gadget",
    "insert_blk_gadget",
)
FULL_OPS = GADGET_OPS + ("insert_ctx_scan",)

OPERATOR_PACKS = {
    "bare": BARE_OPS,  # instruction edits only; no ISA-manual sequences
    "gadgets": GADGET_OPS,  # + MMU / RUNCTX calling-convention snippets
    "full": FULL_OPS,  # + loop that walks hardware context ids
}

MUTATION_OPS = FULL_OPS

OPERATOR_DOCS = {
    "insert": "Insert one random ISA instruction.",
    "remove": "Delete one instruction.",
    "modify_op": "Replace an opcode, keep operands.",
    "modify_operand": "Replace a register field.",
    "modify_imm": "Nudge an immediate.",
    "duplicate_block": "Copy a short subsequence.",
    "swap": "Swap two instructions.",
    "insert_gadget": "Insert 2–5 random ISA instructions (not a named sequence).",
    "crossover": "Splice two parents.",
    "control_flow": "Replace one instruction with a random jump/branch.",
    "revert_mutate": "Take a hall-of-fame program and mutate it.",
    "insert_mmu_gadget": (
        "ISA calling convention for protection registers: "
        "MOVI ctx; CTXGET DATA_BASE; CTXGET DATA_BOUND; ADDPROT. "
        "Not a scheduler. ctx is a single random id, not a loop over guests."
    ),
    "insert_runctx_gadget": (
        "ISA calling convention for RUNCTXI: MOVI ctx; RUNCTXI steps. "
        "One context, one burst. Not round-robin / not a run queue."
    ),
    "insert_blk_gadget": (
        "ISA calling convention for the block device: MOVI block; MOVI addr; "
        "BLKRD or BLKWR. Not a namespace, not a journal."
    ),
    "insert_ctx_scan": (
        "Hardware enumeration: MOVI r,1; <one body instr>; ADDI r,1; "
        "CTXGET N_CONTEXTS; BLT r, n, body. Encodes 'the machine reports how "
        "many contexts it has; you can walk ids'. Body is NOP or one rewritten "
        "parent instruction — not skip-if-halted, not quantum, not until-idle. "
        "That extra control flow, if it appears, must still be discovered."
    ),
}


# HALT in the middle of a random program collapses the rest; it is appended
# separately. Privileged ops are slightly oversampled because they exist on
# the machine — this is not a scheduler skeleton.
_OPS = tuple(int(op) for op in Op if op != Op.HALT)
_BIASED_OPS = _OPS + (
    int(Op.MOVI),
    int(Op.ADDI),
    int(Op.CTXGET),
    int(Op.ADDPROT),
    int(Op.RUNCTXI),
    int(Op.RUNCTX),
    int(Op.BLKRD),
    int(Op.BLKWR),
    int(Op.SVC),
    int(Op.BLT),
    int(Op.JMP),
)


def mmu_gadget(rng: random.Random) -> tuple[Instr, ...]:
    """ISA-manual sequence: load a context id, read its data window, ADDPROT.

    This is the calling convention of the MMU registers, not a scheduler.
    """
    ra, rb, rc = rng.sample(range(1, N_REGS), 3)
    ctx = rng.randint(1, 4)
    return (
        Instr(int(Op.MOVI), ra, 0, 0, ctx),
        Instr(int(Op.CTXGET), rb, ra, 0, int(CtxField.DATA_BASE)),
        Instr(int(Op.CTXGET), rc, ra, 0, int(CtxField.DATA_BOUND)),
        Instr(int(Op.ADDPROT), ra, rb, rc, 0),
    )


def blk_gadget(rng: random.Random) -> tuple[Instr, ...]:
    rb = rng.randint(1, N_REGS - 1)
    rm = rng.choice([x for x in range(1, N_REGS) if x != rb])
    blk = rng.randint(0, 31)
    addr = rng.choice([256, 384, 512, 640])
    if rng.random() < 0.5:
        return (
            Instr(int(Op.MOVI), rb, 0, 0, blk),
            Instr(int(Op.MOVI), rm, 0, 0, addr),
            Instr(int(Op.BLKWR), rb, rm, 0, 0),
        )
    rs = rng.choice([x for x in range(1, N_REGS) if x not in (rb, rm)])
    return (
        Instr(int(Op.MOVI), rb, 0, 0, blk),
        Instr(int(Op.MOVI), rm, 0, 0, addr),
        Instr(int(Op.BLKRD), rs, rb, rm, 0),
    )


def runctx_gadget(rng: random.Random) -> tuple[Instr, ...]:
    ra = rng.randint(1, N_REGS - 1)
    ctx = rng.randint(1, 4)
    steps = rng.choice([8, 16, 32, 64, 128, 256])
    return (
        Instr(int(Op.MOVI), ra, 0, 0, ctx),
        Instr(int(Op.RUNCTXI), ra, 0, 0, steps),
    )


def ctx_scan_gadget(rng: random.Random, body: Instr | None = None) -> tuple[Instr, ...]:
    """Walk context ids 1 .. N_CONTEXTS-1. See OPERATOR_DOCS['insert_ctx_scan']."""
    r = rng.randint(1, 5)
    rn = 7 if r != 7 else 6
    if body is None:
        body_ins = Instr(int(Op.NOP), 0, 0, 0, 0)
    else:
        body_ins = Instr(body.op, r, body.b, body.c, body.imm)
    # pc of BLT in this snippet is 4; body is at 1; imm = 1 - (4+1) = -4.
    return (
        Instr(int(Op.MOVI), r, 0, 0, 1),
        body_ins,
        Instr(int(Op.ADDI), r, r, 0, 1),
        Instr(int(Op.CTXGET), rn, 0, 0, int(CtxField.N_CONTEXTS)),
        Instr(int(Op.BLT), r, rn, 0, -4),
    )


def random_instr(rng: random.Random) -> Instr:
    op = rng.choice(_BIASED_OPS)
    # Immediates: mostly small, occasionally firmware-table / data-window scale.
    roll = rng.random()
    if roll < 0.55:
        imm = rng.randint(-8, 32)
    elif roll < 0.8:
        imm = rng.choice([0, 1, 2, 3, 4, 5, 8, 16, 32, 64, 256, 384, 512, 640])
    else:
        imm = rng.randint(-64, 128)
    imm = max(IMM_MIN, min(IMM_MAX, imm))
    return Instr(op, rng.randrange(N_REGS), rng.randrange(N_REGS), rng.randrange(N_REGS), imm)


def random_program(
    rng: random.Random,
    length: int,
    pack: str = "gadgets",
) -> tuple[Instr, ...]:
    length = max(1, length)
    ops = OPERATOR_PACKS.get(pack, GADGET_OPS)
    body: list[Instr] = []
    if "insert_mmu_gadget" in ops and rng.random() < 0.55:
        body.extend(mmu_gadget(rng))
    if "insert_runctx_gadget" in ops and rng.random() < 0.55:
        body.extend(runctx_gadget(rng))
    if "insert_blk_gadget" in ops and rng.random() < 0.4:
        body.extend(blk_gadget(rng))
    if "insert_ctx_scan" in ops and rng.random() < 0.35:
        body.extend(ctx_scan_gadget(rng))
    body.extend(random_instr(rng) for _ in range(length))
    if rng.random() < 0.7:
        body.append(Instr(int(Op.HALT), 0, 0, 0, 0))
    return tuple(body)


def _splice(seq: tuple[Instr, ...], i: int, j: int, insert: tuple[Instr, ...] = ()) -> tuple[Instr, ...]:
    return seq[:i] + insert + seq[j:]


def mutate(
    parent: Candidate,
    op: str,
    rng: random.Random,
    *,
    hall: list[Candidate] | None = None,
    other: Candidate | None = None,
    max_len: int = 256,
    operators: tuple[str, ...] | None = None,
) -> Candidate:
    seq = parent.instrs
    n = len(seq)
    origin = op

    if op == "insert" or n == 0:
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, (random_instr(rng),))
    elif op == "remove" and n > 1:
        i = rng.randrange(n)
        seq = _splice(seq, i, i + 1)
    elif op == "modify_op" and n:
        i = rng.randrange(n)
        ins = seq[i]
        seq = seq[:i] + (Instr(rng.choice(_OPS), ins.a, ins.b, ins.c, ins.imm),) + seq[i + 1 :]
    elif op == "modify_operand" and n:
        i = rng.randrange(n)
        ins = seq[i]
        which = rng.choice(("a", "b", "c"))
        val = rng.randrange(N_REGS)
        kwargs = {"a": ins.a, "b": ins.b, "c": ins.c}
        kwargs[which] = val
        seq = seq[:i] + (Instr(ins.op, kwargs["a"], kwargs["b"], kwargs["c"], ins.imm),) + seq[i + 1 :]
    elif op == "modify_imm" and n:
        i = rng.randrange(n)
        ins = seq[i]
        delta = rng.choice([-16, -4, -1, 1, 4, 16, rng.randint(-32, 32)])
        seq = seq[:i] + (Instr(ins.op, ins.a, ins.b, ins.c, ins.imm + delta),) + seq[i + 1 :]
    elif op == "duplicate_block" and n:
        i = rng.randrange(n)
        j = rng.randrange(i, min(n, i + 6) + 1)
        k = rng.randrange(n + 1)
        seq = seq[:k] + seq[i:j] + seq[k:]
    elif op == "swap" and n >= 2:
        i, j = rng.sample(range(n), 2)
        lst = list(seq)
        lst[i], lst[j] = lst[j], lst[i]
        seq = tuple(lst)
    elif op == "insert_gadget":
        gadget = tuple(random_instr(rng) for _ in range(rng.randint(2, 5)))
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, gadget)
    elif op == "insert_mmu_gadget":
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, mmu_gadget(rng))
        origin = "insert_mmu_gadget"
    elif op == "insert_runctx_gadget":
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, runctx_gadget(rng))
        origin = "insert_runctx_gadget"
    elif op == "insert_blk_gadget":
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, blk_gadget(rng))
        origin = "insert_blk_gadget"
    elif op == "insert_ctx_scan":
        body = seq[rng.randrange(n)] if n and rng.random() < 0.5 else None
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, ctx_scan_gadget(rng, body))
        origin = "insert_ctx_scan"
    elif op == "crossover" and other is not None and other.instrs:
        cut_a = rng.randrange(n + 1) if n else 0
        cut_b = rng.randrange(len(other.instrs) + 1)
        if rng.random() < 0.5:
            seq = parent.instrs[:cut_a] + other.instrs[cut_b:]
        else:
            seq = other.instrs[:cut_b] + parent.instrs[cut_a:]
        origin = "crossover"
    elif op == "control_flow" and n:
        i = rng.randrange(n)
        ins = seq[i]
        cf = rng.choice([Op.JMP, Op.BEQ, Op.BNE, Op.BLT, Op.BGE, Op.JAL])
        rel = rng.randint(-n, n)
        seq = seq[:i] + (Instr(int(cf), ins.a, ins.b, ins.c, rel),) + seq[i + 1 :]
    elif op == "revert_mutate" and hall:
        allowed = operators or MUTATION_OPS
        choices = [o for o in allowed if o != "revert_mutate"]
        base = rng.choice(hall)
        return mutate(
            base,
            rng.choice(choices or ("insert",)),
            rng,
            hall=hall,
            other=other,
            max_len=max_len,
            operators=allowed,
        )
    else:
        i = rng.randrange(n + 1) if n else 0
        seq = _splice(seq, i, i, (random_instr(rng),))
        origin = "insert"

    if len(seq) > max_len:
        seq = seq[:max_len]
    if not seq:
        seq = (Instr(int(Op.HALT), 0, 0, 0, 0),)
    return Candidate(instrs=seq, origin=origin, parent_id=parent.id)
