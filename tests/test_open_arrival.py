from evoos.boot.pack import live_elite, parse_elite
from evoos.evaluator.open_arrival import evaluate_open_arrival
from evoos.evaluator.workloads import training_workloads
from evoos.experiments.scheduling.baselines import round_robin_live
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox


def test_rr_live_handles_injected_and_replaced_guests():
    r = evaluate_open_arrival(round_robin_live(16).instrs)
    assert r["injected"]
    assert r["replaced"]
    assert r["completed"] >= 3
    assert r["isolation_hits"] == []


def test_evolved_live_handles_open_arrival():
    cfg = MachineConfig(max_steps=200_000, wall_steps=250_000)
    r = evaluate_open_arrival(live_elite(), config=cfg)
    assert r["injected"]
    assert r["isolation_hits"] == []
    assert r["completed"] >= 2
    assert r["switch_count"] > 0


def test_live_elite_still_completes_train_small():
    r = Sandbox(MachineConfig(max_steps=8000)).evaluate(live_elite(), training_workloads()[0])
    assert r.isolation_ok
    assert r.completed == 4


def test_oneshot_elite_still_parses():
    # Original HALT elite remains the differential/oneshot blob.
    instrs = parse_elite()
    assert instrs[70].op == 0
    live = live_elite()
    assert live[70].op == 21
    assert live[:70] == instrs[:70]
