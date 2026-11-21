from evoos.boot.aot import emit_c
from evoos.boot.pack import live_elite, parse_elite, select_supervisor
from evoos.experiments.scheduling.baselines import round_robin_live
from evoos.vm.instruction_set import Op


def test_aot_emits_trampoline_calls_and_labels():
    src = emit_c(round_robin_live(16).instrs, origin="baseline_rr_live_q16")
    assert "evoos_hw_runctx" in src
    assert "evoos_hw_addprot" in src
    assert "evoos_hw_ctxget" in src
    assert "goto L" in src
    assert "supervisor_origin" in src
    assert "void supervisor_main(void)" in src


def test_aot_live_elite_patches_halt_to_jmp():
    oneshot = parse_elite()
    live = live_elite()
    assert oneshot[70].op == Op.HALT
    assert live[70].op == Op.JMP
    src = emit_c(live, origin="evolved_gadgets_s42_live")
    assert "return;" in src  # other paths
    # The former HALT pc is a goto, not a top-level return at L70 only.
    assert "L70:" in src


def test_select_rr_live():
    instrs, origin = select_supervisor("rr_live")
    assert "live" in origin
    assert instrs[-1].op == Op.JMP
