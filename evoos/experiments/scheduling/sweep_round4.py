"""Round 4: isolation-gated shaping vs ungated, milestone MAP-Elites, bare+lenient.

ISA freeze: this round does not add, remove, or renumber opcodes or CTXGET
fields. Evaluator telemetry (`ran_protected`) is not an ISA CSR.
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
from evoos.evaluator.shaping import SHAPE_WEIGHTS, SHAPING_DOCS
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.experiments.scheduling.metrics import diagnose, evaluate_split
from evoos.vm.instruction_set import Op, disassemble, instrs_to_jsonable
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox

SEEDS = (1, 7, 13, 42, 99, 2024)
OUT = Path("experiments/scheduling/results/round4")
ISA_NOTE = (
    "ISA freeze: opcodes HALT..RDFAULT remain 0..31; CTXGET fields including "
    "LAST_RUN_* are unchanged. Round 4 adds evaluator-only `ran_protected` / "
    "`protected_cycles` on the Python Context object — not a CSR, not an opcode. "
    "A C interpreter/boot stub must match the existing 5-tuple encoding only."
)


def _four_eval(cfg: MachineConfig, cand) -> dict:
    sandbox = Sandbox(
        MachineConfig(
            mem_words=cfg.mem_words,
            n_contexts=5,
            max_steps=cfg.max_steps,
            sticky_guest_fault=cfg.sticky_guest_fault,
            supervisor_halt_on_mem=cfg.supervisor_halt_on_mem,
            runctx_posts_status=cfg.runctx_posts_status,
        )
    )
    return evaluate_split(sandbox, cand, list(training_workloads()), list(heldout_workloads()))


def run_a() -> list[dict]:
    sandbox = Sandbox(lenient_config(max_steps=8000, n_contexts=5))
    train, held = list(training_workloads()), list(heldout_workloads())
    rows = []
    for name, cand in (
        ("run_to_completion", run_to_completion()),
        ("round_robin_q16", round_robin(16)),
        ("selfish_first", selfish_first()),
    ):
        info = evaluate_split(sandbox, cand, train, held)
        info.update({"condition": "A", "name": name, "seed": None})
        rows.append(info)
    return rows


def _search_cfg(job: dict) -> SearchConfig:
    return SearchConfig(
        generations=int(job["generations"]),
        population=int(job["population"]),
        seed=int(job["seed"]),
        init_len=24,
        eval_heldout_every=10,
        mode="correctness",
        operator_pack="bare",
        keep_records=False,
        shaping=True,
        gated=bool(job["gated"]),
        curiosity=True,
        milestone_archive=True,
        elite_crossover=True,
        archive_mix=0.7,
        elite_fraction=0.25,
        early_stop_complete=bool(job["curriculum"]),
        log_every=10**9,
    )


def run_c(job: dict) -> dict:
    seed = int(job["seed"])
    curriculum = bool(job["curriculum"])
    gated = bool(job["gated"])
    cfg = lenient_config(max_steps=8000)
    search = _search_cfg(job)
    t0 = time.perf_counter()
    stage_logs = []
    stall = None
    archive_dump = []
    if curriculum:
        from evoos.agent.curriculum import run_curriculum as rc

        cr = rc(cfg, search, gens_per_stage=int(job["gens_per_stage"]))
        state = cr.state
        stall = cr.stall_stage
        stage_logs = [
            {
                "name": s.name,
                "n_guests": s.n_guests,
                "gens_ran": s.gens_ran,
                "best_reward": s.best_reward,
                "completed": s.completed,
                "isolation_ok": s.isolation_ok,
                "promoted": s.promoted,
                "stalled": s.stalled,
                "entered": s.entered,
                "entered_protected": s.entered_protected,
                "completed_protected": s.completed_protected,
                "prot_cover": s.prot_cover,
                "shape_f": s.shape_f,
            }
            for s in cr.stages
        ]
        archive_dump = state.archive.dump()
    else:
        sandbox = Sandbox(lenient_config(n_contexts=5, max_steps=8000))
        search.early_stop_complete = False
        state = run_search(sandbox, list(training_workloads()), list(heldout_workloads()), search)
        archive_dump = state.archive.dump()
    elapsed = time.perf_counter() - t0
    tag = "gated" if gated else "ungated"
    row = {
        "condition": "C",
        "name": f"bare_{'curr' if curriculum else 'flat'}_{tag}_s{seed}",
        "seed": seed,
        "curriculum": curriculum,
        "gated": gated,
        "shaping": True,
        "substrate": "lenient",
        "elapsed_sec": elapsed,
        "search_reward": state.best_reward,
        "stall_stage": stall,
        "stages": stage_logs,
        "four_of_four": False,
        "structural_label": "none",
        "operator_pack": "bare",
        "archive_size": state.archive.size(),
        "archive": archive_dump,
    }
    if state.best is not None:
        split = _four_eval(cfg, state.best)
        row.update(split)
        row["diagnose"] = diagnose(split)
        row["best_origin"] = state.best.origin
        row["program"] = instrs_to_jsonable(state.best.instrs)
        row["disassembly"] = disassemble(state.best.instrs)
        row["search_reward"] = state.best_reward
    return row


def _md_table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


def _mean_std(xs):
    if not xs:
        return float("nan"), float("nan"), float("nan")
    return statistics.fmean(xs), (statistics.pstdev(xs) if len(xs) > 1 else 0.0), max(xs)


def write_reports(out: Path, a_rows, c_rows, elapsed, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fields = [
        "condition",
        "name",
        "seed",
        "curriculum",
        "gated",
        "search_reward",
        "train_reward",
        "held_reward",
        "four_of_four",
        "train_completed",
        "held_completed",
        "structural_label",
        "stall_stage",
        "elapsed_sec",
        "diagnose",
        "archive_size",
    ]

    def _flat(row):
        d = dict(row)
        for k, v in list(d.items()):
            if isinstance(v, (list, dict)):
                d[k] = json.dumps(v)
        return d

    with (out / "condition_c.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in c_rows:
            w.writerow(_flat(r))
    with (out / "curriculum_curves.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "seed",
                "config",
                "gated",
                "stage",
                "n_guests",
                "gens_ran",
                "best_reward",
                "completed",
                "entered",
                "entered_protected",
                "prot_cover",
                "shape_f",
                "promoted",
                "stalled",
            ],
        )
        w.writeheader()
        for r in c_rows:
            for s in r.get("stages") or []:
                w.writerow(
                    {
                        "seed": r["seed"],
                        "config": r["name"],
                        "gated": r["gated"],
                        "stage": s["name"],
                        "n_guests": s["n_guests"],
                        "gens_ran": s["gens_ran"],
                        "best_reward": s["best_reward"],
                        "completed": json.dumps(s["completed"]),
                        "entered": json.dumps(s.get("entered")),
                        "entered_protected": json.dumps(s.get("entered_protected")),
                        "prot_cover": json.dumps(s.get("prot_cover")),
                        "shape_f": s.get("shape_f"),
                        "promoted": s["promoted"],
                        "stalled": s["stalled"],
                    }
                )
    (out / "sweep_raw.json").write_text(
        json.dumps({"A": a_rows, "C": c_rows, "meta": meta}, default=str)
    )
    ex = out / "examples"
    ex.mkdir(parents=True, exist_ok=True)
    for r in c_rows:
        if not r.get("disassembly"):
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(r["name"]))[:120]
        (ex / f"{safe}.txt").write_text(
            f"# {r['name']} 4/4={r.get('four_of_four')} stall={r.get('stall_stage')} "
            f"R={r.get('search_reward')} label={r.get('structural_label')}\n"
            f"# diagnose: {r.get('diagnose')}\n\n"
            + str(r["disassembly"])
        )

    lines = []
    lines.append("# Round 4: gated shaping, milestone MAP-Elites, gated vs ungated")
    lines.append("")
    lines.append(f"Wall time **{elapsed:.1f}s** ({elapsed/60:.1f} min), {meta.get('workers')} processes.")
    lines.append(
        f"Search: gens/stage={meta.get('gens_per_stage')} flat gens={meta.get('generations')} "
        f"pop={meta.get('population')} seeds `{list(meta.get('seeds', SEEDS))}`."
    )
    lines.append("Operator pack **bare**. Substrate **lenient**. Stepping-stones: milestone MAP-Elites + curiosity + elite crossover.")
    lines.append("")
    lines.append("## ISA freeze")
    lines.append("")
    lines.append(ISA_NOTE)
    lines.append(f"Opcode count: **{len(list(Op))}** (`HALT`=0 … `RDFAULT`=31).")
    lines.append("")
    lines.append("## Gated shaping")
    lines.append("")
    lines.append(
        "Progress/entered/complete for a guest count only if that guest retired ≥1 instruction "
        "while its assigned data window was already covered. `prot_cover` is on the path, not a "
        "competing 0.20 term. Ungated is the round-3 mix (the R=0.52 trap)."
    )
    lines.append("")
    lines.append(f"Weights: `{SHAPE_WEIGHTS}`")
    lines.append("")
    lines.append("```")
    lines.append((SHAPING_DOCS or "").strip()[:1200])
    lines.append("```")
    lines.append("")
    lines.append("## Condition A (lenient substrate, handwritten)")
    lines.append("")
    lines.append(
        _md_table(
            ["name", "held complete", "4/4", "held R", "label"],
            [[r["name"], r["held_completed"], r["four_of_four"], f"{r['held_reward']:.3f}", r["structural_label"]] for r in a_rows],
        )
    )
    lines.append("")
    lines.append("## Condition C bare — 4/4 on the *full* 4-guest train+held-out")
    lines.append("")
    groups: dict[tuple, list] = {}
    for r in c_rows:
        key = (bool(r["curriculum"]), bool(r["gated"]))
        groups.setdefault(key, []).append(r)
    sum_rows = []
    n_four = 0
    labels_four = []
    for key, chunk in sorted(groups.items(), key=lambda kv: str(kv[0])):
        curr, gated = key
        rs = [float(x["search_reward"]) for x in chunk]
        nf = sum(1 for x in chunk if x.get("four_of_four"))
        n_four += nf
        mean, std, bestv = _mean_std(rs)
        stalls = [x.get("stall_stage") for x in chunk if x.get("stall_stage")]
        labels = sorted({str(x.get("structural_label")) for x in chunk})
        for x in chunk:
            if x.get("four_of_four"):
                labels_four.append(str(x.get("structural_label")))
        sum_rows.append(
            [
                "curr" if curr else "flat",
                "gated" if gated else "ungated",
                str(len(chunk)),
                f"{mean:.3f}",
                f"{std:.3f}",
                f"{bestv:.3f}",
                f"{nf}/{len(chunk)}",
                ",".join(str(s) for s in stalls[:6]) or "—",
                ", ".join(labels),
            ]
        )
    lines.append(
        _md_table(
            ["curriculum", "shaping", "n", "search R mean", "std", "best", "4/4 train+held", "stall stages", "labels"],
            sum_rows,
        )
    )
    lines.append("")
    lines.append("### Per-seed")
    lines.append("")
    per = []
    for r in sorted(c_rows, key=lambda x: (not x["gated"], not x["curriculum"], int(x["seed"]))):
        per.append(
            [
                r["seed"],
                "curr" if r["curriculum"] else "flat",
                "gated" if r["gated"] else "ungated",
                f"{float(r['search_reward']):.3f}",
                r.get("train_completed"),
                r.get("held_completed"),
                "yes" if r.get("four_of_four") else "no",
                r.get("stall_stage") or "—",
                r.get("structural_label"),
                r.get("archive_size"),
            ]
        )
    lines.append(
        _md_table(
            ["seed", "curr", "gate", "search R", "train", "held", "4/4", "stall", "label", "archive"],
            per,
        )
    )
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    if not c_rows:
        lines.append("No C rows.")
    else:
        lines.append(f"**{n_four}/{len(c_rows)}** bare runs reached 4/4 on full train+held-out.")
    k1_enter = k1_prot = k1_complete = k1_n = k1_ent_p = 0
    stall_counts: dict[str, int] = {}
    promoted_any = 0
    for r in c_rows:
        st = r.get("stall_stage") or "none"
        stall_counts[st] = stall_counts.get(st, 0) + 1
        if any(s.get("promoted") for s in (r.get("stages") or [])):
            promoted_any += 1
        for s in r.get("stages") or []:
            if s.get("name") != "k1":
                continue
            k1_n += 1
            if any(int(x) > 0 for x in (s.get("entered") or [])):
                k1_enter += 1
            if any(int(x) > 0 for x in (s.get("entered_protected") or [])):
                k1_ent_p += 1
            covers = s.get("prot_cover") or []
            if any(any(c) for c in covers):
                k1_prot += 1
            if any(int(x) > 0 for x in (s.get("completed") or [])):
                k1_complete += 1
    if k1_n:
        lines.append(
            f"k1 elites: entered {k1_enter}/{k1_n}, entered_protected {k1_ent_p}/{k1_n}, "
            f"prot_cover {k1_prot}/{k1_n}, completed {k1_complete}/{k1_n}."
        )
    lines.append(f"Curriculum jobs that promoted at least one stage: {promoted_any}.")
    if stall_counts:
        lines.append("Stall histogram: " + ", ".join(f"{k}={v}" for k, v in sorted(stall_counts.items())))
    rr_rtc = {"round-robin-like", "run-to-completion-like"}
    other_four = [lb for lb in labels_four if lb not in rr_rtc]
    if n_four == 0:
        lines.append("No 4/4 elites, so no non-RR/RTC discovery to report.")
    elif other_four:
        lines.append(f"4/4 labels that are not RR/RTC: {sorted(set(other_four))}.")
    else:
        lines.append(
            "Every 4/4 elite is labeled round-robin-like or run-to-completion-like. "
            "Nothing structurally non-RR/RTC appeared at this budget."
        )
    lines.append("")
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--generations", type=int, default=0)
    p.add_argument("--gens-per-stage", type=int, default=0)
    p.add_argument("--population", type=int, default=0)
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--out", type=str, default=str(OUT))
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    quick = args.quick
    gens = args.generations or (8 if quick else 500)
    gps = args.gens_per_stage or (4 if quick else 120)
    pop = args.population or (6 if quick else 56)
    workers = args.workers or os.cpu_count() or 2
    seeds = SEEDS[:2] if quick else SEEDS
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    print("== A ==")
    a_rows = run_a()
    for r in a_rows:
        print(" ", r["name"], r["held_completed"], "4/4", r["four_of_four"])

    configs = [
        {"curriculum": True, "gated": True},
        {"curriculum": True, "gated": False},
        {"curriculum": False, "gated": True},
        {"curriculum": False, "gated": False},
    ]
    jobs = []
    for seed in seeds:
        for c in configs:
            jobs.append(
                {
                    **c,
                    "seed": seed,
                    "generations": gens,
                    "gens_per_stage": gps,
                    "population": pop,
                }
            )
    print(f"== C bare gated vs ungated: {len(jobs)} jobs, {workers} workers, gps={gps} pop={pop} gens={gens} ==")
    c_rows = []
    meta = {"workers": workers, "gens_per_stage": gps, "population": pop, "generations": gens, "seeds": list(seeds)}
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_c, job): job for job in jobs}
        done = 0
        for fut in as_completed(futs):
            row = fut.result()
            c_rows.append(row)
            done += 1
            print(
                f"  [{done}/{len(jobs)}] {row['name']} R={float(row['search_reward']):.3f} "
                f"4/4={row.get('four_of_four')} stall={row.get('stall_stage')} "
                f"held={row.get('held_completed')} {row.get('elapsed_sec', 0):.0f}s"
            )
            write_reports(out, a_rows, c_rows, time.perf_counter() - t0, meta)

    elapsed = time.perf_counter() - t0
    write_reports(out, a_rows, c_rows, elapsed, meta)
    print(f"wrote {out} in {elapsed:.1f}s")
    print((out / "summary.md").read_text()[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
