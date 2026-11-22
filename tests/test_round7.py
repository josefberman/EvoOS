"""Round 7: BLK/SVC ISA, C-hw workloads, bare-density, gadgets still work."""

from __future__ import annotations

from evoos.agent.mutations import OPERATOR_PACKS, mmu_gadget
from evoos.evaluator.hw_workloads import hw_training, pressure_workload, route_workload
from evoos.experiments.scheduling.bare_density import analytic_mmu_gadget, enumerate_gadget_family
from evoos.experiments.scheduling.baselines import round_robin
from evoos.experiments.scheduling.hw_baselines import fifo_swap_rr
from evoos.experiments.scheduling.metrics import evaluate_split
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.vm.instruction_set import CtxField, Instr, Op, Svc, assemble, parse_disassembly
from evoos.vm.machine import Machine, MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox


def test_opcodes_0_31_frozen_and_blk_svc_appended():
    names = [op.name for op in Op]
    assert names[0] == "HALT" and names[31] == "RDFAULT"
    assert names[24] == "RUNCTX" and names[28] == "ADDPROT"
    assert names[32] == "BLKRD" and names[33] == "BLKWR" and names[34] == "SVC"
    assert len(names) == 35
    assert "SCHEDULE" not in names
    assert CtxField.LAST_RUN_CTX == 10
    assert CtxField.LAST_SVC == 14


def test_hw_pack_is_hardware_manual():
    assert "insert_mmu_gadget" in OPERATOR_PACKS["hw"]
    assert "insert_blkrd_gadget" in OPERATOR_PACKS["hw"]
    assert "insert_remap_gadget" in OPERATOR_PACKS["hw"]
    assert "insert_mmu_gadget" not in OPERATOR_PACKS["bare"]


def test_blkrd_blkwr_roundtrip():
    m = Machine(MachineConfig(n_blocks=4, block_words=8, blk_latency=1, n_contexts=2))
    m.mem[100] = 7
    m.mem[101] = 9
    m.install_supervisor(
        assemble(
            """
            MOVI r1, 2
            MOVI r2, 100
            BLKWR r1, r2
            MOVI r3, 200
            BLKRD r4, r1, r3
            HALT
            """
        )
    )
    m.run(200)
    assert m.mem[200] == 7 and m.mem[201] == 9
    assert m.disk.n_write >= 1 and m.disk.n_read >= 1


def test_svc_yields_to_supervisor():
    m = Machine(lenient_config(n_contexts=2, max_steps=200))
    m.contexts[1].code = list(assemble("MOVI r1, 1\nSVC r1\nHALT"))
    m.contexts[1].pc = 0
    m.install_supervisor(
        assemble(
            """
            MOVI r1, 1
            RUNCTXI r1, 20
            CTXGET r2, r0, LAST_SVC
            HALT
            """
        )
    )
    m.run(200)
    assert m.last_svc == int(Svc.PUT)
    assert m.last_svc_ctx == 1
    assert m.n_svc >= 1


def test_round_robin_still_four_of_four_on_old_mix():
    sandbox = Sandbox(lenient_config(max_steps=8000, n_contexts=5))
    info = evaluate_split(
        sandbox, round_robin(16), list(training_workloads()), list(heldout_workloads())
    )
    assert info["four_of_four"] is True


def test_pressure_and_route_workloads_load():
    p = pressure_workload("train", 8)
    r = route_workload("train", 4)
    assert len(p.guests) == 8 and p.kind == "pressure"
    assert all(g.home_block is not None for g in p.guests)
    assert r.kind == "route"
    assert sum(1 for g in r.guests if g.role == "consumer") == 4
    cfg = lenient_config(n_contexts=9, n_blocks=16, block_words=32, max_steps=2000)
    sb = Sandbox(cfg)
    idle = assemble("HALT")
    er = sb.evaluate(idle, p)
    assert er.n_guests == 8
    assert er.completed == 0


def test_fifo_baseline_completes_pressure_and_route():
    cand = fifo_swap_rr(16)
    assert any(ins.op == int(Op.BLKRD) for ins in cand.instrs)
    assert any(ins.op == int(Op.BLKWR) for ins in cand.instrs)
    cfg = lenient_config(
        n_contexts=9, n_blocks=16, block_words=32, max_steps=8000, blk_latency=2
    )
    sb = Sandbox(cfg)
    p = sb.evaluate(cand.instrs, pressure_workload("train", 8))
    r = sb.evaluate(cand.instrs, route_workload("train", 4))
    assert not p.invalid and not r.invalid
    assert p.completed == 8 and p.isolation_ok
    assert r.completed == 8 and r.delivered == 4 and r.isolation_ok


def test_gadget_family_size_and_parse_disassembly():
    fam = enumerate_gadget_family()
    assert fam["family_size"] == 840
    ana = analytic_mmu_gadget()
    assert ana["p_exact_4"] < 1e-12
    g = mmu_gadget(__import__("random").Random(0))
    text = "\n".join(f"{i:4d}:  {ins.pretty()}" for i, ins in enumerate(g))
    back = parse_disassembly(text)
    assert back[0].op == int(Op.MOVI)
