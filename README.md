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

Three experimental conditions, same guests and reward:

| | What is given | What is searched |
| --- | --- | --- |
| **A** | A human-written supervisor in this ISA (RTC, round-robin, selfish) | nothing |
| **B** | The human round-robin *structure*; UCB1 tunes only the `RUNCTXI` quantum | one immediate |
| **C** | Random bytecode + mutation operators (see priors below) | the whole program |

## Priors that are *not* in the ISA but *are* in the search

`RUNCTX` and `ADDPROT` are hardware. The **gadgets** are mutation operators that insert the *calling convention* for those CSRs, not new opcodes:

| Operator pack | Operators beyond insert/delete/tweak | Encodes | Does not encode |
| --- | --- | --- | --- |
| `bare` | none | raw ISA | how to call MMU/RUNCTX |
| `gadgets` | `insert_mmu_gadget`, `insert_runctx_gadget` | `MOVI ctx; CTXGET DATA_*; ADDPROT` and `MOVI ctx; RUNCTXI n` for **one** random context id | round-robin, quanta, skip-halted, until-idle, run queues |
| `full` | gadgets + `insert_ctx_scan` | “the machine reports `N_CONTEXTS`; you can walk ids 1..N-1” with a one-instruction body | skip-if-halted, time-slice policy, outer until-idle loop |

Invalid context ids are **nops** (a trap that halted the supervisor made search degenerate). That is a machine-semantics choice, not an OS.

**Implication for the minimal-priors claim:** Condition C with `gadgets` or `full` is “discover organization given the ISA manual’s examples,” not “discover a kernel from a bag of bits.” The `bare` pack is the closer test of the latter. At the budget below, **bare never reaches 4/4**; gadgets often do. Treat C+gadgets as a positive result about *organization*, and treat C+bare as a negative result about *undirected bytecode search*.

## Milestone 1

Four guest programs share finite data memory and a global instruction budget. Reward

```
R = 0.45F + 0.20S + 0.15E + 0.10G + 0.10N
```

(functional correctness, stability/isolation, efficiency, generalization, novelty). Isolation / memory-corruption failures are a **hard constraint**. Invalid bytecode scores `-1`.

- `--mode correctness` — F and S only
- `--mode qd` — full weights + MAP-Elites

## Setup and commands

Python 3.11+. PyTorch optional.

```bash
pip install -e ".[dev]"
pytest
python train.py --generations 40 --population 24 --seed 42 --mode correctness --operator-pack gadgets
python -m evoos.experiments.scheduling.analyze --db runs/correctness/evoos.sqlite
# Multi-seed A/B/C + ablation (minutes–tens of minutes, uses all CPU cores):
python -m evoos.experiments.scheduling.sweep --out experiments/scheduling/results
```

## Scaled results (measured)

Machine: Cloud Agent VM, CPython 3.12, 4 cores, no GPU. **24.1 min** wall.

```text
python -m evoos.experiments.scheduling.sweep \
  --generations 400 --population 40 --workers 4
```

6 seeds `{1, 7, 13, 42, 99, 2024}` × 2 modes × 3 operator packs = 36 Condition C jobs. Tables: [`experiments/scheduling/results/summary.md`](experiments/scheduling/results/summary.md).

**Condition A (held-out):** RTC and round-robin q=16 complete 4/4, isolation pass, correctness R = 1.000. Selfish: 1/4, R = 0.400.

**Condition B:** UCB1 over 19 quanta. Every quantum that finishes all guests scores correctness R = 1.0, so the bandit keeps **q=1** (first perfect arm). 6/6 seeds 4/4, label `round-robin-like`. Efficiency is *not* in this objective; switch counts still fall as q grows (`q=1` hundreds of switches, `q≥128` four switches ≈ RTC).

**Condition C, 4/4 on train AND held-out with isolation:**

| mode | pack | search R mean ± std (best) | seeds 4/4 | held-out labels of elites |
| --- | --- | --- | --- | --- |
| correctness | bare | 0.347 ± 0.038 (0.400) | **0/6** | (no 4/4) |
| correctness | gadgets | 1.000 ± 0.000 (1.000) | **6/6** | mostly `round-robin-like`, one `other` |
| correctness | full | 1.000 ± 0.000 (1.000) | **5/6** | RR-like / batched / other; one seed held `[4, 3]` |
| qd | bare | 0.305 ± 0.023 (0.332) | **0/6** | (no 4/4) |
| qd | gadgets | 0.857 ± 0.039 (0.879) | **5/6** | other / RR-like; one seed stuck at 3/4 |
| qd | full | 0.878 ± 0.007 (0.892) | **5/6** | other / RTC-like; one seed held `[4, 3]` |

**21/36** C runs hit 4/4. **0/12** bare. The N_CONTEXTS scan did not beat gadgets. QD search rewards are lower than correctness because E/G/N share the weight; several QD elites still 4/4 on held-out.

**A vs B vs C (best held-out correctness R):** all three can reach 1.000 and 4/4. A and B do it with a human RR/RTC; C does it only when MMU/RUNCTX gadgets are enabled, and the resulting programs are messy bytecode whose *behavior* is often RR-like or RTC-like rather than a copy of the baseline source.

## Layout

```
evoos/vm/           instruction_set, machine, sandbox, verifier
evoos/evaluator/    workloads, correctness, reward, novelty
evoos/agent/        mutations (packs), policy, MAP-Elites, search
evoos/experiments/scheduling/  baselines, Condition B, sweep, results/
evoos/placeholders/ stages 2–5 stubs
train.py
```

## Roadmap

| Stage | Question | Status |
| --- | --- | --- |
| 1 | Discover multiplexing + isolation for four competing guests | this milestone |
| 2 | Memory management from behavioral pressure (no `allocate_page` opcode) | `evoos/placeholders/memory.py` |
| 3 | Persistence / a block device, not a VFS opcode | `evoos/placeholders/persistence.py` |
| 4 | Linux syscall slice via differential tests against Linux | `evoos/placeholders/linux_syscalls.py` |
| 5 | QEMU/initramfs boot; AFL++ on the VM and on supervisors | `evoos/placeholders/qemu_boot.py`, `fuzzing.py` |

Next: score Condition B with efficiency so quantum is identifiable; harsher held-out mixes; drop gadgets and spend more compute on `bare`; stages 2–5.
