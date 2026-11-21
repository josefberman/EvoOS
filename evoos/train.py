"""End-to-end scheduling experiment: baselines, then evolutionary search."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.agent.program import Candidate
from evoos.evaluator.reward import RewardWeights, score
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.analyze import classify_trace, opcode_stats, report
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.experiments.scheduling.conditions import Condition
from evoos.tracking import ExperimentLog
from evoos.vm.instruction_set import disassemble, instrs_to_jsonable
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox


def _eval_baseline(sandbox: Sandbox, name: str, cand: Candidate, workloads) -> dict:
    results = [sandbox.evaluate(cand.instrs, w) for w in workloads]
    bd = score(results, novelty=0.0, weights=RewardWeights.correctness_only())
    return {
        "name": name,
        "origin": cand.origin,
        "reward": bd.as_dict(),
        "completed": [r.completed for r in results],
        "n_guests": [r.n_guests for r in results],
        "isolation_ok": all(r.isolation_ok for r in results),
        "switches": [r.switch_count for r in results],
        "labels": {r.workload: classify_trace(r) for r in results},
        "all_four_complete": all(r.completed == r.n_guests and r.isolation_ok for r in results),
    }


def run_experiment(args: argparse.Namespace) -> dict:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cfg = MachineConfig(max_steps=args.max_steps, mem_words=args.mem_words)
    sandbox = Sandbox(cfg)
    train = list(training_workloads())
    held = list(heldout_workloads())

    summary: dict = {
        "condition_target": Condition.C_EMERGENT.value,
        "mode": args.mode,
        "seed": args.seed,
        "generations": args.generations,
        "population": args.population,
        "baselines": {},
    }

    print("== Condition A: hand-written baselines ==")
    for name, cand in (
        ("run_to_completion", run_to_completion()),
        ("round_robin_q16", round_robin(16)),
        ("selfish_first", selfish_first()),
    ):
        train_info = _eval_baseline(sandbox, name, cand, train)
        held_info = _eval_baseline(sandbox, name, cand, held)
        summary["baselines"][name] = {"train": train_info, "heldout": held_info}
        print(
            f"  {name}: train complete={train_info['completed']} "
            f"heldout complete={held_info['completed']} "
            f"isol_ok={held_info['isolation_ok']} "
            f"R={held_info['reward']['total']:.3f}"
        )

    log = ExperimentLog(out / "evoos.sqlite")
    search_cfg = SearchConfig(
        generations=args.generations,
        population=args.population,
        seed=args.seed,
        init_len=args.init_len,
        eval_heldout_every=args.eval_heldout_every,
        mode=args.mode,
        policy=args.policy,
        max_len=cfg.max_supervisor_len,
    )
    log.start_run(args.seed, args.mode, {**vars(args), "config": "SearchConfig"})

    t0 = time.perf_counter()
    n_logged = 0

    def on_gen(gen, state, row):
        nonlocal n_logged
        log.log_generation(row)
        # Persist a few evaluations per generation (best + last).
        if state.best_record is not None:
            rec = state.best_record
            log.log_eval(
                gen,
                rec.candidate.id,
                rec.candidate.origin,
                rec.reward,
                rec.train[0].switch_count if rec.train else 0,
                json.dumps(
                    {
                        "instrs": instrs_to_jsonable(rec.candidate.instrs),
                        "origin": rec.candidate.origin,
                        "id": rec.candidate.id,
                    }
                ),
                rec.invalid,
            )
            n_logged += 1
        print(
            f"  gen {gen:3d}  bestR={row['best_reward']:.3f}  "
            f"meanR={row['mean_reward']:.3f}  invalid={row['n_invalid']}/{row['n_eval']}  "
            f"archive={row['archive_size']}  bestF={row['best_f']:.3f}  "
            f"completed={row['best_completed']}"
        )

    print(f"== Condition C: emergent search ({args.mode}, seed={args.seed}) ==")
    state = run_search(
        sandbox,
        train,
        held,
        search_cfg,
        on_generation=on_gen,
        extra_seeds=None,
    )
    elapsed = time.perf_counter() - t0
    log.log_archive(
        state.archive.dump(),
        {
            e.cell: json.dumps({"instrs": instrs_to_jsonable(e.candidate.instrs)})
            for e in state.archive.cells.values()
        },
    )
    log.close()

    best = state.best
    best_info = None
    if best is not None:
        asm = disassemble(best.instrs)
        (out / "best_supervisor.txt").write_text(asm)
        (out / "best_supervisor.json").write_text(
            json.dumps({"instrs": instrs_to_jsonable(best.instrs), "origin": best.origin, "id": best.id})
        )
        best_info = {
            "id": best.id,
            "origin": best.origin,
            "stats": opcode_stats(best),
            "reward": state.best_record.reward.as_dict() if state.best_record else None,
            "train": _eval_baseline(sandbox, "evolved", best, train),
            "heldout": _eval_baseline(sandbox, "evolved", best, held),
        }
        print("== Best evolved supervisor ==")
        print(asm)
        print("train:", json.dumps(best_info["train"], indent=2))
        print("heldout:", json.dumps(best_info["heldout"], indent=2))
        print(report(sandbox, best))

    summary.update(
        {
            "elapsed_sec": elapsed,
            "evals_per_min": (args.generations * args.population + args.population) / max(elapsed / 60.0, 1e-6),
            "best_reward": state.best_reward,
            "archive_size": state.archive.size(),
            "history": state.history,
            "evolved": best_info,
            "db": str(out / "evoos.sqlite"),
        }
    )
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"elapsed {elapsed:.1f}s  bestR={state.best_reward:.3f}  log={out}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="EvoOS milestone-1 scheduling experiment")
    p.add_argument("--generations", type=int, default=25)
    p.add_argument("--population", type=int, default=16)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--mode", choices=("correctness", "qd"), default="correctness")
    p.add_argument("--policy", choices=("ucb1", "ppo"), default="ucb1")
    p.add_argument("--init-len", type=int, default=20)
    p.add_argument("--max-steps", type=int, default=8000)
    p.add_argument("--mem-words", type=int, default=2048)
    p.add_argument("--eval-heldout-every", type=int, default=5)
    p.add_argument("--out", type=str, default="runs/latest")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    run_experiment(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
