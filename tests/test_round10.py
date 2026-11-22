"""Round 10: in-search leak invalidation, rotating audit, held-out disjointness."""

from __future__ import annotations

from evoos.agent.evolutionary_search import INVALID_REWARD, SearchConfig, evaluate_candidate
from evoos.evaluator.hw_workloads import (
    attach_attacker,
    heldout_audit_workloads,
    official_split,
    route_workload,
    sample_train_audit,
    train_audit_bank,
)
from evoos.evaluator.novelty import NoveltyArchive
from evoos.evaluator.reward import RewardWeights
from evoos.evaluator.share_audit import admission_audit_candidate
from evoos.vm.sandbox import ON_DEVICE_N_CONTEXTS
from evoos.experiments.scheduling.hw_baselines import payload_copy_rr
from evoos.vm.instruction_set import Op, assemble
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import GuestImage, Sandbox, Workload


def test_opcodes_still_0_34():
    names = [op.name for op in Op]
    assert names[31] == "RDFAULT" and names[34] == "SVC"
    assert len(names) == 35


def test_train_bank_disjoint_from_heldout_audit():
    train_names = {w.name for w in train_audit_bank()}
    hold_names = {w.name for w in heldout_audit_workloads()}
    assert train_names.isdisjoint(hold_names)
    assert all(n.startswith("aud_train_") for n in train_names)
    assert all(n.startswith("aud_hold_") for n in hold_names)


def test_heldout_audit_uses_offset_poke_and_mid_insert():
    names = {w.name: w for w in heldout_audit_workloads()}
    mid = names["aud_hold_pair_mid_store"]
    assert mid.guests[1].role == "attacker"
    assert mid.guests[0].role in ("producer", "consumer")
    first = names["aud_hold_pair_first_load"]
    assert first.guests[0].role == "attacker"
    off = names["aud_hold_r2_offbase"]
    imms = {ins.imm for ins in off.guests[-1].code}
    assert {272, 304, 336, 368} <= imms
    assert 256 not in imms


def test_sample_audit_rotates_and_is_deterministic():
    bank = train_audit_bank()
    a0 = [w.name for w in sample_train_audit(bank, 2, seed=1, gen=0)]
    b0 = [w.name for w in sample_train_audit(bank, 2, seed=1, gen=0)]
    a1 = [w.name for w in sample_train_audit(bank, 2, seed=1, gen=1)]
    assert a0 == b0
    assert len(a0) == 2
    # Different generations must be allowed to pick a different pair.
    gens = {tuple(w.name for w in sample_train_audit(bank, 2, seed=1, gen=g)) for g in range(12)}
    assert len(gens) > 1
    assert tuple(a1) in gens


def test_attach_attacker_sets_n_attackers():
    sb = Sandbox(lenient_config(n_contexts=6, n_blocks=8, block_words=32, max_steps=200, blk_latency=2))
    w = attach_attacker(route_workload("train", 1), "store_frames", name="t_atk")
    r = sb.evaluate(payload_copy_rr(8).instrs, w)
    assert r.n_attackers == 1
    assert w.guests[-1].role == "attacker"


def test_pair_only_grant_passes_eight_ctx_admission():
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
    from evoos.agent.program import Candidate

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
        expected_off={0: 99},
        expected={},
        role="consumer",
        token=99,
        pair=1,
        messages=(99,),
    )
    cand = Candidate(instrs=src, origin="pair_only", id=1)
    sb = Sandbox(
        lenient_config(
            n_contexts=ON_DEVICE_N_CONTEXTS,
            n_blocks=4,
            block_words=32,
            max_steps=200,
        )
    )
    r = sb.evaluate(cand.instrs, Workload("pair2", (prod, cons), kind="route"))
    assert not r.share_leak
    assert r.isolation_ok
    adm = admission_audit_candidate(cand)
    assert adm["pass"]


