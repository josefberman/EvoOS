"""Why bare-bits discovery fails: analytic + empirical density of the cover event.

Cover = an ADDPROT range fully contains a guest's [DATA_BASE, DATA_BOUND).
That is the rare event rounds 3–6 stalled on. This module estimates how rare.
"""

from __future__ import annotations

import math
import random
from pathlib import Path

from evoos.agent.mutations import BARE_OPS, mutate, random_instr, random_program
from evoos.agent.program import Candidate
from evoos.evaluator.workloads import GUEST_SLOTS, curriculum_stages, training_workloads
from evoos.vm.instruction_set import N_REGS, Instr, Op
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import Sandbox


def analytic_mmu_gadget() -> dict:
    """Probability a uniform random 4-instruction sequence *is* the MMU gadget.

    Template: MOVI ra, ctx; CTXGET rb, ra, DATA_BASE; CTXGET rc, ra, DATA_BOUND;
    ADDPROT ra, rb, rc with distinct ra,rb,rc in 1..7 and ctx in 1..4.

    Random instruction: 31 non-HALT ops, 8^3 register fields, ~40 typical imms
    used by `random_instr` (mixture, not uniform over 2^21). We report both a
    uniform-imm lower bound and a random_instr-matched estimate.
    """
    n_op = 31
    n_reg = N_REGS
    n_tmpl = 4 * 7 * 6 * 5  # ctx × P(7,3)
    # Per-instruction space if imm is one of the ~40 small/firmware values.
    n_imm_eff = 40
    space_instr = n_op * (n_reg**3) * n_imm_eff
    space_4 = space_instr**4
    p_exact = n_tmpl / space_4 if space_4 else 0.0
    # Independent of order: P(the 4-op motif appears as a contiguous block in
    # a length-L program) ≈ (L-3) * p_exact for L>=4, ignoring overlap.
    p_in_24 = max(0.0, 21 * p_exact)
    # Expected evals to first hit (geometric).
    ev_exact = (1.0 / p_exact) if p_exact else float("inf")
    ev_24 = (1.0 / p_in_24) if p_in_24 else float("inf")
    # 4/4 needs the gadget composed over 4 guests: a scan (~5 instr) plus 4
    # uses, or 4 unrolled copies. Independent copies: p_24**4.
    p_four = p_in_24**4
    ev_four = (1.0 / p_four) if p_four else float("inf")
    return {
        "n_template": n_tmpl,
        "space_instr_eff": space_instr,
        "p_exact_4": p_exact,
        "p_in_len24": p_in_24,
        "expected_evals_one_cover_analytic": ev_24,
        "expected_evals_4of4_independent": ev_four,
        "note": "Effective imm alphabet 40 from random_instr mix; not 2^21.",
    }


def _cover_one(sandbox: Sandbox, instrs, workloads) -> bool:
    for w in workloads:
        r = sandbox.evaluate(instrs, w)
        if any(r.prot_cover):
            return True
    return False


def empirical_random(*, n: int, length: int, seed: int, k1: bool) -> dict:
    rng = random.Random(seed)
    cfg = lenient_config(max_steps=800, n_contexts=2 if k1 else 5)
    sandbox = Sandbox(cfg)
    wls = list(curriculum_stages()[0]["train"] if k1 else training_workloads())
    hits = 0
    run_hits = 0
    for i in range(n):
        prog = random_program(rng, length, pack="bare")
        r = sandbox.evaluate(prog, wls[0])
        if any(r.prot_cover):
            hits += 1
            if r.entered_protected:
                run_hits += 1
        sandbox.clear_cache()
        sandbox = Sandbox(cfg) if i % 500 == 499 else sandbox
    p = hits / n if n else 0.0
    return {
        "n": n,
        "length": length,
        "k1": k1,
        "cover_hits": hits,
        "cover_and_run_hits": run_hits,
        "p_cover": p,
        "expected_evals": (1.0 / p) if p else float("inf"),
    }


def empirical_restricted(*, n: int, seed: int) -> dict:
    """Length-4 programs whose opcodes are only the MMU calling-convention ops."""
    rng = random.Random(seed)
    cfg = lenient_config(max_steps=400, n_contexts=2)
    sandbox = Sandbox(cfg)
    wl = curriculum_stages()[0]["train"][0]
    ops = (int(Op.MOVI), int(Op.CTXGET), int(Op.ADDPROT))
    hits = 0
    for _ in range(n):
        prog = tuple(
            Instr(rng.choice(ops), rng.randrange(8), rng.randrange(8), rng.randrange(8), rng.randint(0, 16))
            for _ in range(4)
        )
        r = sandbox.evaluate(prog, wl)
        if any(r.prot_cover):
            hits += 1
    p = hits / n if n else 0.0
    return {
        "n": n,
        "ops": "MOVI|CTXGET|ADDPROT",
        "cover_hits": hits,
        "p_cover": p,
        "expected_evals": (1.0 / p) if p else float("inf"),
    }


