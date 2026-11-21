"""Evolve a supervisor that stores/retrieves names across power cuts.

Seeds: persist_rr (human Condition A slot table) plus open-arrival elites
and blk-gadget randoms. Fitness: isolation hard-fail, persist correctness
after restart, crash/corrupt recovery, write efficiency. Layout is not
prescribed — classify_disk only labels what landed.

insert_blk_gadget is an ISA calling-convention prior (MOVI block; MOVI addr;
BLKRD/BLKWR), not a namespace.
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
from evoos.boot.pack import OPEN_ELITE_PATH, parse_elite
from evoos.evaluator.persist import classify_disk, evaluate_persist, score_persist
from evoos.experiments.scheduling.baselines import persist_rr, round_robin_live
from evoos.vm.instruction_set import Instr, Op, disassemble, parse_disassembly
from evoos.vm.machine import MachineConfig
from evoos.vm.verifier import VerifyError, verify_supervisor

ROOT = Path(__file__).resolve().parents[3]
ELITE_DIR = ROOT / "experiments/scheduling/results/examples"
OUT_ELITE = ELITE_DIR / "persist_gadgets_elite.txt"
OUT_JSON = ROOT / "experiments/scheduling/results/persist_search.json"
QD_DIR = ELITE_DIR / "persist_qd"


def _jsonable(instrs: tuple[Instr, ...]) -> list[list[int]]:
    return [[i.op, i.a, i.b, i.c, i.imm] for i in instrs]


def load_seeds() -> list[Candidate]:
    out: list[Candidate] = [persist_rr()]
    if OPEN_ELITE_PATH.exists():
        out.append(
            Candidate(
                instrs=parse_disassembly(OPEN_ELITE_PATH.read_text()),
                origin="s99",
                id=-2,
            )
        )
    out.append(Candidate(instrs=parse_elite(), origin="s42", id=-3))
    out.append(round_robin_live())
    return out


def eval_candidate_rows(rows: list[list[int]]) -> dict:
    instrs = tuple(Instr(*r) for r in rows)
    try:
        verify_supervisor(instrs, MachineConfig())
    except VerifyError as exc:
        return {
            "invalid": True,
            "reason": str(exc),
            "reward": -1.0,
            "isolation_ok": True,
            "correct": 0,
            "leak": False,
            "layout": "invalid",
            "n_write": 0,
            "crash_correct": 0,
            "corrupt_correct": 0,
            "n_halt": sum(1 for op, *_ in rows if op == int(Op.HALT)),
            "length": len(rows),
        }
    r = evaluate_persist(instrs)
    r_crash = evaluate_persist(instrs, crash_write=True)
    r_bad = evaluate_persist(instrs, corrupt=True)
    reward = score_persist(r)
    if r["isolation_ok"]:
        # Crash: a dropped write must not leak the other guest's value.
        if not r_crash["isolation_ok"] or r_crash["leak"]:
            reward = -1.0
        else:
            reward += 0.1 * (r_crash["correct"] / 2.0)
            reward += 0.1 * (r_bad["correct"] / 2.0 if r_bad["isolation_ok"] else 0.0)
    layout = classify_disk(r["disk"])
    return {
        "invalid": False,
        "reward": reward,
        "isolation_ok": r["isolation_ok"],
        "correct": r["correct"],
        "got": r["got"],
        "leak": r["leak"],
        "layout": layout,
        "n_write": r["n_write"],
        "crash_correct": r_crash["correct"],
        "crash_isolation": r_crash["isolation_ok"],
        "corrupt_correct": r_bad["correct"],
        "supervisor_fault": r["supervisor_fault"],
        "n_halt": sum(1 for i in instrs if i.op == int(Op.HALT)),
        "length": len(instrs),
        "n_blk": sum(1 for i in instrs if i.op in (int(Op.BLKRD), int(Op.BLKWR))),
        "n_svc": sum(1 for i in instrs if i.op == int(Op.SVC)),
    }


@dataclass
class QDArchive:
    cells: dict[str, tuple[float, Candidate, dict]] = field(default_factory=dict)

    def add(self, cand: Candidate, rec: dict) -> None:
        if rec.get("invalid") or rec["reward"] < 0:
            return
        if not rec.get("isolation_ok"):
            return
        key = rec["layout"]
        prev = self.cells.get(key)
        if prev is None or rec["reward"] > prev[0]:
            self.cells[key] = (rec["reward"], cand, rec)

    def best(self) -> tuple[Candidate, dict] | None:
        if not self.cells:
            return None
        return max(self.cells.values(), key=lambda t: t[0])[1:]  # type: ignore[return-value]


def run_search(
    *,
    generations: int = 12,
    population: int = 20,
    seeds: list[int] | None = None,
    workers: int = 4,
) -> dict:
    seeds = seeds or [1, 7, 13, 99]
    archive = QDArchive()
    history: list[dict] = []
    best: tuple[float, Candidate, dict] | None = None
    operators = GADGET_OPS
    next_id = 1
    seed_cands = load_seeds()

    def consider(cand: Candidate, rec: dict) -> None:
        nonlocal best
        rec["origin"] = cand.origin
        rec["id"] = cand.id
        archive.add(cand, rec)
        if best is None or rec["reward"] > best[0]:
            best = (rec["reward"], cand, rec)

    with ProcessPoolExecutor(max_workers=workers) as pool:
        for seed in seeds:
            rng = random.Random(seed)
            policy = make_policy("ucb1", operators)
            pop: list[Candidate] = []
            for e in seed_cands:
                pop.append(Candidate(instrs=e.instrs, origin=e.origin, id=next_id))
                next_id += 1
            while len(pop) < population:
                pop.append(
                    Candidate(
                        instrs=random_program(rng, rng.randint(20, 56), pack="gadgets"),
                        origin="random",
                        id=next_id,
                    )
                )
                next_id += 1

            def submit_all(cands: list[Candidate]):
                futs = {
                    pool.submit(eval_candidate_rows, _jsonable(c.instrs)): c for c in cands
                }
                out: list[tuple[Candidate, dict]] = []
                for fut in as_completed(futs):
                    c = futs[fut]
                    rec = fut.result()
                    out.append((c, rec))
                return out

            scored = submit_all(pop)
            hall: list[Candidate] = []
            for c, rec in scored:
                consider(c, rec)
                if rec["reward"] > 0.2:
                    hall.append(c)

            seed_best = max(scored, key=lambda t: t[1]["reward"])
            history.append(
                {
                    "seed": seed,
                    "generation": 0,
                    "best_reward": seed_best[1]["reward"],
                    "layout": seed_best[1]["layout"],
                    "correct": seed_best[1]["correct"],
                }
            )
            print(
                f"seed {seed} gen 0 R={seed_best[1]['reward']:.3f} "
                f"layout={seed_best[1]['layout']} correct={seed_best[1]['correct']}/2 "
                f"iso={seed_best[1]['isolation_ok']}",
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
                gen_rewards: list[float] = []
                for c, rec in scored:
                    consider(c, rec)
                    gen_rewards.append(rec["reward"])
                    policy.update(c.origin if c.origin in operators else "insert", rec["reward"])
                    if rec["reward"] > 0.4 and rec.get("isolation_ok"):
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
                        "layout": top[1]["layout"],
                        "correct": top[1]["correct"],
                        "mean_reward": sum(gen_rewards) / max(len(gen_rewards), 1),
                    }
                )
                if gen == 1 or gen % 4 == 0:
                    print(
                        f"seed {seed} gen {gen} R={top[1]['reward']:.3f} "
                        f"layout={top[1]['layout']} correct={top[1]['correct']}/2",
                        flush=True,
                    )

    cells = []
    QD_DIR.mkdir(parents=True, exist_ok=True)
    for label, (r, c, rec) in archive.cells.items():
        cells.append(
            {
                "layout": label,
                "reward": r,
                "origin": c.origin,
                "correct": rec["correct"],
                "n_write": rec["n_write"],
                "length": rec.get("length"),
            }
        )
        (QD_DIR / f"{label}.txt").write_text(
            f"# {c.origin} R={r:.4f} layout={label} correct={rec['correct']}\n"
            + disassemble(c.instrs)
            + "\n"
        )

    baseline_rr = eval_candidate_rows(_jsonable(persist_rr().instrs))
    baseline_live = eval_candidate_rows(_jsonable(round_robin_live().instrs))

    chosen: Candidate | None = None
    chosen_rec: dict = {}
    if best is not None and best[0] > 0.5 and best[2].get("correct", 0) == 2:
        chosen, chosen_rec = best[1], best[2]
        # Do not ship a human baseline as "the OS" if search only rediscovered it.
        if chosen.origin.startswith("baseline_persist"):
            chosen_rec = dict(chosen_rec)
            chosen_rec["note"] = (
                "best scoring program is still persist_rr (human slot table). "
                "Not copied to persist_gadgets_elite as an evolved OS."
            )
        else:
            ELITE_DIR.mkdir(parents=True, exist_ok=True)
            OUT_ELITE.write_text(
                f"# {chosen.origin} R={best[0]:.4f} layout={chosen_rec.get('layout')}\n"
                + disassemble(chosen.instrs)
                + "\n"
            )

    summary = {
        "best_reward": None if best is None else best[0],
        "best_origin": None if best is None else best[1].origin,
        "best_layout": None if best is None else best[2].get("layout"),
        "best_correct": None if best is None else best[2].get("correct"),
        "best_note": chosen_rec.get("note", ""),
        "qd_cells": cells,
        "history": history,
        "baseline_persist_rr": {k: v for k, v in baseline_rr.items() if k != "got"},
        "baseline_rr_live": {k: v for k, v in baseline_live.items() if k != "got"},
        "elite_path": str(OUT_ELITE) if OUT_ELITE.exists() else "",
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in summary if k != "history"}, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--generations", type=int, default=8)
    p.add_argument("--population", type=int, default=16)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seeds", default="1,7,99")
    args = p.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    run_search(
        generations=args.generations,
        population=args.population,
        seeds=seeds,
        workers=args.workers,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
