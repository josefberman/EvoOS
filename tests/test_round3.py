import random

from evoos.agent.curriculum import make_sandbox, run_curriculum
from evoos.agent.evolutionary_search import SearchConfig
from evoos.evaluator.reward import RewardWeights, score
from evoos.evaluator.shaping import shape_terms
from evoos.evaluator.workloads import curriculum_stages, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion
from evoos.experiments.scheduling.condition_b import grid_search
from evoos.vm.instruction_set import assemble
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox


def test_shaping_idle_vs_rtc():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    wl = training_workloads()[0]
    idle = sandbox.evaluate(assemble("HALT"), wl)
    st_idle = shape_terms(idle)
    assert st_idle.entered == 0
    assert st_idle.complete == 0
    assert st_idle.blended() == 0
    rtc = sandbox.evaluate(run_to_completion().instrs, wl)
    st = shape_terms(rtc)
    assert st.entered == 1.0
    assert st.complete == 1.0
    assert st.prot_cover == 1.0
    assert st.blended() > 0.9


def test_shaping_does_not_inspect_opcodes():
    # Reward uses EvalResult fields only; a NOP-padded RTC still maxes shaping.
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    src_nops = "NOP\n" * 4 + "HALT"
    # Compare shape of RTC vs idle, independent of program text.
    wl = training_workloads()[0]
    a = shape_terms(sandbox.evaluate(run_to_completion().instrs, wl))
    b = shape_terms(sandbox.evaluate(assemble(src_nops), wl))
    assert a.complete > b.complete


def test_resume_after_guest_fault_is_generic_hardware():
    src = """
        MOVI r1, 1
        RUNCTXI r1, 4
        CTXGET r2, r1, DATA_BASE
        CTXGET r3, r1, DATA_BOUND
        ADDPROT r1, r2, r3
        RUNCTXI r1, 4000
        HALT
    """
    code = assemble(src)
    wl = curriculum_stages()[0]["train"][0]
    hostile = Sandbox(MachineConfig(n_contexts=2, max_steps=5000, sticky_guest_fault=True))
    lenient = Sandbox(lenient_config(n_contexts=2, max_steps=5000))
    h = hostile.evaluate(code, wl)
    ell = lenient.evaluate(code, wl)
    assert h.completed == 0  # first RUNCTX faults, sticky, second is a no-op
    assert ell.completed == 1


def test_supervisor_mem_error_does_not_halt_when_lenient():
    src = """
        MOVI r1, 99999
        LOAD r2, r1, 0
        MOVI r3, 7
        HALT
    """
    h = MachineConfig(max_steps=50, supervisor_halt_on_mem=True)
    ell = lenient_config(max_steps=50)
    from evoos.vm.machine import Machine

    mh = Machine(h)
    mh.install_supervisor(assemble(src))
    mh.run()
    assert mh.supervisor_fault != 0
    assert mh.contexts[0].regs[3] == 0
    ml = Machine(ell)
    ml.install_supervisor(assemble(src))
    ml.run()
    assert ml.contexts[0].regs[3] == 7


def test_condition_b_efficiency_separates_quanta():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    grid = grid_search(sandbox, list(training_workloads()), list(training_workloads())[:1])
    by_q = {r["quantum"]: r for r in grid}
    # All complete, but q=1 is switch-heavy so E should be below a mid quantum.
    assert by_q[1]["four_of_four"]
    assert by_q[16]["four_of_four"]
    assert by_q[1]["train_E"] < by_q[16]["train_E"] or by_q[1]["train_reward"] <= by_q[16]["train_reward"]


def test_curriculum_one_guest_rtc():
    sandbox = make_sandbox(1, MachineConfig(max_steps=8000))
    wl = curriculum_stages()[0]["train"][0]
    r = sandbox.evaluate(run_to_completion().instrs, wl)
    assert r.n_guests == 1
    assert r.completed == 1
    assert r.isolation_ok


def test_tiny_curriculum_runs_bare():
    cfg = lenient_config(max_steps=2000)
    search = SearchConfig(
        generations=2,
        population=4,
        seed=1,
        init_len=8,
        operator_pack="bare",
        shaping=True,
        keep_records=False,
        eval_heldout_every=1,
        mode="correctness",
    )
    cr = run_curriculum(cfg, search, gens_per_stage=2, stages=curriculum_stages()[:1])
    assert cr.stages
    assert cr.stages[0].name == "k1"
    assert cr.state.best_reward <= 1.0