def empirical_mutate(*, n: int, seed: int) -> dict:
    rng = random.Random(seed)
    cfg = lenient_config(max_steps=800, n_contexts=2)
    sandbox = Sandbox(cfg)
    wl = curriculum_stages()[0]["train"][0]
    parent = Candidate(instrs=random_program(rng, 24, pack="bare"), origin="r", id=1)
    hits = 0
    first = None
    for i in range(n):
        child = mutate(parent, rng.choice(BARE_OPS), rng, max_len=64, operators=BARE_OPS)
        r = sandbox.evaluate(child.instrs, wl)
        if any(r.prot_cover):
            hits += 1
            if first is None:
                first = i + 1
        parent = child
        parent = Candidate(instrs=parent.instrs, origin=parent.origin, id=i + 2)
    p = hits / n if n else 0.0
    return {
        "n": n,
        "cover_hits": hits,
        "p_cover": p,
        "first_hit_eval": first,
        "expected_evals": (1.0 / p) if p else float("inf"),
    }


def enumerate_gadget_family() -> dict:
    """Count covering length-4 programs in the calling-convention family."""
    from evoos.vm.instruction_set import CtxField

    n = 0
    for ctx in range(1, 5):
        for ra in range(1, 8):
            for rb in range(1, 8):
                if rb == ra:
                    continue
                for rc in range(1, 8):
                    if rc in (ra, rb):
                        continue
                    n += 1
                    _ = (
                        Instr(int(Op.MOVI), ra, 0, 0, ctx),
                        Instr(int(Op.CTXGET), rb, ra, 0, int(CtxField.DATA_BASE)),
                        Instr(int(Op.CTXGET), rc, ra, 0, int(CtxField.DATA_BOUND)),
                        Instr(int(Op.ADDPROT), ra, rb, rc, 0),
                    )
    return {"family_size": n, "slots": list(GUEST_SLOTS)}


def observed_round6() -> dict:
    """Measured first-hit from the round-6 equal-eval sweep (real numbers)."""
    # 12 jobs × 320000 evals; 1 cover (blind seed 99, k4_hard gen 229).
    jobs = 12
    evals_per = 320000
    total = jobs * evals_per
    # k4_hard starts after 4 stages × 1000 × 64.
    prior = 4 * 1000 * 64
    first_in_seed = prior + 229 * 64
    return {
        "sweep_evals": total,
        "cover_events": 1,
        "p_hat": 1 / total,
        "expected_evals_from_sweep": total,
        "seed99_first_cover_eval": first_in_seed,
        "seed99_stage_gen": 229,
        "lexicase_cover_events": 0,
        "four_of_four": 0,
    }


def budget_for_four(p_one: float, *, target: float = 0.5) -> float:
    """Evals so that P(at least one 4/4) ≥ target, if 4/4 ≈ p_one**4 independent."""
    p4 = p_one**4 if p_one else 0.0
    if p4 <= 0:
        return float("inf")
    # 1 - (1-p4)^N = target => N = log(1-target)/log(1-p4)
    # Tiny p4 underflows 1-p4 to 1.0 in IEEE-754; use -ln(1-t)/p4.
    denom = math.log1p(-p4)
    if denom == 0.0:
        return math.log(1.0 - target) / -p4
    return math.log(1.0 - target) / denom


