"""Evolutionary search over supervisor bytecode + MAP-Elites + policy update."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Callable

from evoos.agent.map_elites import MapElites
from evoos.agent.mutations import OPERATOR_PACKS, GADGET_OPS, mutate, random_program
from evoos.agent.policy import MutationPolicy, make_policy
from evoos.agent.program import Candidate
from evoos.agent.replay_buffer import ReplayBuffer, Transition
from evoos.evaluator.novelty import NoveltyArchive, behavioral_descriptor, elites_cell, opcode_histogram
from evoos.evaluator.reward import RewardBreakdown, RewardWeights, score
from evoos.vm.sandbox import EvalResult, Sandbox, Workload
from evoos.vm.verifier import VerifyError, verify_supervisor


INVALID_REWARD = -1.0


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
    policy: MutationPolicy = make_policy(config.policy, operators)
    novelty = NoveltyArchive()
    replay = ReplayBuffer()
    state = SearchState()
    hall: list[Candidate] = []
    next_id = 1

    pop: list[Candidate] = []
    for seed in extra_seeds or []:
        pop.append(Candidate(instrs=seed.instrs, origin="seed", id=next_id))
        next_id += 1
    while len(pop) < config.population:
        length = rng.randint(max(4, config.init_len // 2), config.init_len * 2)
        pop.append(
            Candidate(
                instrs=random_program(rng, length, pack=config.operator_pack),
                origin="random",
                id=next_id,
            )
        )
        next_id += 1

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
        )
        rec = EvalRecord(gen, cand, bd, tr, ho, operator, invalid)
        if config.keep_records:
            state.records.append(rec)
        if not invalid:
            cell = elites_cell(tr[0]) if tr else (0, 0)
            state.archive.add(cand, cell, bd.total, gen, bd)
            if bd.total > state.best_reward:
                state.best_reward = bd.total
                state.best = cand
                state.best_record = rec
                hall.append(cand)
                if len(hall) > 32:
                    del hall[: len(hall) - 32]
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

    # Evaluate initial population.
    for cand in pop:
        eval_one(cand, 0, "init", compute_g=True)

    for gen in range(1, config.generations + 1):
        compute_g = config.eval_heldout_every > 0 and (gen % config.eval_heldout_every == 0)
        gen_rewards: list[float] = []
        n_invalid = 0
        children: list[Candidate] = []
        for _ in range(config.population):
            if rng.random() < config.archive_mix:
                parent = state.archive.sample(rng) or rng.choice(pop)
            else:
                parent = rng.choice(pop)
            other = rng.choice(pop)
            operator = policy.select_operator(rng)
            child = mutate(
                parent,
                operator,
                rng,
                hall=hall or None,
                other=other,
                max_len=min(config.max_len, sandbox.config.max_supervisor_len),
                operators=operators,
            )
            child = Candidate(
                instrs=child.instrs,
                origin=child.origin,
                parent_id=parent.id,
                id=next_id,
            )
            next_id += 1
            rec = eval_one(child, gen, operator, compute_g)
            policy.update(operator, rec.reward.total)
            gen_rewards.append(rec.reward.total)
            if rec.invalid:
                n_invalid += 1
            else:
                children.append(child)

        # Next pop: mix of children and archive elites.
        elites = [e.candidate for e in sorted(state.archive.cells.values(), key=lambda e: -e.reward)]
        keep = max(1, int(config.elite_fraction * config.population))
        pop = (elites[:keep] + children + pop)[: config.population]
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
            "mean_valid": sum(valid_rewards) / max(len(valid_rewards), 1) if valid_rewards else INVALID_REWARD,
            "n_invalid": n_invalid,
            "n_eval": len(gen_rewards),
            "archive_size": state.archive.size(),
            "best_f": state.best_record.reward.f if state.best_record else 0.0,
            "best_completed": state.best_record.reward.completed if state.best_record else 0,
            "policy": policy.snapshot(),
        }
        state.history.append(row)
        if on_generation is not None and gen % config.log_every == 0:
            on_generation(gen, state, row)
        if config.early_stop_complete and state.best_record is not None:
            rec = state.best_record
            if rec.train and all(r.completed == r.n_guests and r.isolation_ok for r in rec.train):
                row["promoted"] = True
                break

    state.population = pop
    state.replay = replay  # type: ignore[attr-defined]
    state.policy = policy  # type: ignore[attr-defined]
    return state
