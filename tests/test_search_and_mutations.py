import random

from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.agent.mutations import mutate, random_program
from evoos.agent.program import Candidate
from evoos.evaluator.workloads import training_workloads
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox
from evoos.vm.verifier import VerifyError, verify_supervisor


def test_mutations_stay_typed_or_are_caught():
    rng = random.Random(0)
    parent = Candidate(instrs=random_program(rng, 12), origin="random", id=1)
    for op in (
        "insert",
        "remove",
        "modify_op",
        "modify_operand",
        "modify_imm",
        "duplicate_block",
        "swap",
        "insert_gadget",
        "insert_mmu_gadget",
        "insert_runctx_gadget",
        "control_flow",
    ):
        child = mutate(parent, op, rng, max_len=64)
        assert len(child.instrs) >= 1
        try:
            verify_supervisor(child.instrs, MachineConfig(max_supervisor_len=64))
        except VerifyError:
            pass  # search assigns -1; verifier is the gate


def test_tiny_search_runs():
    sandbox = Sandbox(MachineConfig(max_steps=1500))
    state = run_search(
        sandbox,
        list(training_workloads())[:1],
        [],
        SearchConfig(generations=2, population=4, seed=1, init_len=8, eval_heldout_every=1, mode="correctness"),
    )
    assert state.history
    assert len(state.records) >= 4
    # Random programs are unlikely to complete all four; just ensure scoring happened.
    assert state.best_reward <= 1.0
