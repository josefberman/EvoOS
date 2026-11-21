"""HITL-verify persist shortlist on QEMU ATA kill/reboot; stamp persist_hitl_ok.json."""

from __future__ import annotations

import json
from pathlib import Path

from evoos.boot.pack import PERSIST_ELITE_PATH, PERSIST_HITL_PATH, persist_iso_kind, select_supervisor
from evoos.evaluator.hitl import hitl_boot_instrs, hitl_persist_reboot
from evoos.evaluator.persist import evaluate_persist, evaluate_stage
from evoos.experiments.scheduling.baselines import persist_rr
from evoos.vm.instruction_set import parse_disassembly

ROOT = Path(__file__).resolve().parents[3]


def _live_ok(serial: str) -> bool:
    if "supervisor returned" in serial:
        return False
    if serial.count(" halted ") < 3:
        return False
    if "#PF" not in serial:
        return False
    if "preempt vector=32" not in serial:
        return False
    if "inspect isolation_n=0" not in serial and "isolation_n=0" not in serial:
        return False
    return True


def main() -> int:
    stamp = {
        "persist_ok": False,
        "live_ok": False,
        "kind": persist_iso_kind(),
        "origin": "",
        "note": "",
    }
    kinds = ["persist_rr"]
    if PERSIST_ELITE_PATH.exists():
        kinds.insert(0, "evolved_persist")
    chosen = None
    for kind in kinds:
        instrs, origin = select_supervisor(kind)
        py = evaluate_persist(instrs)
        print(f"python {kind} origin={origin} gated={py['correct']}/2 iso={py['isolation_ok']}")
        if py["correct"] < 2 or not py["isolation_ok"]:
            continue
        hw = hitl_persist_reboot(kind)
        print(f"hitl persist {kind} ok={hw['ok']} rc={hw['returncode']}")
        print(hw.get("stderr", "")[-500:])
        if not hw["ok"]:
            continue
        live = hitl_boot_instrs(instrs, origin, timeout=12.0)
        live_ok = _live_ok(live.get("serial", ""))
        print(f"hitl live {kind} live_ok={live_ok} halted={live.get('halted_lines')} pf={live.get('pf')}")
        stamp.update(
            {
                "persist_ok": True,
                "live_ok": live_ok,
                "kind": kind,
                "origin": origin,
                "python_gated": py["correct"],
                "layout": py.get("layout"),
            }
        )
        chosen = kind
        if kind == "evolved_persist":
            break
    if chosen is None:
        stamp["note"] = "no shortlist passed ATA reboot; persist_rr still used for test-persist"
        stamp["kind"] = "persist_rr"
        py = evaluate_stage(persist_rr().instrs, "D")
        stamp["python_gated"] = py["correct"]
    elif chosen == "persist_rr":
        stamp["note"] = (
            "Condition A persist_rr passed ATA reboot. No evolved elite shipped as ISO storage."
        )
    elif stamp["live_ok"]:
        stamp["note"] = (
            "evolved persist passed ATA reboot and live #PF/preempt; "
            "default ISO can compose with s99 (evolved_open_persist)."
        )
    else:
        stamp["note"] = (
            "evolved persist passed ATA reboot but failed live keyboard/#PF; "
            "default ISO stays s99; test-persist uses evolved_persist."
        )
    PERSIST_HITL_PATH.parent.mkdir(parents=True, exist_ok=True)
    PERSIST_HITL_PATH.write_text(json.dumps(stamp, indent=2) + "\n")
    print(json.dumps(stamp, indent=2))
    return 0 if stamp["persist_ok"] or stamp["kind"] == "persist_rr" else 1


if __name__ == "__main__":
    raise SystemExit(main())
