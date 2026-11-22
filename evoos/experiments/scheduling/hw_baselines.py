"""Hand-written C-hw supervisors: FIFO swap + RR, and direct-copy routing.

These are the hardware-manual analogues of Condition A. They use BLKRD/BLKWR,
ADDPROT/CLRPROT, CTXGET/CTXSET, RUNCTXI, and SVC — not a pager or a pipe opcode.
"""

from __future__ import annotations

from evoos.agent.program import Candidate
from evoos.evaluator.hw_workloads import FRAME, FRAME0, N_FRAMES
from evoos.vm.instruction_set import assemble


def fifo_swap_rr(quantum: int = 32) -> Candidate:
    """4 RAM frames, FIFO victim, round-robin RUNCTXI. Places any unmapped guest."""
    src = f"""
        MOVI r6, 0
        STORE r0, r6, 199

    main:
        MOVI r1, 1
    round:
        CTXGET r2, r1, HALTED
        BNE r2, r0, next
        CTXGET r2, r1, FAULT
        BNE r2, r0, next
        CTXGET r2, r1, DATA_BASE
        BNE r2, r0, run

        LOAD r3, r0, 199
        MOVI r4, {N_FRAMES}
        # victim frame r3; occupant at 200+r3
        MOVI r5, 200
        ADD r5, r5, r3
        LOAD r7, r5, 0
        BEQ r7, r0, place
        CTXGET r6, r7, DATA_BASE
        BEQ r6, r0, place
        ADDI r4, r7, -1
        BLKWR r4, r6
        CLRPROT r7
        MOVI r4, 0
        CTXSET r7, r4, DATA_BASE
        CTXSET r7, r4, DATA_BOUND
        STORE r5, r4, 0
    place:
        MOVI r6, {FRAME}
        MUL r6, r3, r6
        ADDI r6, r6, {FRAME0}
        ADDI r4, r1, -1
        BLKRD r0, r4, r6
        CTXSET r1, r6, DATA_BASE
        MOVI r7, {FRAME}
        ADD r7, r6, r7
        CTXSET r1, r7, DATA_BOUND
        ADDPROT r1, r6, r7
        STORE r5, r1, 0
        LOAD r3, r0, 199
        ADDI r3, r3, 1
        MOVI r4, {N_FRAMES}
        BLT r3, r4, keepclk
        MOVI r3, 0
    keepclk:
        STORE r0, r3, 199

    run:
        RUNCTXI r1, {int(quantum)}
        CTXGET r2, r0, LAST_SVC
        MOVI r3, 1
        BNE r2, r3, notput
        CTXGET r4, r0, LAST_SVC_CTX
        CTXGET r5, r4, DATA_BASE
        LOAD r6, r5, 0
        MOVI r7, 180
        ADD r7, r7, r4
        STORE r7, r6, 0
        MOVI r2, 0
        CTXSET r0, r2, LAST_SVC
        JMP next
    notput:
        MOVI r3, 2
        BNE r2, r3, next
        CTXGET r4, r0, LAST_SVC_CTX
        MOVI r5, 1
        SUB r5, r4, r5
        MOVI r7, 180
        ADD r7, r7, r5
        LOAD r6, r7, 0
        CTXGET r5, r4, DATA_BASE
        STORE r5, r6, 0
        MOVI r2, 0
        CTXSET r0, r2, LAST_SVC
    next:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        JMP main
    """
    return Candidate(instrs=assemble(src), origin="baseline_fifo_rr", id=-10)


def place_first_frames_rr(quantum: int = 32) -> Candidate:
    """RR with at most N_FRAMES residents. No BLKWR/evict — pressure should stall."""
    src = f"""
        MOVI r6, 0
        STORE r0, r6, 199
    main:
        MOVI r1, 1
    round:
        CTXGET r2, r1, HALTED
        BNE r2, r0, next
        CTXGET r2, r1, FAULT
        BNE r2, r0, next
        CTXGET r2, r1, DATA_BASE
        BNE r2, r0, run
        LOAD r3, r0, 199
        MOVI r4, {N_FRAMES}
        BLT r3, r4, place
        JMP next
    place:
        MOVI r6, {FRAME}
        MUL r6, r3, r6
        ADDI r6, r6, {FRAME0}
        ADDI r4, r1, -1
        BLKRD r0, r4, r6
        CTXSET r1, r6, DATA_BASE
        MOVI r7, {FRAME}
        ADD r7, r6, r7
        CTXSET r1, r7, DATA_BOUND
        ADDPROT r1, r6, r7
        ADDI r3, r3, 1
        STORE r0, r3, 199
    run:
        RUNCTXI r1, {int(quantum)}
        CTXGET r2, r0, LAST_SVC
        MOVI r3, 1
        BNE r2, r3, notput
        CTXGET r4, r0, LAST_SVC_CTX
        CTXGET r5, r4, DATA_BASE
        LOAD r6, r5, 0
        MOVI r7, 180
        ADD r7, r7, r4
        STORE r7, r6, 0
        MOVI r2, 0
        CTXSET r0, r2, LAST_SVC
        JMP next
    notput:
        MOVI r3, 2
        BNE r2, r3, next
        CTXGET r4, r0, LAST_SVC_CTX
        MOVI r5, 1
        SUB r5, r4, r5
        MOVI r7, 180
        ADD r7, r7, r5
        LOAD r6, r7, 0
        CTXGET r5, r4, DATA_BASE
        STORE r5, r6, 0
        MOVI r2, 0
        CTXSET r0, r2, LAST_SVC
    next:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        JMP main
    """
    return Candidate(instrs=assemble(src), origin="baseline_place_only", id=-11)


