"""Round 10: safety in the search loop, not a post-hoc audit."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from collections import Counter
from pathlib import Path

from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.agent.program import Candidate
from evoos.agent.routing_curriculum import routing_sandbox
from evoos.evaluator.hw_workloads import (
    generalization_held,
    generalization_train,
    heldout_audit_workloads,
    hw_heldout,
    hw_training,
    mix_variant,
    mixed_pressure_route,
    official_split,
    route_workload,
    train_audit_bank,
)
from evoos.evaluator.novelty import org_label
from evoos.evaluator.share_audit import (
    admission_audit_candidate,
    audit_candidate,
    heldout_audit_candidate,
)
from evoos.experiments.scheduling.hw_baselines import fifo_swap_rr, payload_copy_rr
from evoos.experiments.scheduling.metrics import case_failure_table, diagnose, evaluate_split
from evoos.export.embed import embed_document, write_embed
from evoos.vm.instruction_set import (
    Op,
    disassemble,
    instrs_from_jsonable,
    instrs_to_jsonable,
    parse_disassembly,
)

SEEDS = (1, 7, 13, 42, 99, 2024)
OUT = Path("experiments/scheduling/results/round10")
ISA_NOTE = (
    "ISA: opcodes 0–31 unchanged; 32–34 are BLKRD/BLKWR/SVC. "
    "No pipe / message-queue opcode."
)
# Mix (8) + two sampled attackers; extra ctx slots so grants cannot hide.
N_SANDBOX_GUESTS = 12


def _load_examples(root: Path, preferred: list[str], id_base: int) -> list[Candidate]:
    found: dict[str, Candidate] = {}
    if root.exists():
        for p in sorted(root.glob("*.txt")):
            try:
                instrs = parse_disassembly(p.read_text())
            except ValueError:
                continue
            found[p.stem] = Candidate(instrs=instrs, origin=p.stem, id=id_base - len(found))
    out = [found[n] for n in preferred if n in found]
    for name, cand in found.items():
        if name not in preferred:
            out.append(cand)
    return out


def _seed_pool() -> list[Candidate]:
    """Round-8/9 elites that pass the on-device 8-ctx admission audit.

    Round-9 1/13/2024 fail that gate (ADDPROT extra ctx ids). They are not seeds.
    Round-8 shared-window blobs are kept only if the hard constraint accepts them.
    """
    r9 = _load_examples(
        Path("experiments/scheduling/results/round9/examples"),
        ["gen_s1", "gen_s13", "gen_s2024", "gen_s7", "gen_s42", "gen_s99"],
        -10,
    )
    r8 = _load_examples(
        Path("experiments/scheduling/results/round8/examples"),
        ["route_s42", "route_s1", "route_s13", "route_s2024", "route_s99", "route_s7"],
        -30,
    )
    # Passers first. Leaky round-8 blobs stay in the pool so mutations can drop extra grants.
    ordered = list(r8) + list(r9)
    passers = [c for c in ordered if admission_audit_candidate(c).get("pass")]
    seen: set[str] = set()
    out: list[Candidate] = []
    for cand in passers + ordered:
        key = cand.origin or str(id(cand))
        if key in seen:
            continue
        seen.add(key)
        out.append(cand)
    return out


def _ckpt(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, default=str))
    tmp.replace(path)


def _md_table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(x) for x in r) + " |")
    return "\n".join(lines)


def search_train() -> list:
    """Official 4/4 cores plus one shuffled variant. Sampled audit is attached per generation."""
    return [
        route_workload("train", 4),
        mixed_pressure_route("train"),
        generalization_train()[2],  # gen_rev4_train
    ]


def run_baselines(sb) -> list[dict]:
    rows = []
    train, held = official_split()
    r7_train, r7_held = list(hw_training()), list(hw_heldout())
    for name, cand in (("payload_copy_rr", payload_copy_rr(32)), ("fifo_swap_rr", fifo_swap_rr(32))):
        split = evaluate_split(sb, cand, train, held)
        r7 = evaluate_split(sb, cand, r7_train, r7_held)
        held_audit = admission_audit_candidate(cand)
        train_audit = audit_candidate(sb, cand)
        r0 = sb.evaluate(cand.instrs, train[0])
        split.update(
            {
                "name": name,
                "suite": "official_4x4",
                "r7_four_of_four": r7["four_of_four"],
                "r7_train_completed": r7["train_completed"],
                "held_audit_pass": held_audit["pass"],
                "held_audit_n_leak": held_audit["n_leak"],
                "train_audit_pass": train_audit["pass"],
                "n_clrprot": r0.n_clrprot,
                "n_stale": r0.n_stale,
                "style": r0.route_style,
                "org": org_label(r0),
                "delivered": r0.delivered,
            }
        )
        rows.append(split)
    return rows


def run_one(job: dict, seeds: list[Candidate], ckpt_cb=None) -> dict:
    seed = int(job["seed"])
    gens = int(job["gens_per_stage"])
    pop = int(job["population"])
    workers = int(job.get("eval_workers", 1))
    max_steps = int(job.get("max_steps", 4000))
    sb = routing_sandbox(N_SANDBOX_GUESTS, max_steps=max_steps)
    train = search_train()
    held = list(generalization_held())[:2]
    cfg = SearchConfig(
        generations=gens,
        population=pop,
        seed=seed,
        init_len=48,
        max_len=128,
        mode="correctness",
        operator_pack="hw",
        shaping=True,
        gated=True,
        log_every=int(job.get("log_every", 50)),
        policy="ucb1",
        eval_workers=workers,
        selection="epsilon_lexicase",
        dataflow=True,
        hw_archive=True,
        route_archive=True,
        early_stop_complete=True,
        eval_heldout_every=0,
        curiosity=True,
        elite_crossover=True,
        archive_mix=0.7,
        elite_fraction=0.25,
        keep_records=False,
        audit_bank=train_audit_bank(),
        audit_sample=2,
        leak_invalid=True,
        revoke_shaping=True,
    )
    curves = []

    def on_generation(gen, state, row):
        curves.append(
            {
                "generation": gen,
                "best_reward": row.get("best_reward"),
                "n_eval": row.get("n_eval"),
                "n_invalid": row.get("n_invalid"),
            }
        )
        if ckpt_cb is not None and gen % max(int(job.get("log_every", 50)), 1) == 0:
            ckpt_cb({"seed": seed, "generation": gen, "best_reward": state.best_reward})

    t0 = time.perf_counter()
    state = run_search(sb, train, held, cfg, extra_seeds=seeds, on_generation=on_generation)
    elapsed = time.perf_counter() - t0
    row = {
        "condition": "C-hw-safe",
        "name": f"safe_s{seed}",
        "seed": seed,
        "elapsed_sec": elapsed,
        "search_reward": state.best_reward,
        "n_eval": sum(int(h.get("n_eval") or 0) for h in state.history),
        "n_invalid": sum(int(h.get("n_invalid") or 0) for h in state.history),
        "gens_ran": len(state.history),
        "four_of_four": False,
        "r7_four_of_four": False,
        "held_audit_pass": False,
        "live_curve": curves[-40:],
        "first_solved": dict(state.first_solved),
        "operator_pack": "hw",
    }
    if state.best is None:
        return row
    cand = state.best
    off_train, off_held = official_split()
    split = evaluate_split(sb, cand, off_train, off_held)
    r7 = evaluate_split(sb, cand, list(hw_training()), list(hw_heldout()))
    held_audit = admission_audit_candidate(cand)
    train_audit = audit_candidate(sb, cand)
    r4 = sb.evaluate(cand.instrs, route_workload("train", 4))
    r4h = sb.evaluate(cand.instrs, route_workload("heldout", 3))
    mix = sb.evaluate(cand.instrs, mixed_pressure_route("train"))
    mixh = sb.evaluate(cand.instrs, mixed_pressure_route("heldout"))
    shuf = sb.evaluate(cand.instrs, train[2])
    failures = case_failure_table(sb, cand, off_train, off_held)
    row.update(
        {
            "four_of_four": split["four_of_four"],
            "train_completed": split["train_completed"],
            "held_completed": split["held_completed"],
            "train_isolation": split["train_isolation"],
            "held_isolation": split["held_isolation"],
            "train_reward": split["train_reward"],
            "held_reward": split["held_reward"],
            "diagnose": diagnose(split),
            "r7_four_of_four": r7["four_of_four"],
            "r7_train_completed": r7["train_completed"],
            "r7_held_completed": r7["held_completed"],
            "r7_diagnose": diagnose(r7),
            "r4_delivered": r4.delivered,
            "r4_held_delivered": r4h.delivered,
            "r4_completed": r4.completed,
            "mix_delivered": mix.delivered,
            "mix_completed": mix.completed,
            "mix_held_completed": mixh.completed,
            "shuf_delivered": shuf.delivered,
            "shuf_completed": shuf.completed,
            "style": r4.route_style or mix.route_style,
            "org": org_label(r4 if r4.delivered else mix),
            "shared_window": bool((r4.shared_window and r4.delivered) or (mix.shared_window and mix.delivered)),
            "revoked": bool(r4.revoked or mix.revoked),
            "share_leak": bool(r4.share_leak or mix.share_leak or r4h.share_leak or mixh.share_leak),
            "n_clrprot": max(r4.n_clrprot, mix.n_clrprot, r4h.n_clrprot, mixh.n_clrprot),
            "n_stale": max(r4.n_stale, mix.n_stale, r4h.n_stale, mixh.n_stale),
            "held_audit_pass": held_audit["pass"],
            "held_audit_n_leak": held_audit["n_leak"],
            "held_audit_n_attacker_fail": held_audit["n_attacker_fail"],
            "held_audit_n_revoked": held_audit["n_revoked"],
            "held_audit_n_stale": held_audit.get("n_stale", 0),
            "held_audit_n_clrprot": held_audit.get("n_clrprot", 0),
            "held_audit_cases": held_audit["cases"],
            "train_audit_pass": train_audit["pass"],
            "train_audit_n_leak": train_audit["n_leak"],
            "isolation": split["train_isolation"] and split["held_isolation"] and held_audit["pass"],
            "case_failures": failures,
            "disassembly": disassemble(cand.instrs),
            "program": instrs_to_jsonable(cand.instrs),
            "best_origin": cand.origin,
            "length": len(cand.instrs),
        }
    )
    return row


def embed_cases():
    train, held = official_split()
    return (
        train
        + held
        + list(search_train())
        + list(heldout_audit_workloads())
        + list(train_audit_bank())
        + list(hw_training())
        + list(hw_heldout())
        + [mix_variant("heldout", route_first=True, name="boot_mix_rf")]
    )


def _aggregate_blockers(c_rows: list[dict]) -> list[list]:
    """Per-case failure table across seeds that missed official 4/4."""
    counts: Counter = Counter()
    role_counts: Counter = Counter()
    isol_fail = 0
    n = 0
    for r in c_rows:
        if r.get("four_of_four"):
            continue
        n += 1
        for case in r.get("case_failures") or []:
            key = f"{case.get('split')}:{case.get('workload')}"
            if not case.get("isolation_ok"):
                isol_fail += 1
                counts[key + " isolation"] += 1
            miss = int(case.get("n_guests") or 0) - int(case.get("completed") or 0)
            if miss > 0:
                counts[key + f" incomplete({miss})"] += 1
            for g in case.get("failed") or []:
                role_counts[f"{key} {g.get('role') or g.get('name')}"] += 1
    rows = []
    for k, v in counts.most_common():
        rows.append([k, v, f"{v}/{n}" if n else "0"])
    return rows, role_counts.most_common(), n, isol_fail


def write_reports(out: Path, a_rows, c_rows, elapsed, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    embed_dir = out / "embed"
    embed_dir.mkdir(exist_ok=True)
    ex = out / "examples"
    ex.mkdir(exist_ok=True)

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
        "four_of_four",
        "r7_four_of_four",
        "r4_delivered",
        "r4_held_delivered",
        "mix_completed",
        "mix_held_completed",
        "shuf_completed",
        "style",
        "org",
        "shared_window",
        "revoked",
        "share_leak",
        "held_audit_pass",
        "n_clrprot",
        "n_stale",
        "elapsed_sec",
        "n_eval",
        "n_invalid",
        "gens_ran",
    ]
    with (out / "condition_c.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in c_rows:
            w.writerow(_flat(r))
    (out / "sweep_raw.json").write_text(json.dumps({"A": a_rows, "C": c_rows, "meta": meta}, default=str))
    cases = embed_cases()
    for r in c_rows:
        if not r.get("disassembly"):
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(r["name"]))[:120]
        audit_flag = bool(r.get("held_audit_pass"))
        (ex / f"{safe}.txt").write_text(
            f"# {r['name']} 4/4={r.get('four_of_four')} r7={r.get('r7_four_of_four')} "
            f"held_audit={audit_flag} R={r.get('search_reward')} org={r.get('org')} "
            f"style={r.get('style')} origin={r.get('best_origin')}\n"
            f"# r4={r.get('r4_delivered')}/{r.get('r4_held_delivered')} mix={r.get('mix_completed')} "
            f"leak={r.get('share_leak')} revoked={r.get('revoked')} n_clrprot={r.get('n_clrprot')} "
            f"n_stale={r.get('n_stale')}\n\n"
            + str(r["disassembly"])
        )
        instrs = instrs_from_jsonable(r["program"])
        doc = embed_document(
            supervisor=instrs,
            origin=str(r.get("best_origin") or ""),
            label=str(r["name"]),
            cases=cases,
            machine={
                "n_contexts": N_SANDBOX_GUESTS + 1,
                "n_blocks": 16,
                "block_words": 32,
                "blk_latency": 2,
                "max_steps": 8000,
                "mem_words": 2048,
                "lenient": True,
            },
            notes=(
                f"round10 seed={r.get('seed')} style={r.get('style')} org={r.get('org')} "
                f"4/4={r.get('four_of_four')} audit_pass={audit_flag} n_clrprot={r.get('n_clrprot')}"
            ),
        )
        doc["audit_pass"] = audit_flag
        write_embed(embed_dir / f"{safe}.json", doc, instrs)

    lines = []
    lines.append("# Round 10: in-search safety (hard leak + rotating attackers)")
    lines.append("")
    lines.append(f"Wall time **{elapsed:.1f}s** ({elapsed/60:.1f} min), eval_workers={meta.get('eval_workers')}.")
    lines.append(ISA_NOTE)
    lines.append(f"Opcode count: **{len(list(Op))}**.")
    lines.append(
        f"generations={meta.get('generations')} pop={meta.get('population')} seeds={meta.get('seeds')} "
        f"n_seeds_in={meta.get('n_seeds_in')} audit_sample={meta.get('audit_sample')}."
    )
    lines.append("")
    lines.append("Boot-track embed: [`embed/`](embed/) + [`evoos/export/FORMAT.md`](../../../../evoos/export/FORMAT.md).")
    lines.append("Elites carry `audit_pass` from the on-device 8-ctx admission gate (`n_contexts=8`, pair window `[256,384)`).")
    lines.append("")
    lines.append("## Baselines (official 4/4 split = route_4 + mix, train AND held)")
    lines.append("")
    lines.append(
        _md_table(
            ["name", "4/4", "r7 4/4", "train completed", "held completed", "held-out audit", "style", "n_clrprot"],
            [
                [
                    r["name"],
                    r.get("four_of_four"),
                    r.get("r7_four_of_four"),
                    r.get("train_completed"),
                    r.get("held_completed"),
                    r.get("held_audit_pass"),
                    r.get("style"),
                    r.get("n_clrprot"),
                ]
                for r in a_rows
            ],
        )
    )
    lines.append("")
    lines.append("## Condition C-hw in-search safety")
    lines.append("")
    n4 = sum(1 for r in c_rows if r.get("four_of_four"))
    n7 = sum(1 for r in c_rows if r.get("r7_four_of_four"))
    n_audit = sum(1 for r in c_rows if r.get("held_audit_pass"))
    n_leak = sum(1 for r in c_rows if r.get("share_leak") or (r.get("held_audit_n_leak") or 0) > 0)
    rs = [float(x.get("search_reward") or 0) for x in c_rows]
    mean = statistics.fmean(rs) if rs else float("nan")
    std = statistics.pstdev(rs) if len(rs) > 1 else 0.0
    bl_r4 = next((r.get("delivered") for r in a_rows if r.get("name") == "payload_copy_rr"), None)
    lines.append(
        _md_table(
            ["arm", "search R mean ± std (best)", "4/4 split", "held-out audit", "r7 4/4", "mean n_eval"],
            [
                [
                    "safe_lexicase",
                    f"{mean:.3f} ± {std:.3f} ({max(rs):.3f})" if rs else "n/a",
                    f"**{n4}/{len(c_rows)}**",
                    f"**{n_audit}/{len(c_rows)}**",
                    f"**{n7}/{len(c_rows)}**",
                    f"{statistics.fmean([int(x.get('n_eval') or 0) for x in c_rows]):.0f}" if c_rows else "n/a",
                ]
            ],
        )
    )
    lines.append("")
    lines.append(
        _md_table(
            [
                "seed",
                "R",
                "4/4",
                "held audit",
                "r4 deliv t/h",
                "mix t/h",
                "style",
                "org",
                "n_clrprot",
                "n_stale",
                "leak",
            ],
            [
                [
                    r.get("seed"),
                    f"{float(r.get('search_reward') or 0):.3f}",
                    r.get("four_of_four"),
                    r.get("held_audit_pass"),
                    f"{r.get('r4_delivered')}/{r.get('r4_held_delivered')}",
                    f"{r.get('mix_completed')}/{r.get('mix_held_completed')}",
                    r.get("style"),
                    r.get("org"),
                    r.get("n_clrprot"),
                    r.get("n_stale"),
                    r.get("share_leak"),
                ]
                for r in c_rows
            ],
        )
    )
    styles = sorted({r.get("style") for r in c_rows if r.get("style")})
    orgs = sorted({r.get("org") for r in c_rows if r.get("org")})
    n_share = sum(1 for r in c_rows if r.get("shared_window"))
    lines.append("")
    lines.append(
        f"Styles: {styles}. Orgs: {orgs}. Shared-window: **{n_share}/{len(c_rows)}**. "
        f"Official 4/4: **{n4}/{len(c_rows)}**. Held-out audit pass: **{n_audit}/{len(c_rows)}**. "
        f"Round-7 pressure+route 4/4: **{n7}/{len(c_rows)}**. "
        f"Any leak on official or held-out audit: **{n_leak}/{len(c_rows)}**. "
        f"payload_copy_rr r4 delivered={bl_r4}."
    )
    lines.append("")
    lines.append(
        "4/4 split = route_4 + mix, train AND held, all guests complete + isolation. "
        "Held-out audit is never in the rotating train bank (different poke bases, insert slots, pair counts)."
    )
    lines.append(
        "Search hard constraint: third-guest grant or attacker window → invalid (R=-1), not a parent. "
        "Two attacker workloads sampled from the train bank each generation. "
        "CLRPROT after delivery is rewarded; leftover open windows are a stale cost (`n_stale`, `n_clrprot`)."
    )
    lines.append("")
    if n4 == 0 and c_rows:
        blockers, roles, n_miss, isol_fail = _aggregate_blockers(c_rows)
        lines.append("## Per-case failure table (official 4/4 is 0/N)")
        lines.append("")
        lines.append(f"{n_miss} elites missed 4/4. Isolation-fail case rows: {isol_fail}.")
        lines.append("")
        if blockers:
            lines.append(_md_table(["case", "count", "fraction of missed seeds"], blockers))
        if roles:
            lines.append("")
            lines.append(_md_table(["failed guest", "count"], [[k, v] for k, v in roles[:24]]))
        lines.append("")
        (out / "case_failures.json").write_text(
            json.dumps(
                {
                    "n_miss": n_miss,
                    "isol_fail_rows": isol_fail,
                    "blockers": blockers,
                    "roles": roles,
                    "per_seed": [
                        {"seed": r.get("seed"), "diagnose": r.get("diagnose"), "cases": r.get("case_failures")}
                        for r in c_rows
                    ],
                },
                default=str,
            )
        )
    best = max(
        c_rows,
        key=lambda x: (
            int(bool(x.get("four_of_four"))),
            int(bool(x.get("held_audit_pass"))),
            int(x.get("r4_delivered") or 0),
            int(x.get("r4_held_delivered") or 0),
            int(x.get("mix_completed") or 0),
            int(x.get("n_clrprot") or 0),
            -int(x.get("n_stale") or 0),
            float(x.get("search_reward") or 0),
        ),
        default=None,
    )
    if best and best.get("disassembly"):
        lines.append("")
        lines.append(
            f"### Best elite `{best['name']}` 4/4=`{best.get('four_of_four')}` "
            f"held_audit=`{best.get('held_audit_pass')}` style=`{best.get('style')}` org=`{best.get('org')}`"
        )
        lines.append("")
        lines.append(
            f"diagnose: {best.get('diagnose')}; n_clrprot={best.get('n_clrprot')} n_stale={best.get('n_stale')}"
        )
        lines.append("")
        lines.append("```")
        lines.append(str(best.get("disassembly") or ""))
        lines.append("```")
    lines.append("")
    (out / "summary.md").write_text("\n".join(lines) + "\n")
    (out / "README.md").write_text(
        "Round 10 in-search safety: [`summary.md`](summary.md). "
        "Embed: [`embed/`](embed/) (`evoos-embed-v1`, `audit_pass` flag).\n"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--quick", action="store_true")
    p.add_argument("--generations", type=int, default=0)
    p.add_argument("--population", type=int, default=0)
    p.add_argument("--eval-workers", type=int, default=0)
    p.add_argument("--out", type=str, default=str(OUT))
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    quick = args.quick
    gens = args.generations or (2 if quick else 3800)
    pop = args.population or (6 if quick else 40)
    workers = args.eval_workers or os.cpu_count() or 2
    seeds = SEEDS[:2] if quick else SEEDS
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ckpt_dir = out / "checkpoints"
    ckpt_dir.mkdir(exist_ok=True)

    t0 = time.perf_counter()
    sb = routing_sandbox(N_SANDBOX_GUESTS, max_steps=1500 if quick else 5000)
    print("== A-hw baselines (official 4/4 + held-out audit) ==")
    a_rows = run_baselines(sb)
    for r in a_rows:
        print(" ", r["name"], "4/4", r.get("four_of_four"), "held_audit", r.get("held_audit_pass"))

    gadget_seeds = _seed_pool()
    print(
        f"seeded {len(gadget_seeds)} elites "
        f"(first={gadget_seeds[0].origin if gadget_seeds else None})"
    )
    c_rows: list[dict] = []
    raw_path = out / "sweep_raw.json"
    if raw_path.exists():
        try:
            prev = json.loads(raw_path.read_text())
            c_rows = list(prev.get("C") or [])
            print(f"resuming {len(c_rows)} C rows")
        except Exception:
            c_rows = []
    done = {int(r["seed"]) for r in c_rows if r.get("seed") is not None}
    meta = {
        "eval_workers": workers,
        "generations": gens,
        "population": pop,
        "seeds": list(seeds),
        "n_seeds_in": len(gadget_seeds),
        "audit_sample": 2,
        "leak_invalid": True,
        "revoke_shaping": True,
    }

    def persist():
        write_reports(out, a_rows, c_rows, time.perf_counter() - t0, meta)

    persist()
    print(f"== C-hw safe {len(seeds)} jobs gens={gens} pop={pop} workers={workers} ==")
    for seed in seeds:
        if int(seed) in done:
            print(f"  skip s{seed}")
            continue
        print(f"  start s{seed}")
        row = run_one(
            {
                "seed": seed,
                "gens_per_stage": gens,
                "population": pop,
                "eval_workers": workers,
                "log_every": 1 if quick else 50,
                "max_steps": 1500 if quick else 4000,
            },
            gadget_seeds,
            ckpt_cb=lambda info: _ckpt(ckpt_dir / "live.json", info),
        )
        c_rows.append(row)
        done.add(int(seed))
        persist()
        print(
            f"  done {row['name']} R={float(row['search_reward']):.3f} "
            f"4/4={row.get('four_of_four')} held_audit={row.get('held_audit_pass')} "
            f"style={row.get('style')} n_clrprot={row.get('n_clrprot')} "
            f"n_eval={row.get('n_eval')} {row.get('elapsed_sec', 0):.0f}s"
        )
    elapsed = time.perf_counter() - t0
    persist()
    print(f"wrote {out} in {elapsed:.1f}s")
    print((out / "summary.md").read_text()[:2400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
