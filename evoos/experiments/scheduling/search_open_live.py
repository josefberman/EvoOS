"""Evolve a never-halting supervisor for open arrivals (gadgets pack).

Seeds: existing gadgets elites. Fitness: isolation hard-fail, HALT-before-
horizon fail, completed arrivals, fairness, latency. Final shortlist is
re-scored in QEMU (HITL). Opcode semantics are not changed.
"""

from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from evoos.agent.mutations import GADGET_OPS, mutate, random_program
from evoos.agent.policy import make_policy
from evoos.agent.program import Candidate
from evoos.boot.pack import ELITE_PATH, live_elite, parse_elite
from evoos.evaluator.open_arrival import evaluate_open_arrival, score_open_live
from evoos.evaluator.workloads import training_workloads
from evoos.experiments.scheduling.analyze import classify_trace
from evoos.experiments.scheduling.baselines import round_robin_live
from evoos.vm.instruction_set import Instr, Op, disassemble, parse_disassembly
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox
from evoos.vm.verifier import VerifyError, verify_supervisor

ROOT = Path(__file__).resolve().parents[3]
ELITE_DIR = ROOT / "experiments/scheduling/results/examples"
OUT_ELITE = ELITE_DIR / "open_arrival_gadgets_elite.txt"
OUT_JSON = ROOT / "experiments/scheduling/results/open_arrival_search.json"
QD_DIR = ELITE_DIR / "open_arrival_qd"


def load_gadget_elites() -> list[Candidate]:
    out: list[Candidate] = []
    for path in sorted(ELITE_DIR.glob("correctness_gadgets_s*.txt")):
        instrs = parse_disassembly(path.read_text())
        out.append(Candidate(instrs=instrs, origin=path.stem, id=-(len(out) + 1)))
    if not out:
        out.append(Candidate(instrs=parse_elite(ELITE_PATH), origin="s42", id=-1))
    return out


def _jsonable(instrs: tuple[Instr, ...]) -> list[list[int]]:
    return [[i.op, i.a, i.b, i.c, i.imm] for i in instrs]


def eval_candidate_rows(rows: list[list[int]], slice_steps: int, max_slices: int) -> dict:
    instrs = tuple(Instr(*r) for r in rows)
    try:
        verify_supervisor(instrs, MachineConfig())
    except VerifyError as exc:
        return {
            "invalid": True,
            "reason": str(exc),
            "reward": -1.0,
            "halted_early": True,
            "isolation_ok": True,
            "completed": 0,
            "train_completed": 0,
            "fairness": 0.0,
            "latency": 0.0,
            "label": "invalid",
            "switches": 0,
            "n_halt": sum(1 for op, *_ in rows if op == int(Op.HALT)),
        }
    cfg = MachineConfig(max_steps=200_000, wall_steps=250_000)
    oa = evaluate_open_arrival(
        instrs, config=cfg, slice_steps=slice_steps, max_slices=max_slices
    )
    train = Sandbox(MachineConfig(max_steps=8000)).evaluate(instrs, training_workloads()[0])
    reward = score_open_live(oa, train)
    return {
        "invalid": False,
        "reward": reward,
        "halted_early": oa["halted_early"],
        "isolation_ok": oa["isolation_ok"] and train.isolation_ok,
        "completed": oa["completed"],
        "train_completed": train.completed,
        "train_n": train.n_guests,
        "fairness": oa["fairness"],
        "latency": oa["latency"],
        "label": classify_trace(train),
        "switches": train.switch_count,
        "oa_switches": oa["switch_count"],
        "injected": oa["injected"],
        "replaced": oa["replaced"],
        "guest_cycles": oa["guest_cycles"],
        "horizon_survived": oa["horizon_survived"],
        "n_halt": sum(1 for i in instrs if i.op == int(Op.HALT)),
        "length": len(instrs),
    }


@dataclass
class QDArchive:
    """Small quality-diversity set keyed by behavioral label."""

    cells: dict[str, tuple[float, Candidate, dict]] = field(default_factory=dict)

    def add(self, cand: Candidate, rec: dict) -> None:
        if rec.get("invalid") or rec["reward"] < 0:
            return
        if not rec.get("isolation_ok"):
            return
        key = rec["label"]
        prev = self.cells.get(key)
        if prev is None or rec["reward"] > prev[0]:
            self.cells[key] = (rec["reward"], cand, rec)

    def best(self) -> tuple[Candidate, dict] | None:
        if not self.cells:
            return None
        r, c, rec = max(self.cells.values(), key=lambda t: t[0])
        return c, rec


