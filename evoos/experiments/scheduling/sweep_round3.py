"""Round 3: bare-ISA curriculum, shaping, substrate ablations, Condition B efficiency."""

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from evoos.agent.curriculum import run_curriculum
from evoos.agent.evolutionary_search import SearchConfig, run_search
from evoos.evaluator.shaping import SHAPE_WEIGHTS, SHAPING_DOCS
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.experiments.scheduling.baselines import round_robin, run_to_completion, selfish_first
from evoos.experiments.scheduling.condition_b import bandit_search, grid_search
from evoos.experiments.scheduling.metrics import diagnose, evaluate_split
from evoos.vm.instruction_set import disassemble, instrs_to_jsonable
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox

SEEDS = (1, 7, 13, 42, 99, 2024)
OUT = Path("experiments/scheduling/results/round3")


def _substrate(name: str, **kw) -> MachineConfig:
    if name == "hostile":
        return MachineConfig(**kw)
    if name == "lenient":
        return lenient_config(**kw)
    if name == "lenient_no_resume":
        return lenient_config(sticky_guest_fault=True, **kw)
    if name == "lenient_no_memerr":
        return lenient_config(supervisor_halt_on_mem=True, **kw)
    if name == "lenient_no_status":
        return lenient_config(runctx_posts_status=False, **kw)
    raise KeyError(name)


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
    train, held = list(training_workloads()), list(heldout_workloads())
    return evaluate_split(sandbox, cand, train, held)


def run_a() -> list[dict]:
    sandbox = Sandbox(MachineConfig(max_steps=8000, n_contexts=5))
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


def run_b(seed: int, pulls: int) -> dict:
    sandbox = Sandbox(MachineConfig(max_steps=8000, n_contexts=5))
    train, held = list(training_workloads()), list(heldout_workloads())
    t0 = time.perf_counter()
    result = bandit_search(sandbox, train, held, seed=seed, pulls=pulls)
    elapsed = time.perf_counter() - t0
    cand = round_robin(result.best_quantum)
    info = evaluate_split(sandbox, cand, train, held)
    b_score = result.best
    info.update(
        {
            "condition": "B",
            "name": f"rr_q{result.best_quantum}",
            "seed": seed,
            "best_quantum": result.best_quantum,
            "b_train_reward": b_score["train_reward"],
            "b_held_reward": b_score["held_reward"],
            "b_train_E": b_score.get("train_E"),
            "elapsed_sec": elapsed,
            "search_reward": b_score["train_reward"],
        }
    )
    return info


