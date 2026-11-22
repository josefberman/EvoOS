"""Curriculum over guest count. Bare ISA only — no gadget operators."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from evoos.agent.evolutionary_search import (
    SearchConfig,
    SearchState,
    archive_has_protected_complete,
    run_search,
)
from evoos.agent.mutations import BARE_OPS
from evoos.agent.program import Candidate
from evoos.evaluator.workloads import curriculum_stages, heldout_workloads, training_workloads
from evoos.vm.machine import MachineConfig
from evoos.vm.sandbox import Sandbox


@dataclass
class StageLog:
    name: str
    n_guests: int
    gens_ran: int
    best_reward: float
    completed: list[int]
    isolation_ok: bool
    promoted: bool
    stalled: bool
    entered: list[int] = field(default_factory=list)
    prot_cover: list[list[bool]] = field(default_factory=list)
    shape_f: float = 0.0
    entered_protected: list[int] = field(default_factory=list)
    completed_protected: list[int] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)
    n_eval: int = 0
    first_solved: dict[str, int] = field(default_factory=dict)
    cases_solved: dict[str, int] = field(default_factory=dict)


@dataclass
class CurriculumResult:
    state: SearchState
    stages: list[StageLog] = field(default_factory=list)
    stall_stage: str | None = None


def make_sandbox(n_guests: int, base: MachineConfig) -> Sandbox:
    cfg = MachineConfig(
        mem_words=base.mem_words,
        n_contexts=n_guests + 1,
        max_steps=base.max_steps,
        max_supervisor_len=base.max_supervisor_len,
        sticky_guest_fault=base.sticky_guest_fault,
        supervisor_halt_on_mem=base.supervisor_halt_on_mem,
        runctx_posts_status=base.runctx_posts_status,
    )
    return Sandbox(cfg)


def run_curriculum(
    base_config: MachineConfig,
    search: SearchConfig,
    *,
    gens_per_stage: int,
    stages: tuple[dict, ...] | None = None,
    extra_seeds: list[Candidate] | None = None,
    on_generation: Callable[[int, SearchState, dict], None] | None = None,
) -> CurriculumResult:
    stages = stages or curriculum_stages()
    pop: list[Candidate] = list(extra_seeds or [])
    logs: list[StageLog] = []
    last_state: SearchState | None = None
    stall: str | None = None
    if search.learned is None and search.policy in ("reinforce", "gru", "ppo", "learned"):
        from evoos.agent.learned_policy import LearnedEditPolicy

        search.learned = LearnedEditPolicy(operators=BARE_OPS)
    for i, stage in enumerate(stages):
        sandbox = make_sandbox(int(stage["n_guests"]), base_config)
        cfg = SearchConfig(
            generations=gens_per_stage,
            population=search.population,
            seed=search.seed + i * 1009,
            init_len=search.init_len,
            eval_heldout_every=search.eval_heldout_every,
            mode=search.mode,
            policy=search.policy,
            max_len=search.max_len,
            operator_pack="bare",
            keep_records=False,
            shaping=search.shaping,
            rich_efficiency=search.rich_efficiency,
            early_stop_complete=True,
            log_every=search.log_every,
            gated=search.gated,
            curiosity=search.curiosity,
            milestone_archive=search.milestone_archive,
            elite_crossover=search.elite_crossover,
            archive_mix=search.archive_mix,
            elite_fraction=search.elite_fraction,
            learned=search.learned,
            eval_workers=search.eval_workers,
            selection=search.selection,
            dataflow=search.dataflow,
            representation=search.representation,
            epsilon=search.epsilon,
        )
        state = run_search(
            sandbox,
            list(stage["train"]),
            list(stage["heldout"]),
            cfg,
            extra_seeds=pop or None,
            on_generation=on_generation,
        )
        last_state = state
        pop = list(getattr(state, "population", []) or [])
        stones = list(state.archive.elites())
        if state.best is not None:
            pop = [state.best] + stones + [c for c in pop if c is not state.best]
        else:
            pop = stones + pop
        # Dedup by id, keep order.
        seen: set[int] = set()
        uniq: list[Candidate] = []
        for c in pop:
            if c.id in seen:
                continue
            seen.add(c.id)
            uniq.append(c)
        pop = uniq
        train_done = False
        completed: list[int] = []
        entered: list[int] = []
        covers: list[list[bool]] = []
        entered_p: list[int] = []
        completed_p: list[int] = []
        isol = True
        shape_f = 0.0
        if state.best_record and state.best_record.train:
            completed = [r.completed for r in state.best_record.train]
            entered = [r.entered for r in state.best_record.train]
            covers = [list(r.prot_cover) for r in state.best_record.train]
            entered_p = [r.entered_protected for r in state.best_record.train]
            completed_p = [r.completed_protected for r in state.best_record.train]
            isol = all(r.isolation_ok for r in state.best_record.train)
            train_done = isol and all(
                r.completed == r.n_guests for r in state.best_record.train
            )
            if state.best_record.reward.details:
                shape_f = float(state.best_record.reward.details.get("shape", {}).get("blended", 0.0) or 0.0)
        n_g = int(stage["n_guests"])
        stone = archive_has_protected_complete(state, n_g)
        # k1 (and any 1-guest stage): promote on a protected-complete stepping stone.
        if (not train_done) and stone and n_g <= 1:
            train_done = True
        gens_ran = len(state.history)
        promoted = train_done
        stalled = (not train_done) and (i < len(stages) - 1)
        last_row = state.history[-1] if state.history else {}
        logs.append(
            StageLog(
                name=str(stage["name"]),
                n_guests=int(stage["n_guests"]),
                gens_ran=gens_ran,
                best_reward=state.best_reward,
                completed=completed,
                isolation_ok=isol,
                promoted=promoted,
                stalled=stalled,
                entered=entered,
                prot_cover=covers,
                shape_f=shape_f,
                entered_protected=entered_p,
                completed_protected=completed_p,
                history=[
                    {k: v for k, v in row.items() if k != "policy"}
                    for row in state.history
                ],
                n_eval=sum(int(row.get("n_eval", 0)) for row in state.history),
                first_solved=dict(state.first_solved),
                cases_solved=dict(last_row.get("cases_solved") or {}),
            )
        )
        if stalled and stall is None:
            stall = str(stage["name"])
        # Always continue with the current population (honest: may stall).
    if last_state is None:
        last_state = SearchState()
    return CurriculumResult(state=last_state, stages=logs, stall_stage=stall)


def final_four_guest_eval(base: MachineConfig, cand: Candidate | None) -> tuple[Sandbox, list, list]:
    cfg = MachineConfig(
        mem_words=base.mem_words,
        n_contexts=5,
        max_steps=base.max_steps,
        sticky_guest_fault=base.sticky_guest_fault,
        supervisor_halt_on_mem=base.supervisor_halt_on_mem,
        runctx_posts_status=base.runctx_posts_status,
    )
    return Sandbox(cfg), list(training_workloads()), list(heldout_workloads())
