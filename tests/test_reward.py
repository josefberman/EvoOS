from evoos.evaluator.correctness import summarize_correctness
from evoos.evaluator.reward import RewardWeights, score
from evoos.evaluator.workloads import training_workloads
from evoos.experiments.scheduling.baselines import run_to_completion
from evoos.vm.instruction_set import assemble
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import EvalResult, Sandbox
from evoos.vm.verifier import VerifyError, verify_supervisor


def _fake_result(**kwargs) -> EvalResult:
    n = kwargs.get("n_guests", 4)
    defaults = dict(
        workload="fake",
        split="train",
        completed=4,
        n_guests=n,
        progressed=4,
        isolation_hits=[],
        corruption=False,
        supervisor_fault=0,
        supervisor_halted=True,
        cycles=1000,
        switch_count=4,
        guest_cycles=[100, 100, 100, 100],
        halt_cycles=[100, 200, 300, 400],
        outcomes=[],
        runctx_log=[],
        mem_fingerprint=0,
    )
    defaults.update(kwargs)
    return EvalResult(**defaults)


def test_vacuous_isolation_is_not_stability():
    idle = _fake_result(completed=0, progressed=0, guest_cycles=[0, 0, 0, 0], isolation_hits=[])
    bd = score([idle], novelty=0.0, weights=RewardWeights.correctness_only())
    assert bd.s == 0.0
    assert bd.total == 0.0


def test_hard_constraint_zeros_reward_despite_throughput():
    bad = _fake_result(
        completed=4,
        isolation_hits=[(1, 400)],
        corruption=True,
        cycles=10,
        guest_cycles=[1000, 1000, 1000, 1000],
    )
    good_looking_e = 1.0
    bd = score([bad], novelty=1.0, weights=RewardWeights())
    assert bd.hard_fail
    assert bd.total == 0.0
    assert bd.e == 0.0
    assert bd.n == 0.0
    assert bd.g == 0.0
    # Throughput / novelty must not leak into the total.
    assert bd.total < good_looking_e


def test_correctness_only_ignores_novelty():
    ok = _fake_result()
    a = score([ok], novelty=1.0, weights=RewardWeights.correctness_only())
    b = score([ok], novelty=0.0, weights=RewardWeights.correctness_only())
    assert a.total == b.total
    qd_a = score([ok], novelty=1.0, weights=RewardWeights.quality_diversity())
    qd_b = score([ok], novelty=0.0, weights=RewardWeights.quality_diversity())
    assert qd_a.total > qd_b.total


def test_invalid_program_is_not_scored_as_success():
    sandbox = Sandbox(MachineConfig(max_supervisor_len=4, max_steps=100))
    huge = assemble("NOP\n" * 20 + "HALT")
    result = sandbox.evaluate(huge, training_workloads()[0])
    assert result.invalid
    try:
        verify_supervisor(huge, sandbox.config)
        assert False
    except VerifyError:
        pass


def test_rtc_baseline_scores_well():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    wl = training_workloads()[0]
    result = sandbox.evaluate(run_to_completion().instrs, wl)
    c = summarize_correctness(result, [g.needed_cycles for g in wl.guests])
    assert c.all_complete
    assert c.isolation_ok
    bd = score([result], weights=RewardWeights.correctness_only())
    assert bd.total > 0.8
    assert not bd.hard_fail
