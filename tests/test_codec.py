from evoos.boot.pack import pack_workload, parse_elite, select_supervisor
from evoos.evaluator.workloads import training_workloads
from evoos.vm.codec import dumps_image, loads_image, mem_fingerprint
from evoos.vm.instruction_set import assemble, dumps_program, loads_program
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import Sandbox


def test_program_roundtrip():
    src = """
        MOVI r1, 42
        ADDI r2, r1, -3
        HALT
    """
    code = assemble(src)
    blob = dumps_program(code)
    assert loads_program(blob) == code
    assert blob[:4] == b"\x15\x20\x0e\x00"


def test_elite_parses_and_completes_train_small():
    instrs = parse_elite()
    assert len(instrs) >= 70
    r = Sandbox(MachineConfig(max_steps=8000)).evaluate(instrs, training_workloads()[0])
    assert r.isolation_ok
    assert r.completed == 4


def test_image_roundtrip_matches_run():
    instrs, origin = select_supervisor("evolved")
    wl = training_workloads()[0]
    blob = pack_workload(instrs, wl, origin=origin)
    m1, meta = loads_image(blob)
    assert meta.origin == origin
    assert meta.workload == wl.name
    m1.run()
    m2, _ = loads_image(blob)
    m2.run()
    assert mem_fingerprint(m1.mem) == mem_fingerprint(m2.mem)
    assert m1.cycles == m2.cycles
    assert m1.switch_count == m2.switch_count
    assert [c.halted for c in m1.contexts] == [c.halted for c in m2.contexts]


def test_dumps_image_header_size():
    m = Machine(MachineConfig())
    m.install_supervisor(assemble("HALT"))
    blob = dumps_image(m)
    assert blob[:4] == b"EVS0"
    assert len(blob) >= 160
