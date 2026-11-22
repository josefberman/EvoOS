"""Round 9: generalization variants, third-guest leak, embed."""

from __future__ import annotations

from evoos.evaluator.cases import case_vector
from evoos.evaluator.hw_workloads import (
    generalization_train,
    guest_attacker,
    official_split,
    route_variant,
    route_workload,
)
from evoos.evaluator.share_audit import audit_candidate
from evoos.experiments.scheduling.hw_baselines import payload_copy_rr
from evoos.vm.instruction_set import Op, assemble
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import GuestImage, Sandbox, Workload


def test_opcodes_still_0_34():
    names = [op.name for op in Op]
    assert names[31] == "RDFAULT" and names[34] == "SVC"
    assert len(names) == 35


def test_variant_perm_changes_ctx_ids():
    w0 = route_workload("train", 2, name="p0")
    w1 = route_variant("train", pairs=2, perm=(1, 0, 3, 2), name="p1")
    assert w0.guests[0].role == "producer" and w0.guests[0].pair == 2
    assert w1.guests[0].role == "consumer"
    assert w1.guests[0].pair == 2
    assert w1.guests[1].role == "producer" and w1.guests[1].pair == 1


def test_store_off_expected():
    w = route_variant("train", pairs=1, messages=(7, 8), store_off=2, name="off")
    assert w.guests[1].expected_off == {2: 7, 3: 8}


def test_payload_copy_still_official_4_of_4():
    cand = payload_copy_rr(16)
    sb = Sandbox(lenient_config(n_contexts=9, n_blocks=16, block_words=32, max_steps=6000, blk_latency=2))
    train, held = official_split()
    tr = [sb.evaluate(cand.instrs, w) for w in train]
    ho = [sb.evaluate(cand.instrs, w) for w in held]
    assert all(r.isolation_ok and r.completed == r.n_guests for r in tr + ho)


def test_third_guest_grant_is_a_leak():
    cfg = lenient_config(n_contexts=4, n_blocks=4, block_words=32, max_steps=200)
    src = assemble(
        """
        MOVI r1, 1
        MOVI r2, 256
        CTXSET r1, r2, DATA_BASE
        MOVI r3, 288
        CTXSET r1, r3, DATA_BOUND
        ADDPROT r1, r2, r3
        MOVI r1, 2
        CTXSET r1, r2, DATA_BASE
        CTXSET r1, r3, DATA_BOUND
        ADDPROT r1, r2, r3
        MOVI r1, 3
        ADDPROT r1, r2, r3
        MOVI r1, 1
        RUNCTXI r1, 16
        MOVI r1, 2
        RUNCTXI r1, 16
        HALT
        """
    )
    prod = GuestImage(
        name="p",
        code=assemble("MOVI r3, 99\nSTORE r1, r3, 0\nHALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        role="producer",
        token=99,
        pair=2,
        messages=(99,),
    )
    cons = GuestImage(
        name="c",
        code=assemble("HALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        expected_off={0: 99},
        role="consumer",
        token=99,
        pair=1,
        messages=(99,),
    )
    atk = guest_attacker(2, "store_frames")
    sb = Sandbox(cfg)
    r = sb.evaluate(src, Workload("leak", (prod, cons, atk), kind="route"))
    assert r.share_leak
    assert not r.isolation_ok
    assert not r.attacker_contained


def test_pair_share_still_not_a_leak():
    cfg = lenient_config(n_contexts=3, n_blocks=4, block_words=32, max_steps=200)
    src = assemble(
        """
        MOVI r1, 1
        MOVI r2, 256
        CTXSET r1, r2, DATA_BASE
        MOVI r3, 288
        CTXSET r1, r3, DATA_BOUND
        ADDPROT r1, r2, r3
        MOVI r1, 2
        CTXSET r1, r2, DATA_BASE
        CTXSET r1, r3, DATA_BOUND
        ADDPROT r1, r2, r3
        MOVI r1, 1
        RUNCTXI r1, 16
        MOVI r1, 2
        RUNCTXI r1, 16
        HALT
        """
    )
    prod = GuestImage(
        name="p",
        code=assemble("MOVI r3, 99\nSTORE r1, r3, 0\nHALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        role="producer",
        token=99,
        pair=2,
        messages=(99,),
    )
    cons = GuestImage(
        name="c",
        code=assemble("HALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        expected_off={0: 99},
        role="consumer",
        token=99,
        pair=1,
        messages=(99,),
    )
    sb = Sandbox(cfg)
    r = sb.evaluate(src, Workload("share", (prod, cons), kind="route"))
    assert r.isolation_ok
    assert not r.share_leak
    assert r.shared_window
    assert r.delivered == 1


def test_lexicase_has_share_and_variant_cases():
    cand = payload_copy_rr(16)
    sb = Sandbox(lenient_config(n_contexts=9, n_blocks=16, block_words=32, max_steps=4000, blk_latency=2))
    w = generalization_train()[2]
    r = sb.evaluate(cand.instrs, w)
    names, vec = case_vector([r], gated=True)
    assert any(n.endswith(".share_ok") for n in names)
    assert any(n.endswith(".revoked") for n in names)


def test_payload_copy_audit_no_leak():
    cand = payload_copy_rr(16)
    sb = Sandbox(lenient_config(n_contexts=9, n_blocks=16, block_words=32, max_steps=6000, blk_latency=2))
    aud = audit_candidate(sb, cand)
    assert aud["n_leak"] == 0
    assert aud["pass"]
