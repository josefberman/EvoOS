"""Hardware-in-the-loop: AOT a supervisor, boot QEMU, parse serial, score.

Python search still uses the ISA VM. This module re-scores the same bytecode
on real ring-3 guests + MMU + PIT. Agreement is on completed/isolation, not
cycle counts (RUNCTX N is a timer quantum on hardware).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import time
from pathlib import Path

from evoos.boot.aot import emit_c
from evoos.boot.pack import select_supervisor
from evoos.evaluator.open_arrival import evaluate_open_arrival
from evoos.evaluator.workloads import training_workloads
from evoos.vm.instruction_set import Instr
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "native"
BUILD = ROOT / "build"


def python_score(kind: str) -> dict:
    instrs, origin = select_supervisor(kind)
    r = Sandbox(MachineConfig(max_steps=8000)).evaluate(instrs, training_workloads()[0])
    oa = evaluate_open_arrival(instrs, config=MachineConfig(max_steps=200_000, wall_steps=250_000))
    return {
        "origin": origin,
        "completed": r.completed,
        "isolation_ok": r.isolation_ok,
        "switches": r.switch_count,
        "open_completed": oa["completed"],
        "open_isolation": oa["isolation_hits"] == [],
    }


def hitl_boot_instrs(
    instrs: tuple[Instr, ...] | list[Instr],
    origin: str,
    timeout: float = 8.0,
    disk: Path | None = None,
) -> dict:
    """AOT `instrs`, rebuild the ISO, boot BIOS QEMU, parse serial."""
    aot = NATIVE / "supervisor_aot.c"
    aot.write_text(emit_c(instrs, origin=origin, note="hitl"))
    # File is newer than pack.py so the Makefile generate rule is skipped.
    subprocess.run(["make", "-C", str(NATIVE), "iso", "SKIP_AOT=1"], check=True)
    iso = BUILD / "evoos.iso"
    serial = BUILD / "hitl-serial.log"
    serial.unlink(missing_ok=True)
    qemu = [
        "qemu-system-x86_64",
        "-cdrom",
        str(iso),
        "-m",
        "128M",
        "-display",
        "none",
        "-vga",
        "std",
        "-serial",
        f"file:{serial}",
        "-device",
        "isa-debug-exit,iobase=0xf4,iosize=0x04",
        "-no-reboot",
        "-cpu",
        "qemu64",
        "-qmp",
        f"unix:{BUILD / 'hitl.qmp'},server,nowait",
    ]
    if disk is not None:
        qemu[3:3] = ["-drive", f"file={disk},if=ide,format=raw,index=0,media=disk"]
    try:
        os.unlink(BUILD / "hitl.qmp")
    except OSError:
        pass
    proc = subprocess.Popen(qemu, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    deadline = time.time() + timeout
    text = ""
    try:
        while time.time() < deadline:
            if serial.exists():
                text = serial.read_text(errors="replace")
                if "halted" in text and ("preempt" in text or "fault=" in text):
                    break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if serial.exists():
            text = serial.read_text(errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    halted = text.count(" halted ")
    pf = "#PF" in text or "vector=14" in text
    preempt = "preempt vector=32" in text
    isolation_ok = "isolation_n=0" in text or (
        "isolation_n=" not in text and "isolation_n=1" not in text
    )
    if "isolation_n=" in text:
        isolation_ok = "isolation_n=0" in text and "isolation_n=1" not in text
    returned = "supervisor returned" in text
    return {
        "origin": origin,
        "serial": text,
        "halted_lines": halted,
        "pf": pf,
        "preempt": preempt,
        "isolation_ok": isolation_ok,
        "returned": returned,
        "qemu_status": proc.returncode,
    }


def hitl_boot(kind: str, timeout: float = 8.0) -> dict:
    """Rebuild the ISO with a named supervisor and boot BIOS QEMU."""
    instrs, origin = select_supervisor(kind)
    return hitl_boot_instrs(instrs, origin, timeout=timeout)


def compare(kind: str) -> dict:
    py = python_score(kind)
    hw = hitl_boot(kind)
    agree_isolation = py["isolation_ok"] == hw["isolation_ok"]
    return {"python": py, "hitl": {k: v for k, v in hw.items() if k != "serial"}, "agree_isolation": agree_isolation}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--supervisor", default="evolved_open")
    p.add_argument("--boot", action="store_true")
    args = p.parse_args(argv)
    py = python_score(args.supervisor)
    print("python", py)
    if args.boot:
        hw = hitl_boot(args.supervisor)
        print("hitl", {k: v for k, v in hw.items() if k != "serial"})
        print("--- serial ---")
        print(hw["serial"][-4000:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
