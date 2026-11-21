"""Curriculum over guest count. Bare ISA only — no gadget operators."""

from __future__ import annotations

from dataclasses import dataclass, field

from evoos.agent.evolutionary_search import SearchConfig, SearchState, run_search
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
) -> CurriculumResult:
    stages = stages or curriculum_stages()
    pop: list[Candidate] = []
    logs: list[StageLog] = []
    last_state: SearchState | None = None
    stall: str | None = None
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
            log_every=10**9,
        )
        state = run_search(
            sandbox,
            list(stage["train"]),
            list(stage["heldout"]),
            cfg,
            extra_seeds=pop or None,
        )
        last_state = state
        pop = list(getattr(state, "population", []) or [])
        if state.best is not None:
            pop = [state.best] + [c for c in pop if c is not state.best]
        train_done = False
        completed: list[int] = []
        entered: list[int] = []
        covers: list[list[bool]] = []
        isol = True
        shape_f = 0.0
        if state.best_record and state.best_record.train:
            completed = [r.completed for r in state.best_record.train]
            entered = [r.entered for r in state.best_record.train]
            covers = [list(r.prot_cover) for r in state.best_record.train]
            isol = all(r.isolation_ok for r in state.best_record.train)
            train_done = isol and all(
                r.completed == r.n_guests for r in state.best_record.train
            )
            if state.best_record.reward.details:
                shape_f = float(state.best_record.reward.details.get("shape", {}).get("blended", 0.0) or 0.0)
        gens_ran = len(state.history)
        promoted = train_done
        stalled = (not train_done) and (i < len(stages) - 1)
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