def _prefer_non_rr(candidates: list[tuple[Candidate, dict]], slack: float = 0.05) -> tuple[Candidate, dict]:
    """Among HITL-or-Python survivors, prefer non-RR/RTC if within slack of best."""
    if not candidates:
        raise ValueError("empty shortlist")
    best_r = max(rec["reward"] for _, rec in candidates)
    cutoff = best_r - slack
    diverse = [
        (c, rec)
        for c, rec in candidates
        if rec["reward"] >= cutoff
        and rec["label"] not in ("round-robin-like", "run-to-completion-like")
    ]
    pool = diverse or [(c, rec) for c, rec in candidates if rec["reward"] >= cutoff]
    # Prefer horizon survivors, then higher reward, then non-RR label.
    pool.sort(
        key=lambda t: (
            int(t[1].get("horizon_survived", False)),
            t[1]["reward"],
            int(t[1]["label"] not in ("round-robin-like", "run-to-completion-like")),
            -t[1].get("n_halt", 99),
        ),
        reverse=True,
    )
    return pool[0]


def run_search(
    *,
    generations: int = 48,
    population: int = 28,
    seeds: list[int] | None = None,
    slice_steps: int = 250,
    max_slices: int = 40,
    workers: int = 4,
) -> dict:
    seeds = seeds or [1, 7, 13, 42, 99, 2024]
    elites = load_gadget_elites()
    archive = QDArchive()
    history: list[dict] = []
    best: tuple[float, Candidate, dict] | None = None
    operators = GADGET_OPS
    next_id = 1
    train_wl_name = training_workloads()[0].name

    def consider(cand: Candidate, rec: dict) -> None:
        nonlocal best
        rec["origin"] = cand.origin
        rec["id"] = cand.id
        archive.add(cand, rec)
        if best is None or rec["reward"] > best[0]:
            best = (rec["reward"], cand, rec)

    payloads_done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for seed in seeds:
            rng = random.Random(seed)
            policy = make_policy("ucb1", operators)
            pop: list[Candidate] = []
            for e in elites:
                pop.append(Candidate(instrs=e.instrs, origin=e.origin, id=next_id))
                next_id += 1
            while len(pop) < population:
                length = rng.randint(16, 48)
                pop.append(
                    Candidate(
                        instrs=random_program(rng, length, pack="gadgets"),
                        origin="random",
                        id=next_id,
                    )
                )
                next_id += 1

            def submit_all(cands: list[Candidate]):
                futs = {
                    pool.submit(eval_candidate_rows, _jsonable(c.instrs), slice_steps, max_slices): c
                    for c in cands
                }
                out: list[tuple[Candidate, dict]] = []
                for fut in as_completed(futs):
                    c = futs[fut]
                    rec = fut.result()
                    out.append((c, rec))
                return out

            scored = submit_all(pop)
            payloads_done += len(scored)
            hall: list[Candidate] = []
            for c, rec in scored:
                consider(c, rec)
                if rec["reward"] > 0.2:
                    hall.append(c)
                if rec["invalid"]:
                    policy.update(c.origin if c.origin in operators else "insert", -1.0)
                else:
                    policy.update("init", rec["reward"])

            seed_best = max(scored, key=lambda t: t[1]["reward"])
            history.append(
                {
                    "seed": seed,
                    "generation": 0,
                    "best_reward": seed_best[1]["reward"],
                    "best_label": seed_best[1]["label"],
                    "halted_early": seed_best[1]["halted_early"],
                    "completed": seed_best[1]["completed"],
                }
            )
            print(
                f"seed {seed} gen 0 R={seed_best[1]['reward']:.3f} "
                f"label={seed_best[1]['label']} halt_early={seed_best[1]['halted_early']} "
                f"oa={seed_best[1]['completed']}/4 train={seed_best[1]['train_completed']}/4",
                flush=True,
            )

            for gen in range(1, generations + 1):
                children: list[Candidate] = []
                for _ in range(population):
                    parent = rng.choice(hall or pop)
                    other = rng.choice(pop)
                    op = policy.select_operator(rng)
                    child = mutate(
                        parent,
                        op,
                        rng,
                        hall=hall or None,
                        other=other,
                        max_len=256,
                        operators=operators,
                    )
                    child = Candidate(
                        instrs=child.instrs,
                        origin=child.origin,
                        parent_id=parent.id,
                        id=next_id,
                    )
                    next_id += 1
                    children.append(child)
                scored = submit_all(children)
                payloads_done += len(scored)
                gen_rewards: list[float] = []
                for c, rec in scored:
                    consider(c, rec)
                    gen_rewards.append(rec["reward"])
                    opname = c.origin if c.origin in operators else "insert"
                    policy.update(opname, rec["reward"])
                    if rec["reward"] > 0.3:
                        hall.append(c)
                        pop.append(c)
                    if rec.get("horizon_survived") and rec["completed"] >= 3:
                        hall.append(c)
                        pop.append(c)
                if len(pop) > population * 4:
                    pop = pop[-population * 3 :]
                if len(hall) > 48:
                    hall = hall[-48:]
                top = max(scored, key=lambda t: t[1]["reward"])
                history.append(
                    {
                        "seed": seed,
                        "generation": gen,
                        "best_reward": top[1]["reward"],
                        "best_label": top[1]["label"],
                        "halted_early": top[1]["halted_early"],
                        "completed": top[1]["completed"],
                        "mean_reward": sum(gen_rewards) / max(len(gen_rewards), 1),
                    }
                )
                if gen == 1 or gen % 8 == 0 or (
                    top[1].get("horizon_survived") and top[1]["label"] not in ("round-robin-like", "run-to-completion-like")
                ):
                    print(
                        f"seed {seed} gen {gen} R={top[1]['reward']:.3f} "
                        f"label={top[1]['label']} halt_early={top[1]['halted_early']} "
                        f"oa={top[1]['completed']}/4 survived={top[1].get('horizon_survived')}",
                        flush=True,
                    )

    looping = []
    for label, (r, c, rec) in archive.cells.items():
        looping.append(
            {
                "label": label,
                "reward": r,
                "origin": c.origin,
                "halted_early": rec["halted_early"],
                "completed": rec["completed"],
                "train_completed": rec["train_completed"],
                "horizon_survived": rec.get("horizon_survived"),
                "isolation_ok": rec["isolation_ok"],
                "length": rec.get("length"),
                "n_halt": rec.get("n_halt"),
            }
        )

    # Baselines (not seeds): rr_live and human HALT→JMP elite.
    baselines = {
        "rr_live": round_robin_live(16),
        "human_halt_jmp": Candidate(instrs=live_elite(), origin="human_halt_jmp", id=-100),
    }
    base_scores = {}
    for name, cand in baselines.items():
        rec = eval_candidate_rows(_jsonable(cand.instrs), 400, 80)
        rec["origin"] = name
        base_scores[name] = {k: v for k, v in rec.items() if k != "guest_cycles"}

    result = {
        "evals": payloads_done,
        "train_workload": train_wl_name,
        "pack": "gadgets",
        "generations": generations,
        "population": population,
        "seeds": seeds,
        "qd": looping,
        "baselines": base_scores,
        "best": None if best is None else {**best[2], "origin": best[1].origin, "n_instrs": len(best[1].instrs)},
        "history_tail": history[-24:],
    }
    return {
        "report": result,
        "archive": archive,
        "best": best,
        "elites": elites,
    }


