"""Round 7: close bare-bits; Condition C-hw on the hardware-manual pack."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from pathlib import Path

from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.agent.program import Candidate
from evoos.evaluator.hw_workloads import hw_heldout, hw_training
from evoos.evaluator.novelty import org_label
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.bare_density import run_report, write_markdown
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.experiments.scheduling.hw_baselines import (
    fifo_swap_deaf,
    fifo_swap_rr,
    place_first_frames_rr,
)
from evoos.experiments.scheduling.metrics import diagnose, evaluate_split
from evoos.vm.instruction_set import Op, disassemble, instrs_to_jsonable, parse_disassembly
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox

SEEDS = (1, 7, 13, 42, 99, 2024)
OUT = Path("experiments/scheduling/results/round7")
ISA_NOTE = (
    "ISA: opcodes 0–31 unchanged; 32–34 are BLKRD/BLKWR/SVC from the persist-blk "
    "boot track. No pager, pipe, or schedule_process opcode."
)


def round2_gadget_elites() -> list[Candidate]:
    root = Path("experiments/scheduling/results/examples")
    out: list[Candidate] = []
    if not root.exists():
        return out
    for p in sorted(root.glob("correctness_gadgets_*.txt")):
        try:
            instrs = parse_disassembly(p.read_text())
        except ValueError:
            continue
        out.append(Candidate(instrs=instrs, origin=p.stem, id=-(10 + len(out))))
    return out


def _ckpt_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, default=str))
    tmp.replace(path)


def hw_sandbox(max_steps: int = 8000) -> Sandbox:
    return Sandbox(
        lenient_config(
            n_contexts=9,
            n_blocks=16,
            block_words=32,
            blk_latency=2,
            max_steps=max_steps,
            max_supervisor_len=192,
        )
    )


def run_baselines() -> list[dict]:
    rows = []
    old_sb = Sandbox(lenient_config(max_steps=8000, n_contexts=5))
    train, held = list(training_workloads()), list(heldout_workloads())
    for name, cand in (
        ("run_to_completion", run_to_completion()),
        ("round_robin_q16", round_robin(16)),
        ("selfish_first", selfish_first()),
    ):
        info = evaluate_split(old_sb, cand, train, held)
        info.update({"suite": "old4", "name": name})
        rows.append(info)
    hw_sb = hw_sandbox(8000)
    htrain, hheld = list(hw_training()), list(hw_heldout())
    for name, cand in (
        ("fifo_swap_rr", fifo_swap_rr(32)),
        ("direct_copy_router", fifo_swap_rr(32)),
        ("place_first_frames_rr", place_first_frames_rr(32)),
        ("fifo_swap_deaf", fifo_swap_deaf(32)),
    ):
        info = evaluate_split(hw_sb, cand, htrain, hheld)
        info.update({"suite": "hw", "name": name})
        info["org"] = org_label(hw_sb.evaluate(cand.instrs, htrain[0]))
        rows.append(info)
    return rows


def run_c_hw(job: dict, seeds: list[Candidate], ckpt_cb=None) -> dict:
    seed = int(job["seed"])
    search = SearchConfig(
        generations=int(job["generations"]),
        population=int(job["population"]),
        seed=seed,
        init_len=32,
        max_len=128,
        eval_heldout_every=0,
        mode="qd",
        operator_pack="hw",
        keep_records=False,
        shaping=True,
        gated=True,
        curiosity=True,
        hw_archive=True,
        elite_crossover=True,
        archive_mix=0.7,
        elite_fraction=0.25,
        early_stop_complete=True,
        log_every=int(job.get("log_every", 25)),
        policy="ucb1",
        eval_workers=int(job.get("eval_workers", 1)),
        selection="epsilon_lexicase",
        dataflow=True,
    )
    sandbox = hw_sandbox(int(job.get("max_steps", 4000)))
    t0 = time.perf_counter()
    curves = []

    def on_generation(gen, state, row):
        curves.append(
            {
                "generation": gen,
                "best_reward": row.get("best_reward"),
                "archive_size": row.get("archive_size"),
                "n_eval": row.get("n_eval"),
                "first_solved": row.get("first_solved"),
            }
        )
        if ckpt_cb is not None and gen % max(int(job.get("log_every", 25)), 1) == 0:
            ckpt_cb(
                {
                    "seed": seed,
                    "generation": gen,
                    "best_reward": state.best_reward,
                    "archive_size": state.archive.size(),
                }
            )

    extras = list(seeds)
    state = run_search(
        sandbox,
        list(hw_training()),
        list(hw_heldout()),
        search,
        extra_seeds=extras or None,
        on_generation=on_generation,
    )
    elapsed = time.perf_counter() - t0
    n_eval = sum(int(h.get("n_eval") or 0) for h in state.history)
    row = {
        "condition": "C-hw",
        "name": f"hw_lex_s{seed}",
        "seed": seed,
        "elapsed_sec": elapsed,
        "search_reward": state.best_reward,
        "four_of_four": False,
        "archive_size": state.archive.size(),
        "archive": state.archive.dump(),
        "n_eval": n_eval,
        "first_solved": dict(state.first_solved),
        "live_curve": curves,
        "operator_pack": "hw",
    }
    if state.best is not None:
        split = evaluate_split(
            hw_sandbox(8000), state.best, list(hw_training()), list(hw_heldout())
        )
        row.update(split)
        row["diagnose"] = diagnose(split)
        row["best_origin"] = state.best.origin
        row["program"] = instrs_to_jsonable(state.best.instrs)
        row["disassembly"] = disassemble(state.best.instrs)
        tr0 = hw_sandbox(8000).evaluate(state.best.instrs, hw_training()[0])
        row["org"] = org_label(tr0)
        row["n_blk_write"] = tr0.n_blk_write
        row["n_blk_read"] = tr0.n_blk_read
        row["delivered"] = tr0.delivered
        row["n_resident"] = tr0.n_resident
        row["search_reward"] = state.best_reward
    return row


def _md_table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


def write_reports(out: Path, density: dict, a_rows, c_rows, elapsed, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "why_bare_fails.md").write_text(write_markdown(density))
    (out / "bare_density.json").write_text(json.dumps(density, default=str, indent=2))

    def _flat(row):
        d = dict(row)
        for k, v in list(d.items()):
            if isinstance(v, (list, dict)):
                d[k] = json.dumps(v)
        return d

    fields = [
        "condition",
        "name",
        "seed",
        "search_reward",
        "train_reward",
        "held_reward",
        "four_of_four",
        "train_completed",
        "held_completed",
        "org",
        "elapsed_sec",
        "archive_size",
        "n_eval",
        "n_blk_write",
        "delivered",
        "diagnose",
    ]
    with (out / "condition_c.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in c_rows:
            w.writerow(_flat(r))
    (out / "sweep_raw.json").write_text(
        json.dumps({"density": density, "A": a_rows, "C": c_rows, "meta": meta}, default=str)
    )
    ex = out / "examples"
    ex.mkdir(parents=True, exist_ok=True)
    for r in c_rows:
        if not r.get("disassembly"):
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(r["name"]))[:120]
        (ex / f"{safe}.txt").write_text(
            f"# {r['name']} 4/4={r.get('four_of_four')} R={r.get('search_reward')} "
            f"org={r.get('org')} origin={r.get('best_origin')}\n"
            f"# diagnose: {r.get('diagnose')}\n"
            f"# blk_r={r.get('n_blk_read')} blk_w={r.get('n_blk_write')} "
            f"delivered={r.get('delivered')} resident={r.get('n_resident')}\n\n"
            + str(r["disassembly"])
        )

    lines = []
    lines.append("# Round 7: close bare-bits; Condition C-hw")
    lines.append("")
    lines.append(f"Wall time **{elapsed:.1f}s** ({elapsed/60:.1f} min), eval_workers={meta.get('eval_workers')}.")
    lines.append(ISA_NOTE)
    lines.append(f"Opcode count: **{len(list(Op))}** (`HALT`=0 … `SVC`=34).")
    lines.append("")
    lines.append("Bare-line close: [`why_bare_fails.md`](why_bare_fails.md).")
    lines.append("")
    lines.append("## Condition A / A-hw")
    lines.append("")
    lines.append(
        _md_table(
            ["suite", "name", "train completed", "held completed", "4/4", "held R"],
            [
                [
                    r.get("suite"),
                    r["name"],
                    r.get("train_completed"),
                    r.get("held_completed"),
                    r.get("four_of_four"),
                    f"{float(r.get('held_reward') or 0):.3f}",
                ]
                for r in a_rows
            ],
        )
    )
    lines.append("")
    lines.append("## Condition C-hw (hardware-manual pack, pressure+route)")
    lines.append("")
    lines.append(
        "Pack `hw` = gadgets + ctx-scan + BLKRD/BLKWR calling-convention snippets. "
        "Honestly framed as the hardware manual, not bare bits. Environment: 8 guests "
        "/ 4 RAM frames + block device; 4 producer/consumer pairs via SVC PUT/GET. "
        "QD MAP-Elites cell `(completed, blk_writes, svc+delivered, switch_bin, isolation)` "
        "+ epsilon-lexicase. Seeded from round-2 gadget elites."
    )
    lines.append("")
    n4 = sum(1 for r in c_rows if r.get("four_of_four"))
    rs = [float(x.get("search_reward") or 0) for x in c_rows]
    mean = statistics.fmean(rs) if rs else float("nan")
    std = statistics.pstdev(rs) if len(rs) > 1 else 0.0
    best = max(rs) if rs else float("nan")
    lines.append(
        _md_table(
            ["arm", "search R mean ± std (best)", "4/4 train+held", "mean n_eval"],
            [
                [
                    "hw_lexicase",
                    f"{mean:.3f} ± {std:.3f} ({best:.3f})",
                    f"**{n4}/{len(c_rows)}**",
                    f"{statistics.fmean([int(x.get('n_eval') or 0) for x in c_rows]):.0f}"
                    if c_rows
                    else "n/a",
                ]
            ],
        )
    )
    lines.append("")
    lines.append(
        _md_table(
            ["seed", "R", "train completed", "held completed", "org", "blk_w", "delivered", "archive"],
            [
                [
                    r.get("seed"),
                    f"{float(r.get('search_reward') or 0):.3f}",
                    r.get("train_completed"),
                    r.get("held_completed"),
                    r.get("org"),
                    r.get("n_blk_write"),
                    r.get("delivered"),
                    r.get("archive_size"),
                ]
                for r in c_rows
            ],
        )
    )
    orgs = sorted({r.get("org") for r in c_rows if r.get("org")})
    lines.append("")
    lines.append(f"Organizations observed: {orgs or '(none)'}.")
    four = [r for r in c_rows if r.get("four_of_four")]
    if four:
        lines.append("")
        lines.append("### 4/4 elite disassembly pointers")
        lines.append("")
        for r in four:
            lines.append(
                f"- `{r['name']}` org=`{r.get('org')}` origin=`{r.get('best_origin')}` "
                f"→ [`examples/{r['name']}.txt`](examples/{r['name']}.txt)"
            )
            lines.append("")
            lines.append("```")
            lines.append(str(r.get("disassembly") or "")[:3000])
            lines.append("```")
    else:
        lines.append("No 4/4 elites on the pressure+route mix.")
        best_row = max(c_rows, key=lambda x: float(x.get("search_reward") or 0), default=None)
        if best_row and best_row.get("disassembly"):
            lines.append("")
            lines.append(f"Best elite `{best_row['name']}` org=`{best_row.get('org')}`:")
            lines.append("")
            lines.append("```")
            lines.append(str(best_row.get("disassembly") or "")[:3000])
            lines.append("```")
    lines.append("")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    (out / "README.md").write_text(
        "Round 7: [`summary.md`](summary.md), [`why_bare_fails.md`](why_bare_fails.md). "
        "ISA 0–31 frozen; 32–34 = BLKRD/BLKWR/SVC.\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--generations", type=int, default=0)
    p.add_argument("--population", type=int, default=0)
    p.add_argument("--eval-workers", type=int, default=0)
    p.add_argument("--density-n", type=int, default=0)
    p.add_argument("--skip-density", action="store_true")
    p.add_argument("--out", type=str, default=str(OUT))
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    quick = args.quick
    gens = args.generations or (3 if quick else 4000)
    pop = args.population or (6 if quick else 64)
    workers = args.eval_workers or os.cpu_count() or 2
    dens_n = args.density_n or (400 if quick else 20000)
    seeds = SEEDS[:2] if quick else SEEDS
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ckpt_dir = out / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    print("== density ==")
    dens_path = out / "bare_density.json"
    if args.skip_density and dens_path.exists():
        density = json.loads(dens_path.read_text())
        print("reused", dens_path)
    else:
        density = run_report(random_n=dens_n, mutate_n=max(200, dens_n // 2), seed=0)
        print(write_markdown(density)[:1500])

    print("== A / A-hw ==")
    a_rows = run_baselines()
    for r in a_rows:
        print(" ", r.get("suite"), r["name"], r.get("train_completed"), "4/4", r.get("four_of_four"))

    gadget_seeds = round2_gadget_elites()
    print(f"seeded {len(gadget_seeds)} round-2 gadget elites")

    c_rows: list[dict] = []
    raw_path = out / "sweep_raw.json"
    if raw_path.exists():
        try:
            prev = json.loads(raw_path.read_text())
            c_rows = list(prev.get("C") or [])
            print(f"resuming with {len(c_rows)} C-hw rows")
        except Exception:
            c_rows = []
    done = {int(r["seed"]) for r in c_rows if r.get("seed") is not None}
    meta = {
        "eval_workers": workers,
        "generations": gens,
        "population": pop,
        "seeds": list(seeds),
        "target_evals_per_seed": gens * pop,
        "n_gadget_seeds": len(gadget_seeds),
    }

    def persist():
        write_reports(out, density, a_rows, c_rows, time.perf_counter() - t0, meta)

    def live_ckpt(info):
        _ckpt_write(ckpt_dir / "live.json", info)

    persist()
    jobs = [
        {
            "seed": seed,
            "generations": gens,
            "population": pop,
            "eval_workers": workers,
            "log_every": 1 if quick else 25,
            "max_steps": 2000 if quick else 4000,
        }
        for seed in seeds
    ]
    print(f"== C-hw {len(jobs)} jobs gps={gens} pop={pop} workers={workers} ==")
    for job in jobs:
        if int(job["seed"]) in done:
            print(f"  skip s{job['seed']}")
            continue
        print(f"  start s{job['seed']}")
        row = run_c_hw(job, gadget_seeds, ckpt_cb=live_ckpt)
        c_rows.append(row)
        done.add(int(job["seed"]))
        persist()
        print(
            f"  done {row['name']} R={float(row['search_reward']):.3f} "
            f"4/4={row.get('four_of_four')} org={row.get('org')} "
            f"n_eval={row.get('n_eval')} {row.get('elapsed_sec', 0):.0f}s"
        )
    elapsed = time.perf_counter() - t0
    persist()
    print(f"wrote {out} in {elapsed:.1f}s")
    print((out / "summary.md").read_text()[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
