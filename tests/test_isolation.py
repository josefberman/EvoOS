from evoos.evaluator.workloads import GUEST_SLOTS, isolation_workload, training_workloads
from evoos.experiments.scheduling.baselines import run_to_completion
from evoos.vm.instruction_set import assemble
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox


def test_isolation_detected_when_prot_is_too_wide():
    """A supervisor that maps all of physical memory lets a guest smash a neighbor."""
    victim = GUEST_SLOTS[1][0] + 2
    src = f"""
        MOVI r1, 1
        MOVI r2, 0
        MOVI r3, 2048
        ADDPROT r1, r2, r3
        RUNCTXI r1, 40
        HALT
    """
    sandbox = Sandbox(MachineConfig(max_steps=500))
    result = sandbox.evaluate(assemble(src), isolation_workload())
    assert result.isolation_hits or result.corruption
    assert not result.isolation_ok
    # The malicious store targeted guest 1's data.
    assert any(addr == victim for (_, addr) in result.isolation_hits) or result.corruption


def test_baseline_rtc_does_not_violate_isolation():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    for wl in training_workloads():
        result = sandbox.evaluate(run_to_completion().instrs, wl)
        assert result.isolation_ok
        assert result.completed == 4


def test_no_prot_means_guest_fault_not_corruption():
    src = """
        MOVI r1, 1
        RUNCTXI r1, 40
        HALT
    """
    sandbox = Sandbox(MachineConfig(max_steps=200))
    result = sandbox.evaluate(assemble(src), isolation_workload())
    # Guest never obtained a store right, so the smash should not land.
    assert result.isolation_ok
    assert result.outcomes[0].fault != 0 or not result.outcomes[0].halted or result.outcomes[0].cycles > 0
