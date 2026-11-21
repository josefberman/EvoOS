from evoos.boot.pack import persist_oneshot_storage
from evoos.evaluator.persist import (
    classify_disk,
    evaluate_persist,
    evaluate_stage,
    promote,
    score_persist,
)
from evoos.experiments.scheduling.baselines import persist_rr, round_robin_live
from evoos.vm.instruction_set import Op, assemble, dumps_program, loads_program, program_version


def test_program_version_2_roundtrip():
    code = assemble(
        """
        MOVI r1, 0
        MOVI r2, 0
        BLKRD r3, r1, r2
        BLKWR r1, r2
        SVC r1
        HALT
        """
    )
    assert program_version(code) == 2
    assert loads_program(dumps_program(code)) == code
    assert any(i.op == Op.BLKRD for i in code)


def test_v1_program_still_version_1():
    code = assemble("MOVI r1, 1\nHALT")
    blob = dumps_program(code)
    assert blob[4] == 1
    assert loads_program(blob) == code


def test_persist_rr_isolates_and_survives_restart():
    r = evaluate_persist(persist_rr().instrs)
    assert r["isolation_ok"]
    assert not r["leak"]
    assert r["correct"] == 2
    assert r["got"] == [42, 99]
    assert r["n_read_get"] > 0
    assert score_persist(r) > 0.7
    label = classify_disk(r["disk"])
    assert label in ("dense-table", "sparse-hash-like", "append-log-like", "other")


def test_rr_live_does_not_persist():
    r = evaluate_persist(round_robin_live().instrs)
    assert r["correct"] == 0
    assert not r["leak"]


def test_cross_guest_same_name_is_not_a_leak_if_namespaced():
    r = evaluate_persist(persist_rr().instrs)
    assert r["got"][0] != r["got"][1]


def test_dropped_write_is_not_a_cross_guest_leak():
    r = evaluate_persist(persist_rr().instrs, crash_write=True)
    assert r["isolation_ok"]
    assert not r["leak"]
    assert r["correct"] < 2


def test_corrupt_unused_block_still_recalls():
    r = evaluate_persist(persist_rr().instrs, corrupt=True)
    assert r["isolation_ok"]
    assert r["correct"] == 2


def test_hardcoded_get_without_device_gets_no_gated_credit():
    """MOVI the expected payload after wipe is not a store."""
    src = """
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
        RUNCTXI r1, 32
        CTXGET r4, r1, DATA_BASE
        MOVI r5, 42
        ADDI r6, r4, 2
        STORE r6, r5, 0
        JMP main
    skip:
        ADDI r1, r1, 1
        CTXGET r7, r0, N_CONTEXTS
        BLT r1, r7, round
        JMP main
    """
    r = evaluate_persist(assemble(src))
    assert r["raw_match"] >= 1 or r["got"][0] in (0, 42, 0x5A5A5A5A)
    assert r["correct"] == 0


def test_curriculum_a_persist_rr_and_promotion():
    r = evaluate_stage(persist_rr().instrs, "A")
    assert r["correct"] == 1
    assert r["n_read_get"] > 0
    assert promote(r)


def test_persist_oneshot_drops_scheduler_keeps_blk():
    h = persist_oneshot_storage(persist_rr().instrs)
    assert any(i.op == Op.BLKWR for i in h)
    assert any(i.op == Op.BLKRD for i in h)
    assert h[-1].op == Op.HALT
    assert not any(i.op in (Op.RUNCTX, Op.RUNCTXI) for i in h)


def test_curriculum_d_persist_rr():
    r = evaluate_stage(persist_rr().instrs, "D")
    assert r["correct"] == 2
    assert r["isolation_ok"]
    assert promote(r)
