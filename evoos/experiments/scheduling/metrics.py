"""Shared scoring of a supervisor on train + held-out mixes."""

from __future__ import annotations

from evoos.agent.program import Candidate
from evoos.evaluator.cases import case_vector, cases_solved, summarize_cases
from evoos.evaluator.reward import RewardWeights, score
from evoos.experiments.scheduling.analyze import classify_trace, opcode_stats
from evoos.vm.sandbox import Sandbox, Workload


def evaluate_split(
    sandbox: Sandbox,
    cand: Candidate,
    train: list[Workload],
    heldout: list[Workload],
) -> dict:
    train_res = [sandbox.evaluate(cand.instrs, w) for w in train]
    held_res = [sandbox.evaluate(cand.instrs, w) for w in heldout]
    train_bd = score(train_res, novelty=0.0, weights=RewardWeights.correctness_only())
    held_bd = score(held_res, novelty=0.0, weights=RewardWeights.correctness_only())
    four = all(r.completed == r.n_guests and r.isolation_ok for r in train_res + held_res)
    names, vec = case_vector(train_res + held_res, gated=True)
    solved = cases_solved(names, vec)
    summary = summarize_cases(names, vec)
    labels = {r.workload: classify_trace(r) for r in train_res + held_res}
    # Majority behavioral label on held-out, then train.
    held_labels = [classify_trace(r) for r in held_res]
    structural = max(set(held_labels), key=held_labels.count) if held_labels else "other"
    return {
        "train_reward": train_bd.total,
        "held_reward": held_bd.total,
        "train_completed": [r.completed for r in train_res],
        "held_completed": [r.completed for r in held_res],
        "train_n": [r.n_guests for r in train_res],
        "held_n": [r.n_guests for r in held_res],
        "train_isolation": all(r.isolation_ok for r in train_res),
        "held_isolation": all(r.isolation_ok for r in held_res),
        "four_of_four": four,
        "labels": labels,
        "structural_label": structural,
        "train_switches": [r.switch_count for r in train_res],
        "held_switches": [r.switch_count for r in held_res],
        "train_guest_cycles": [r.guest_cycles for r in train_res],
        "held_guest_cycles": [r.guest_cycles for r in held_res],
        "opcode_stats": opcode_stats(cand),
        "origin": cand.origin,
        "length": len(cand),
        "case_names": names,
        "case_vec": vec,
        "cases_solved": solved,
        "cases_summary": summary,
    }


def case_failure_table(
    sandbox: Sandbox,
    cand: Candidate,
    train: list[Workload],
    heldout: list[Workload],
) -> list[dict]:
    """Per-guest blockers on the official split (used when 4/4 is 0/N)."""
    rows = []
    for split, ws in (("train", train), ("held", heldout)):
        for w in ws:
            r = sandbox.evaluate(cand.instrs, w)
            failed = []
            for i, o in enumerate(r.outcomes):
                if o.correct:
                    continue
                role = w.guests[i].role if i < len(w.guests) else ""
                failed.append(
                    {
                        "i": i,
                        "name": o.name,
                        "role": role,
                        "halted": o.halted,
                        "fault": int(o.fault),
                        "cycles": o.cycles,
                    }
                )
            rows.append(
                {
                    "split": split,
                    "workload": w.name,
                    "completed": r.completed,
                    "n_guests": r.n_guests,
                    "delivered": r.delivered,
                    "isolation_ok": r.isolation_ok,
                    "share_leak": bool(r.share_leak),
                    "n_clrprot": int(r.n_clrprot),
                    "n_stale": int(getattr(r, "n_stale", 0) or 0),
                    "style": r.route_style,
                    "failed": failed,
                }
            )
    return rows


def diagnose(row: dict) -> str:
    """Short note when 4/4 failed."""
    if row.get("four_of_four"):
        return "4/4 train+heldout with isolation"
    bits = []
    if not row.get("train_isolation") or not row.get("held_isolation"):
        bits.append("isolation failure")
    tc, hc = row.get("train_completed", []), row.get("held_completed", [])
    bits.append(f"train completed {tc} held {hc}")
    cyc = row.get("train_guest_cycles") or []
    if cyc:
        starved = []
        for mix in cyc:
            starved.append([i for i, c in enumerate(mix) if c <= 2])
        bits.append(f"near-zero-cycle guests (train idx) {starved}")
    bits.append(f"label={row.get('structural_label')}")
    return "; ".join(bits)
