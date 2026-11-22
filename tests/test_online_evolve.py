from evoos.boot.seed_c import emit_header
from evoos.evaluator.workloads import guest_pressure, guest_route
from evoos.experiments.scheduling.baselines import fifo_swap_like, place_first
from evoos.vm.instruction_set import Op


def test_seed_header_contains_s99_and_persist_rr():
    src = emit_header("evolved_open")
    assert "SEED_INCUMBENT_N" in src
    assert "SEED_S99_N" in src
    assert "SEED_PERSIST_RR_N" in src
    assert "SEED_FIFO_SWAP_N" in src
    assert "SEED_PLACE_FIRST_N" in src
    assert "evolved_open_arrival" in src
    assert "c_hw_fifo_swap_like" in src
    assert "c_hw_place_first" in src
    assert "BLKWR" in src or "33," in src


def test_pressure_and_route_guests():
    p = guest_pressure(0, 40)
    assert p.name.startswith("pressure")
    r = guest_route(1, 2, 77)
    assert any(i.op == Op.SVC for i in r.code)


def test_c_hw_standin_seeds_are_never_halt_schedulers():
    fifo = fifo_swap_like()
    place = place_first()
    assert any(i.op == Op.RUNCTXI for i in fifo.instrs)
    assert any(i.op == Op.RUNCTXI for i in place.instrs)
    assert fifo.instrs[-1].op != Op.HALT
    assert place.instrs[-1].op != Op.HALT
    assert any(i.op == Op.CTXGET and i.imm == 3 for i in place.instrs)
