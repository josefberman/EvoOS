# Scheduling experiment results

Real numbers from this run. Isolation failures cannot be compensated by throughput.

- Wall time: **1445.4s** (24.1 min) on 4 processes
- Condition C search: 400 generations × 40 population, seeds `[1, 7, 13, 42, 99, 2024]`
- Modes: `correctness`, `qd`. Operator packs: `['bare', 'gadgets', 'full']`

## Operator packs (what they encode)

- **bare**: instruction edits only
- **gadgets**: insert_mmu_gadget, insert_runctx_gadget
- **full**: insert_mmu_gadget, insert_runctx_gadget, insert_ctx_scan

- `insert_mmu_gadget`: ISA calling convention for protection registers: MOVI ctx; CTXGET DATA_BASE; CTXGET DATA_BOUND; ADDPROT. Not a scheduler. ctx is a single random id, not a loop over guests.
- `insert_runctx_gadget`: ISA calling convention for RUNCTXI: MOVI ctx; RUNCTXI steps. One context, one burst. Not round-robin / not a run queue.
- `insert_ctx_scan`: Hardware enumeration: MOVI r,1; <one body instr>; ADDI r,1; CTXGET N_CONTEXTS; BLT r, n, body. Encodes 'the machine reports how many contexts it has; you can walk ids'. Body is NOP or one rewritten parent instruction — not skip-if-halted, not quantum, not until-idle. That extra control flow, if it appears, must still be discovered.

## Condition A (hand-written)

| supervisor | train complete | held complete | 4/4 isol | correctness R | label |
| --- | --- | --- | --- | --- | --- |
| run_to_completion | [4, 4] | [4, 4] | yes | 1.000 | run-to-completion-like |
| round_robin_q16 | [4, 4] | [4, 4] | yes | 1.000 | round-robin-like |
| selfish_first | [1, 1] | [1, 1] | no | 0.400 | starving |

## Condition B (UCB1 on RR quantum)

| seed | learned q | held complete | 4/4 isol | held R | label |
| --- | --- | --- | --- | --- | --- |
| 7 | 1 | [4, 4] | yes | 1.000 | round-robin-like |
| 13 | 1 | [4, 4] | yes | 1.000 | round-robin-like |
| 1 | 1 | [4, 4] | yes | 1.000 | round-robin-like |
| 42 | 1 | [4, 4] | yes | 1.000 | round-robin-like |
| 99 | 1 | [4, 4] | yes | 1.000 | round-robin-like |
| 2024 | 1 | [4, 4] | yes | 1.000 | round-robin-like |

Exhaustive grid best quantum = **1** (train R=1.000, held R=1.000, 4/4=yes).

On the **correctness** objective every quantum in `{1 … 4000}` that runs all four guests scores R = 1.0 (F and S saturate). The bandit therefore has no gradient among working quanta and keeps the first perfect arm it tries (`q=1`). Switch counts do change: `q=1` ≈ 228/322 context switches on the two training mixes; `q=16` ≈ 16/23; `q≥128` ≈ 4/4 (run-to-completion). Distinguishing those would need efficiency in the Condition B reward, which this comparison deliberately does not use.

## Condition C (emergent), by mode × pack

| mode | pack | n seeds | search R mean | std | best | held correctness mean | held best | seeds 4/4 train+held | labels |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| correctness | bare | 6 | 0.347 | 0.038 | 0.400 | 0.347 | 0.400 | 0/6 | batched/priority-like, other, round-robin-like, starving |
| correctness | gadgets | 6 | 1.000 | 0.000 | 1.000 | 1.000 | 1.000 | 6/6 | other, round-robin-like |
| correctness | full | 6 | 1.000 | 0.000 | 1.000 | 0.987 | 1.000 | 5/6 | batched/priority-like, other, round-robin-like |
| qd | bare | 6 | 0.305 | 0.023 | 0.332 | 0.307 | 0.360 | 0/6 | batched/priority-like, other, round-robin-like, starving |
| qd | gadgets | 6 | 0.857 | 0.039 | 0.879 | 0.973 | 1.000 | 5/6 | other, round-robin-like |
| qd | full | 6 | 0.878 | 0.007 | 0.892 | 0.987 | 1.000 | 5/6 | other, run-to-completion-like |

### Per-seed Condition C

