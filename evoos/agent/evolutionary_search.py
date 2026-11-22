"""Evolutionary search over supervisor bytecode + MAP-Elites + policy update."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from evoos.agent.learned_policy import LearnedEditPolicy, telemetry_vec
from evoos.agent.lexicase import lexicase_select
from evoos.agent.map_elites import MapElites
from evoos.agent.mutations import OPERATOR_PACKS, GADGET_OPS, mutate, random_program
from evoos.evaluator.cases import case_vector, summarize_cases
from evoos.agent.policy import MutationPolicy, make_policy
from evoos.agent.program import Candidate
from evoos.agent.replay_buffer import ReplayBuffer, Transition
from evoos.evaluator.novelty import (
    NoveltyArchive,
    behavioral_descriptor,
    elites_cell,
    hw_cell,
    is_non_baseline_route,
    milestone_cell,
    route_cell,
    opcode_histogram,
)
from evoos.evaluator.reward import RewardBreakdown, RewardWeights, score
from evoos.vm.sandbox import EvalResult, Sandbox, Workload
from evoos.vm.verifier import VerifyError, verify_supervisor


INVALID_REWARD = -1.0


def remote_evaluate(payload: dict) -> dict:
    """Process-pool worker: run the sandbox, return EvalResults. No novelty."""
    from evoos.vm.instruction_set import Instr
    from evoos.vm.machine import MachineConfig
    from evoos.vm.sandbox import Sandbox
    from evoos.vm.verifier import VerifyError, verify_supervisor

    instrs = tuple(Instr(*x) for x in payload["instrs"])
    cfg = MachineConfig(**payload["machine"])
    try:
        verify_supervisor(instrs, cfg)
    except VerifyError as exc:
        return {"invalid": True, "reason": str(exc), "train": [], "held": []}
    sb = Sandbox(cfg)
    train = [sb.evaluate(instrs, w) for w in payload["train"]]
    held = [sb.evaluate(instrs, w) for w in payload["held"]] if payload.get("compute_held") else []
    invalid = bool(train and train[0].invalid)
    return {
        "invalid": invalid,
        "train": train,
        "held": held,
        "reason": train[0].invalid_reason if invalid and train else "",
    }


def _machine_dict(cfg) -> dict:
    return {
        "mem_words": cfg.mem_words,
        "n_contexts": cfg.n_contexts,
        "max_steps": cfg.max_steps,
        "max_supervisor_len": cfg.max_supervisor_len,
        "sticky_guest_fault": cfg.sticky_guest_fault,
        "supervisor_halt_on_mem": cfg.supervisor_halt_on_mem,
        "runctx_posts_status": cfg.runctx_posts_status,
        "n_blocks": getattr(cfg, "n_blocks", 32),
        "block_words": getattr(cfg, "block_words", 16),
        "blk_latency": getattr(cfg, "blk_latency", 4),
    }


def archive_has_protected_complete(state: SearchState, n_guests: int) -> bool:
    """True if the archive holds a protected-complete elite for this guest count.

    Cell is (n_prot, n_entered_protected, n_progressed_protected, n_completed, n_guests).
    k1 promotion looks for 1/1 completed_protected with matching n_guests.
    """
    if n_guests <= 0:
        return False
    for cell in state.archive.cells:
        if len(cell) >= 5 and int(cell[3]) >= n_guests and int(cell[4]) == n_guests:
            return True
    return False


def _invalid_breakdown(reason: str = "") -> RewardBreakdown:
    return RewardBreakdown(
        total=INVALID_REWARD,
        f=0,
        s=0,
        e=0,
        g=0,
        n=0,
        hard_fail=False,
        isolation_ok=True,
        completed=0,
        n_guests=0,
        details={"invalid": True, "reason": reason},
    )


def _make_eval_pool(n_workers: int):
    if n_workers <= 1:
        return None
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor

    # fork: payloads are pickled either way, but workers are already imported.
    # spawn was ~10× slower than in-process round-4 evals on this VM.
    ctx = mp.get_context("fork")
    return ProcessPoolExecutor(max_workers=int(n_workers), mp_context=ctx)


@dataclass
class SearchConfig:
    generations: int = 30
    population: int = 20
    seed: int = 0
    init_len: int = 24
    elite_fraction: float = 0.2
    archive_mix: float = 0.5
    eval_heldout_every: int = 1
    mode: str = "qd"  # "correctness" | "qd"
    policy: str = "ucb1"
    max_len: int = 256
    log_every: int = 1
    operator_pack: str = "gadgets"
    keep_records: bool = True
    shaping: bool = False
    rich_efficiency: bool = False
    early_stop_complete: bool = False
    gated: bool = False
    curiosity: bool = False
    milestone_archive: bool = False
    elite_crossover: bool = False
    eval_workers: int = 1
    # If set, this object is used in-process (not pickled across job workers).
    learned: object | None = None
    selection: str = "fitness"  # fitness | lexicase | epsilon_lexicase
    dataflow: bool = False
    representation: str = "linear"  # linear | cgp
    epsilon: float | None = None  # None + epsilon_lexicase => auto MAD
    hw_archive: bool = False
    route_archive: bool = False


@dataclass
class EvalRecord:
    generation: int
    candidate: Candidate
    reward: RewardBreakdown
    train: list[EvalResult]
    heldout: list[EvalResult]
    operator: str
    invalid: bool


@dataclass
class SearchState:
    best: Candidate | None = None
    best_reward: float = -1e9
    best_record: EvalRecord | None = None
    history: list[dict] = field(default_factory=list)
    archive: MapElites = field(default_factory=MapElites)
    records: list[EvalRecord] = field(default_factory=list)
    population: list[Candidate] = field(default_factory=list)
    first_solved: dict[str, int] = field(default_factory=dict)
    case_names: list[str] = field(default_factory=list)


def _weights(mode: str) -> RewardWeights:
    if mode == "correctness":
        return RewardWeights.correctness_only()
    if mode == "condition_b":
        return RewardWeights.condition_b()
    return RewardWeights.quality_diversity()


def _needed(workloads: list[Workload]) -> list[int]:
    # Per-guest needed cycles of the first workload; used as a rough scale.
    if not workloads:
        return []
    return [g.needed_cycles for g in workloads[0].guests]


def evaluate_candidate(
    sandbox: Sandbox,
    candidate: Candidate,
    train: list[Workload],
    heldout: list[Workload],
    novelty: NoveltyArchive,
    weights: RewardWeights,
    compute_heldout: bool,
    shaping: bool = False,
    rich_efficiency: bool = False,
    gated: bool = False,
) -> tuple[RewardBreakdown, list[EvalResult], list[EvalResult], bool]:
    try:
        verify_supervisor(candidate.instrs, sandbox.config)
    except VerifyError:
        empty = RewardBreakdown(
            total=INVALID_REWARD,
            f=0,
            s=0,
            e=0,
            g=0,
            n=0,
            hard_fail=False,
            isolation_ok=True,
            completed=0,
            n_guests=0,
            details={"invalid": True},
        )
        return empty, [], [], True

    train_results = [sandbox.evaluate(candidate.instrs, w) for w in train]
    if train_results and train_results[0].invalid:
        empty = RewardBreakdown(
            total=INVALID_REWARD,
            f=0,
            s=0,
            e=0,
            g=0,
            n=0,
            hard_fail=False,
            isolation_ok=True,
            completed=0,
            n_guests=0,
            details={"invalid": True, "reason": train_results[0].invalid_reason},
        )
        return empty, train_results, [], True

    held_results: list[EvalResult] = []
    if compute_heldout:
        held_results = [sandbox.evaluate(candidate.instrs, w) for w in heldout]

    beh = behavioral_descriptor(train_results[0]) if train_results else (0.0,)
    hist = opcode_histogram(candidate.instrs)
    vec = tuple(beh) + tuple(hist[:8])
    n_score = 0.0
    if weights.n:
        n_score = novelty.score(vec)
        novelty.add(vec)

    breakdown = score(
        train_results,
        heldout_results=held_results or None,
        novelty=n_score,
        weights=weights,
        needed_cycles=_needed(train),
        shaping=shaping,
        rich_efficiency=rich_efficiency,
        gated=gated,
    )
    return breakdown, train_results, held_results, False


def run_search(
    sandbox: Sandbox,
    train: list[Workload],
    heldout: list[Workload],
    config: SearchConfig,
    *,
    on_generation: Callable[[int, SearchState, dict], None] | None = None,
    extra_seeds: list[Candidate] | None = None,
) -> SearchState:
    rng = random.Random(config.seed)
    weights = _weights(config.mode)
    operators = OPERATOR_PACKS.get(config.operator_pack, GADGET_OPS)
    learned = config.learned
    if learned is None and config.policy in ("reinforce", "gru", "ppo", "learned"):
        learned = LearnedEditPolicy(operators=operators)
        config.learned = learned
    policy: MutationPolicy = learned if learned is not None else make_policy(config.policy, operators)
    novelty = NoveltyArchive()
    replay = ReplayBuffer()
    state = SearchState()
    hall: list[Candidate] = []
    next_id = 1

    cgp_genomes: dict[int, tuple] = {}
    fw_pool = ()
    if train:
        from evoos.agent.dataflow import firmware_const_pool

        fw_pool = firmware_const_pool(
            n_guests=len(train[0].guests),
            mem_words=sandbox.config.mem_words,
            max_steps=sandbox.config.max_steps,
        )
    pop: list[Candidate] = []
    for seed in extra_seeds or []:
        pop.append(Candidate(instrs=seed.instrs, origin="seed", id=next_id))
        next_id += 1
    while len(pop) < config.population:
        if config.representation == "cgp":
            from evoos.agent import cgp as _cgp

            cand, genome = _cgp.random_candidate(rng, config.init_len, fw_pool)
            cand = Candidate(instrs=cand.instrs, origin="cgp", id=next_id)
            cgp_genomes[cand.id] = genome
            pop.append(cand)
        else:
            length = rng.randint(max(4, config.init_len // 2), config.init_len * 2)
            pop.append(
                Candidate(
                    instrs=random_program(rng, length, pack=config.operator_pack),
                    origin="random",
                    id=next_id,
                )
            )
        next_id += 1

    def finish_eval(
        cand: Candidate,
        gen: int,
        operator: str,
        bd: RewardBreakdown,
        tr: list,
        ho: list,
        invalid: bool,
    ) -> EvalRecord:
        rec = EvalRecord(gen, cand, bd, tr, ho, operator, invalid)
        if config.keep_records:
            state.records.append(rec)
        new_cell = False
        if not invalid:
            if config.route_archive and tr:
                cell = route_cell(tr[0])
            elif config.hw_archive and tr:
                cell = hw_cell(tr[0])
            elif config.milestone_archive and tr:
                cell = milestone_cell(tr[0])
            else:
                cell = elites_cell(tr[0]) if tr else (0, 0)
            new_cell = cell not in state.archive.cells
            state.archive.add(cand, cell, bd.total, gen, bd)
            better = bd.total > state.best_reward
            if (not better) and tr and abs(bd.total - state.best_reward) < 1e-9:
                # Equal correctness: keep a non-copy routing style over direct copy.
                old_tr = state.best_record.train if state.best_record else None
                old_copy = bool(old_tr) and not is_non_baseline_route(old_tr[0])
                if old_copy and is_non_baseline_route(tr[0]):
                    better = True
            if better:
                state.best_reward = bd.total
                state.best = cand
                state.best_record = rec
                hall.append(cand)
                if len(hall) > 32:
                    del hall[: len(hall) - 32]
        rec.new_cell = new_cell  # type: ignore[attr-defined]
        rec.isolation_ok = (not invalid) and (all(r.isolation_ok for r in tr) if tr else True)  # type: ignore[attr-defined]
        if tr and not invalid:
            names, vec = case_vector(tr, gated=config.gated)
            rec.cases = vec  # type: ignore[attr-defined]
            case_bank[cand.id] = vec
            isol_ok[cand.id] = bool(rec.isolation_ok)
            if not state.case_names:
                state.case_names = names
            for nm, v in zip(names, vec):
                if v >= 1.0 and nm not in state.first_solved:
                    state.first_solved[nm] = gen
        replay.add(
            Transition(
                parent=cand.instrs,
                operator=operator,
                child=cand.instrs,
                reward=bd.total,
                descriptor=behavioral_descriptor(tr[0]) if tr else tuple(),
                generation=gen,
            )
        )
        return rec

    def eval_one(cand: Candidate, gen: int, operator: str, compute_g: bool) -> EvalRecord:
        bd, tr, ho, invalid = evaluate_candidate(
            sandbox,
            cand,
            train,
            heldout,
            novelty,
            weights,
            compute_g,
            shaping=config.shaping,
            rich_efficiency=config.rich_efficiency,
            gated=config.gated,
        )
        return finish_eval(cand, gen, operator, bd, tr, ho, invalid)

    def ingest_remote(cand: Candidate, gen: int, operator: str, remote: dict) -> EvalRecord:
        if remote.get("invalid"):
            return finish_eval(
                cand,
                gen,
                operator,
                _invalid_breakdown(str(remote.get("reason", ""))),
                remote.get("train") or [],
                remote.get("held") or [],
                True,
            )
        tr = remote.get("train") or []
        ho = remote.get("held") or []
        beh = behavioral_descriptor(tr[0]) if tr else (0.0,)
        hist = opcode_histogram(cand.instrs)
        vec = tuple(beh) + tuple(hist[:8])
        n_score = 0.0
        if weights.n:
            n_score = novelty.score(vec)
            novelty.add(vec)
        bd = score(
            tr,
            heldout_results=ho or None,
            novelty=n_score,
            weights=weights,
            needed_cycles=_needed(train),
            shaping=config.shaping,
            rich_efficiency=config.rich_efficiency,
            gated=config.gated,
        )
        return finish_eval(cand, gen, operator, bd, tr, ho, False)

    def eval_batch(
        cands: list[Candidate], gen: int, ops: list[str], compute_g: bool
    ) -> list[EvalRecord]:
        if pool is None or not cands:
            return [eval_one(c, gen, op, compute_g) for c, op in zip(cands, ops)]
        payloads = [
            {
                "instrs": [(i.op, i.a, i.b, i.c, i.imm) for i in c.instrs],
                "machine": _machine_dict(sandbox.config),
                "train": train,
                "held": heldout,
                "compute_held": compute_g,
            }
            for c in cands
        ]
        remotes = list(
            pool.map(
                remote_evaluate,
                payloads,
                chunksize=max(1, len(payloads) // max(config.eval_workers, 1)),
            )
        )
        return [ingest_remote(c, gen, op, r) for c, op, r in zip(cands, ops, remotes)]

    parent_r: dict[int, float] = {}
    parent_tele: dict[int, list[float]] = {}
    parent_pool: dict[int, tuple[int, ...]] = {}
    case_bank: dict[int, list[float]] = {}
    isol_ok: dict[int, bool] = {}
    pool = _make_eval_pool(int(config.eval_workers))
    try:
        init_recs = eval_batch(pop, 0, ["init"] * len(pop), True)
        for cand, rec0 in zip(pop, init_recs):
            parent_r[cand.id] = rec0.reward.total
            parent_tele[cand.id] = telemetry_vec(
                rec0.train[0] if rec0.train else None, rec0.reward.total
            )
            if rec0.train:
                seen: list[int] = []
                for r in rec0.train:
                    seen.extend(r.observed_consts)
                parent_pool[cand.id] = tuple(dict.fromkeys(list(fw_pool) + seen))

        for gen in range(1, config.generations + 1):
            compute_g = config.eval_heldout_every > 0 and (gen % config.eval_heldout_every == 0)
            gen_rewards: list[float] = []
            n_invalid = 0
            children: list[Candidate] = []
            batch_learn: list = []
            max_len = min(config.max_len, sandbox.config.max_supervisor_len)
            pending: list[tuple] = []
            picks: list[tuple] = []
            use_lex = config.selection in ("lexicase", "epsilon_lexicase")
            eligible_ids = [c.id for c in pop if isol_ok.get(c.id, True)]
            if use_lex and eligible_ids:
                n_pick = config.population
                parent_ids = lexicase_select(
                    [c.id for c in pop],
                    {cid: case_bank[cid] for cid in eligible_ids if cid in case_bank},
                    rng,
                    n=n_pick,
                    eligible_ids=eligible_ids,
                    epsilon=config.epsilon,
                    auto_epsilon=(config.selection == "epsilon_lexicase" and config.epsilon is None),
                )
                id_map = {c.id: c for c in pop}
                lex_parents = [id_map[i] for i in parent_ids if i in id_map]
            else:
                lex_parents = []
            for i in range(config.population):
                if use_lex and lex_parents:
                    parent = lex_parents[i % len(lex_parents)]
                    if rng.random() < config.archive_mix:
                        parent = (
                            state.archive.sample_curious(rng)
                            if config.curiosity
                            else state.archive.sample(rng)
                        ) or parent
                elif rng.random() < config.archive_mix:
                    if config.curiosity:
                        parent = state.archive.sample_curious(rng) or rng.choice(pop)
                    else:
                        parent = state.archive.sample(rng) or rng.choice(pop)
                else:
                    parent = rng.choice(pop)
                if config.elite_crossover and state.archive.size() >= 2:
                    other = state.archive.sample_other(rng, parent) or rng.choice(pop)
                else:
                    other = rng.choice(pop)
                picks.append((parent, other))
            if learned is not None and hasattr(learned, "act_batch"):
                teles = [
                    parent_tele.get(p.id, telemetry_vec(None, parent_r.get(p.id, 0.0)))
                    for p, _o in picks
                ]
                results = learned.act_batch(
                    [p for p, _o in picks],
                    teles,
                    rng,
                    max_len=max_len,
                    others=[o for _p, o in picks],
                    hall=hall or None,
                )
                for (parent, _other), (child, sampled) in zip(picks, results):
                    child = Candidate(
                        instrs=child.instrs,
                        origin=child.origin,
                        parent_id=parent.id,
                        id=next_id,
                    )
                    next_id += 1
                    pending.append((parent, child, sampled, child.origin))
            else:
                for parent, other in picks:
                    operator = policy.select_operator(rng)
                    if config.elite_crossover and rng.random() < 0.15:
                        operator = "crossover"
                    if config.representation == "cgp":
                        from evoos.agent import cgp as _cgp

                        src = cgp_genomes.get(parent.id) or _cgp.genome_from_instrs(parent.instrs)
                        genome = _cgp.mutate_genome(src, rng, const_pool=fw_pool)
                        child_p = _cgp.decode(genome)
                        child = Candidate(
                            instrs=child_p.instrs,
                            origin="cgp",
                            parent_id=parent.id,
                            id=next_id,
                        )
                        cgp_genomes[child.id] = genome
                        operator = "cgp_mutate"
                    else:
                        eff = None
                        consts = parent_pool.get(parent.id, fw_pool)
                        if config.dataflow:
                            from evoos.agent.dataflow import effective_indices

                            eff = effective_indices(parent.instrs)
                        child = mutate(
                            parent,
                            operator,
                            rng,
                            hall=hall or None,
                            other=other,
                            max_len=max_len,
                            operators=operators,
                            effective=eff,
                            const_pool=consts,
                            dataflow=config.dataflow,
                        )
                        child = Candidate(
                            instrs=child.instrs,
                            origin=child.origin,
                            parent_id=parent.id,
                            id=next_id,
                        )
                    next_id += 1
                    pending.append((parent, child, None, operator))
            recs = eval_batch(
                [ch for _p, ch, _s, _o in pending],
                gen,
                [op for _p, _c, _s, op in pending],
                compute_g,
            )
            for (parent, child, sampled, operator), rec in zip(pending, recs):
                delta = rec.reward.total - parent_r.get(parent.id, 0.0)
                if getattr(rec, "new_cell", False) and learned is not None:
                    delta += float(getattr(learned, "new_cell_bonus", 0.08))
                policy.update(operator, delta if learned is not None else rec.reward.total)
                if sampled is not None:
                    batch_learn.append((sampled, delta))
                parent_r[child.id] = rec.reward.total
                parent_tele[child.id] = telemetry_vec(
                    rec.train[0] if rec.train else None, rec.reward.total
                )
                if rec.train:
                    seen: list[int] = []
                    for r in rec.train:
                        seen.extend(r.observed_consts)
                    parent_pool[child.id] = tuple(dict.fromkeys(list(fw_pool) + seen))
                gen_rewards.append(rec.reward.total)
                if rec.invalid:
                    n_invalid += 1
                elif config.selection in ("lexicase", "epsilon_lexicase") and not isol_ok.get(
                    child.id, True
                ):
                    pass
                else:
                    children.append(child)
            if learned is not None and hasattr(learned, "learn_batch") and batch_learn:
                row_loss = learned.learn_batch(batch_learn)
            else:
                row_loss = 0.0

            elites = [
                e.candidate
                for e in sorted(state.archive.cells.values(), key=lambda e: -e.reward)
            ]
            keep = max(
                1,
                int(config.elite_fraction * config.population),
                len(elites) if config.milestone_archive else 0,
            )
            keep = min(keep, max(config.population, len(elites)))
            pop = (elites[:keep] + children + pop)[: max(config.population, keep)]
            if not pop:
                pop = [
                    Candidate(
                        instrs=random_program(rng, config.init_len, pack=config.operator_pack),
                        origin="random",
                        id=next_id,
                    )
                ]
                next_id += 1

            valid_rewards = [r for r in gen_rewards if r > INVALID_REWARD + 1e-9]
            mean_r = sum(gen_rewards) / max(len(gen_rewards), 1)
            row = {
                "generation": gen,
                "best_reward": state.best_reward,
                "mean_reward": mean_r,
                "mean_valid": (
                    sum(valid_rewards) / max(len(valid_rewards), 1)
                    if valid_rewards
                    else INVALID_REWARD
                ),
                "n_invalid": n_invalid,
                "n_eval": len(gen_rewards),
                "archive_size": state.archive.size(),
                "best_f": state.best_record.reward.f if state.best_record else 0.0,
                "best_completed": (
                    state.best_record.reward.completed if state.best_record else 0
                ),
                "policy": policy.snapshot(),
                "policy_loss": row_loss,
                "cases_solved": (
                    summarize_cases(
                        state.case_names, getattr(state.best_record, "cases", [])
                    )
                    if state.best_record is not None and state.case_names
                    else {}
                ),
                "first_solved": dict(state.first_solved),
            }
            state.history.append(row)
            if on_generation is not None and (
                config.log_every <= 1 or gen % config.log_every == 0
            ):
                on_generation(gen, state, row)
            if config.early_stop_complete:
                rec = state.best_record
                full = (
                    rec is not None
                    and rec.train
                    and all(r.completed == r.n_guests and r.isolation_ok for r in rec.train)
                )
                n_g = len(train[0].guests) if train else 0
                stone = archive_has_protected_complete(state, n_g)
                if full or stone:
                    row["promoted"] = True
                    row["promoted_stone"] = bool(stone and not full)
                    break
    finally:
        if pool is not None:
            pool.shutdown(wait=True)

    state.population = pop
    state.replay = replay  # type: ignore[attr-defined]
    state.policy = policy  # type: ignore[attr-defined]
    return state
