"""Dataflow-aware linear GP helpers.

These are generic genetic-programming techniques (effective-code mutation,
use-def operand bias, runtime constant pools). They are not OS priors: they
do not insert run queues, skip-halted loops, or named scheduler gadgets.

Effective code is the backward slice of instructions that can influence a
side effect (STORE, ADDPROT, CLRPROT, RUNCTX/I, CTXSET, HALT). Operand
mutation prefers registers that an earlier instruction defined. Immediates
are sampled from values observed at runtime (CTXGET results, firmware
sizes) when a pool is provided.
"""

from __future__ import annotations

from evoos.vm.instruction_set import N_REGS, Instr, Op


DATAFLOW_DOCS = __doc__

# Instructions whose result is written to regs[a].
_DEST_A = {
    int(Op.MOV),
    int(Op.MOVI),
    int(Op.ADD),
    int(Op.SUB),
    int(Op.MUL),
    int(Op.AND),
    int(Op.OR),
    int(Op.XOR),
    int(Op.SHL),
    int(Op.SHR),
    int(Op.NOT),
    int(Op.ADDI),
    int(Op.LOAD),
    int(Op.CMP),
    int(Op.JAL),
    int(Op.CTXGET),
    int(Op.RDTIME),
    int(Op.RDFAULT),
    int(Op.BLKRD),
}

# Hardware / memory side effects — the program "does something."
_SIDE = {
    int(Op.STORE),
    int(Op.ADDPROT),
    int(Op.CLRPROT),
    int(Op.RUNCTX),
    int(Op.RUNCTXI),
    int(Op.CTXSET),
    int(Op.HALT),
    int(Op.BLKRD),
    int(Op.BLKWR),
    int(Op.SVC),
}


def dest_reg(ins: Instr) -> int | None:
    if ins.op in _DEST_A and 0 <= ins.a < N_REGS:
        return ins.a
    return None


def use_regs(ins: Instr) -> tuple[int, ...]:
    op = ins.op
    used: list[int] = []
    if op == int(Op.MOVI):
        return ()
    if op in (int(Op.MOV), int(Op.NOT), int(Op.ADDI), int(Op.LOAD), int(Op.RDTIME), int(Op.RDFAULT)):
        used.append(ins.b)
    elif op in (
        int(Op.ADD),
        int(Op.SUB),
        int(Op.MUL),
        int(Op.AND),
        int(Op.OR),
        int(Op.XOR),
        int(Op.SHL),
        int(Op.SHR),
        int(Op.CMP),
    ):
        used.extend((ins.b, ins.c))
    elif op == int(Op.STORE):
        used.extend((ins.a, ins.b))
    elif op in (int(Op.BEQ), int(Op.BNE), int(Op.BLT), int(Op.BGE)):
        used.extend((ins.a, ins.b))
    elif op == int(Op.JR):
        used.append(ins.a)
    elif op == int(Op.RUNCTX):
        used.extend((ins.a, ins.b))
    elif op == int(Op.RUNCTXI):
        used.append(ins.a)
    elif op == int(Op.CTXGET):
        used.append(ins.b)
    elif op == int(Op.CTXSET):
        used.extend((ins.a, ins.b))
    elif op == int(Op.ADDPROT):
        used.extend((ins.a, ins.b, ins.c))
    elif op == int(Op.CLRPROT):
        used.append(ins.a)
    elif op == int(Op.BLKRD):
        used.extend((ins.b, ins.c))
    elif op == int(Op.BLKWR):
        used.extend((ins.a, ins.b))
    return tuple(r for r in used if 0 <= r < N_REGS)


def last_defs(instrs: tuple[Instr, ...] | list[Instr]) -> dict[int, int]:
    """Register -> index of the last instruction that defines it."""
    last: dict[int, int] = {}
    for i, ins in enumerate(instrs):
        d = dest_reg(ins)
        if d is not None:
            last[d] = i
    return last


def effective_indices(instrs: tuple[Instr, ...] | list[Instr]) -> frozenset[int]:
    """Backward slice from side-effecting instructions (linear, no jump-sensitive)."""
    n = len(instrs)
    if n == 0:
        return frozenset()
    live: set[int] = set()
    eff: set[int] = set()
    for i in range(n - 1, -1, -1):
        ins = instrs[i]
        d = dest_reg(ins)
        side = ins.op in _SIDE
        used = use_regs(ins)
        if side or (d is not None and d in live):
            eff.add(i)
            if d is not None:
                live.discard(d)
            live.update(used)
        elif ins.op in (int(Op.BEQ), int(Op.BNE), int(Op.BLT), int(Op.BGE), int(Op.JMP), int(Op.JAL), int(Op.JR)):
            # Control flow can enable later side effects; mark it effective.
            eff.add(i)
            live.update(used)
    return frozenset(eff)


def pick_index(
    n: int,
    rng,
    effective: frozenset[int] | None,
    *,
    prefer: float = 0.8,
) -> int:
    if n <= 0:
        return 0
    if effective:
        xs = [i for i in effective if 0 <= i < n]
        if xs and rng.random() < prefer:
            return xs[rng.randrange(len(xs))]
    return rng.randrange(n)


def pick_defined_reg(last: dict[int, int], before: int, rng, fallback: int | None = None) -> int:
    defined = [r for r, i in last.items() if i < before]
    if defined and rng.random() < 0.75:
        return defined[rng.randrange(len(defined))]
    if fallback is not None:
        return fallback
    return rng.randrange(N_REGS)


def pick_const(pool: tuple[int, ...] | list[int] | None, rng, fallback: int) -> int:
    if pool and rng.random() < 0.6:
        return int(pool[rng.randrange(len(pool))])
    return fallback


def firmware_const_pool(*, n_guests: int, mem_words: int, max_steps: int) -> tuple[int, ...]:
    """Values the supervisor can observe without executing: firmware + slot layout."""
    from evoos.evaluator.workloads import GUEST_SLOTS
    from evoos.vm.instruction_set import FW_GUEST_TABLE, MAGIC

    vals = {
        0,
        1,
        n_guests,
        n_guests + 1,
        mem_words,
        max_steps,
        MAGIC,
        FW_GUEST_TABLE,
        8,
        16,
        32,
        64,
        128,
        256,
    }
    for lo, hi in GUEST_SLOTS[: max(n_guests, 1)]:
        vals.add(lo)
        vals.add(hi)
        vals.add(hi - lo)
    # C-hw RAM frames (4 × 32 words starting at 256). Hardware layout, not a pager.
    for i in range(4):
        vals.add(256 + 32 * i)
        vals.add(256 + 32 * (i + 1))
    for f in range(16):  # CtxField 0..15
        vals.add(f)
    return tuple(sorted(vals))
