"""Curriculum MAP-Elites search for persistence (stage 3).

Seeds: persist_rr (Condition A comparison), s99, s42, stepping-stone QD
elites (append-log-like 1/2), blk-gadget randoms.

Curriculum: A one key no crash; B several keys + random power cuts; C bad
blocks; D two guests, isolation hard-fail. Promote on partial success.

Gated GET credit lives in evoos.evaluator.persist. insert_blk_gadget is a
calling-convention prior, not a namespace.

Checkpointed; resume with --checkpoint.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from evoos.agent.mutations import GADGET_OPS, mutate, random_program
from evoos.agent.policy import make_policy
from evoos.agent.program import Candidate
from evoos.boot.pack import OPEN_ELITE_PATH, parse_elite
from evoos.evaluator.persist import (
    LAYOUTS,
    STAGES,
    classify_disk,
    evaluate_persist,
    evaluate_stage,
    persist_cell,
    program_rng,
    promote,
    score_persist,
)
from evoos.experiments.scheduling.baselines import persist_rr, round_robin_live
from evoos.vm.instruction_set import Instr, Op, disassemble, parse_disassembly
from evoos.vm.machine import MachineConfig
from evoos.vm.verifier import VerifyError, verify_supervisor

ROOT = Path(__file__).resolve().parents[3]
ELITE_DIR = ROOT / "experiments/scheduling/results/examples"
OUT_ELITE = ELITE_DIR / "persist_gadgets_elite.txt"
OUT_JSON = ROOT / "experiments/scheduling/results/persist_search.json"
QD_DIR = ELITE_DIR / "persist_qd"
CKPT = ROOT / "experiments/scheduling/results/persist_curriculum_ckpt.json"
CURVE = ROOT / "experiments/scheduling/results/persist_curriculum_curve.jsonl"


def _jsonable(instrs: tuple[Instr, ...]) -> list[list[int]]:
    return [[i.op, i.a, i.b, i.c, i.imm] for i in instrs]


def _from_rows(rows: list[list[int]]) -> tuple[Instr, ...]:
    return tuple(Instr(*r) for r in rows)


def load_seeds() -> list[Candidate]:
    out: list[Candidate] = [persist_rr()]
    if OPEN_ELITE_PATH.exists():
        out.append(
            Candidate(instrs=parse_disassembly(OPEN_ELITE_PATH.read_text()), origin="s99", id=-2)
        )
    out.append(Candidate(instrs=parse_elite(), origin="s42", id=-3))
    out.append(round_robin_live())
    stone = ELITE_DIR / "persist_qd" / "append-log-like.txt"
    if stone.exists():
        out.append(Candidate(instrs=parse_disassembly(stone.read_text()), origin="stone_append", id=-4))
    empty = ELITE_DIR / "persist_qd" / "empty.txt"
    if empty.exists():
        out.append(Candidate(instrs=parse_disassembly(empty.read_text()), origin="stone_empty", id=-5))
    return out


def eval_curriculum_rows(rows: list[list[int]]) -> dict:
    instrs = _from_rows(rows)
    n_halt = sum(1 for op, *_ in rows if op == int(Op.HALT))
    n_blk = sum(1 for op, *_ in rows if op in (int(Op.BLKRD), int(Op.BLKWR)))
    base = {
        "invalid": False,
        "n_halt": n_halt,
        "n_blk": n_blk,
        "length": len(rows),
        "stages": {},
        "max_stage": None,
        "reward": -1.0,
        "isolation_ok": True,
        "correct": 0,
        "layout": "empty",
        "leak": False,
        "n_write": 0,
        "cell": [0, 0],
    }
    try:
        verify_supervisor(instrs, MachineConfig())
    except VerifyError as exc:
        base["invalid"] = True
        base["reason"] = str(exc)
        return base
    rng = program_rng(rows)
    unlocked = "A"
    last = None
    for st in STAGES:
        r = evaluate_stage(instrs, st, rng=rng)
        slim = {
            "stage": st,
            "correct": r["correct"],
            "n": r["n"],
            "raw_match": r["raw_match"],
            "isolation_ok": r["isolation_ok"],
            "leak": r["leak"],
            "n_write": r["n_write"],
            "n_read_get": r["n_read_get"],
            "layout": r["layout"],
            "supervisor_fault": r["supervisor_fault"],
            "reward": score_persist(r),
            "promoted": promote(r),
        }
        base["stages"][st] = slim
        last = r
        unlocked = st
        if not slim["promoted"]:
            break
    assert last is not None
    stage_bonus = 0.2 * STAGES.index(unlocked)
    reward = score_persist(last) + stage_bonus
    if not last["isolation_ok"] or last["leak"]:
        reward = -1.0
    x, y = persist_cell(last)
    y = min(y + STAGES.index(unlocked), 7)
    base.update(
        {
            "max_stage": unlocked,
            "reward": reward,
            "isolation_ok": last["isolation_ok"],
            "correct": last["correct"],
            "layout": last["layout"],
            "leak": last["leak"],
            "n_write": last["n_write"],
            "cell": [x, y],
            "got": last["got"],
        }
    )
    return base


@dataclass
class PersistArchive:
    """MAP-Elites over (layout, gated-correct+stage) with curiosity visits."""

    cells: dict[tuple[int, int], tuple[float, Candidate, dict]] = field(default_factory=dict)
    visits: dict[tuple[int, int], int] = field(default_factory=dict)

    def add(self, cand: Candidate, rec: dict) -> bool:
        if rec.get("invalid") or rec["reward"] < 0:
            return False
        if rec.get("leak"):
            return False
        key = (int(rec["cell"][0]), int(rec["cell"][1]))
        self.visits[key] = self.visits.get(key, 0) + 1
        prev = self.cells.get(key)
        if prev is None or rec["reward"] > prev[0]:
            self.cells[key] = (rec["reward"], cand, rec)
            return True
        return False

    def curiosity_sample(self, rng: random.Random) -> Candidate | None:
        if not self.cells:
            return None
        keys = list(self.cells)
        weights = []
        for k in keys:
            v = self.visits.get(k, 0)
            r = self.cells[k][0]
            weights.append((1.0 / math.sqrt(v + 1)) * (1.0 + max(0.0, r)))
        return self.cells[rng.choices(keys, weights=weights, k=1)[0]][1]

    def stepping_stones(self) -> list[Candidate]:
        out = []
        for _, c, rec in self.cells.values():
            if rec.get("max_stage") in ("A", "B", "C") and rec["reward"] > 0:
                out.append(c)
        return out

    def best_evolved(self) -> tuple[Candidate, dict] | None:
        ranked = sorted(self.cells.values(), key=lambda t: t[0], reverse=True)
        for r, c, rec in ranked:
            if str(c.origin).startswith("baseline_persist"):
                continue
            if rec.get("max_stage") == "D" and rec.get("correct", 0) >= 2 and rec.get("isolation_ok"):
                return c, rec
        for r, c, rec in ranked:
            if str(c.origin).startswith("baseline_persist"):
                continue
            if rec["reward"] > 0.5 and rec.get("isolation_ok"):
                return c, rec
        return None

    def dump(self) -> list[dict]:
        rows = []
        for k, (r, c, rec) in sorted(self.cells.items()):
            rows.append(
                {
                    "cell": list(k),
                    "reward": r,
                    "origin": c.origin,
                    "layout": rec.get("layout"),
                    "max_stage": rec.get("max_stage"),
                    "correct": rec.get("correct"),
                    "length": rec.get("length"),
                    "visits": self.visits.get(k, 0),
                    "instrs": _jsonable(c.instrs),
                }
            )
        return rows


def _save_ckpt(path: Path, archive: PersistArchive, history: list, evals: int, t0: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = {
        "evals": evals,
        "elapsed": time.time() - t0,
        "archive": archive.dump(),
        "visits": {f"{a},{b}": v for (a, b), v in archive.visits.items()},
        "history": history[-400:],
    }
    path.write_text(json.dumps(blob) + "\n")


def _load_ckpt(path: Path, next_id: list[int]) -> tuple[PersistArchive, list, int]:
    archive = PersistArchive()
    history: list = []
    evals = 0
    if not path.exists():
        return archive, history, evals
    data = json.loads(path.read_text())
    evals = int(data.get("evals", 0))
    history = list(data.get("history", []))
    for row in data.get("archive", []):
        cand = Candidate(
            instrs=_from_rows(row["instrs"]),
            origin=row.get("origin", "ckpt"),
            id=next_id[0],
        )
        next_id[0] += 1
        rec = {
            "reward": row["reward"],
            "layout": row.get("layout", "empty"),
            "max_stage": row.get("max_stage"),
            "correct": row.get("correct", 0),
            "isolation_ok": True,
            "leak": False,
            "invalid": False,
            "cell": row["cell"],
            "length": row.get("length"),
            "n_write": 0,
        }
        archive.cells[(row["cell"][0], row["cell"][1])] = (row["reward"], cand, rec)
    for k, v in data.get("visits", {}).items():
        a, b = k.split(",")
        archive.visits[(int(a), int(b))] = int(v)
    print(f"resumed checkpoint evals={evals} cells={archive.cells and len(archive.cells)}", flush=True)
    return archive, history, evals


def run_search(
    *,
    wall_seconds: float = 3000.0,
    population: int = 24,
    workers: int = 4,
    seeds: list[int] | None = None,
    checkpoint: Path | None = None,
    resume: bool = True,
) -> dict:
    seeds = seeds or [1, 7, 13, 42, 99, 2024, 8, 33]
    ckpt_path = checkpoint or CKPT
    operators = GADGET_OPS
    next_id = [1]
    archive, history, evals = _load_ckpt(ckpt_path, next_id) if resume else (PersistArchive(), [], 0)
    t0 = time.time()
    seed_cands = load_seeds()
    policy = make_policy("ucb1", operators)
    rng = random.Random(seeds[0])

    def consider(cand: Candidate, rec: dict) -> None:
        rec["origin"] = cand.origin
        rec["id"] = cand.id
        archive.add(cand, rec)

    with ProcessPoolExecutor(max_workers=workers) as pool:

        def submit(cands: list[Candidate]) -> list[tuple[Candidate, dict]]:
            futs = {pool.submit(eval_curriculum_rows, _jsonable(c.instrs)): c for c in cands}
            out = []
            for fut in as_completed(futs):
                c = futs[fut]
                rec = fut.result()
                out.append((c, rec))
            return out

        # Baseline Condition A once.
        base_rec = eval_curriculum_rows(_jsonable(persist_rr().instrs))
        consider(persist_rr(), base_rec)
        evals += 1
        print(
            f"baseline persist_rr R={base_rec['reward']:.3f} stage={base_rec['max_stage']} "
            f"correct={base_rec['correct']} layout={base_rec['layout']}",
            flush=True,
        )

        if not archive.cells:
            init = []
            for e in seed_cands:
                init.append(Candidate(instrs=e.instrs, origin=e.origin, id=next_id[0]))
                next_id[0] += 1
            while len(init) < population:
                init.append(
                    Candidate(
                        instrs=random_program(rng, rng.randint(24, 64), pack="gadgets"),
                        origin="random",
                        id=next_id[0],
                    )
                )
                next_id[0] += 1
            scored = submit(init)
            evals += len(scored)
            for c, rec in scored:
                consider(c, rec)
            top = max(scored, key=lambda t: t[1]["reward"])
            print(
                f"init R={top[1]['reward']:.3f} stage={top[1]['max_stage']} "
                f"layout={top[1]['layout']} cells={len(archive.cells)}",
                flush=True,
            )

        gen = 0
        seed_i = 0
        last_ckpt = time.time()
        while time.time() - t0 < wall_seconds:
            gen += 1
            seed_i = (seed_i + 1) % len(seeds)
            rng = random.Random(seeds[seed_i] * 10007 + gen)
            stones = archive.stepping_stones()
            children: list[Candidate] = []
            for _ in range(population):
                pick = rng.random()
                if pick < 0.45 and archive.cells:
                    parent = archive.curiosity_sample(rng)
                elif pick < 0.7 and stones:
                    parent = rng.choice(stones)
                elif pick < 0.85:
                    parent = rng.choice(seed_cands)
                    parent = Candidate(instrs=parent.instrs, origin=parent.origin, id=next_id[0])
                else:
                    parent = Candidate(
                        instrs=random_program(rng, rng.randint(20, 56), pack="gadgets"),
                        origin="random",
                        id=next_id[0],
                    )
                    next_id[0] += 1
                    children.append(parent)
                    continue
                assert parent is not None
                other = archive.curiosity_sample(rng) or rng.choice(seed_cands)
                op = policy.select_operator(rng)
                child = mutate(
                    parent,
                    op,
                    rng,
                    hall=stones or None,
                    other=other,
                    max_len=256,
                    operators=operators,
                )
                child = Candidate(
                    instrs=child.instrs,
                    origin=child.origin,
                    parent_id=parent.id,
                    id=next_id[0],
                )
                next_id[0] += 1
                children.append(child)
            scored = submit(children)
            evals += len(scored)
            rewards = []
            for c, rec in scored:
                consider(c, rec)
                rewards.append(rec["reward"])
                policy.update(c.origin if c.origin in operators else "insert", rec["reward"])
            top = max(scored, key=lambda t: t[1]["reward"])
            ev = {
                "gen": gen,
                "seed": seeds[seed_i],
                "elapsed": time.time() - t0,
                "evals": evals,
                "best_batch": top[1]["reward"],
                "mean_batch": sum(rewards) / max(len(rewards), 1),
                "stage": top[1]["max_stage"],
                "layout": top[1]["layout"],
                "cells": len(archive.cells),
                "origin": top[1].get("origin") or top[0].origin,
            }
            history.append(ev)
            CURVE.parent.mkdir(parents=True, exist_ok=True)
            with CURVE.open("a") as fh:
                fh.write(json.dumps(ev) + "\n")
            if gen == 1 or gen % 5 == 0:
                be = archive.best_evolved()
                br = be[1]["reward"] if be else None
                print(
                    f"gen {gen} t={ev['elapsed']:.0f}s evals={evals} batchR={top[1]['reward']:.3f} "
                    f"stage={top[1]['max_stage']} layout={top[1]['layout']} "
                    f"cells={len(archive.cells)} evolvedR={br}",
                    flush=True,
                )
            if time.time() - last_ckpt >= 60:
                _save_ckpt(ckpt_path, archive, history, evals, t0)
                last_ckpt = time.time()

    _save_ckpt(ckpt_path, archive, history, evals, t0)

    QD_DIR.mkdir(parents=True, exist_ok=True)
    cells_out = []
    for (x, y), (r, c, rec) in archive.cells.items():
        layout = rec.get("layout") or (LAYOUTS[x] if x < len(LAYOUTS) else "other")
        cells_out.append(
            {
                "cell": [x, y],
                "layout": layout,
                "reward": r,
                "origin": c.origin,
                "max_stage": rec.get("max_stage"),
                "correct": rec.get("correct"),
                "length": rec.get("length"),
            }
        )
        (QD_DIR / f"{layout}-c{x}-{y}.txt").write_text(
            f"# {c.origin} R={r:.4f} layout={layout} stage={rec.get('max_stage')} gated={rec.get('correct')}\n"
            + disassemble(c.instrs)
            + "\n"
        )

    baseline = eval_curriculum_rows(_jsonable(persist_rr().instrs))
    evolved = archive.best_evolved()
    note = ""
    elite_path = ""
    if evolved is None:
        note = (
            "no evolved program reached Condition A (persist_rr) on gated D. "
            "persist_rr remains the comparison baseline, not the OS."
        )
    else:
        c, rec = evolved
        if rec.get("max_stage") == "D" and rec.get("correct", 0) >= 2:
            ELITE_DIR.mkdir(parents=True, exist_ok=True)
            header = (
                f"# {c.origin} R={rec['reward']:.4f} layout={rec.get('layout')} "
                f"stage=D gated={rec.get('correct')} isolation_ok={rec.get('isolation_ok')}\n"
            )
            OUT_ELITE.write_text(header + disassemble(c.instrs) + "\n")
            elite_path = str(OUT_ELITE)
            note = "wrote persist_gadgets_elite.txt (evolved, gated D 2/2)"
        else:
            note = (
                f"best evolved is {c.origin} stage={rec.get('max_stage')} "
                f"gated={rec.get('correct')} — not shipped (below persist_rr D)."
            )

    summary = {
        "evals": evals,
        "elapsed": time.time() - t0,
        "cells": cells_out,
        "history_tail": history[-30:],
        "baseline_persist_rr": {
            k: baseline[k]
            for k in ("reward", "max_stage", "correct", "layout", "isolation_ok", "leak")
        },
        "best_evolved": None
        if evolved is None
        else {
            "origin": evolved[0].origin,
            "reward": evolved[1]["reward"],
            "max_stage": evolved[1].get("max_stage"),
            "correct": evolved[1].get("correct"),
            "layout": evolved[1].get("layout"),
            "isolation_ok": evolved[1].get("isolation_ok"),
        },
        "note": note,
        "elite_path": elite_path,
        "curve": str(CURVE),
        "checkpoint": str(ckpt_path),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: summary[k] for k in summary if k != "history_tail"}, indent=2))
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--wall-seconds", type=float, default=3000.0)
    p.add_argument("--population", type=int, default=24)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--seeds", default="1,7,13,42,99,2024,8,33")
    p.add_argument("--no-resume", action="store_true")
    args = p.parse_args(argv)
    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    run_search(
        wall_seconds=args.wall_seconds,
        population=args.population,
        workers=args.workers,
        seeds=seeds,
        resume=not args.no_resume,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
