import pytest

from evoos.vm.instruction_set import Instr, Op, assemble
from evoos.vm.machine import MachineConfig
from evoos.vm.verifier import VerifyError, verify_guest, verify_supervisor


def test_rejects_unknown_opcode():
    with pytest.raises(VerifyError):
        verify_supervisor([Instr(999, 0, 0, 0, 0)])


def test_rejects_bad_register():
    with pytest.raises(VerifyError):
        verify_supervisor([Instr(int(Op.MOV), 99, 0, 0, 0)])


def test_rejects_privileged_in_guest():
    code = assemble("MOVI r1, 1\nRUNCTXI r1, 4\nHALT")
    with pytest.raises(VerifyError):
        verify_guest(code)
    verify_supervisor(code)  # allowed for supervisor


def test_rejects_empty_and_too_long():
    with pytest.raises(VerifyError):
        verify_supervisor([])
    cfg = MachineConfig(max_supervisor_len=2)
    with pytest.raises(VerifyError):
        verify_supervisor(assemble("NOP\nNOP\nHALT"), cfg)


def test_accepts_well_typed_supervisor():
    code = assemble(
        """
        MOVI r1, 1
        CTXGET r2, r1, DATA_BASE
        ADDPROT r1, r2, r2
        RUNCTXI r1, 8
        HALT
        """
    )
    verify_supervisor(code)
    verify_guest(assemble("MOVI r1, 1\nADDI r1, r1, 1\nHALT"))
