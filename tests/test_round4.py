from evoos.agent.map_elites import MapElites
from evoos.agent.program import Candidate
from evoos.evaluator.novelty import milestone_cell
from evoos.evaluator.reward import RewardWeights, score
from evoos.evaluator.shaping import shape_terms
from evoos.evaluator.workloads import curriculum_stages, training_workloads
from evoos.experiments.scheduling.baselines import run_to_completion
from evoos.vm.instruction_set import Op, assemble
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox


def test_gated_zeros_unprotected_runctx():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=2000))
    wl = curriculum_stages()[0]["train"][0]
    bare_run = assemble("MOVI r1, 1\nRUNCTXI r1, 32\nHALT")
    r = sandbox.evaluate(bare_run, wl)
    assert r.entered >= 1
    assert r.entered_protected == 0
    ungated = shape_terms(r, gated=False)
    gated = shape_terms(r, gated=True)
    assert ungated.entered > 0
    assert gated.entered == 0
    assert gated.progress == 0
    assert gated.complete == 0
    bd = score([r], weights=RewardWeights.correctness_only(), shaping=True, gated=True)
    assert bd.total < 0.2
    assert bd.s == 0.0


def test_gated_counts_protected_then_run():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=5000))
    wl = curriculum_stages()[0]["train"][0]
    src = """
        MOVI r1, 1
        CTXGET r2, r1, DATA_BASE
        CTXGET r3, r1, DATA_BOUND
        ADDPROT r1, r2, r3
        RUNCTXI r1, 4000
        HALT
    """
    r = sandbox.evaluate(assemble(src), wl)
    assert r.entered_protected == 1
    assert r.completed_protected == 1
    st = shape_terms(r, gated=True)
    assert st.prot_cover == 1.0
    assert st.complete == 1.0
    assert st.blended() > 0.9


def test_gated_rtc_still_maxes():
    sandbox = Sandbox(lenient_config(max_steps=8000))
    wl = training_workloads()[0]
    r = sandbox.evaluate(run_to_completion().instrs, wl)
    st = shape_terms(r, gated=True)
    assert st.complete == 1.0
    assert st.prot_cover == 1.0
    assert r.entered_protected == r.n_guests


def test_milestone_cells_separate_cover_from_unprotected_enter():
    sandbox = Sandbox(lenient_config(n_contexts=2, max_steps=2000))
    wl = curriculum_stages()[0]["train"][0]
    run_only = sandbox.evaluate(assemble("MOVI r1, 1\nRUNCTXI r1, 16\nHALT"), wl)
    prot_only = sandbox.evaluate(
        assemble(
            "MOVI r1, 1\nCTXGET r2, r1, DATA_BASE\nCTXGET r3, r1, DATA_BOUND\nADDPROT r1, r2, r3\nHALT"
        ),
        wl,
    )
    assert milestone_cell(run_only) != milestone_cell(prot_only)
    assert milestone_cell(run_only)[0] == 0
    assert milestone_cell(prot_only)[0] == 1


def test_curiosity_prefers_unvisited_cell():
    rng = __import__("random").Random(0)
    arch = MapElites()
    a = Candidate(instrs=assemble("HALT"), origin="a", id=1)
    b = Candidate(instrs=assemble("NOP\nHALT"), origin="b", id=2)
    arch.add(a, (0, 0, 0, 0, 1), 0.9, 0)
    arch.add(b, (1, 0, 0, 0, 1), 0.2, 0)
    for _ in range(8):
        arch.sample_curious(rng)
    # Cover-only cell must be visited despite lower reward.
    assert arch.visits[(1, 0, 0, 0, 1)] >= 1


def test_isa_opcodes_unchanged():
    # Round 4 must not add/remove/renumber opcodes (parallel C interpreter freeze).
    names = [op.name for op in Op]
    assert names[:5] == ["HALT", "NOP", "MOV", "MOVI", "ADD"]
    assert "RUNCTX" in names and "ADDPROT" in names
    assert "LAST_RUN_STEPS" not in names  # CSR field, not an opcode
    assert names[31] == "RDFAULT"
    assert names[32] == "BLKRD" and names[34] == "SVC"
    assert len(list(Op)) == 35
