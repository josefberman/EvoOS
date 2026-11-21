"""Build an EVS0 machine image (supervisor + guests + firmware words)."""

from __future__ import annotations

import argparse
from pathlib import Path

from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, round_robin_live
from evoos.vm.codec import ImageMeta, dumps_image
from evoos.vm.instruction_set import Instr, dumps_program, parse_disassembly
from evoos.vm.machine import Machine, MachineConfig
from evoos.vm.sandbox import Workload, load_firmware

ELITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments/scheduling/results/examples/correctness_gadgets_s42_round-robin-like.txt"
)
OPEN_ELITE_PATH = (
    Path(__file__).resolve().parents[2]
    / "experiments/scheduling/results/examples/open_arrival_gadgets_elite.txt"
)


def parse_elite(path: Path | None = None) -> tuple[Instr, ...]:
    p = path or ELITE_PATH
    return parse_disassembly(p.read_text())


def live_elite(path: Path | None = None) -> tuple[Instr, ...]:
    """One-instruction live edition of the round-2 gadgets elite.

    Instr 70 is HALT (ends the one-shot demo). Replace it with JMP back into
    the evolved RUNCTX loop so newly installed guests are noticed. No new
    opcodes; the rest of the blob is bit-identical to
    ``correctness_gadgets_s42_round-robin-like``.
    """
    instrs = list(parse_elite(path))
    if len(instrs) > 70 and instrs[70].op == 0:  # HALT
        # JMP target = pc + 1 + imm. pc=70 → 40 => imm = -31
        instrs[70] = Instr(op=21, a=0, b=0, c=0, imm=-31)
    return tuple(instrs)


def select_supervisor(kind: str) -> tuple[tuple[Instr, ...], str]:
    k = kind.lower().replace("-", "_")
    if k in ("rr", "round_robin", "baseline", "baseline_rr"):
        cand = round_robin(16)
        return cand.instrs, cand.origin
    if k in ("rr_live", "round_robin_live", "live_rr"):
        cand = round_robin_live(16)
        return cand.instrs, cand.origin
    if k in ("evolved", "elite", "gadgets", "evolved_oneshot"):
        return parse_elite(), "evolved_gadgets_s42"
    if k in ("evolved_live", "elite_live"):
        return live_elite(), "evolved_gadgets_s42_live"
    if k in ("evolved_open", "open", "live"):
        if not OPEN_ELITE_PATH.exists():
            raise FileNotFoundError(
                f"missing {OPEN_ELITE_PATH}; run python -m evoos.experiments.scheduling.search_open_live"
            )
        instrs = parse_disassembly(OPEN_ELITE_PATH.read_text())
        header = OPEN_ELITE_PATH.read_text().splitlines()[0].lstrip("# ").split()
        origin = header[0] if header else "evolved_open_arrival_gadgets"
        return instrs, origin
    raise ValueError(
        f"unknown supervisor {kind!r} (use evolved_open|evolved|evolved_live|rr|rr_live)"
    )


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
    p.add_argument("--supervisor", default="evolved", help="evolved|rr|rr_live")
    p.add_argument("--workload", default="train_small")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--program-out", default="", help="also write supervisor.bin")
    p.add_argument(
        "--live",
        action="store_true",
        help="raise max_steps/wall_steps so firmware can slice indefinitely",
    )
    args = p.parse_args(argv)
    instrs, origin = select_supervisor(args.supervisor)
    wl = _workload_by_name(args.workload)
    cfg = None
    if args.live:
        cfg = MachineConfig(max_steps=1_000_000_000, wall_steps=2_000_000_000)
    blob = pack_workload(instrs, wl, config=cfg, origin=origin)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)
    if args.program_out:
        Path(args.program_out).write_bytes(dumps_program(instrs))
    print(f"wrote {out} ({len(blob)} bytes) supervisor={origin} workload={wl.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
