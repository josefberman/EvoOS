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
- `BLKRD` / `BLKWR` — copy a fixed-size block to/from a dumb device (latency, not a pager)
- `SVC` — unprivileged trap; supervisor reads `LAST_SVC` / `LAST_SVC_CTX` (not a pipe)

**Forbidden in the ISA**, on purpose: `schedule_process`, `yield_to_next`, `allocate_page`, run queues, priority bands, wait queues, pipes, or any other traditional OS component as a primitive. If round-robin, swap, or routing appear, they must appear as *patterns in bytecode*.

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
| `hw` | full + `insert_blkrd/blkwr`, `insert_remap`, `insert_svc_status` | calling convention for BLK*, window CTXSET, SVC status CSRs — **the hardware manual** | eviction/swap policy, frame allocator, pipes, message queues |

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
# Round 4: gated vs ungated shaping + milestone MAP-Elites (bare, lenient):
python -m evoos.experiments.scheduling.sweep_round4 --out experiments/scheduling/results/round4
# Round 5: learned GRU edit policy vs blind UCB1 (equal evals, gated curriculum):
python -m evoos.experiments.scheduling.sweep_round5 --out experiments/scheduling/results/round5
# Round 6: lexicase + dataflow GP vs blind UCB1 (equal evals, gated curriculum):
python -m evoos.experiments.scheduling.sweep_round6 --out experiments/scheduling/results/round6
# Round 7: close bare; Condition C-hw (hardware-manual pack, pressure+route):
python -m evoos.experiments.scheduling.sweep_round7 --out experiments/scheduling/results/round7
# Round 8: C-hw routing curriculum (shared-window QD + embed format):
python -m evoos.experiments.scheduling.sweep_round8 --out experiments/scheduling/results/round8
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

## Round 4 (gated shaping + stepping stones)