def fifo_swap_deaf(quantum: int = 32) -> Candidate:
    """FIFO swap + RR that ignores LAST_SVC. Pressure can complete; routing cannot."""
    src = f"""
        MOVI r6, 0
        STORE r0, r6, 199
    main:
        MOVI r1, 1
    round:
        CTXGET r2, r1, HALTED
        BNE r2, r0, next
        CTXGET r2, r1, FAULT
        BNE r2, r0, next
        CTXGET r2, r1, DATA_BASE
        BNE r2, r0, run
        LOAD r3, r0, 199
        MOVI r4, {N_FRAMES}
        MOVI r5, 200
        ADD r5, r5, r3
        LOAD r7, r5, 0
        BEQ r7, r0, place
        CTXGET r6, r7, DATA_BASE
        BEQ r6, r0, place
        ADDI r4, r7, -1
        BLKWR r4, r6
        CLRPROT r7
        MOVI r4, 0
        CTXSET r7, r4, DATA_BASE
        CTXSET r7, r4, DATA_BOUND
        STORE r5, r4, 0
    place:
        MOVI r6, {FRAME}
        MUL r6, r3, r6
        ADDI r6, r6, {FRAME0}
        ADDI r4, r1, -1
        BLKRD r0, r4, r6
        CTXSET r1, r6, DATA_BASE
        MOVI r7, {FRAME}
        ADD r7, r6, r7
        CTXSET r1, r7, DATA_BOUND
        ADDPROT r1, r6, r7
        STORE r5, r1, 0
        LOAD r3, r0, 199
        ADDI r3, r3, 1
        MOVI r4, {N_FRAMES}
        BLT r3, r4, keepclk
        MOVI r3, 0
    keepclk:
        STORE r0, r3, 199
    run:
        RUNCTXI r1, {int(quantum)}
    next:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        JMP main
    """
    return Candidate(instrs=assemble(src), origin="baseline_fifo_deaf", id=-12)


def direct_copy_router(quantum: int = 32) -> Candidate:
    """FIFO place + RR + copy PUT token into the paired consumer window."""
    return fifo_swap_rr(quantum=quantum)


def payload_copy_rr(quantum: int = 32, n_frames: int = 8, n_words: int = 4) -> Candidate:
    """Place-all (up to n_frames) + copy n_words from producer window to consumer.

    This is the hand-written direct-copy baseline for the routing curriculum.
    Not a pipe opcode: LOAD/STORE after LAST_SVC.
    """
    copies = []
    for off in range(int(n_words)):
        copies.append(f"        LOAD r6, r5, {off}")
        copies.append(f"        STORE r7, r6, {off}")
    copy_src = "\n".join(copies)
    src = f"""
        MOVI r6, 0
        STORE r0, r6, 199
    main:
        MOVI r1, 1
    round:
        CTXGET r2, r1, HALTED
        BNE r2, r0, next
        CTXGET r2, r1, FAULT
        BNE r2, r0, next
        CTXGET r2, r1, DATA_BASE
        BNE r2, r0, run
        LOAD r3, r0, 199
        MOVI r4, {int(n_frames)}
        BLT r3, r4, place
        JMP next
    place:
        MOVI r6, {FRAME}
        MUL r6, r3, r6
        ADDI r6, r6, {FRAME0}
        ADDI r4, r1, -1
        BLKRD r0, r4, r6
        CTXSET r1, r6, DATA_BASE
        MOVI r7, {FRAME}
        ADD r7, r6, r7
        CTXSET r1, r7, DATA_BOUND
        ADDPROT r1, r6, r7
        ADDI r3, r3, 1
        STORE r0, r3, 199
    run:
        RUNCTXI r1, {int(quantum)}
        CTXGET r2, r0, LAST_SVC
        MOVI r3, 1
        BNE r2, r3, notput
        CTXGET r4, r0, LAST_SVC_CTX
        CTXGET r5, r4, DATA_BASE
        MOVI r7, 160
        MOVI r3, 4
        MUL r3, r4, r3
        ADD r7, r7, r3
{copy_src}
        MOVI r2, 0
        CTXSET r0, r2, LAST_SVC
        JMP next
    notput:
        MOVI r3, 2
        BNE r2, r3, next
        CTXGET r4, r0, LAST_SVC_CTX
        MOVI r5, 1
        SUB r5, r4, r5
        MOVI r7, 160
        MOVI r3, 4
        MUL r3, r5, r3
        ADD r7, r7, r3
        CTXGET r5, r4, DATA_BASE
        BEQ r5, r0, next
        MOV r5, r7
        CTXGET r7, r4, DATA_BASE
{copy_src}
        MOVI r2, 0
        CTXSET r0, r2, LAST_SVC
    next:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        JMP main
    """
    return Candidate(instrs=assemble(src), origin="baseline_payload_copy", id=-13)
