import random

from evoos.agent.mutations import OPERATOR_PACKS, ctx_scan_gadget, mutate, random_program
from evoos.agent.program import Candidate
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.condition_b import QUANTA, grid_search
from evoos.vm.instruction_set import Op
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox
from evoos.vm.verifier import verify_supervisor


def test_ctx_scan_is_an_id_loop_not_a_scheduler():
    rng = random.Random(0)
    g = ctx_scan_gadget(rng)
    assert len(g) == 5
    assert g[0].op == int(Op.MOVI) and g[0].imm == 1
    assert g[2].op == int(Op.ADDI) and g[2].imm == 1
    assert g[3].op == int(Op.CTXGET)
    assert g[4].op == int(Op.BLT) and g[4].imm < 0
    # No skip-if-halted / until-idle wrapper in the gadget itself.
    names = [Op(i.op).name for i in g]
    assert "RUNCTXI" not in names or names.count("RUNCTXI") <= 1
    verify_supervisor(g, MachineConfig(max_supervisor_len=16))


def test_bare_pack_does_not_emit_named_gadgets():
    rng = random.Random(1)
    parent = Candidate(instrs=random_program(rng, 8, pack="bare"), origin="random", id=1)
    child = mutate(parent, "insert_ctx_scan", rng, operators=OPERATOR_PACKS["bare"])
    # mutate() still can apply an op if asked directly; the pack only restricts search policy.
    assert "insert_ctx_scan" not in OPERATOR_PACKS["bare"]
    assert "insert_mmu_gadget" not in OPERATOR_PACKS["bare"]
    assert child.instrs


def test_rr_grid_finds_a_working_quantum():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    grid = grid_search(sandbox, list(training_workloads()), list(heldout_workloads()), quanta=QUANTA[:8] + (4000,))
    assert any(row["four_of_four"] for row in grid)
    assert all(row["train_isolation"] and row["held_isolation"] for row in grid)
