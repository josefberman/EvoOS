# EvoOS

**Research question:** can an RL / evolutionary agent discover a *functional operating system* from behavioral objectives alone, without being taught conventional OS architecture?

The artifact being evolved is a **supervisor program** in a small, typed bytecode. The agent must learn an *implementation* (how to multiplex a machine among competing guest programs, isolate their memory, and keep them making progress). It is not being trained to operate Linux, xv6, or any other existing kernel.

## The no-architectural-priors rule

The instruction set is a virtual *computer*, not a virtual *operating system*.

Allowed hardware:

- load / store, arithmetic, compare / branch
- `RUNCTX` / `RUNCTXI` — run a hardware context for N steps and return
- `CTXGET` / `CTXSET` — read or poke context metadata (PC, halt, fault, data window, …)
- `ADDPROT` / `CLRPROT` — range-based memory-protection registers (an MMU-like mechanism)
- a cycle timer (`RDTIME`)

**Forbidden in the ISA**, on purpose: `schedule_process`, `yield_to_next`, `allocate_page`, run queues, priority bands, wait queues, or any other traditional OS component as a primitive. If round-robin, priorities, or something stranger appear, they must appear as *patterns in bytecode*.

Condition A (this repo: hand-written baselines) uses that ISA the way a human OS designer would. Condition B (hooked, not trained here) would tune knobs on a human skeleton. **Condition C** is the target: search from random bytecode.

## Milestone 1 (this tree)

A scheduling experiment: four guest programs share finite data memory and a global instruction budget. Reward

\[
R = 0.45F + 0.20S + 0.15E + 0.10G + 0.10N
\]

(functional correctness, stability/isolation, efficiency, generalization on held-out workloads, novelty of implementation). Isolation / memory-corruption failures are a **hard constraint**: throughput and novelty cannot compensate. Invalid bytecode scores `-1`.

Two scoring modes:

- `--mode correctness` — F and S only (comparison run)
- `--mode qd` — full weights + MAP-Elites archive

## Layout

```
evoos/vm/           instruction_set, machine, sandbox, verifier
evoos/evaluator/    workloads, correctness, reward, novelty
evoos/agent/        mutations, policy (UCB1 / PPO hook), MAP-Elites, search
evoos/experiments/scheduling/  baselines, conditions, analysis
evoos/placeholders/ stages 2–5 stubs
train.py            end-to-end experiment CLI
tests/
```

## Setup

Python 3.11+. PyTorch is optional (only for the PPO mutation-policy hook).

```bash
pip install -e ".[dev]"
pytest
```

## Run the scheduling experiment

```bash
python train.py --generations 25 --population 16 --seed 42 --mode correctness --out runs/correctness
python train.py --generations 25 --population 16 --seed 42 --mode qd --out runs/qd
python -m evoos.experiments.scheduling.analyze --db runs/correctness/evoos.sqlite
```

`train.py` always scores the hand-written baselines first (run-to-completion, round-robin, selfish), then runs Condition C search. Logs are SQLite (`runs/*/evoos.sqlite`); TensorBoard scalars are written if `torch.utils.tensorboard` is installed.

## Roadmap

| Stage | Question | Status |
| --- | --- | --- |
| 1 | Discover multiplexing + isolation for four competing guests | this milestone |
| 2 | Memory management from behavioral pressure (no `allocate_page` opcode) | `evoos/placeholders/memory.py` |
| 3 | Persistence / a block device, not a VFS opcode | `evoos/placeholders/persistence.py` |
| 4 | Linux syscall slice via differential tests against Linux | `evoos/placeholders/linux_syscalls.py` |
| 5 | QEMU/initramfs boot; AFL++ on the VM and on supervisors | `evoos/placeholders/qemu_boot.py`, `fuzzing.py` |

## Search operators vs OS priors

Mutations are bytecode edits (insert/remove/modify, block duplicate, crossover, jump tweaks). Two extra operators insert **ISA-manual gadgets**, not schedulers:

- MMU gadget: `MOVI ctx` → `CTXGET DATA_BASE/BOUND` → `ADDPROT`
- dispatch gadget: `MOVI ctx` → `RUNCTXI steps`

Those are the calling convention of the protection registers and the run-context primitive. They do **not** encode round-robin, priorities, or a run queue. Invalid context ids are nops (a trap that halted the supervisor made search degenerate; see below).

## Milestone 1 results (measured, not invented)

Machine: this Cloud Agent VM, CPython 3.12, no GPU. Command:

```bash
python train.py --generations 40 --population 24 --seed 42 --mode correctness --out runs/correctness
python train.py --generations 25 --population 16 --seed 7 --mode qd --out runs/qd
```

**Condition A (hand-written, same bytecode), held-out workloads**

| supervisor | guests complete | isolation | correctness R | behavioral label |
| --- | --- | --- | --- | --- |
| run-to-completion | 4/4 and 4/4 | pass | 1.000 | run-to-completion-like |
| round-robin q=16 | 4/4 and 4/4 | pass | 1.000 | round-robin-like |
| selfish (ctx 1 only) | 1/4 and 1/4 | pass | 0.400 | starving |

**Condition C, correctness mode** (40 gens × 24, seed 42, 5.7 s, ~10k evals/min):

- Best training reward **0.840**. Isolation held on train and held-out (no hard-constraint failures in the elite).
- Evolved supervisor completed **3/4 + 3/4** guests on the two training mixes, **3/4 + 2/4** on held-out. It did **not** get all four programs to completion.
- Disassembly is a messy 50-instruction bag of `MOVI`/`CTXGET`/`ADDPROT`/`RUNCTXI` with a couple of backward branches. Analysis labeled it `other` — not round-robin (only four context switches) and not a clean run-to-completion of every guest. Context 3 typically ran two instructions and faulted; the other three often finished.
- Without the MMU/RUNCTX gadgets and with invalid-id traps killing the supervisor, a 40×24 run stayed at **R = 0** (never dispatched). That substrate was too hostile to count as a test of “can organization be discovered.”

**Condition C, quality-diversity mode** (25 gens × 16, seed 7, 6.0 s): best composite **R = 0.377**. Elite completed 1/4 guests on train and held-out, isolation held. MAP-Elites filled 4 cells. Novelty pressure did not beat the correctness-only run on F in this short budget.

**Assessment:** milestone 1 has a cheap deterministic VM, a real isolation oracle, working human baselines, and a search loop that *does* climb from 0.28 → 0.84 and produces a supervisor that multiplexes *some* guests without corrupting memory. It has **not** discovered a general scheduler that matches Condition A on held-out workloads. Next: longer runs, a loop-over-`N_CONTEXTS` mutation that still is not a scheduler, Condition B (RL on the round-robin quantum), and harsher held-out mixes.
