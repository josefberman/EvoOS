from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.analyze import classify_trace
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first, unprotected_rtc
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox


def test_rtc_completes_all_training_and_heldout():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    cand = run_to_completion()
    for wl in list(training_workloads()) + list(heldout_workloads()):
        r = sandbox.evaluate(cand.instrs, wl)
        assert r.isolation_ok, wl.name
        assert r.completed == 4, (wl.name, [o.name for o in r.outcomes if not o.correct], [o.mismatches for o in r.outcomes])
        assert classify_trace(r) == "run-to-completion-like"


def test_round_robin_completes_all_training():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    cand = round_robin(16)
    for wl in training_workloads():
        r = sandbox.evaluate(cand.instrs, wl)
        assert r.isolation_ok, wl.name
        assert r.completed == 4, wl.name
        assert r.switch_count > 8


def test_selfish_starves_others():
    sandbox = Sandbox(MachineConfig(max_steps=4000))
    r = sandbox.evaluate(selfish_first().instrs, training_workloads()[0])
    assert r.completed <= 1
    assert r.guest_cycles[0] > 0
    assert r.guest_cycles[1] == 0


def test_unprotected_does_not_complete_memory_guests():
    sandbox = Sandbox(MachineConfig(max_steps=4000))
    r = sandbox.evaluate(unprotected_rtc().instrs, training_workloads()[0])
    assert r.completed == 0
    assert r.isolation_ok