def run_c(job: dict) -> dict:
    seed = int(job["seed"])
    curriculum = bool(job["curriculum"])
    shaping = bool(job["shaping"])
    substrate = job["substrate"]
    gens = int(job["generations"])
    pop = int(job["population"])
    gens_per_stage = int(job["gens_per_stage"])
    cfg = _substrate(substrate, max_steps=8000)
    search = SearchConfig(
        generations=gens,
        population=pop,
        seed=seed,
        init_len=24,
        eval_heldout_every=10,
        mode="correctness",
        operator_pack="bare",
        keep_records=False,
        shaping=shaping,
        early_stop_complete=curriculum,
        log_every=10**9,
    )
    t0 = time.perf_counter()
    stage_logs = []
    stall = None
    if curriculum:
        from evoos.agent.curriculum import run_curriculum as rc

        cr = rc(cfg, search, gens_per_stage=gens_per_stage)
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
                "prot_cover": s.prot_cover,
                "shape_f": s.shape_f,
            }
            for s in cr.stages
        ]
    else:
        sandbox = Sandbox(
            MachineConfig(
                n_contexts=5,
                max_steps=8000,
                sticky_guest_fault=cfg.sticky_guest_fault,
                supervisor_halt_on_mem=cfg.supervisor_halt_on_mem,
                runctx_posts_status=cfg.runctx_posts_status,
            )
        )
        search.early_stop_complete = False
        state = run_search(sandbox, list(training_workloads()), list(heldout_workloads()), search)
    elapsed = time.perf_counter() - t0
    row = {
        "condition": "C",
        "name": f"bare_{'curr' if curriculum else 'flat'}_{'shape' if shaping else 'noshape'}_{substrate}_s{seed}",
        "seed": seed,
        "curriculum": curriculum,
        "shaping": shaping,
        "substrate": substrate,
        "elapsed_sec": elapsed,
        "search_reward": state.best_reward,
        "stall_stage": stall,
        "stages": stage_logs,
        "four_of_four": False,
        "structural_label": "none",
        "operator_pack": "bare",
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


def write_reports(out: Path, a_rows, b_rows, c_rows, b_grid, elapsed, meta: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    fields = [
        "condition",
        "name",
        "seed",
        "curriculum",
        "shaping",
        "substrate",
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
        "best_quantum",
        "b_train_reward",
        "b_train_E",
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
    with (out / "condition_abc.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in a_rows + b_rows + c_rows:
            w.writerow(_flat(r))
    with (out / "condition_b_grid.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["quantum", "train_reward", "held_reward", "train_E", "held_E", "four_of_four", "train_switches"],
            extrasaction="ignore",
        )
        w.writeheader()
        for r in b_grid:
            w.writerow(_flat(r))
    with (out / "curriculum_curves.csv").open("w", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["seed", "config", "stage", "n_guests", "gens_ran", "best_reward", "completed", "entered", "prot_cover", "shape_f", "promoted", "stalled"],
        )
        w.writeheader()
        for r in c_rows:
            for s in r.get("stages") or []:
                w.writerow(
                    {
                        "seed": r["seed"],
                        "config": r["name"],
                        "stage": s["name"],
                        "n_guests": s["n_guests"],
                        "gens_ran": s["gens_ran"],
                        "best_reward": s["best_reward"],
                        "completed": json.dumps(s["completed"]),
                        "entered": json.dumps(s.get("entered")),
                        "prot_cover": json.dumps(s.get("prot_cover")),
                        "shape_f": s.get("shape_f"),
                        "promoted": s["promoted"],
                        "stalled": s["stalled"],
                    }
                )
    (out / "sweep_raw.json").write_text(json.dumps({"A": a_rows, "B": b_rows, "C": c_rows, "B_grid": b_grid, "meta": meta}, default=str))
    ex = out / "examples"
    ex.mkdir(parents=True, exist_ok=True)
    for r in c_rows:
        if not r.get("disassembly"):
            continue
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in str(r["name"]))[:120]
        (ex / f"{safe}.txt").write_text(
            f"# {r['name']} 4/4={r.get('four_of_four')} stall={r.get('stall_stage')} R={r.get('search_reward')}\n"
            f"# diagnose: {r.get('diagnose')}\n\n"
            + str(r["disassembly"])
        )

    lines = []
    lines.append("# Round 3: bare ISA, curriculum, shaping, substrate")
    lines.append("")
    lines.append(f"Wall time **{elapsed:.1f}s** ({elapsed/60:.1f} min), {meta.get('workers')} processes.")
    lines.append(
        f"Search: gens/stage={meta.get('gens_per_stage')} pop={meta.get('population')} "
        f"seeds `{list(meta.get('seeds', SEEDS))}`."
    )
    lines.append("Operator pack is **bare** (no MMU/RUNCTX gadgets, no ctx-scan).")
    lines.append("")
    lines.append("## Shaping terms")
    lines.append("")
    lines.append("```")
    lines.append((SHAPING_DOCS or "").strip())
    lines.append("```")
    lines.append("")
    lines.append(f"Weights: `{SHAPE_WEIGHTS}`")
    lines.append("")
    lines.append("## Substrate flags (generic hardware, not OS)")
    lines.append("")
    lines.append("| flag | hostile (round 2) | lenient | why it is hardware |")
    lines.append("| --- | --- | --- | --- |")
    lines.append("| `sticky_guest_fault` | guest dead after PROT/MEM until CTXSET | RUNCTX retries the faulting instruction | resume-after-abort, like a CPU restarting the faulting load |")
    lines.append("| `supervisor_halt_on_mem` | supervisor halt | sticky error, skip access, continue | imprecise abort vs machine check |")
    lines.append("| `runctx_posts_status` | off | LAST_RUN_STEPS/FAULT/CTX CSRs | result of the last context-switch instruction |")
    lines.append("")
    lines.append("## Condition A")
    lines.append("")
    lines.append(
        _md_table(
            ["name", "held complete", "4/4", "held R", "label"],
            [[r["name"], r["held_completed"], r["four_of_four"], f"{r['held_reward']:.3f}", r["structural_label"]] for r in a_rows],
        )
    )
    lines.append("")
    lines.append("## Condition B (efficiency in the objective)")
    lines.append("")
    lines.append(
        _md_table(
            ["seed", "q", "B-train R", "E", "held complete", "4/4"],
            [
                [
                    r.get("seed"),
                    r.get("best_quantum"),
                    f"{float(r.get('b_train_reward') or 0):.3f}",
                    f"{float(r.get('b_train_E') or 0):.3f}",
                    r.get("held_completed"),
                    r.get("four_of_four"),
                ]
                for r in b_rows
            ],
        )
    )
    if b_grid:
        best = max(b_grid, key=lambda r: (r["train_reward"], r.get("train_E", 0)))
        lines.append("")
        lines.append(
            f"Grid best q=**{best['quantum']}** trainR={best['train_reward']:.3f} E={best.get('train_E', 0):.3f} "
            f"switches={best.get('train_switches')}."
        )
    lines.append("")
    lines.append("## Condition C bare — 4/4 on the *full* 4-guest train+held-out")
    lines.append("")
    groups = {}
    for r in c_rows:
        key = (bool(r["curriculum"]), bool(r["shaping"]), str(r["substrate"]))
        groups.setdefault(key, []).append(r)
    sum_rows = []
    n_four = 0
    for key, chunk in sorted(groups.items(), key=lambda kv: str(kv[0])):
        curr, shape, sub = key
        rs = [float(x["search_reward"]) for x in chunk]
        nf = sum(1 for x in chunk if x.get("four_of_four"))
        n_four += nf
        mean, std, bestv = _mean_std(rs)
        stalls = [x.get("stall_stage") for x in chunk if x.get("stall_stage")]
        labels = sorted({str(x.get("structural_label")) for x in chunk})
        sum_rows.append(
            [
                "curr" if curr else "flat",
                "shape" if shape else "noshape",
                sub,
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
            ["curriculum", "shaping", "substrate", "n", "search R mean", "std", "best", "4/4 train+held", "stall stages", "labels"],
            sum_rows,
        )
    )
    lines.append("")
    lines.append("### Per-seed")
    lines.append("")
    per = []
    for r in sorted(c_rows, key=lambda x: (str(x["substrate"]), str(x["curriculum"]), int(x["seed"]))):
        per.append(
            [
                r["seed"],
                "curr" if r["curriculum"] else "flat",
                "shape" if r["shaping"] else "noshape",
                r["substrate"],
                f"{float(r['search_reward']):.3f}",
                r.get("train_completed"),
                r.get("held_completed"),
                "yes" if r.get("four_of_four") else "no",
                r.get("stall_stage") or "—",
                r.get("structural_label"),
            ]
        )
    lines.append(_md_table(["seed", "curr", "shape", "substrate", "search R", "train", "held", "4/4", "stall", "label"], per))
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    if not c_rows:
        lines.append("No C rows.")
    elif n_four == 0:
        lines.append(
            f"**No bare seed reached 4/4 on full train+held-out** ({len(c_rows)} runs). "
            "See stall_stage: k1 means never completed even one guest; "
            "k2–k4 means it could not compose a second/third/fourth context."
        )
    else:
        lines.append(f"{n_four}/{len(c_rows)} bare runs reached 4/4 on the full 4-guest split.")
    k1_enter = k1_prot = k1_complete = k1_n = 0
    stall_counts: dict[str, int] = {}
    for r in c_rows:
        st = r.get("stall_stage") or "none"
        stall_counts[st] = stall_counts.get(st, 0) + 1
        for s in r.get("stages") or []:
            if s.get("name") != "k1":
                continue
            k1_n += 1
            if any(int(x) > 0 for x in (s.get("entered") or [])):
                k1_enter += 1
            covers = s.get("prot_cover") or []
            if any(any(c) for c in covers):
                k1_prot += 1
            if any(int(x) > 0 for x in (s.get("completed") or [])):
                k1_complete += 1
    if k1_n:
        lines.append(
            f"k1 elites: entered a guest in **{k1_enter}/{k1_n}**, "
            f"prot_cover in **{k1_prot}/{k1_n}**, completed in **{k1_complete}/{k1_n}**."
        )
    if stall_counts:
        lines.append("Stall histogram: " + ", ".join(f"{k}={v}" for k, v in sorted(stall_counts.items())))
    lines.append("")
    lines.append("## Path to a bootable x86_64 ISO")
    lines.append("")
    lines.append(
        "The end artifact is not a Python research VM. It is a **bootable ISO** whose "
        "runtime *is* the evolved bytecode, hosted by a tiny stub, not a Linux clone."
    )
    lines.append("")
    lines.append("1. **Freeze the ISA as a byte encoding** (already a 5-tuple of ints; pack as 32- or 64-bit words). No Python objects at runtime. A C or Rust interpreter/JIT of that encoding is the host.")
    lines.append("2. **Boot shim (BIOS first, UEFI next):** Limine or a ~few-hundred-line custom Multiboot2/BIOS `boot.asm` that enters long mode, identity-maps a few GiB, loads a ramdisk blob `supervisor.bin` + guest images, and jumps to `evoos_interp`. QEMU: `qemu-system-x86_64 -cdrom evoos.iso -nographic -serial mon:stdio`. VirtualBox: the same ISO, I/O APIC on, EFI optional once UEFI Limine works.")
    lines.append("3. **Embed the evolved program** as a flat binary in the ISO (xoriso/grub-mkrescue or Limine `limine-deploy`). The stub does not contain a scheduler, heap, or VFS — it exposes the same primitives as the research VM: physical memory, contexts as register+PC frames, RUNCTX as “run this frame for N instructions”, ADDPROT as a software MPU over the physical map, a PIT/APIC timer as RDTIME, VGA/serial as a STORE window.")
    lines.append("4. **What the user sees:** serial/VGA text from guest STORE-mapped output (later a framebuffer range). Milestone-1 analogue: four demo tasks printing completion lines. Later: a prompt that is itself guest bytecode, not bash-on-Linux.")
    lines.append("5. **Test ladder:** (a) host interpreter on Linux matching the Python VM on golden traces, (b) QEMU ISO serial log vs the same traces, (c) VirtualBox screenshot/serial, BIOS then UEFI.")
    lines.append("6. **Non-conventional target, not xv6-in-a-trenchcoat:** do *not* grow a Unix process table, page allocator, or POSIX VFS as privileged code. Aim for a **single address-space capability machine**: guest “programs” are contexts with MPU ranges; naming and persistence are discovered as STORE protocols against a block window; multiplexing stays whatever bytecode evolution found (RR-like, batched, or stranger). The stub is firmware; the OS *is* the supervisor blob. If we need Linux syscalls, they are a translation layer in bytecode under differential tests — never the native architecture.")
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
    p.add_argument("--b-pulls", type=int, default=40)
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    quick = args.quick
    gens = args.generations or (6 if quick else 500)
    gps = args.gens_per_stage or (4 if quick else 100)
    pop = args.population or (6 if quick else 36)
    workers = args.workers or os.cpu_count() or 2
    b_pulls = 8 if quick else args.b_pulls
    seeds = SEEDS[:2] if quick else SEEDS
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    print("== A ==")
    a_rows = run_a()
    for r in a_rows:
        print(" ", r["name"], r["held_completed"], "4/4", r["four_of_four"])

    print("== B grid + bandit ==")
    sandbox = Sandbox(MachineConfig(max_steps=8000, n_contexts=5))
    b_grid = grid_search(sandbox, list(training_workloads()), list(heldout_workloads()))
    b_rows = []
    with ProcessPoolExecutor(max_workers=min(workers, len(seeds))) as ex:
        futs = {ex.submit(run_b, s, b_pulls): s for s in seeds}
        for fut in as_completed(futs):
            row = fut.result()
            b_rows.append(row)
            print(f"  seed={row['seed']} q={row.get('best_quantum')} E={row.get('b_train_E')} 4/4={row['four_of_four']}")

    primary = [
        {"curriculum": True, "shaping": True, "substrate": "lenient"},
        {"curriculum": False, "shaping": True, "substrate": "lenient"},
        {"curriculum": True, "shaping": False, "substrate": "lenient"},
        {"curriculum": True, "shaping": True, "substrate": "hostile"},
    ]
    leave = [
        {"curriculum": True, "shaping": True, "substrate": "lenient_no_resume"},
        {"curriculum": True, "shaping": True, "substrate": "lenient_no_memerr"},
        {"curriculum": True, "shaping": True, "substrate": "lenient_no_status"},
    ]
    configs = primary + leave
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
    print(f"== C bare: {len(jobs)} jobs, {workers} workers, gps={gps} pop={pop} ==")
    c_rows = []
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
            write_reports(
                out,
                a_rows,
                b_rows,
                c_rows,
                b_grid,
                time.perf_counter() - t0,
                {"workers": workers, "gens_per_stage": gps, "population": pop, "seeds": list(seeds)},
            )

    elapsed = time.perf_counter() - t0
    write_reports(
        out,
        a_rows,
        b_rows,
        c_rows,
        b_grid,
        elapsed,
        {"workers": workers, "gens_per_stage": gps, "population": pop, "seeds": list(seeds)},
    )
    print(f"wrote {out} in {elapsed:.1f}s")
    print((out / "summary.md").read_text()[:2500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
