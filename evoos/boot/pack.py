"""Build an EVS0 machine image (supervisor + guests + firmware words)."""

from __future__ import annotations

import argparse
from pathlib import Path

from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin
from evoos.vm.codec import ImageMeta, dumps_image
from evoos.vm.instruction_set import Instr, dumps_program, parse_disassembly
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import Workload, load_firmware

ELITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments/scheduling/results/examples/correctness_gadgets_s42_round-robin-like.txt"
)


def parse_elite(path: Path | None = None) -> tuple[Instr, ...]:
    p = path or ELITE_PATH
    return parse_disassembly(p.read_text())


def select_supervisor(kind: str) -> tuple[tuple[Instr, ...], str]:
    k = kind.lower().replace("-", "_")
    if k in ("rr", "round_robin", "baseline", "baseline_rr"):
        cand = round_robin(16)
        return cand.instrs, cand.origin
    if k in ("evolved", "elite", "gadgets"):
        return parse_elite(), "evolved_gadgets_s42"
    raise ValueError(f"unknown supervisor {kind!r} (use evolved|rr)")


def pack_workload(
    supervisor: tuple[Instr, ...],
    workload: Workload,
    config: MachineConfig | None = None,
    origin: str = "",
) -> bytes:
    cfg = config or MachineConfig()
    m = Machine(cfg)
    load_firmware(m, workload)
    m.install_supervisor(supervisor)
    expected: list[tuple[int, int, int]] = []
    names: list[str] = []
    for i, g in enumerate(workload.guests):
        names.append(g.name)
        for addr, val in g.expected.items():
            expected.append((i + 1, addr, val))
    meta = ImageMeta(
        origin=origin,
        workload=workload.name,
        guest_names=names,
        expected=expected,
    )
    return dumps_image(m, meta)


def _workload_by_name(name: str) -> Workload:
    all_wl = list(training_workloads()) + list(heldout_workloads())
    for w in all_wl:
        if w.name == name:
            return w
    raise SystemExit(f"unknown workload {name!r}; have {[w.name for w in all_wl]}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pack an EvoOS boot/differential image")
    p.add_argument("--supervisor", default="evolved", help="evolved|rr")
    p.add_argument("--workload", default="train_small")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--program-out", default="", help="also write supervisor.bin")
    args = p.parse_args(argv)
    instrs, origin = select_supervisor(args.supervisor)
    wl = _workload_by_name(args.workload)
    blob = pack_workload(instrs, wl, origin=origin)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    if args.program_out:
        Path(args.program_out).write_bytes(dumps_program(instrs))
    print(f"wrote {out} ({len(blob)} bytes) supervisor={origin} workload={wl.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