def test_extra_ctx_grant_of_pair_subrange_is_a_leak():
    """n_guests=2 must still see ADDPROT of ctx 4 (on-device 8-ctx)."""
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
        MOVI r1, 4
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
        expected_off={0: 99},
        expected={},
        role="consumer",
        token=99,
        pair=1,
        messages=(99,),
    )
    sb = Sandbox(
        lenient_config(
            n_contexts=ON_DEVICE_N_CONTEXTS,
            n_blocks=4,
            block_words=32,
            max_steps=200,
        )
    )
    r = sb.evaluate(src, Workload("pair2", (prod, cons), kind="route"))
    assert r.share_leak
    assert not r.isolation_ok
    hits = [tid for tid, _addr in r.isolation_hits]
    assert 4 in hits


def test_round9_claimed_leak_safe_fail_on_device_admission():
    from pathlib import Path

    from evoos.agent.program import Candidate
    from evoos.vm.instruction_set import parse_disassembly

    root = Path("experiments/scheduling/results/round9/examples")
    for stem in ("gen_s1", "gen_s13", "gen_s2024"):
        path = root / f"{stem}.txt"
        if not path.exists():
            continue
        cand = Candidate(instrs=parse_disassembly(path.read_text()), origin=stem, id=0)
        aud = admission_audit_candidate(cand)
        assert not aud["pass"], f"{stem} must fail 8-ctx admission (leaked extra ctx)"


def test_payload_copy_official_and_heldout_audit():
    cand = payload_copy_rr(16)
    sb = Sandbox(
        lenient_config(
            n_contexts=13,
            n_blocks=16,
            block_words=32,
            max_steps=6000,
            blk_latency=2,
        )
    )
    train, held = official_split()
    tr = [sb.evaluate(cand.instrs, w) for w in train]
    ho = [sb.evaluate(cand.instrs, w) for w in held]
    assert all(r.isolation_ok and r.completed == r.n_guests for r in tr + ho)
    # Direct-copy places leftover ctx ids into [256,384). That is an on-device leak.
    adm = admission_audit_candidate(cand)
    assert not adm["pass"]


def test_third_guest_grant_is_invalid_in_search():
    cfg = lenient_config(n_contexts=5, n_blocks=4, block_words=32, max_steps=200)
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
    from evoos.agent.program import Candidate

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
    )
    cons = GuestImage(
        name="c",
        code=assemble("HALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected_off={0: 99},
        expected={},
        role="consumer",
        token=99,
        pair=1,
    )
    atk = GuestImage(
        name="a",
        code=assemble("HALT"),
        data_base=0,
        data_bound=0,
        init={},
        expected={},
        role="attacker",
    )
    w = Workload("leak_pair", (prod, cons, atk), split="train", kind="route")
    sb = Sandbox(cfg)
    cand = Candidate(instrs=src, origin="leak", id=1)
    r = sb.evaluate(cand.instrs, w)
    assert r.share_leak
    bd, tr, _ho, invalid = evaluate_candidate(
        sb,
        cand,
        [w],
        [],
        NoveltyArchive(),
        RewardWeights.correctness_only(),
        False,
        leak_invalid=True,
    )
    assert invalid
    assert bd.total == INVALID_REWARD
    assert tr and tr[0].share_leak


def test_rotating_audit_search_does_not_crash():
    from evoos.agent.evolutionary_search import run_search
    from evoos.agent.routing_curriculum import routing_sandbox
    from evoos.evaluator.hw_workloads import mixed_pressure_route, route_workload

    sb = routing_sandbox(12, max_steps=800)
    train = [route_workload("train", 2), mixed_pressure_route("train")]
    cfg = SearchConfig(
        generations=3,
        population=6,
        seed=1,
        init_len=16,
        max_len=48,
        mode="correctness",
        operator_pack="hw",
        selection="epsilon_lexicase",
        leak_invalid=True,
        revoke_shaping=True,
        audit_bank=train_audit_bank(),
        audit_sample=2,
        keep_records=False,
        eval_workers=1,
        log_every=1,
    )
    state = run_search(sb, train, [], cfg, extra_seeds=[payload_copy_rr(8)])
    assert state.best is not None
    assert len(state.history) == 3


def test_search_config_safety_fields():
    cfg = SearchConfig(leak_invalid=True, revoke_shaping=True, audit_sample=2, audit_bank=train_audit_bank())
    assert cfg.leak_invalid and cfg.revoke_shaping
    assert cfg.audit_sample == 2
    assert len(cfg.audit_bank) >= 4
