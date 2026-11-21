from evoos.experiments.scheduling.baselines import round_robin, run_to_completion
from evoos.evaluator.workloads import training_workloads
from evoos.vm.instruction_set import assemble
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import Sandbox, load_firmware


def test_interpreter_deterministic_on_loop():
    src = """
        MOVI r1, 0
        MOVI r2, 10
    loop:
        BGE r1, r2, done
        ADDI r1, r1, 1
        JMP loop
    done:
        HALT
    """
    code = assemble(src)
    hashes = []
    for _ in range(3):
        m = Machine(MachineConfig(max_steps=1000))
        m.install_supervisor(code)
        m.run()
        hashes.append(m.state_hash())
    assert hashes[0] == hashes[1] == hashes[2]
    assert m.contexts[0].regs[1] == 10


def test_sandbox_repeatable_from_snapshot():
    sandbox = Sandbox(MachineConfig(max_steps=8000))
    wl = training_workloads()[0]
    cand = run_to_completion()
    a = sandbox.evaluate(cand.instrs, wl)
    b = sandbox.evaluate(cand.instrs, wl)
    assert a.mem_fingerprint == b.mem_fingerprint
    assert a.cycles == b.cycles
    assert a.guest_cycles == b.guest_cycles
    assert a.completed == b.completed == 4


def test_seeded_search_not_required_for_vm_determinism():
    """Two machines loaded the same way match even if Python hash seed differs."""
    cfg = MachineConfig(max_steps=4000)
    wl = training_workloads()[0]
    cand = round_robin(8)

    def run_once():
        m = Machine(cfg)
        load_firmware(m, wl)
        snap = m.snapshot()
        m.restore(snap)
        m.install_supervisor(cand.instrs)
        m.run()
        return m.state_hash(), m.cycles, [c.cycles for c in m.contexts]

    h1, c1, g1 = run_once()
    h2, c2, g2 = run_once()
    assert h1 == h2
    assert c1 == c2
    assert g1 == g2