Same machine, **24.4 min**, 6 seeds, bare pack, **lenient** substrate. Tables: [`experiments/scheduling/results/round4/summary.md`](experiments/scheduling/results/round4/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round4 \
  --gens-per-stage 120 --generations 500 --population 56 --workers 4
```

Gated: entered/progress/complete for a guest count only if that guest retired ≥1 instruction while its window was already covered. MAP-Elites cells are `(n_prot, n_entered_protected, n_progressed_protected, n_completed, n_guests)` with curiosity parent selection and elite crossover. **ISA frozen** (opcodes 0–31 unchanged); `ran_protected` is evaluator telemetry, not a CSR.

| curriculum | gate | search R mean ± std (best) | 4/4 |
| --- | --- | --- | --- |
| flat | ungated | 0.387 ± 0.100 (0.520) | **0/6** |
| flat | gated | 0.000 ± 0.000 (0.000) | **0/6** |
| curr | ungated | 0.523 ± 0.170 (0.760) | **0/6** (two seeds 2/4) |
| curr | gated | 0.067 ± 0.149 (0.400) | **0/6** (seed 99: 1/4 protected complete) |

**0/24** 4/4. Gated kills the R=0.52 trap (flat gated is all `no-dispatch`). It does not compose ADDPROT-then-RUNCTX at this budget except one curriculum seed that covers and completes a single guest. Nothing non-RR/RTC at 4/4 because nothing reached 4/4. Round 3's one flat 4/4 did not replicate under the milestone archive (ungated flat 0/6).

Next: a C/Rust interpreter with golden traces against this Python VM; Limine ISO. The remaining search gap is covering the guest window from random 5-tuples, not ISA design.

## Round 5 (learned edit policy vs blind mutation)

Same machine, **254.1 min**, 6 seeds, bare pack, gated curriculum, **lenient** substrate. Tables: [`experiments/scheduling/results/round5/summary.md`](experiments/scheduling/results/round5/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round5 \
  --gens-per-stage 1000 --population 64 --eval-workers 4
```

GRU (instruction tokens + 12-d telemetry) trained with REINFORCE/PPO-clip on child−parent reward, shared across seeds, ε-greedy UCB1 mix. Blind arm: independent UCB1. Equal evals: **320000**/seed (9.5× round-4 curriculum). k1 promotes on a 1/1 protected-complete archive cell. **ISA frozen** (32 opcodes). Search `max_len=64`.

| arm | search R mean ± std (best) | 4/4 train+held | mean n_eval |
| --- | --- | --- | --- |
| learned | 0.000 ± 0.000 (0.000) | **0/6** | 320000 |
| blind | 0.067 ± 0.149 (0.400) | **0/6** (seed 99: 1/4 protected complete) | 320000 |

**0/12** 4/4. The net never left the idle cell (advantages 0; no reward gradient). Blind seed 99 again finds a k4 1/4 cover-then-run stone (archive 6, label `starving`); that is a 4-guest cell, so k1 promotion never fired (0/12). Preferred visit counts: `modify_imm`, `modify_operand`, `crossover`. Nothing non-RR/RTC at 4/4. Covering the guest window from random 5-tuples is still the gap, not the mutation policy.

## Round 6 (lexicase + dataflow GP vs blind)

Same machine, **43.4 min**, 6 seeds, bare pack, gated curriculum, **lenient** substrate. Tables: [`experiments/scheduling/results/round6/summary.md`](experiments/scheduling/results/round6/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round6 \
  --gens-per-stage 1000 --population 64 --eval-workers 4
```

Epsilon-lexicase over per-guest milestones (`cover`, `entered_p`, `progress_p`, `complete_p`, `nofault`) plus isolation; isolation-fail individuals are excluded from the parent pool. Dataflow linear GP: mutate effective code (backward slice from STORE/ADDPROT/RUNCTX/…), use-def operand bias, immediates from a CTXGET/firmware constant pool. These are generic GP techniques, documented in `evoos/agent/dataflow.py`, not OS priors. Cartesian GP is implemented (`evoos/agent/cgp.py`) but was not given sweep budget. Equal evals vs round-5 blind: **320000**/seed. ISA frozen (32 opcodes). Wall time is lower than round 5 because unused novelty k-NN scoring is skipped when N=0; eval count is the same.

| arm | search R mean ± std (best) | 4/4 train+held | mean n_eval |
| --- | --- | --- | --- |
| lexicase_dataflow | 0.000 ± 0.000 (0.000) | **0/6** | 320000 |
| blind | 0.067 ± 0.149 (0.400) | **0/6** (seed 99: 1/4 protected complete) | 320000 |

**0/12** 4/4. Cases solved first in every run (gen 0): `isolation` and `nofault`. Cover/entered_p/complete_p: **0/6** lexicase seeds, **1/6** blind (seed 99, `k4_hard` guest 0 cover at gen 229, entered_p/complete_p at 246). Lexicase never left `no-dispatch`. Blind seed 99 again labels `starving` (archive 4). k1 promotion 0/12. No 4/4 elite, so no disassembly of a 4/4 program. Covering a guest window from random 5-tuples is still the rare event; lexicase has no signal on that case when the whole population scores 0.

## Why bare-bits discovery fails

Cover = `ADDPROT` range contains a guest's `[DATA_BASE, DATA_BOUND)`. Rounds 3–6 used four search methods on the bare pack (UCB1, curriculum+shaping, learned GRU, lexicase+dataflow). Gated 4/4 counts: **1/42** then **0/24 + 0/12 + 0/12**. The one hit (round 3, ungated flat) did not replicate.

The MMU calling-convention family is **840** length-4 programs (4 ctx ids × P(7,3) regs). Effective per-instruction alphabet ≈ `6.35e5` (31 ops × 8³ regs × 40 immediates). P(a uniform length-4 block is the gadget) ≈ `5.17e-21`. P(a length-24 random program contains that block) ≈ `1.09e-19`. Expected evals to first such block ≈ **9.21e18**. Four independent copies for 4/4: **7.2e75**.

Empirical (this run): 0/20000 random length-24 on k1, 0/10000 on 4-guest train, 0/10000 single-lineage mutate. Restricted length-4 `MOVI|CTXGET|ADDPROT`: **1/20000** (p̂=`5e-5`). Round-6 sweep: **1** cover in **3.84e6** evals (blind seed 99, k4_hard gen 229, ≈270656 evals into that seed); lexicase cover 0; 4/4 0. Sweep p̂ = `2.6e-7`. If 4/4 were four independent covers at that p̂, 50% chance would take **1.51e26** evals — a lower bound, because 4/4 also needs a scan and RUNCTX after cover.

Bare search has no gradient on cover: isolation and nofault are gen-0 for everyone. The line is closed. Numbers: [`experiments/scheduling/results/round7/why_bare_fails.md`](experiments/scheduling/results/round7/why_bare_fails.md).

## Round 7 (Condition C-hw: hardware-manual pack, richer environment)

Same machine, **198.2 min**, 6 seeds, pack `hw`, QD MAP-Elites + epsilon-lexicase + dataflow, seeded from round-2 gadget elites. Tables: [`experiments/scheduling/results/round7/summary.md`](experiments/scheduling/results/round7/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round7 \
  --generations 4000 --population 64 --eval-workers 4
```

ISA: opcodes 0–31 unchanged; 32–34 = `BLKRD`/`BLKWR`/`SVC` from the persist-blk boot track. Condition **C-hw** gives the hardware manual (MMU/RUNCTX/BLK/window-CTXSET/SVC-status gadgets) and withholds paging/routing policy. Environment: **8 guests / 4 RAM frames** + block device (pressure); 4 producer/consumer pairs via `SVC` PUT/GET (communication). Reward: completion, isolation (hard), latency, fairness. MAP-Elites cell `(completed, blk_writes, svc+delivered, switch_bin, isolation)`. **256000** evals/seed.

**Condition A-hw (hand-written):** `fifo_swap_rr` / `direct_copy_router` complete **8/8 + 8/8** train and **8/8 + 6/6** held (4/4, held R=1.000). `place_first_frames_rr` (no evict) **4/8 + 4/8**. `fifo_swap_deaf` (swap, ignore SVC) **8/8 + 4/8**.

**Condition C-hw 4/4 on pressure+route train AND held-out:** **0/6**. Search R **0.530 ± 0.040** (best 0.596, seed 99).

| seed | train completed | held | org (pressure) | blk_w | route delivered | isolation |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [2, 5] | [2, 3] | fifo-swap-like+rr | 57 | 1/4 | pass |
| 7 | [1, 5] | [1, 4] | fifo-swap-like+rr | 122 | 1/4 | **fail** |
| 13 | [3, 5] | [3, 4] | fifo-swap-like+rr | 39 | 1/4 | pass |
| 42 | [4, 4] | [4, 4] | blk-user | 2 | 1/4 | pass |
| 99 | [4, 5] | [4, 4] | fifo-swap-like+rr | 7 | 1/4 | pass |
| 2024 | [3, 4] | [3, 3] | fifo-swap-like+rr | 210 | 0/4 | pass |

Organizations that emerged: **fifo-swap-like+rr** (5/6; BLKWR eviction + RR RUNCTXI) and **blk-user** (seed 42; BLKRD into frames, almost no evict — matches `place_first_frames_rr`). Routing is a single-pair copy (1/4 delivered) vs the hand-written direct-copy baseline (4/4). Seed 99 is the strongest elite (4/8 pressure, 5/8 route, 8/8 resident, no starved guests on train). Nobody reaches the FIFO baseline's 8/8 under pressure: search invents placement and some swap, not a complete victim-clock. Disassembly: [`round7/examples/`](experiments/scheduling/results/round7/examples/).

## Round 8 (C-hw routing curriculum: shared windows)

Same machine, **30.0 min** wall, 6 seeds, pack `hw`. Curriculum 1 pair → ordered payloads → 3 pairs → 8-guest pressure. Per-message lexicase `{w}.m{i}.{delivered,payload,recipient}`; isolation is hard. MAP-Elites `route_cell` (delivered, n_msg_ok, style, switch_bin, isolation); equal-R prefers non-copy. Tables: [`experiments/scheduling/results/round8/summary.md`](experiments/scheduling/results/round8/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round8 --out experiments/scheduling/results/round8
```

gens_per_stage=2500, pop=48, 4 workers. **1,115,472** evals (mean 185912/seed). r1/r2 early-stopped; r4 always 2500 gens. ISA still 0–34; no pipe opcode.

**Baselines.** `payload_copy_rr`: r1 1/1, r2 3/3, r3 3/3, r4 4/4, style `copy-supervisor`. `fifo_swap_rr`: r1 1/1, r2 1/3, r3 3/3, r4 4/4 (1-word mailbox; `disk-buffer` under pressure because the consumer is swapped to `home_block`).

**Condition C-hw routing 4/4** (route_4 + mix train **and** held, all guests complete): **0/6**. Search R **0.912 ± 0.055** (best 0.960). Isolation **6/6**.

| seed | R | r1 | r2 n_ok | r3 | r4 train/held | mix | style | org |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.960 | 0 | 1 | 3 | 4/3 | 2 | shared-window | blk-user+shared-window |
| 7 | 0.840 | 1 | 1 | 2 | 2/2 | 1 | shared-window | blk-user+shared-window |
| 13 | 0.940 | 1 | 1 | 3 | 3/3 | 2 | shared-window | blk-user+shared-window |
| 42 | 0.960 | 1 | 1 | 3 | 4/3 | 2 | shared-window | blk-user+shared-window |
| 99 | 0.830 | 1 | 3 | 2 | 2/2 | 1 | shared-window | fifo-swap-like+shared-window |
| 2024 | 0.940 | 1 | 3 | 2 | 2/2 | 1 | shared-window | blk-user+shared-window |

r4 delivered mean **2.83** (best **4**). **2/6** seeds hit 4/4 messages on the four-pair train (1 and 42) vs round-7 evolved **1/4** and baseline **4/4**. **6/6** elites are `shared-window`: overlapping `CTXSET` windows + `ADDPROT` of the same range to two guest ids. Dual-granted overlap is not a leak. Nobody evolved copy-through-supervisor. Seed 42 is the strongest generalizer (r1=1 and r4=4). Seed 1’s r4=4 elite fails the 2-guest sandbox (specialized to 8 contexts). Ordering (r2 3/3) survives on seeds 99 and 2024 only.

Boot-track embed (`evoos-embed-v1`): lossless JSON 5-tuples + `.bin` quintuples. Spec [`evoos/export/FORMAT.md`](evoos/export/FORMAT.md). Elites [`round8/embed/`](experiments/scheduling/results/round8/embed/). Disassembly [`round8/examples/`](experiments/scheduling/results/round8/examples/).
