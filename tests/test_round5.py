"""Round 5: learned edit policy, k1 stone promotion, ISA freeze."""

from __future__ import annotations

import random

from evoos.agent.curriculum import run_curriculum
from evoos.agent.evolutionary_search import (
    SearchConfig,
    SearchState,
    archive_has_protected_complete,
    run_search,
)
from evoos.agent.learned_policy import N_OP, LearnedEditPolicy, telemetry_vec
from evoos.agent.map_elites import MapElites
from evoos.agent.mutations import BARE_OPS, apply_edit
from evoos.agent.program import Candidate
from evoos.evaluator.workloads import curriculum_stages
from evoos.vm.instruction_set import Instr, Op, assemble
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import Sandbox


COVER_RUN = """
    MOVI r1, 1
    CTXGET r2, r1, DATA_BASE
    CTXGET r3, r1, DATA_BOUND
    ADDPROT r1, r2, r3
    RUNCTXI r1, 4000
    HALT
"""


def test_isa_opcodes_still_frozen():
    names = [op.name for op in Op]
    assert names[0] == "HALT" and names[31] == "RDFAULT"
    assert names[24] == "RUNCTX" and names[28] == "ADDPROT"
    assert N_OP == 35
    assert names[32] == "BLKRD" and names[34] == "SVC"
    assert "SCHEDULE" not in names


def test_apply_edit_insert_remove_modify():
    parent = Candidate(instrs=assemble("NOP\nHALT"), origin="p", id=1)
    ins = Instr(int(Op.MOVI), 1, 0, 0, 3)
    inserted = apply_edit(parent, "insert", 0, new_instr=ins)
    assert len(inserted.instrs) == 3
    assert inserted.instrs[0].op == int(Op.MOVI)
    removed = apply_edit(inserted, "remove", 0)
    assert len(removed.instrs) == 2
    modified = apply_edit(parent, "modify_op", 0, new_instr=Instr(int(Op.ADD), 0, 0, 0, 0))
    assert modified.instrs[0].op == int(Op.ADD)
    assert modified.instrs[0].imm == parent.instrs[0].imm
    cf = apply_edit(parent, "control_flow", 0, new_instr=Instr(int(Op.JMP), 0, 0, 0, -1))
    assert cf.instrs[0].op == int(Op.JMP)


def test_learned_policy_act_and_learn():
    pol = LearnedEditPolicy(operators=BARE_OPS, epsilon=0.0)
    assert pol._torch, "round 5 requires CPU torch"
    parent = Candidate(instrs=assemble("NOP\nHALT"), origin="p", id=1)
    tele = telemetry_vec(None, 0.0)
    child, sampled = pol.act(parent, tele, random.Random(0), max_len=32)
    assert sampled is not None
    assert sampled.kind in BARE_OPS
    assert len(child.instrs) >= 1
    loss = pol.learn_batch([(sampled, 0.15), (sampled, -0.05)])
    assert isinstance(loss, float)
    snap = pol.snapshot()
    assert snap["torch"] is True
    assert snap["n_updates"] == 1


def test_archive_k1_stone_is_protected_complete():
    state = SearchState()
    state.archive = MapElites()
    stone = Candidate(instrs=assemble(COVER_RUN), origin="stone", id=1)
    idle = Candidate(instrs=assemble("HALT"), origin="idle", id=2)
    state.archive.add(idle, (0, 0, 0, 0, 1), 0.0, 0)
    assert not archive_has_protected_complete(state, 1)
    state.archive.add(stone, (1, 1, 1, 1, 1), 0.9, 1)
    assert archive_has_protected_complete(state, 1)
    assert not archive_has_protected_complete(state, 2)


def test_k1_promotes_on_protected_complete_seed():
    cfg = lenient_config(max_steps=5000)
    search = SearchConfig(
        generations=6,
        population=4,
        seed=99,
        init_len=8,
        operator_pack="bare",
        shaping=True,
        gated=True,
        curiosity=True,
        milestone_archive=True,
        elite_crossover=True,
        keep_records=False,
        eval_heldout_every=2,
        mode="correctness",
        early_stop_complete=True,
        policy="ucb1",
    )
    seed = Candidate(instrs=assemble(COVER_RUN), origin="stone99", id=1)
    cr = run_curriculum(
        cfg,
        search,
        gens_per_stage=6,
        stages=curriculum_stages()[:1],
        extra_seeds=[seed],
    )
    assert cr.stages[0].promoted
    assert cr.stages[0].n_guests == 1
    assert archive_has_protected_complete(cr.state, 1) or (
        cr.stages[0].completed_protected and cr.stages[0].completed_protected[0] >= 1
    )
    assert cr.stages[0].gens_ran <= 6


def test_search_early_stops_on_k1_stone():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=5000))
    wl = curriculum_stages()[0]["train"][0]
    cand = Candidate(instrs=assemble(COVER_RUN), origin="stone", id=1)
    cfg = SearchConfig(
        generations=8,
        population=4,
        seed=0,
        gated=True,
        shaping=True,
        milestone_archive=True,
        early_stop_complete=True,
        operator_pack="bare",
        keep_records=False,
        mode="correctness",
        eval_heldout_every=99,
    )
    state = run_search(sandbox, [wl], [], cfg, extra_seeds=[cand])
    assert archive_has_protected_complete(state, 1)
    assert len(state.history) < 8
