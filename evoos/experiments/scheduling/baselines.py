"""Hand-written supervisors in the *same* bytecode as evolved candidates.

These are Condition A (conventional). They use only ISA primitives — there is
still no `schedule_process` opcode — but a human chose the organization.
"""

from __future__ import annotations

from evoos.agent.program import Candidate
from evoos.vm.instruction_set import assemble


def run_to_completion() -> Candidate:
    """Grant each guest its data window, then run it until it yields back."""
    src = """
        MOVI r1, 1
    setup:
        CTXGET r2, r1, DATA_BASE
        CTXGET r3, r1, DATA_BOUND
        ADDPROT r1, r2, r3
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, setup

        MOVI r1, 1
    run:
        RUNCTXI r1, 4000
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, run
        HALT
    """
    return Candidate(instrs=assemble(src), origin="baseline_rtc", id=-1)


def round_robin_live(quantum: int = 16) -> Candidate:
    """Same policy as round_robin, but the outer loop never HALTs.

    Idle rounds JMP back to `main` so newly installed (unhalted) guests are
    noticed. That is still bytecode policy, not a firmware scheduler.
    """
    src = f"""
        MOVI r1, 1
    setup:
        CTXGET r2, r1, DATA_BASE
        CTXGET r3, r1, DATA_BOUND
        ADDPROT r1, r2, r3
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, setup

    main:
        MOVI r1, 1
    round:
        CTXGET r2, r1, HALTED
        BNE r2, r0, skip
        CTXGET r2, r1, FAULT
        BNE r2, r0, skip
        RUNCTXI r1, {int(quantum)}
    skip:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        JMP main
    """
    return Candidate(instrs=assemble(src), origin=f"baseline_rr_live_q{quantum}", id=-5)


def round_robin(quantum: int = 16) -> Candidate:
    """Grant each guest its data window, then rotate RUNCTXI quanta."""
    src = f"""
        MOVI r1, 1
    setup:
        CTXGET r2, r1, DATA_BASE
        CTXGET r3, r1, DATA_BOUND
        ADDPROT r1, r2, r3
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, setup

    main:
        MOVI r1, 1
        MOVI r5, 0
    round:
        CTXGET r2, r1, HALTED
        BNE r2, r0, skip
        CTXGET r2, r1, FAULT
        BNE r2, r0, skip
        RUNCTXI r1, {int(quantum)}
        MOVI r5, 1
    skip:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        BNE r5, r0, main
        HALT
    """
    return Candidate(instrs=assemble(src), origin=f"baseline_rr_q{quantum}", id=-2)


def selfish_first() -> Candidate:
    """Runs only context 1. Control baseline for starvation."""
    src = """
        MOVI r1, 1
        CTXGET r2, r1, DATA_BASE
        CTXGET r3, r1, DATA_BOUND
        ADDPROT r1, r2, r3
        RUNCTXI r1, 4000
        HALT
    """
    return Candidate(instrs=assemble(src), origin="baseline_selfish", id=-3)


def unprotected_rtc() -> Candidate:
    """Runs guests without ADDPROT. Guests that touch memory should fault."""
    src = """
        MOVI r1, 1
    run:
        RUNCTXI r1, 4000
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, run
        HALT
    """
    return Candidate(instrs=assemble(src), origin="baseline_unprotected", id=-4)


ALL_BASELINES = {
    "run_to_completion": run_to_completion,
    "round_robin": round_robin,
    "round_robin_live": round_robin_live,
    "selfish_first": selfish_first,
    "unprotected_rtc": unprotected_rtc,
}
