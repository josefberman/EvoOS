"""Multi-seed Condition A/B/C sweep. Parallel across processes.

Usage:
  python -m evoos.experiments.scheduling.sweep
  python -m evoos.experiments.scheduling.sweep --quick
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.agent.mutations import OPERATOR_DOCS, OPERATOR_PACKS
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.experiments.scheduling.condition_b import bandit_search, grid_search
from evoos.experiments.scheduling.metrics import diagnose, evaluate_split
from evoos.vm.instruction_set import disassemble, instrs_to_jsonable
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox

SEEDS = (1, 7, 13, 42, 99, 2024)
RESULTS = Path("experiments/scheduling/results")


def _sandbox() -> Sandbox:
    return Sandbox(MachineConfig(max_steps=8000, mem_words=2048))


def _workloads():
    return list(training_workloads()), list(heldout_workloads())


def run_condition_a() -> list[dict]:
    sandbox = _sandbox()
    train, held = _workloads()
    rows = []
    for name, cand in (
        ("run_to_completion", run_to_completion()),
        ("round_robin_q16", round_robin(16)),
        ("selfish_first", selfish_first()),
    ):
        info = evaluate_split(sandbox, cand, train, held)
        info.update({"condition": "A", "name": name, "seed": None, "mode": "n/a", "pack": "handwritten"})
        rows.append(info)
    return rows


def run_condition_b(seed: int, pulls: int) -> dict:
    sandbox = _sandbox()
    train, held = _workloads()
    t0 = time.perf_counter()
    result = bandit_search(sandbox, train, held, seed=seed, pulls=pulls)
    elapsed = time.perf_counter() - t0
    cand = round_robin(result.best_quantum)
    info = evaluate_split(sandbox, cand, train, held)
    info.update(
        {
            "condition": "B",
            "name": f"rr_quantum_{result.best_quantum}",
            "seed": seed,
            "mode": "bandit_quantum",
            "pack": "human_rr_skeleton",
            "best_quantum": result.best_quantum,
            "pulls": pulls,
            "elapsed_sec": elapsed,
            "search_reward": result.best["train_reward"],
            "grid": result.grid,
        }
    )
    return info


def run_condition_c(job: dict) -> dict:
    seed = int(job["seed"])
    mode = job["mode"]
    pack = job["pack"]
    gens = int(job["generations"])
    pop = int(job["population"])
    sandbox = _sandbox()
    train, held = _workloads()
    cfg = SearchConfig(
        generations=gens,
        population=pop,
        seed=seed,
        init_len=20,
        eval_heldout_every=10,
        mode=mode,
        policy="ucb1",
        operator_pack=pack,
        keep_records=False,
        log_every=10**9,
    )
    t0 = time.perf_counter()
    state = run_search(sandbox, train, held, cfg)
    elapsed = time.perf_counter() - t0
    info = {
        "condition": "C",
        "name": f"c_{mode}_{pack}_s{seed}",
        "seed": seed,
        "mode": mode,
        "pack": pack,
        "generations": gens,
        "population": pop,
        "elapsed_sec": elapsed,
        "search_reward": state.best_reward,
        "archive_size": state.archive.size(),
        "evals": gens * pop + pop,
        "four_of_four": False,
        "structural_label": "none",
        "diagnose": "no candidate",
    }
    if state.best is not None:
        split = evaluate_split(sandbox, state.best, train, held)
        info.update(split)
        info["search_reward"] = state.best_reward
        info["diagnose"] = diagnose(split)
        info["best_origin"] = state.best.origin
        info["best_id"] = state.best.id
        info["program"] = instrs_to_jsonable(state.best.instrs)
        info["disassembly"] = disassemble(state.best.instrs)
    return info


def _mean_std(xs: list[float]) -> tuple[float, float, float]:
    if not xs:
        return (float("nan"), float("nan"), float("nan"))
    mean = statistics.fmean(xs)
    std = statistics.pstdev(xs) if len(xs) > 1 else 0.0
    return mean, std, max(xs)


def _write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            flat = dict(row)
            for k, v in list(flat.items()):
                if isinstance(v, (list, dict)):
                    flat[k] = json.dumps(v)
            w.writerow(flat)


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def write_reports(
    out: Path,
    a_rows: list[dict],
    b_rows: list[dict],
    c_rows: list[dict],
    b_grid: list[dict],
    elapsed: float,
    gens: int,
    pop: int,
    workers: int,
) -> None:
    out.mkdir(parents=True, exist_ok=True)

    c_fields = [
        "condition",
        "name",
        "seed",
        "mode",
        "pack",
        "search_reward",
        "train_reward",
        "held_reward",
        "four_of_four",
        "train_completed",
        "held_completed",
        "train_isolation",
        "held_isolation",
        "structural_label",
        "elapsed_sec",
        "archive_size",
        "evals",
        "diagnose",
        "best_origin",
        "length",
    ]
    _write_csv(out / "condition_c_seeds.csv", c_rows, c_fields)
    _write_csv(
        out / "ablation.csv",
        c_rows,
        c_fields,
    )
    abc = []
    for r in a_rows:
        abc.append({**r, "search_reward": r.get("train_reward")})
    for r in b_rows:
        abc.append(r)
    for r in c_rows:
        abc.append(r)
    _write_csv(out / "condition_abc.csv", abc, c_fields + ["best_quantum"])
    _write_csv(
        out / "condition_b_grid.csv",
        b_grid,
        ["quantum", "train_reward", "held_reward", "four_of_four", "train_completed", "held_completed", "train_switches"],
    )

    lines: list[str] = []
    lines.append("# Scheduling experiment results")
    lines.append("")
    lines.append("Real numbers from this run. Isolation failures cannot be compensated by throughput.")
    lines.append("")
    lines.append(f"- Wall time: **{elapsed:.1f}s** ({elapsed / 60:.1f} min) on {workers} processes")
    lines.append(f"- Condition C search: {gens} generations × {pop} population, seeds `{list(SEEDS)}`")
    lines.append(f"- Modes: `correctness`, `qd`. Operator packs: `{list(OPERATOR_PACKS)}`")
    lines.append("")
    lines.append("## Operator packs (what they encode)")
    lines.append("")
    for pack, ops in OPERATOR_PACKS.items():
        extra = [o for o in ops if o in ("insert_mmu_gadget", "insert_runctx_gadget", "insert_ctx_scan")]
        lines.append(f"- **{pack}**: {', '.join(extra) if extra else 'instruction edits only'}")
    lines.append("")
    for key in ("insert_mmu_gadget", "insert_runctx_gadget", "insert_ctx_scan"):
        lines.append(f"- `{key}`: {OPERATOR_DOCS[key]}")
    lines.append("")
    lines.append("## Condition A (hand-written)")
    lines.append("")
    a_table = []
    for r in a_rows:
        a_table.append(
            [
                str(r["name"]),
                json.dumps(r["train_completed"]),
                json.dumps(r["held_completed"]),
                "yes" if r["four_of_four"] else "no",
                f"{r['train_reward']:.3f}",
                str(r["structural_label"]),
            ]
        )
    lines.append(_md_table(["supervisor", "train complete", "held complete", "4/4 isol", "correctness R", "label"], a_table))
    lines.append("")
    lines.append("## Condition B (UCB1 on RR quantum)")
    lines.append("")
    b_table = []
    for r in b_rows:
        b_table.append(
            [
                str(r["seed"]),
                str(r.get("best_quantum")),
                json.dumps(r.get("held_completed")),
                "yes" if r["four_of_four"] else "no",
                f"{r['held_reward']:.3f}",
                str(r["structural_label"]),
            ]
        )
    lines.append(_md_table(["seed", "learned q", "held complete", "4/4 isol", "held R", "label"], b_table))
    lines.append("")
    if b_grid:
        best_g = max(b_grid, key=lambda r: r["train_reward"])
        lines.append(
            f"Exhaustive grid best quantum = **{best_g['quantum']}** "
            f"(train R={best_g['train_reward']:.3f}, held R={best_g['held_reward']:.3f}, "
            f"4/4={'yes' if best_g['four_of_four'] else 'no'})."
        )
    lines.append("")
    lines.append("## Condition C (emergent), by mode × pack")
    lines.append("")

    summary_rows = []
    n_four_total = 0
    n_c = 0
    for mode in ("correctness", "qd"):
        for pack in OPERATOR_PACKS:
            chunk = [r for r in c_rows if r["mode"] == mode and r["pack"] == pack]
            if not chunk:
                continue
            rewards = [float(r["search_reward"]) for r in chunk]
            held_r = [float(r.get("held_reward") or 0) for r in chunk]
            n_four = sum(1 for r in chunk if r.get("four_of_four"))
            n_four_total += n_four
            n_c += len(chunk)
            mean, std, best = _mean_std(rewards)
            hmean, hstd, hbest = _mean_std(held_r)
            labels = ", ".join(sorted({str(r.get("structural_label")) for r in chunk}))
            summary_rows.append(
                [
                    mode,
                    pack,
                    str(len(chunk)),
                    f"{mean:.3f}",
                    f"{std:.3f}",
                    f"{best:.3f}",
                    f"{hmean:.3f}",
                    f"{hbest:.3f}",
                    f"{n_four}/{len(chunk)}",
                    labels,
                ]
            )
    lines.append(
        _md_table(
            [
                "mode",
                "pack",
                "n seeds",
                "search R mean",
                "std",
                "best",
                "held correctness mean",
                "held best",
                "seeds 4/4 train+held",
                "labels",
            ],
            summary_rows,
        )
    )
    lines.append("")
    lines.append("### Per-seed Condition C")
    lines.append("")
    per = []
    for r in sorted(c_rows, key=lambda x: (str(x["mode"]), str(x["pack"]), int(x["seed"]))):
        per.append(
            [
                str(r["mode"]),
                str(r["pack"]),
                str(r["seed"]),
                f"{float(r['search_reward']):.3f}",
                json.dumps(r.get("train_completed")),
                json.dumps(r.get("held_completed")),
                "yes" if r.get("four_of_four") else "no",
                str(r.get("structural_label")),
                str(r.get("diagnose", ""))[:80],
            ]
        )
    lines.append(
        _md_table(
            ["mode", "pack", "seed", "search R", "train", "held", "4/4", "label", "diagnose"],
            per,
        )
    )
    lines.append("")
    lines.append("## A vs B vs C")
    lines.append("")
    def _best_of(rows, key="held_reward"):
        if not rows:
            return None
        return max(rows, key=lambda r: float(r.get(key) or 0))

    a_best = _best_of(a_rows)
    b_best = _best_of(b_rows)
    c_best = _best_of(c_rows)
    cmp_rows = []
    for tag, r in (("A", a_best), ("B", b_best), ("C", c_best)):
        if r is None:
            continue
        cmp_rows.append(
            [
                tag,
                str(r.get("name")),
                json.dumps(r.get("held_completed")),
                "yes" if r.get("four_of_four") else "no",
                f"{float(r.get('held_reward') or 0):.3f}",
                str(r.get("structural_label")),
            ]
        )
    lines.append(_md_table(["condition", "best name", "held complete", "4/4", "held R", "label"], cmp_rows))
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    if n_c == 0:
        lines.append("No Condition C rows.")
    elif n_four_total == 0:
        lines.append(
            f"**No Condition C seed reached 4/4 guests complete with isolation on train AND held-out** "
            f"({n_c} runs). Typical failure: one guest context never gets a matching ADDPROT+RUNCTX pair, "
            f"so it executes ~2 instructions (first LOAD faults) or zero. Gadgets let search complete 2–3 "
            f"guests; enumerating all four still requires composing a scan or four unrolled setups."
        )
    else:
        lines.append(f"{n_four_total}/{n_c} Condition C runs reached 4/4 on train+held-out with isolation.")
    lines.append("")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    (out / "sweep_raw.json").write_text(
        json.dumps({"A": a_rows, "B": b_rows, "C": c_rows, "B_grid": b_grid}, indent=2, default=str)
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="EvoOS multi-seed A/B/C sweep")
    p.add_argument("--quick", action="store_true", help="tiny budget for tests")
    p.add_argument("--generations", type=int, default=0)
    p.add_argument("--population", type=int, default=0)
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--out", type=str, default=str(RESULTS))
    p.add_argument("--b-pulls", type=int, default=40)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gens = args.generations or (8 if args.quick else 400)
    pop = args.population or (6 if args.quick else 40)
    workers = args.workers or os.cpu_count() or 2
    b_pulls = 8 if args.quick else args.b_pulls
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    print("== Condition A ==")
    a_rows = run_condition_a()
    for r in a_rows:
        print(f"  {r['name']}: held={r['held_completed']} 4/4={r['four_of_four']} R={r['held_reward']:.3f}")

    print("== Condition B grid + bandit ==")
    sandbox = _sandbox()
    train, held = _workloads()
    b_grid = grid_search(sandbox, train, held)
    b_jobs = list(SEEDS if not args.quick else SEEDS[:2])
    b_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=min(workers, len(b_jobs))) as ex:
        futs = {ex.submit(run_condition_b, seed, b_pulls): seed for seed in b_jobs}
        for fut in as_completed(futs):
            row = fut.result()
            b_rows.append(row)
            print(f"  seed={row['seed']} q={row.get('best_quantum')} 4/4={row['four_of_four']} heldR={row['held_reward']:.3f}")

    packs = list(OPERATOR_PACKS)
    modes = ("correctness", "qd")
    c_jobs = []
    seeds = SEEDS if not args.quick else SEEDS[:2]
    for seed in seeds:
        for mode in modes:
            for pack in packs:
                c_jobs.append(
                    {
                        "seed": seed,
                        "mode": mode,
                        "pack": pack,
                        "generations": gens,
                        "population": pop,
                    }
                )
    print(f"== Condition C: {len(c_jobs)} jobs, {workers} workers, {gens}×{pop} ==")
    c_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_condition_c, job): job for job in c_jobs}
        done = 0
        for fut in as_completed(futs):
            row = fut.result()
            c_rows.append(row)
            done += 1
            print(
                f"  [{done}/{len(c_jobs)}] {row['mode']}/{row['pack']}/s{row['seed']} "
                f"searchR={float(row['search_reward']):.3f} 4/4={row.get('four_of_four')} "
                f"held={row.get('held_completed')} {row.get('elapsed_sec', 0):.0f}s"
            )
            # Incremental dump so a kill still leaves numbers.
            write_reports(out, a_rows, b_rows, c_rows, b_grid, time.perf_counter() - t0, gens, pop, workers)

    elapsed = time.perf_counter() - t0
    write_reports(out, a_rows, b_rows, c_rows, b_grid, elapsed, gens, pop, workers)
    print(f"wrote {out} in {elapsed:.1f}s")
    print((out / "summary.md").read_text()[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
