"""Round 6: lexicase cases, dataflow GP, CGP decode, ISA freeze."""

from __future__ import annotations

import random
from collections import Counter

from evoos.agent.cgp import decode, mutate_genome, random_genome
from evoos.agent.curriculum import run_curriculum
from evoos.agent.dataflow import DATAFLOW_DOCS, effective_indices, last_defs, pick_defined_reg
from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.agent.lexicase import lexicase_index, lexicase_select
from evoos.agent.mutations import mutate
from evoos.agent.program import Candidate
from evoos.evaluator.cases import case_vector
from evoos.evaluator.workloads import GUEST_SLOTS, curriculum_stages
from evoos.vm.instruction_set import Instr, Op, assemble
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import Sandbox


def test_isa_opcodes_still_frozen_round6():
    names = [op.name for op in Op]
    assert names[0] == "HALT" and names[31] == "RDFAULT"
    assert names[24] == "RUNCTX" and names[28] == "ADDPROT"
    assert names[32] == "BLKRD" and names[34] == "SVC"
    assert "SCHEDULE" not in names
    assert "LAST_RUN_STEPS" in {f.name for f in __import__("evoos.vm.instruction_set", fromlist=["CtxField"]).CtxField}


def test_lexicase_prefers_unique_case_winners():
    scores = [
        [1.0, 0.0],
        [0.0, 1.0],
        [0.0, 0.0],
    ]
    rng = random.Random(0)
    counts = Counter(lexicase_index(scores, rng) for _ in range(400))
    assert counts[0] > 80 and counts[1] > 80
    assert counts[2] == 0


def test_epsilon_lexicase_binary_equals_lexicase():
    scores = [[1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]
    rng_a = random.Random(7)
    rng_b = random.Random(7)
    a = [lexicase_index(scores, rng_a) for _ in range(80)]
    b = [lexicase_index(scores, rng_b, auto_epsilon=True) for _ in range(80)]
    assert a == b


def test_lexicase_select_skips_missing_and_ineligible():
    rng = random.Random(1)
    bank = {1: [1.0, 0.0], 2: [0.0, 1.0], 3: [0.0, 0.0]}
    picks = lexicase_select([1, 2, 3], bank, rng, n=20, eligible_ids=[1, 2])
    assert all(p in (1, 2) for p in picks)
    assert set(picks) == {1, 2}


def test_effective_code_backward_slice():
    instrs = (
        Instr(int(Op.MOVI), 1, 0, 0, 5),
        Instr(int(Op.ADD), 2, 1, 1, 0),
        Instr(int(Op.STORE), 0, 2, 0, 0),
        Instr(int(Op.MOVI), 7, 0, 0, 99),
        Instr(int(Op.HALT), 0, 0, 0, 0),
    )
    eff = effective_indices(instrs)
    assert {0, 1, 2, 4} <= set(eff)
    assert 3 not in eff
    defs = last_defs(instrs)
    assert defs[1] == 0 and defs[2] == 1 and defs[7] == 3
    rng = random.Random(0)
    picked = [pick_defined_reg(defs, 2, rng, fallback=7) for _ in range(40)]
    assert picked.count(1) + picked.count(2) >= 25
    assert "not OS priors" in DATAFLOW_DOCS


def test_const_pool_from_ctxget():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=400))
    wl = curriculum_stages()[0]["train"][0]
    cand = Candidate(
        instrs=assemble(
            """
            MOVI r1, 1
            CTXGET r2, r1, DATA_BASE
            CTXGET r3, r1, DATA_BOUND
            HALT
            """
        )
    )
    r = sandbox.evaluate(cand.instrs, wl)
    lo, hi = GUEST_SLOTS[0]
    assert lo in r.observed_consts
    assert hi in r.observed_consts


def test_gated_cases_require_protected_entry():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=800))
    wl = curriculum_stages()[0]["train"][0]
    bare_run = Candidate(
        instrs=assemble(
            """
            MOVI r1, 1
            RUNCTXI r1, 4000
            HALT
            """
        )
    )
    r = sandbox.evaluate(bare_run.instrs, wl)
    names, vec = case_vector([r], gated=True)
    by = dict(zip(names, vec))
    assert by["curr_k1_train.isolation"] == 1.0
    assert by["curr_k1_train.g0.entered_p"] == 0.0
    assert by["curr_k1_train.g0.progress_p"] == 0.0
    assert by["curr_k1_train.g0.complete_p"] == 0.0


def test_dataflow_mutate_uses_pool_and_effective():
    parent = Candidate(
        instrs=assemble("MOVI r1, 1\nADD r2, r1, r1\nSTORE r0, r2, 0\nHALT"),
        origin="p",
        id=1,
    )
    rng = random.Random(3)
    child = mutate(
        parent,
        "modify_imm",
        rng,
        dataflow=True,
        const_pool=(256, 384, 1),
        effective=effective_indices(parent.instrs),
    )
    assert len(child.instrs) >= 1
    child2 = mutate(parent, "modify_operand", rng, dataflow=True, const_pool=(256,))
    assert isinstance(child2.instrs[0], Instr)


def test_cgp_decode_stays_on_frozen_isa():
    rng = random.Random(11)
    g = random_genome(rng, 8, const_pool=(1, 256, 384))
    prog = decode(g)
    assert prog[-1].op == int(Op.HALT)
    assert all(0 <= ins.op <= 31 for ins in prog)
    g2 = mutate_genome(g, rng, const_pool=(256,))
    assert len(g2) == len(g)


def test_lexicase_dataflow_search_records_cases():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=400))
    wl = curriculum_stages()[0]["train"][0]
    cfg = SearchConfig(
        generations=2,
        population=6,
        seed=1,
        init_len=8,
        max_len=24,
        operator_pack="bare",
        shaping=True,
        gated=True,
        curiosity=True,
        milestone_archive=True,
        keep_records=False,
        mode="correctness",
        eval_heldout_every=0,
        policy="ucb1",
        selection="epsilon_lexicase",
        dataflow=True,
    )
    state = run_search(sandbox, [wl], [], cfg)
    assert state.case_names
    assert any("isolation" in n for n in state.case_names)
    assert state.history
    assert "first_solved" in state.history[-1]


def test_curriculum_forwards_lexicase_fields():
    cfg = lenient_config(max_steps=400)
    search = SearchConfig(
        generations=2,
        population=4,
        seed=2,
        init_len=6,
        max_len=16,
        operator_pack="bare",
        shaping=True,
        gated=True,
        keep_records=False,
        mode="correctness",
        policy="ucb1",
        selection="lexicase",
        dataflow=True,
        eval_heldout_every=0,
    )
    cr = run_curriculum(cfg, search, gens_per_stage=2, stages=curriculum_stages()[:1])
    assert cr.stages[0].n_guests == 1
    assert isinstance(cr.stages[0].first_solved, dict)
