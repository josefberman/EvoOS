"""Routing curriculum for Condition C-hw.

Promote on partial success (at least `promote_delivered` messages with isolation).
Seed the next stage from archive elites. Pack is `hw` (hardware manual).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from evoos.agent.curriculum import StageLog
from evoos.agent.evolutionary_search import SearchConfig, SearchState, run_search
from evoos.agent.program import Candidate
from evoos.evaluator.hw_workloads import routing_stages
from evoos.vm.machine import MachineConfig, lenient_config
from evoos.vm.sandbox import Sandbox


@dataclass
class RoutingCurriculumResult:
    state: SearchState
    stages: list[StageLog] = field(default_factory=list)
    stall_stage: str | None = None


def routing_sandbox(n_guests: int, max_steps: int = 4000) -> Sandbox:
    return Sandbox(
        lenient_config(
            n_contexts=n_guests + 1,
            n_blocks=16,
            block_words=32,
            blk_latency=2,
            max_steps=max_steps,
            max_supervisor_len=192,
            wall_steps=max(max_steps + 4000, 12000),
        )
    )


def run_routing_curriculum(
    search: SearchConfig,
    *,
    gens_per_stage: int,
    extra_seeds: list[Candidate] | None = None,
    on_generation: Callable[[int, SearchState, dict], None] | None = None,
    max_steps: int = 4000,
    stages: tuple[dict, ...] | None = None,
) -> RoutingCurriculumResult:
    stages = stages or routing_stages()
    pop: list[Candidate] = list(extra_seeds or [])
    logs: list[StageLog] = []
    last_state: SearchState | None = None
    stall: str | None = None
    for i, stage in enumerate(stages):
        n_g = int(stage["n_guests"])
        sandbox = routing_sandbox(n_g, max_steps=max_steps)
        cfg = SearchConfig(
            generations=gens_per_stage,
            population=search.population,
            seed=search.seed + i * 1009,
            init_len=search.init_len,
            eval_heldout_every=0,
            mode="correctness",
            policy=search.policy,
            max_len=search.max_len,
            operator_pack="hw",
            keep_records=False,
            shaping=search.shaping,
            early_stop_complete=True,
            log_every=search.log_every,
            gated=search.gated,
            curiosity=True,
            elite_crossover=True,
            archive_mix=0.7,
            elite_fraction=0.25,
            eval_workers=search.eval_workers,
            selection=search.selection,
            dataflow=True,
            hw_archive=True,
            route_archive=True,
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
        stones = list(state.archive.elites())
        if state.best is not None:
            pop = [state.best] + stones + list(state.population or [])
        else:
            pop = stones + list(state.population or [])
        seen: set[int] = set()
        uniq: list[Candidate] = []
        for c in pop:
            if c.id in seen:
                continue
            seen.add(c.id)
            uniq.append(c)
        pop = uniq[: max(search.population * 2, 16)]

        isol = True
        completed: list[int] = []
        delivered = 0
        n_msg_ok = 0
        style = "none"
        if state.best_record and state.best_record.train:
            tr = state.best_record.train
            completed = [r.completed for r in tr]
            isol = all(r.isolation_ok for r in tr)
            delivered = max(int(r.delivered or 0) for r in tr)
            n_msg_ok = max(sum(1 for x in (r.msg_ok or []) if x) for r in tr)
            style = str(tr[0].route_style or "none")
        need = int(stage.get("promote_delivered") or 1)
        promoted = isol and (delivered >= need or n_msg_ok >= need)
        last_stage = i == len(stages) - 1
        stalled = (not promoted) and (not last_stage)
        logs.append(
            StageLog(
                name=str(stage["name"]),
                n_guests=n_g,
                gens_ran=len(state.history),
                best_reward=state.best_reward,
                completed=completed,
                isolation_ok=isol,
                promoted=promoted,
                stalled=stalled,
                n_eval=sum(int(row.get("n_eval", 0)) for row in state.history),
                first_solved=dict(state.first_solved),
            )
        )
        logs[-1].shape_f = float(delivered)
        logs[-1].entered = [n_msg_ok]
        logs[-1].prot_cover = [[style]]  # type: ignore[list-item]
        if stalled and stall is None:
            stall = str(stage["name"])
        # Always continue (honest: later stages still run on stones).
    if last_state is None:
        last_state = SearchState()
    return RoutingCurriculumResult(state=last_state, stages=logs, stall_stage=stall)