def write_elite(path: Path, cand: Candidate, rec: dict, header: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = disassemble(cand.instrs)
    path.write_text(f"# {header}\n{body}\n")


def shortlist_from_archive(archive: QDArchive, best: tuple[float, Candidate, dict] | None) -> list[tuple[Candidate, dict]]:
    items = [(c, rec) for _, ( _, c, rec) in archive.cells.items()]
    if best is not None:
        items.append((best[1], best[2]))
    # Unique by disassembly text.
    seen: set[str] = set()
    uniq: list[tuple[Candidate, dict]] = []
    items.sort(key=lambda t: t[1]["reward"], reverse=True)
    for c, rec in items:
        key = disassemble(c.instrs, labels=False)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((c, rec))
    return uniq[:8]


def hitl_filter(shortlist: list[tuple[Candidate, dict]], timeout: float = 10.0) -> list[tuple[Candidate, dict, dict]]:
    from evoos.evaluator.hitl import hitl_boot_instrs

    out = []
    for i, (c, rec) in enumerate(shortlist):
        if not rec.get("horizon_survived"):
            print(f"HITL skip {i} origin={c.origin} (python halted_early)", flush=True)
            continue
        origin = f"evolved_open_arrival_{c.origin}"
        print(f"HITL boot {i} origin={origin} pyR={rec['reward']:.3f} label={rec['label']}", flush=True)
        hw = hitl_boot_instrs(c.instrs, origin=origin, timeout=timeout)
        hw_ok = (
            hw.get("halted_lines", 0) >= 3
            and hw.get("preempt")
            and hw.get("isolation_ok")
            and not hw.get("returned", False)
        )
        rec = dict(rec)
        rec["hitl_ok"] = hw_ok
        rec["hitl"] = {k: v for k, v in hw.items() if k != "serial"}
        print(f"  hitl_ok={hw_ok} halted={hw.get('halted_lines')} pf={hw.get('pf')} preempt={hw.get('preempt')} returned={hw.get('returned')}", flush=True)
        out.append((c, rec, hw))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Open-arrival never-HALT search (gadgets pack)")
    p.add_argument("--generations", type=int, default=48)
    p.add_argument("--population", type=int, default=28)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seeds", default="1,7,13,42,99,2024")
    p.add_argument("--no-hitl", action="store_true")
    p.add_argument("--slice-steps", type=int, default=250)
    p.add_argument("--max-slices", type=int, default=40)
    args = p.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    bundle = run_search(
        generations=args.generations,
        population=args.population,
        seeds=seeds,
        slice_steps=args.slice_steps,
        max_slices=args.max_slices,
        workers=args.workers,
    )
    archive: QDArchive = bundle["archive"]
    best = bundle["best"]
    report = bundle["report"]

    # Full-horizon re-score of QD cells.
    full = []
    for label, (r, c, rec) in archive.cells.items():
        rec2 = eval_candidate_rows(_jsonable(c.instrs), 400, 80)
        rec2["origin"] = c.origin
        rec2["label_train"] = rec2["label"]
        rec2["search_reward"] = r
        rec2["cell"] = label
        full.append((c, rec2))
        print(
            f"full-horizon cell={label} R={rec2['reward']:.3f} survived={rec2['horizon_survived']} "
            f"oa={rec2['completed']}/4 train={rec2['train_completed']}/4 n_halt={rec2['n_halt']}",
            flush=True,
        )

    survivors = [(c, rec) for c, rec in full if rec.get("horizon_survived") and rec["isolation_ok"]]
    report["full_horizon_qd"] = [
        {k: v for k, v in rec.items() if k != "guest_cycles"} | {"origin": c.origin}
        for c, rec in full
    ]

    hitl_rows = []
    chosen: tuple[Candidate, dict] | None = None
    if not args.no_hitl and survivors:
        ranked = shortlist_from_archive(archive, best)
        # Prefer python survivors in the HITL queue.
        ranked = [(c, rec) for c, rec in ranked if rec.get("horizon_survived")] or ranked
        # Re-bind to full-horizon records when possible.
        by_src = {disassemble(c.instrs, labels=False): rec for c, rec in full}
        ranked2 = []
        for c, rec in ranked:
            rec_f = by_src.get(disassemble(c.instrs, labels=False), rec)
            ranked2.append((c, rec_f))
        hitl = hitl_filter(ranked2[:6])
        hitl_ok = [(c, rec) for c, rec, hw in hitl if rec.get("hitl_ok")]
        hitl_rows = [
            {
                "origin": c.origin,
                "label": rec["label"],
                "reward": rec["reward"],
                "hitl_ok": rec.get("hitl_ok"),
                "hitl": rec.get("hitl"),
            }
            for c, rec, _ in hitl
        ]
        if hitl_ok:
            chosen = _prefer_non_rr(hitl_ok)
    if chosen is None and survivors:
        chosen = _prefer_non_rr(survivors)
        report["hitl_note"] = "no HITL-ok elite; falling back to python survivor"
    if chosen is None and best is not None:
        report["hitl_note"] = "no looping elite; best halted_early candidate recorded only"
        chosen = (best[1], best[2])

    report["hitl"] = hitl_rows
    if chosen is None:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, indent=2, default=str))
        print("no candidate at all", flush=True)
        return 1

    cand, rec = chosen
    survived = bool(rec.get("horizon_survived"))
    origin = f"evolved_open_arrival_gadgets_{rec.get('label', 'other')}"
    header = (
        f"{origin} label={rec.get('label')} searchR={rec.get('reward'):.3f} "
        f"oa={rec.get('completed')}/4 train={rec.get('train_completed')}/4 "
        f"halted_early={rec.get('halted_early')} isolation_ok={rec.get('isolation_ok')} "
        f"hitl_ok={rec.get('hitl_ok')} pack=gadgets"
    )
    write_elite(OUT_ELITE, cand, rec, header)
    QD_DIR.mkdir(parents=True, exist_ok=True)
    for label, (_, c, rec_c) in archive.cells.items():
        write_elite(
            QD_DIR / f"{label.replace('/', '_')}.txt",
            c,
            rec_c,
            f"qd cell={label} R={rec_c['reward']:.3f} survived={rec_c.get('horizon_survived')}",
        )
    report["chosen"] = {
        "origin": origin,
        "label": rec.get("label"),
        "reward": rec.get("reward"),
        "halted_early": rec.get("halted_early"),
        "horizon_survived": survived,
        "completed": rec.get("completed"),
        "train_completed": rec.get("train_completed"),
        "isolation_ok": rec.get("isolation_ok"),
        "hitl_ok": rec.get("hitl_ok"),
        "n_instrs": len(cand.instrs),
        "n_halt": rec.get("n_halt"),
        "path": str(OUT_ELITE.relative_to(ROOT)),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, default=str))
    print("wrote", OUT_ELITE, header, flush=True)
    print(disassemble(cand.instrs)[:2000], flush=True)
    if not survived:
        print("HONEST: no evolved looping elite; ISO must not silently fall back", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
