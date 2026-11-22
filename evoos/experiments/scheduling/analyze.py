"""Disassemble the best evolved supervisor and compare it to baselines."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from evoos.agent.program import Candidate
from evoos.evaluator.reward import RewardWeights, score
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.vm.instruction_set import Op, disassemble, instrs_from_jsonable
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import EvalResult, Sandbox


def classify_trace(result: EvalResult) -> str:
    """Heuristic label from *behavior* on a workload, not from comments in the code."""
    n = result.n_guests
    switches = result.switch_count
    if result.completed == 0 and switches == 0:
        return "no-dispatch"
    if switches <= n + 1 and result.completed == n:
        return "run-to-completion-like"
    # Interleaving: successive RUNCTX targets are not a single ctx for long.
    log = result.runctx_log
    if len(log) >= n * 3:
        runs = [cid for _, cid, ran in log if ran > 0]
        changes = sum(1 for a, b in zip(runs, runs[1:]) if a != b)
        frac = changes / max(len(runs) - 1, 1)
        if frac > 0.6:
            return "round-robin-like"
        if frac < 0.25:
            return "batched/priority-like"
    if result.completed < n and any(c == 0 for c in result.guest_cycles):
        return "starving"
    return "other"


def opcode_stats(cand: Candidate) -> dict:
    counts: dict[str, int] = {}
    n_back = 0
    for i, ins in enumerate(cand.instrs):
        try:
            name = Op(ins.op).name
        except ValueError:
            name = f"OP{ins.op}"
        counts[name] = counts.get(name, 0) + 1
        if ins.op in (Op.JMP, Op.BEQ, Op.BNE, Op.BLT, Op.BGE, Op.JAL) and ins.imm < 0:
            n_back += 1
    return {
        "length": len(cand),
        "counts": counts,
        "n_runctx": counts.get("RUNCTX", 0) + counts.get("RUNCTXI", 0),
        "n_addprot": counts.get("ADDPROT", 0),
        "n_backward": n_back,
    }


def evaluate_named(sandbox: Sandbox, name: str, cand: Candidate, workloads) -> dict:
    results = [sandbox.evaluate(cand.instrs, w) for w in workloads]
    bd = score(results, heldout_results=None, novelty=0.0, weights=RewardWeights.correctness_only())
    labels = {r.workload: classify_trace(r) for r in results}
    return {
        "name": name,
        "reward": bd.as_dict(),
        "completed": [r.completed for r in results],
        "isolation_ok": [r.isolation_ok for r in results],
        "switches": [r.switch_count for r in results],
        "guest_cycles": [r.guest_cycles for r in results],
        "labels": labels,
        "all_complete": all(r.completed == r.n_guests and r.isolation_ok for r in results),
    }


def load_best_from_db(db_path: Path) -> Candidate | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT program_json FROM evaluations WHERE invalid=0 ORDER BY reward DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or not row[0]:
        return None
    data = json.loads(row[0])
    instrs = instrs_from_jsonable(data["instrs"] if isinstance(data, dict) else data)
    return Candidate(instrs=instrs, origin="evolved")


def report(sandbox: Sandbox, evolved: Candidate | None) -> str:
    train = list(training_workloads())
    held = list(heldout_workloads())
    lines: list[str] = []
    baselines = {
        "run_to_completion": run_to_completion(),
        "round_robin_q16": round_robin(16),
        "selfish_first": selfish_first(),
    }
    lines.append("=== Condition A: hand-written baselines (held-out) ===")
    for name, cand in baselines.items():
        info = evaluate_named(sandbox, name, cand, held)
        lines.append(json.dumps(info, indent=2))

    if evolved is None:
        lines.append("No evolved candidate provided.")
        return "\n".join(lines)

    lines.append("=== Condition C: evolved supervisor ===")
    lines.append(opcode_stats(evolved).__repr__())
    lines.append("--- disassembly ---")
    lines.append(disassemble(evolved.instrs))
    lines.append("--- held-out ---")
    lines.append(json.dumps(evaluate_named(sandbox, "evolved", evolved, held), indent=2))
    lines.append("--- train ---")
    lines.append(json.dumps(evaluate_named(sandbox, "evolved", evolved, train), indent=2))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Analyze EvoOS scheduling supervisors")
    p.add_argument("--db", type=Path, default=None, help="SQLite log from train.py")
    p.add_argument("--max-steps", type=int, default=8000)
    args = p.parse_args(argv)
    sandbox = Sandbox(MachineConfig(max_steps=args.max_steps))
    evolved = load_best_from_db(args.db) if args.db else None
    print(report(sandbox, evolved))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
