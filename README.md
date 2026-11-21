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
# Round 2: gadgets vs bare (minutes–tens of minutes):
python -m evoos.experiments.scheduling.sweep --out experiments/scheduling/results
# Round 3: bare-only curriculum + shaping + substrate ablations:
python -m evoos.experiments.scheduling.sweep_round3 --out experiments/scheduling/results/round3
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

## Round 3 (bare ISA: curriculum, shaping, substrate)

Same machine, **23.6 min**, 6 seeds, **bare pack only**. Tables: [`experiments/scheduling/results/round3/summary.md`](experiments/scheduling/results/round3/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round3 \
  --gens-per-stage 100 --population 36 --workers 4
```

Curriculum stages: 1 → 2 → 3 → 4 guests, then a harder 4-guest mix. Promote when the stage's train guests all complete with isolation. Shaping replaces sparse F with behavioral observables (`entered`, `prot_cover`, `progress`, `complete`) — not opcode bonuses. Lenient substrate: RUNCTX retries a faulting guest instruction, supervisor mem errors post a CSR instead of halting, RUNCTX writes LAST_RUN_* CSRs.

**Condition B with efficiency.** R = 0.45F + 0.20S + 0.35E. Grid E rises with quantum (`q=1` E=0.444 / 228+322 switches → `q=128` E=0.781 / 4+4 switches). UCB1 6/6 seeds pick **q=192** (tied with the large-q plateau; 4/4, held R≈0.93). The identifiable quantum is “almost RTC.”

**Condition C, 4/4 on full 4-guest train AND held-out:**

| curriculum | shaping | substrate | search R mean ± std (best) | 4/4 |
| --- | --- | --- | --- | --- |
| flat | yes | lenient | 0.607 ± 0.285 (1.000) | **1/6** (seed 7, RR-like). Seed 2024: train 4/4, held `[4, 3]` |
| curr | yes | lenient | 0.533 ± 0.142 (0.760) | **0/6** |
| curr | no | lenient | 0.307 ± 0.050 (0.360) | **0/6** |
| curr | yes | hostile | 0.427 ± 0.126 (0.600) | **0/6** |
| curr | yes | lenient, no resume | 0.487 ± 0.139 (0.720) | **0/6** |
| curr | yes | lenient, no memerr | 0.440 ± 0.146 (0.680) | **0/6** |
| curr | yes | lenient, no status | 0.530 ± 0.153 (0.760) | **0/6** |

**1/42** bare runs hit 4/4. That one is **flat + shaping + lenient**, not the curriculum. All 36 curriculum jobs **stalled at k1** (0/36 completed the single guest; 0/36 had `prot_cover`; 24/36 entered). Recurring R=**0.52** is `entered+progress` without an MPU window: RUNCTX lets the guest retire 1–2 ops then fault, which outscores ADDPROT without RUNCTX. Later stages of a few shaped+lenient seeds still reach 2/4 on the 4-guest eval (prot_cover appears after k1). Status CSRs are not load-bearing; resume and imprecise mem abort move the mean a little and do not unblock k1.

Round 2 said bare is 0/12 at 400×40 with gadgets off. Round 3 shows a **single** bare 4/4 when the substrate is lenient, shaping is on, and the 500-gen budget is spent on four guests rather than gated by a 1-guest promotion. That is not a reliable discovery procedure. The ISA is still integer 5-tuples (`op,a,b,c,imm`); nothing Python-only is required to interpret it on a later boot stub.

## Path to a bootable x86_64 ISO

The evolved supervisor *is* the OS. A tiny host stub (C or Rust interpreter/JIT of the frozen word encoding) plus Limine or a Multiboot2 `boot.asm` (BIOS first, UEFI next) is firmware, not a kernel. Embed `supervisor.bin` in the ISO; map serial/VGA as a STORE window. Test in QEMU (`qemu-system-x86_64 -cdrom evoos.iso -nographic -serial mon:stdio`) before VirtualBox. Do not grow a Unix process table, page allocator, or POSIX VFS in the stub — aim for a **single address-space capability machine** whose multiplexing is whatever bytecode search found. Details: the ISO section of the round-3 summary.

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
| 5 | Bootable x86_64 ISO in QEMU then VirtualBox (BIOS, then UEFI); stub interprets evolved bytecode | `evoos/placeholders/qemu_boot.py`, `fuzzing.py` |

Next: break the k1 `entered`-without-`prot_cover` local optimum (shaping weights or a cover-before-progress gate that is still behavioral); a C/Rust interpreter with golden traces against this Python VM; Limine ISO.