def run_report(*, random_n: int = 8000, mutate_n: int = 4000, seed: int = 0) -> dict:
    ana = analytic_mmu_gadget()
    fam = enumerate_gadget_family()
    obs = observed_round6()
    emp_k1 = empirical_random(n=random_n, length=24, seed=seed, k1=True)
    emp_k4 = empirical_random(n=max(2000, random_n // 2), length=24, seed=seed + 1, k1=False)
    emp_mut = empirical_mutate(n=mutate_n, seed=seed + 2)
    emp_rest = empirical_restricted(n=max(2000, random_n), seed=seed + 3)
    p_emp = emp_k1["p_cover"] or obs["p_hat"]
    return {
        "analytic": ana,
        "gadget_family": fam,
        "empirical_random_k1": emp_k1,
        "empirical_random_k4": emp_k4,
        "empirical_mutate": emp_mut,
        "empirical_restricted": emp_rest,
        "observed_round6": obs,
        "budget_50pct_4of4_from_emp": budget_for_four(p_emp, target=0.5),
        "budget_50pct_4of4_from_sweep_phat": budget_for_four(obs["p_hat"], target=0.5),
    }


def write_markdown(rep: dict) -> str:
    ana = rep["analytic"]
    obs = rep["observed_round6"]
    k1 = rep["empirical_random_k1"]
    k4 = rep["empirical_random_k4"]
    mut = rep["empirical_mutate"]
    fam = rep["gadget_family"]
    lines = [
        "# Why bare-bits discovery fails",
        "",
        "Rounds 3–6 searched random 5-tuples (bare pack) with four methods: "
        "UCB1, curriculum+shaping, learned GRU edits, and lexicase+dataflow. "
        "Combined 4/4 count on the gated 4-guest mix: **1/42** (round 3, ungated flat, "
        "did not replicate) then **0/24 + 0/12 + 0/12**. The bottleneck is the "
        "**cover** event: `ADDPROT` must contain a guest's `[DATA_BASE, DATA_BOUND)`.",
        "",
        "## Analytic density",
        "",
        f"Calling-convention family size (length-4 MMU gadget): **{fam['family_size']}** "
        f"(4 ctx ids × P(7,3) distinct regs). Guest windows: `{fam['slots']}`.",
        "",
        f"Effective per-instruction alphabet used by `random_instr` ≈ `{ana['space_instr_eff']:.3g}` "
        f"(31 ops × 8³ regs × 40 immediates). Four-instruction space ≈ "
        f"`{ana['space_instr_eff']**4:.3g}`.",
        "",
        f"P(a uniform length-4 block **is** the gadget) ≈ `{ana['p_exact_4']:.3g}`.",
        f"P(a length-24 random program contains that block) ≈ `{ana['p_in_len24']:.3g}`.",
        f"Expected evals to first such block: **{ana['expected_evals_one_cover_analytic']:.3g}**.",
        f"If four independent copies were required for 4/4: "
        f"**{ana['expected_evals_4of4_independent']:.3g}** evals.",
        "",
        "A loop over `N_CONTEXTS` (the `insert_ctx_scan` gadget) is **not** in the "
        "bare pack. Bare search must invent that five-instruction scan as well as "
        "the MMU calling convention.",
        "",
        "## Empirical density (this run)",
        "",
        f"Random bare length-24 on k1 ({k1['n']} programs): cover hits "
        f"**{k1['cover_hits']}**, p̂=`{k1['p_cover']:.3g}`, "
        f"E[evals]=`{k1['expected_evals']}` "
        f"(cover+run **{k1['cover_and_run_hits']}**).",
        "",
        f"Random bare length-24 on 4-guest train ({k4['n']} programs): cover hits "
        f"**{k4['cover_hits']}**, p̂=`{k4['p_cover']:.3g}`, "
        f"E[evals]=`{k4['expected_evals']}`.",
        "",
        f"Single-lineage bare mutate ({mut['n']} evals): cover hits **{mut['cover_hits']}**, "
        f"first hit at eval `{mut['first_hit_eval']}`, p̂=`{mut['p_cover']:.3g}`.",
        "",
        f"Restricted length-4 `MOVI|CTXGET|ADDPROT` ({rep['empirical_restricted']['n']} programs): "
        f"cover hits **{rep['empirical_restricted']['cover_hits']}**, "
        f"p̂=`{rep['empirical_restricted']['p_cover']:.3g}`, "
        f"E[evals]=`{rep['empirical_restricted']['expected_evals']}`. "
        "Even inside this three-opcode subset, cover needs the exact CTXGET field pair "
        "(`DATA_BASE` then `DATA_BOUND`) and matching registers — the 840-member family.",
        "",
        "## Observed search (round 6, real numbers)",
        "",
        f"12 jobs × 320000 evals = **{obs['sweep_evals']}**. Cover events: "
        f"**{obs['cover_events']}** (blind seed 99, k4_hard gen {obs['seed99_stage_gen']}, "
        f"≈{obs['seed99_first_cover_eval']} evals into that seed). Lexicase cover events: "
        f"**{obs['lexicase_cover_events']}**. 4/4: **{obs['four_of_four']}**.",
        f"Sweep p̂ = `{obs['p_hat']:.3g}` (1/{obs['sweep_evals']}).",
        "",
        f"Budget for 50% chance of a 4/4 if four covers were independent at that p̂: "
        f"**{rep['budget_50pct_4of4_from_sweep_phat']:.3g}** evals. "
        "They are not independent — 4/4 also needs a scan/unroll and RUNCTX after "
        "cover — so this is a **lower** bound on the remaining search. Round-2 "
        "gadgets reach 4/4 because `insert_mmu_gadget` *is* the 840-member family.",
        "",
        "## Close",
        "",
        "Bare-substrate discovery is closed. Further budget on random 5-tuples "
        "does not create a gradient on cover (lexicase cases are 0 for everyone "
        "until the event). The main line moves to Condition **C-hw**: the "
        "hardware-manual (gadget) pack, with a richer environment so *organization* "
        "(placement, eviction, routing) is not given.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import json
    import time

    t0 = time.perf_counter()
    rep = run_report()
    rep["elapsed_sec"] = time.perf_counter() - t0
    out = Path("experiments/scheduling/results/round7")
    out.mkdir(parents=True, exist_ok=True)
    (out / "bare_density.json").write_text(json.dumps(rep, default=str, indent=2))
    md = write_markdown(rep)
    (out / "why_bare_fails.md").write_text(md)
    print(md)
    print("elapsed", f"{rep['elapsed_sec']:.1f}s")
