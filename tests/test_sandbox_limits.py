from evoos.vm.instruction_set import assemble
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import Sandbox
from evoos.evaluator.workloads import training_workloads
from evoos.vm.verifier import VerifyError, verify_supervisor


def test_invalid_context_id_is_a_nop():
    m = Machine(MachineConfig(max_steps=50))
    m.install_supervisor(assemble("RUNCTXI r0, 10\nCLRPROT r0\nMOVI r1, 7\nHALT"))
    m.run()
    assert m.supervisor_fault == 0
    assert m.contexts[0].regs[1] == 7
    assert m.switch_count == 0


def test_fall_off_end_halts_without_fault():
    m = Machine(MachineConfig(max_steps=50))
    m.install_supervisor(assemble("MOVI r1, 1\nADDI r1, r1, 1"))
    m.run()
    assert m.contexts[0].halted
    assert m.supervisor_fault == 0
    assert m.contexts[0].regs[1] == 2


def test_instruction_budget_stops_infinite_loop():
    src = """
    spin:
        NOP
        JMP spin
    """
    code = assemble(src)
    cfg = MachineConfig(max_steps=200, wall_steps=500)
    m = Machine(cfg)
    m.install_supervisor(code)
    m.run()
    assert m.cycles == 200
    assert m.halted


def test_memory_out_of_range_faults():
    src = """
        MOVI r1, 99999
        LOAD r2, r1, 0
        HALT
    """
    m = Machine(MachineConfig(mem_words=128, max_steps=50))
    m.install_supervisor(assemble(src))
    m.run()
    assert m.supervisor_fault != 0
    assert m.contexts[0].fault != 0


def test_supervisor_length_limit_enforced_by_verifier():
    cfg = MachineConfig(max_supervisor_len=8)
    code = assemble("NOP\n" * 9 + "HALT")
    try:
        verify_supervisor(code, cfg)
        assert False, "expected VerifyError"
    except VerifyError:
        pass
    sandbox = Sandbox(cfg)
    result = sandbox.evaluate(code, training_workloads()[0])
    assert result.invalid
