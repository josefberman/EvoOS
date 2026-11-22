"""Round 5: learned GRU edit policy vs blind UCB1, equal evals, gated curriculum.

ISA freeze: opcodes/semantics unchanged. Parallel agent maps ADDPROT/RUNCTX
onto real x86; this sweep must not add, remove, or renumber ops.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from pathlib import Path

from evoos.agent.evolutionary_search import SearchConfig
from evoos.agent.learned_policy import LearnedEditPolicy
from evoos.agent.mutations import BARE_OPS
from evoos.evaluator.shaping import SHAPE_WEIGHTS, SHAPING_DOCS
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.experiments.scheduling.metrics import diagnose, evaluate_split
from evoos.vm.instruction_set import Op, disassemble, instrs_to_jsonable
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox

SEEDS = (1, 7, 13, 42, 99, 2024)
OUT = Path("experiments/scheduling/results/round5")
ISA_NOTE = (
    "ISA freeze: opcodes HALT..RDFAULT remain 0..31; CTXGET fields including "
    "LAST_RUN_* are unchanged. Round 5 trains an edit policy over existing "
    "5-tuples. A C interpreter/boot stub must match the existing encoding only."
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


def _search_cfg(job: dict, learned=None) -> SearchConfig:
    return SearchConfig(
        generations=int(job["generations"]),
        population=int(job["population"]),
        seed=int(job["seed"]),
        init_len=24,
        max_len=64,
        eval_heldout_every=0,
        mode="correctness",
        operator_pack="bare",
        keep_records=False,
        shaping=True,
        gated=True,
        curiosity=True,
        milestone_archive=True,
        elite_crossover=True,
        archive_mix=0.7,
        elite_fraction=0.25,
        early_stop_complete=True,
        log_every=int(job.get("log_every", 25)),
        policy=str(job["policy"]),
        learned=learned,
        eval_workers=int(job.get("eval_workers", 1)),
    )


def _ckpt_write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, default=str))
    tmp.replace(path)


def run_c(job: dict, learned=None, ckpt_cb=None) -> dict:
    seed = int(job["seed"])
    arm = str(job["arm"])
    cfg = lenient_config(max_steps=4000)
    search = _search_cfg(job, learned=learned)
    t0 = time.perf_counter()
    from evoos.agent.curriculum import run_curriculum as rc

    curves_acc: list[dict] = []

    def on_generation(gen, state, row):
        curves_acc.append(
            {
                "generation": gen,
                "best_reward": row.get("best_reward"),
                "mean_reward": row.get("mean_reward"),
                "archive_size": row.get("archive_size"),
                "policy_loss": row.get("policy_loss"),
                "n_eval": row.get("n_eval"),
            }
        )
        if ckpt_cb is not None and gen % max(int(job.get("log_every", 25)), 1) == 0:
            ckpt_cb(
                {
                    "arm": arm,
                    "seed": seed,
                    "generation": gen,
                    "best_reward": state.best_reward,
                    "archive_size": state.archive.size(),
                    "n_history": len(state.history),
                }
            )

    cr = rc(cfg, search, gens_per_stage=int(job["gens_per_stage"]), on_generation=on_generation)
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
            "n_eval": s.n_eval,
            "history": s.history,
        }
        for s in cr.stages
    ]
    elapsed = time.perf_counter() - t0
    n_eval = sum(int(s.get("n_eval") or 0) for s in stage_logs)
    snap = {}
    if learned is not None and hasattr(learned, "snapshot"):
        snap = learned.snapshot()
    elif hasattr(state, "policy") and hasattr(state.policy, "snapshot"):
        snap = state.policy.snapshot()
    row = {
        "condition": "C",
        "name": f"bare_curr_gated_{arm}_s{seed}",
        "seed": seed,
        "arm": arm,
        "policy": job["policy"],
        "curriculum": True,
        "gated": True,
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
        "archive": state.archive.dump(),
        "n_eval": n_eval,
        "policy_snapshot": snap,
        "live_curve": curves_acc,
    }
    if state.best is not None:
        split = _four_eval(lenient_config(max_steps=8000), state.best)
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
        "arm",
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
        "n_eval",
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

    with (out / "learning_curves.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "arm",
                "seed",
                "stage",
                "generation",
                "cum_eval",
                "best_reward",
                "mean_reward",
                "archive_size",
                "policy_loss",
            ],
        )
        w.writeheader()
        for r in c_rows:
            cum = 0
            for s in r.get("stages") or []:
                for h in s.get("history") or []:
                    cum += int(h.get("n_eval") or 0)
                    w.writerow(
                        {
                            "arm": r.get("arm"),
                            "seed": r["seed"],
                            "stage": s["name"],
                            "generation": h.get("generation"),
                            "cum_eval": cum,
                            "best_reward": h.get("best_reward"),
                            "mean_reward": h.get("mean_reward"),
                            "archive_size": h.get("archive_size"),
                            "policy_loss": h.get("policy_loss"),
                        }
                    )

    with (out / "curriculum_curves.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "seed",
                "arm",
                "stage",
                "n_guests",
                "gens_ran",
                "n_eval",
                "best_reward",
                "completed",
                "entered",
                "entered_protected",
                "completed_protected",
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
                        "arm": r.get("arm"),
                        "stage": s["name"],
                        "n_guests": s["n_guests"],
                        "gens_ran": s["gens_ran"],
                        "n_eval": s.get("n_eval"),
                        "best_reward": s["best_reward"],
                        "completed": json.dumps(s["completed"]),
                        "entered": json.dumps(s.get("entered")),
                        "entered_protected": json.dumps(s.get("entered_protected")),
                        "completed_protected": json.dumps(s.get("completed_protected")),
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
            f"R={r.get('search_reward')} label={r.get('structural_label')} arm={r.get('arm')}\n"
            f"# diagnose: {r.get('diagnose')}\n"
            f"# n_eval={r.get('n_eval')} archive={r.get('archive_size')}\n\n"
            + str(r["disassembly"])
        )

    lines = []
    lines.append("# Round 5: learned edit policy vs blind mutation")
    lines.append("")
    lines.append(f"Wall time **{elapsed:.1f}s** ({elapsed/60:.1f} min), eval_workers={meta.get('eval_workers')}.")
    lines.append(
        f"Search: gens/stage={meta.get('gens_per_stage')} pop={meta.get('population')} "
        f"seeds `{list(meta.get('seeds', SEEDS))}`. Operator pack **bare**. Substrate **lenient**. "
        f"Gated curriculum + milestone MAP-Elites. k1 promotes on a 1/1 protected-complete stone."
    )
    lines.append(
        f"Target evals/seed ≈ {int(meta.get('gens_per_stage', 0)) * int(meta.get('population', 0)) * 5} "
        f"(5 stages, before early-stop). Round 4 was 120×56×5 = 33600; this run is "
        f"~{((int(meta.get('gens_per_stage', 0)) * int(meta.get('population', 0)) * 5) / 33600):.1f}× that budget."
    )
    lines.append("")
    lines.append("## ISA freeze")
    lines.append("")
    lines.append(ISA_NOTE)
    lines.append(f"Opcode count: **{len(list(Op))}** (`HALT`=0 … `RDFAULT`=31).")
    lines.append("")
    lines.append("## Policy")
    lines.append("")
    lines.append(
        "Learned arm: GRU over instruction tokens (op,a,b,c,imm) plus a 12-d telemetry/"
        "milestone vector. Heads: edit kind, position, opcode, registers, immediate table. "
        "REINFORCE with a value baseline on (child−parent) reward, plus a new-cell bonus from "
        "the MAP-Elites archive. ε-greedy mixes a UCB1 operator arm. One network is shared "
        "across seeds (online). Blind arm: UCB1 over the same bare operators, independent per seed."
    )
    lines.append("")
    lines.append(f"Shaping weights: `{SHAPE_WEIGHTS}`")
    lines.append("")
    lines.append("```")
    lines.append((SHAPING_DOCS or "").strip()[:800])
    lines.append("```")
    lines.append("")
    lines.append("## Condition A (lenient, handwritten)")
    lines.append("")
    lines.append(
        _md_table(
            ["name", "held complete", "4/4", "held R", "label"],
            [
                [
                    r["name"],
                    r["held_completed"],
                    r["four_of_four"],
                    f"{r['held_reward']:.3f}",
                    r["structural_label"],
                ]
                for r in a_rows
            ],
        )
    )
    lines.append("")
    lines.append("## Condition C — learned vs blind, equal eval budget")
    lines.append("")
    groups: dict[str, list] = {}
    for r in c_rows:
        groups.setdefault(str(r.get("arm")), []).append(r)
    sum_rows = []
    n_four = 0
    labels_four = []
    for arm, chunk in sorted(groups.items()):
        rs = [float(x["search_reward"]) for x in chunk]
        nf = sum(1 for x in chunk if x.get("four_of_four"))
        n_four += nf
        mean, std, bestv = _mean_std(rs)
        evals = [int(x.get("n_eval") or 0) for x in chunk]
        stalls = [x.get("stall_stage") for x in chunk if x.get("stall_stage")]
        labels = sorted({str(x.get("structural_label")) for x in chunk})
        for x in chunk:
            if x.get("four_of_four"):
                labels_four.append(str(x.get("structural_label")))
        sum_rows.append(
            [
                arm,
                str(len(chunk)),
                f"{mean:.3f}",
                f"{std:.3f}",
                f"{bestv:.3f}",
                f"{nf}/{len(chunk)}",
                f"{statistics.fmean(evals):.0f}" if evals else "0",
                ",".join(str(s) for s in stalls[:6]) or "—",
                ", ".join(labels),
            ]
        )
    lines.append(
        _md_table(
            [
                "arm",
                "n",
                "search R mean",
                "std",
                "best",
                "4/4 train+held",
                "mean n_eval",
                "stall stages",
                "labels",
            ],
            sum_rows,
        )
    )
    lines.append("")
    lines.append("### Per-seed")
    lines.append("")
    per = []
    for r in sorted(c_rows, key=lambda x: (str(x.get("arm")), int(x["seed"]))):
        per.append(
            [
                r.get("arm"),
                r["seed"],
                f"{float(r['search_reward']):.3f}",
                r.get("train_completed"),
                r.get("held_completed"),
                "yes" if r.get("four_of_four") else "no",
                r.get("stall_stage") or "—",
                r.get("structural_label"),
                r.get("archive_size"),
                r.get("n_eval"),
            ]
        )
    lines.append(
        _md_table(
            ["arm", "seed", "search R", "train", "held", "4/4", "stall", "label", "archive", "n_eval"],
            per,
        )
    )
    lines.append("")
    lines.append("## Edits the learned policy preferred")
    lines.append("")
    learned_rows = [r for r in c_rows if r.get("arm") == "learned"]
    if learned_rows:
        last = learned_rows[-1].get("policy_snapshot") or {}
        counts = last.get("kind_counts") or {}
        adv = last.get("kind_adv") or last.get("preferred_edits") or []
        if counts:
            ranked = sorted(counts.items(), key=lambda kv: -kv[1])
            lines.append("Visit counts (shared net, after last learned seed):")
            lines.append("")
            lines.append(
                _md_table(
                    ["edit", "count"],
                    [[k, v] for k, v in ranked],
                )
            )
            lines.append("")
        if isinstance(adv, dict) and adv:
            ranked_a = sorted(adv.items(), key=lambda kv: -float(kv[1]))
            lines.append("Mean advantage EMA:")
            lines.append("")
            lines.append(
                _md_table(
                    ["edit", "adv"],
                    [[k, f"{float(v):.4f}"] for k, v in ranked_a],
                )
            )
            lines.append("")
        elif isinstance(adv, list) and adv:
            lines.append("Preferred edits (kind, adv): " + ", ".join(str(x) for x in adv))
            lines.append("")
    else:
        lines.append("No learned-arm rows.")
        lines.append("")
    lines.append("## Learning curves")
    lines.append("")
    lines.append(
        "Per-generation `best_reward` vs cumulative evaluations: "
        "[`learning_curves.csv`](learning_curves.csv). Stage-level promotion: "
        "[`curriculum_curves.csv`](curriculum_curves.csv)."
    )
    lines.append("")
    # Compact mean best_reward at matched eval checkpoints.
    for arm, chunk in sorted(groups.items()):
        finals = [float(x["search_reward"]) for x in chunk]
        n_ev = [int(x.get("n_eval") or 0) for x in chunk]
        lines.append(
            f"- **{arm}**: final search R mean {statistics.fmean(finals):.3f} "
            f"over {sum(n_ev)} total evals "
            f"({statistics.fmean(n_ev):.0f}/seed)."
        )
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    if not c_rows:
        lines.append("No C rows.")
    else:
        lines.append(
            f"**{n_four}/{len(c_rows)}** bare runs reached 4/4 on full train+held-out "
            f"({sum(1 for x in c_rows if x.get('arm')=='learned' and x.get('four_of_four'))}/"
            f"{sum(1 for x in c_rows if x.get('arm')=='learned')} learned, "
            f"{sum(1 for x in c_rows if x.get('arm')=='blind' and x.get('four_of_four'))}/"
            f"{sum(1 for x in c_rows if x.get('arm')=='blind')} blind)."
        )
    k1_n = k1_enter = k1_ent_p = k1_prot = k1_complete = k1_promote = 0
    stall_counts: dict[str, int] = {}
    for r in c_rows:
        st = r.get("stall_stage") or "none"
        stall_counts[st] = stall_counts.get(st, 0) + 1
        for s in r.get("stages") or []:
            if s.get("name") != "k1":
                continue
            k1_n += 1
            if s.get("promoted"):
                k1_promote += 1
            if any(int(x) > 0 for x in (s.get("entered") or [])):
                k1_enter += 1
            if any(int(x) > 0 for x in (s.get("entered_protected") or [])):
                k1_ent_p += 1
            covers = s.get("prot_cover") or []
            if any(any(c) for c in covers):
                k1_prot += 1
            if any(int(x) > 0 for x in (s.get("completed_protected") or s.get("completed") or [])):
                k1_complete += 1
    if k1_n:
        lines.append(
            f"k1: entered {k1_enter}/{k1_n}, entered_protected {k1_ent_p}/{k1_n}, "
            f"prot_cover {k1_prot}/{k1_n}, protected-complete {k1_complete}/{k1_n}, "
            f"promoted {k1_promote}/{k1_n}."
        )
    if stall_counts:
        lines.append("Stall histogram: " + ", ".join(f"{k}={v}" for k, v in sorted(stall_counts.items())))
    rr_rtc = {"round-robin-like", "run-to-completion-like"}
    other_four = [lb for lb in labels_four if lb not in rr_rtc]
    four_rows = [r for r in c_rows if r.get("four_of_four")]
    if n_four == 0:
        lines.append("No 4/4 elites, so no non-RR/RTC discovery to report.")
    elif other_four:
        lines.append(f"4/4 labels that are not RR/RTC: {sorted(set(other_four))}.")
    else:
        lines.append(
            "Every 4/4 elite is labeled round-robin-like or run-to-completion-like. "
            "Nothing structurally non-RR/RTC appeared at this budget."
        )
    if four_rows:
        lines.append("")
        lines.append("### 4/4 elite disassembly pointers")
        lines.append("")
        for r in four_rows:
            lines.append(
                f"- `{r['name']}` label=`{r.get('structural_label')}` "
                f"origin=`{r.get('best_origin')}` → [`examples/{r['name']}.txt`](examples/{r['name']}.txt)"
            )
    lines.append("")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    (out / "README.md").write_text(
        "Round 5 tables: [`summary.md`](summary.md). CSVs: `condition_c.csv`, "
        "`learning_curves.csv`, `curriculum_curves.csv`. Disassemblies: `examples/`. "
        "ISA opcodes were not changed.\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--gens-per-stage", type=int, default=0)
    p.add_argument("--population", type=int, default=0)
    p.add_argument("--eval-workers", type=int, default=0)
    p.add_argument("--out", type=str, default=str(OUT))
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    quick = args.quick
    gps = args.gens_per_stage or (2 if quick else 1000)
    pop = args.population or (4 if quick else 64)
    workers = args.eval_workers or os.cpu_count() or 2
    seeds = SEEDS[:2] if quick else SEEDS
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ckpt_dir = out / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    print("== A ==")
    a_rows = run_a()
    for r in a_rows:
        print(" ", r["name"], r["held_completed"], "4/4", r["four_of_four"])

    c_rows: list[dict] = []
    raw_path = out / "sweep_raw.json"
    if raw_path.exists():
        try:
            prev = json.loads(raw_path.read_text())
            c_rows = list(prev.get("C") or [])
            print(f"resuming with {len(c_rows)} completed C rows")
        except Exception:
            c_rows = []
    done = {(r.get("arm"), int(r["seed"])) for r in c_rows if r.get("arm") is not None}

    meta = {
        "eval_workers": workers,
        "gens_per_stage": gps,
        "population": pop,
        "generations": gps,
        "seeds": list(seeds),
        "round4_evals_per_seed": 120 * 56 * 5,
        "target_evals_per_seed": gps * pop * 5,
    }

    def persist():
        write_reports(out, a_rows, c_rows, time.perf_counter() - t0, meta)

    def live_ckpt(info):
        _ckpt_write(ckpt_dir / "live.json", info)

    # Shared GRU across learned seeds.
    shared = LearnedEditPolicy(operators=BARE_OPS, epsilon=0.12, new_cell_bonus=0.08)
    policy_path = ckpt_dir / "policy.pt"
    if policy_path.exists():
        try:
            import torch

            try:
                blob = torch.load(policy_path, map_location="cpu", weights_only=False)
            except TypeError:
                blob = torch.load(policy_path, map_location="cpu")
            if isinstance(blob, dict) and "net" in blob:
                shared.load_state_dict(blob["net"])
                shared.kind_counts.update(blob.get("counts") or {})
                shared.n_updates = int(blob.get("n") or 0)
            print("loaded shared policy from", policy_path)
        except Exception as exc:
            print("policy load skipped:", exc)

    jobs = []
    for arm, policy in (("learned", "learned"), ("blind", "ucb1")):
        for seed in seeds:
            jobs.append(
                {
                    "arm": arm,
                    "policy": policy,
                    "seed": seed,
                    "generations": gps,
                    "gens_per_stage": gps,
                    "population": pop,
                    "eval_workers": workers,
                    "log_every": 1 if quick else 25,
                }
            )

    print(
        f"== C learned vs blind: {len(jobs)} jobs, eval_workers={workers}, "
        f"gps={gps} pop={pop} ~{gps * pop * 5} evals/seed =="
    )
    for job in jobs:
        key = (job["arm"], int(job["seed"]))
        if key in done:
            print(f"  skip {job['arm']} s{job['seed']} (checkpoint)")
            continue
        learned = shared if job["arm"] == "learned" else None
        print(f"  start {job['arm']} s{job['seed']}")
        row = run_c(job, learned=learned, ckpt_cb=live_ckpt)
        c_rows.append(row)
        done.add(key)
        if job["arm"] == "learned":
            shared.save(policy_path)
            _ckpt_write(ckpt_dir / f"learned_seed{job['seed']}.json", {"snapshot": row.get("policy_snapshot")})
        persist()
        print(
            f"  done {row['name']} R={float(row['search_reward']):.3f} "
            f"4/4={row.get('four_of_four')} stall={row.get('stall_stage')} "
            f"n_eval={row.get('n_eval')} {row.get('elapsed_sec', 0):.0f}s"
        )

    elapsed = time.perf_counter() - t0
    persist()
    print(f"wrote {out} in {elapsed:.1f}s")
    print((out / "summary.md").read_text()[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