| mode | pack | seed | search R | train | held | 4/4 | label | diagnose |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| correctness | bare | 1 | 0.360 | [0, 0] | [0, 0] | no | round-robin-like | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| correctness | bare | 7 | 0.320 | [0, 0] | [0, 0] | no | starving | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| correctness | bare | 13 | 0.360 | [0, 0] | [0, 0] | no | other | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| correctness | bare | 42 | 0.280 | [0, 0] | [0, 0] | no | starving | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| correctness | bare | 99 | 0.400 | [1, 1] | [1, 1] | no | batched/priority-like | train completed [1, 1] held [1, 1]; near-zero-cycle guests (train idx) [[0, 1, 3 |
| correctness | bare | 2024 | 0.360 | [0, 0] | [0, 0] | no | round-robin-like | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| correctness | full | 1 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | full | 7 | 1.000 | [4, 4] | [4, 4] | yes | batched/priority-like | 4/4 train+heldout with isolation |
| correctness | full | 13 | 1.000 | [4, 4] | [4, 3] | no | round-robin-like | train completed [4, 4] held [4, 3]; near-zero-cycle guests (train idx) [[], []]; |
| correctness | full | 42 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | full | 99 | 1.000 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| correctness | full | 2024 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | gadgets | 1 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | gadgets | 7 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | gadgets | 13 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | gadgets | 42 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| correctness | gadgets | 99 | 1.000 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| correctness | gadgets | 2024 | 1.000 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| qd | bare | 1 | 0.332 | [0, 0] | [0, 0] | no | round-robin-like | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| qd | bare | 7 | 0.321 | [0, 0] | [0, 0] | no | other | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| qd | bare | 13 | 0.273 | [0, 0] | [0, 0] | no | starving | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| qd | bare | 42 | 0.291 | [0, 0] | [0, 0] | no | round-robin-like | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| qd | bare | 99 | 0.286 | [0, 0] | [0, 0] | no | batched/priority-like | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| qd | bare | 2024 | 0.328 | [0, 0] | [0, 0] | no | round-robin-like | train completed [0, 0] held [0, 0]; near-zero-cycle guests (train idx) [[0, 1, 2 |
| qd | full | 1 | 0.877 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| qd | full | 7 | 0.878 | [4, 4] | [4, 3] | no | other | train completed [4, 4] held [4, 3]; near-zero-cycle guests (train idx) [[], []]; |
| qd | full | 13 | 0.892 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| qd | full | 42 | 0.872 | [4, 4] | [4, 4] | yes | run-to-completion-like | 4/4 train+heldout with isolation |
| qd | full | 99 | 0.879 | [4, 4] | [4, 4] | yes | run-to-completion-like | 4/4 train+heldout with isolation |
| qd | full | 2024 | 0.874 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| qd | gadgets | 1 | 0.873 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| qd | gadgets | 7 | 0.878 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| qd | gadgets | 13 | 0.879 | [4, 4] | [4, 4] | yes | other | 4/4 train+heldout with isolation |
| qd | gadgets | 42 | 0.771 | [3, 3] | [3, 3] | no | other | train completed [3, 3] held [3, 3]; near-zero-cycle guests (train idx) [[3], [3] |
| qd | gadgets | 99 | 0.866 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |
| qd | gadgets | 2024 | 0.873 | [4, 4] | [4, 4] | yes | round-robin-like | 4/4 train+heldout with isolation |

## A vs B vs C

| condition | best name | held complete | 4/4 | held R | label |
| --- | --- | --- | --- | --- | --- |
| A | run_to_completion | [4, 4] | yes | 1.000 | run-to-completion-like |
| B | rr_quantum_1 | [4, 4] | yes | 1.000 | round-robin-like |
| C | c_correctness_gadgets_s1 | [4, 4] | yes | 1.000 | round-robin-like |

## Diagnosis

**21/36** Condition C runs reached 4/4 guests complete with isolation on train AND held-out.

Ablation (same 400×40, 6 seeds):

- **bare** (no ISA-manual gadgets): **0/12** 4/4. Best correctness seed completed 1/4 guests. Search reward mean 0.35 (correctness) / 0.31 (qd). Random instruction edits do not find ADDPROT+RUNCTX composition at this budget.
- **gadgets** (MMU + RUNCTX calling convention): **6/6** correctness 4/4, **5/6** qd 4/4. Search R = 1.000 ± 0.000 (correctness) and 0.857 ± 0.039 (qd).
- **full** (gadgets + context-id scan): **5/6** correctness 4/4, **5/6** qd 4/4. The extra scan operator did **not** beat gadgets; two seeds missed one held-out guest (`[4, 3]`).

So the load-bearing prior is the ISA calling-convention gadgets, not the N_CONTEXTS loop. That weakens the “no priors” reading of Condition C: the agent is not given a scheduler, but it *is* given how to talk to the MMU and RUNCTX. Bare search is the closer test of “from a raw ISA,” and it fails here.

Behavioral labels of 4/4 elites are mixed (`round-robin-like`, `run-to-completion-like`, `batched/priority-like`, `other`). The classifier uses switch interleaving, not source comments; several gadget-run elites look RR-like without being the hand-written RR program. Disassemblies: `experiments/scheduling/results/examples/`.

