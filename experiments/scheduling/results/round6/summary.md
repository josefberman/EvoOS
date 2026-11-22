# Round 6: lexicase + dataflow GP vs blind UCB1

Wall time **2603.4s** (43.4 min), eval_workers=4.
Search: gens/stage=1000 pop=64 seeds `[1, 7, 13, 42, 99, 2024]`. Operator pack **bare**. Substrate **lenient**. Gated curriculum + milestone MAP-Elites. Isolation is a hard lexicase filter.
Target evals/seed ≈ 320000 (5 stages, before early-stop). Equal-eval vs round-5 blind (1000×64×5 = 320000).

## ISA freeze

ISA freeze: opcodes HALT..RDFAULT remain 0..31; CTXGET fields including LAST_RUN_* are unchanged. Round 6 changes selection (lexicase over per-guest milestones) and mutation bias (effective code, use-def, runtime constant pool). A C interpreter/boot stub must match the existing encoding only.
Opcode count: **32** (`HALT`=0 … `RDFAULT`=31).

## Selection and representation

Lexicase / epsilon-lexicase over per-guest milestones (`cover`, `entered_p`, `progress_p`, `complete_p`, `nofault`) plus isolation, per workload. Binary cases have MAD=0, so epsilon-lexicase reduces to lexicase. This is Helmuth/Spector/La Cava selection, not an OS prior.

Dataflow-aware linear GP (generic, documented in `evoos/agent/dataflow.py`):

Dataflow-aware linear GP helpers.

These are generic genetic-programming techniques (effective-code mutation, use-def operand bias, runtime constant pools). They are not OS priors: they do not insert run queues, skip-halted loops, or named scheduler gadgets.

Effective code is the backward slice of instructions that can influence a side effect (STORE, ADDPROT, CLRPROT, RUNCTX/I, CTXSET, HALT). Operand mutation prefers registers that an earlier instruction defined. Immediates are sampled from values observed at runtime (CTXGET results, firmware sizes) when a pool is provided.

Cartesian GP (`evoos/agent/cgp.py`) is implemented as an optional genome (1-row nodes decoded to the frozen 5-tuple ISA). The default sweep does not spend budget on it unless `--cgp-seeds` > 0.

Shaping weights: `{'entered': 0.15, 'prot_cover': 0.2, 'progress': 0.25, 'complete': 0.4}`

## Condition A (oracle)

| name | train completed | held completed | 4/4 | held R |
| --- | --- | --- | --- | --- |
| run_to_completion | [4, 4] | [4, 4] | True | 1.000 |
| round_robin_q16 | [4, 4] | [4, 4] | True | 1.000 |
| selfish_first | [1, 1] | [1, 1] | False | 0.400 |

## Condition C

| arm | search R mean ± std (best) | 4/4 train+held | mean n_eval |
| --- | --- | --- | --- |
| blind | 0.067 ± 0.149 (0.400) | **0/6** | 320000 |
| lexicase_dataflow | 0.000 ± 0.000 (0.000) | **0/6** | 320000 |

Per seed:

| arm | seed | R | train completed | held completed | 4/4 | label | stall | archive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blind | 1 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| blind | 7 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| blind | 13 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| blind | 42 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| blind | 99 | 0.400 | [1, 1] | [1, 1] | False | starving | k1 | 4 |
| blind | 2024 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| lexicase_dataflow | 1 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| lexicase_dataflow | 7 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| lexicase_dataflow | 13 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| lexicase_dataflow | 42 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| lexicase_dataflow | 99 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |
| lexicase_dataflow | 2024 | 0.000 | [0, 0] | [0, 0] | False | no-dispatch | k1 | 1 |

**0/12** 4/4 (0 lexicase_dataflow, 0 blind).
k1: entered 0/12, entered_protected 0/12, prot_cover 0/12, protected-complete 0/12, promoted 0/12.
Stall histogram: k1=12

## Which test cases get solved first

| case | earliest gen | median gen | frac of runs |
| --- | --- | --- | --- |
| k1.curr_k1_train.g0.nofault | 0 | 0 | 1.00 |
| k1.curr_k1_train.isolation | 0 | 0 | 1.00 |
| k2.curr_k2_train.g0.nofault | 0 | 0 | 1.00 |
| k2.curr_k2_train.g1.nofault | 0 | 0 | 1.00 |
| k2.curr_k2_train.isolation | 0 | 0 | 1.00 |
| k3.curr_k3_train.g0.nofault | 0 | 0 | 1.00 |
| k3.curr_k3_train.g1.nofault | 0 | 0 | 1.00 |
| k3.curr_k3_train.g2.nofault | 0 | 0 | 1.00 |
| k3.curr_k3_train.isolation | 0 | 0 | 1.00 |
| k4.curr_k4_train.g0.nofault | 0 | 0 | 1.00 |
| k4.curr_k4_train.g1.nofault | 0 | 0 | 1.00 |
| k4.curr_k4_train.g2.nofault | 0 | 0 | 1.00 |
| k4.curr_k4_train.g3.nofault | 0 | 0 | 1.00 |
| k4.curr_k4_train.isolation | 0 | 0 | 1.00 |
| k4_hard.curr_k4h_train.g0.nofault | 0 | 0 | 1.00 |
| k4_hard.curr_k4h_train.g1.nofault | 0 | 0 | 1.00 |
| k4_hard.curr_k4h_train.g2.nofault | 0 | 0 | 1.00 |
| k4_hard.curr_k4h_train.g3.nofault | 0 | 0 | 1.00 |
| k4_hard.curr_k4h_train.isolation | 0 | 0 | 1.00 |
| k4_hard.curr_k4h_train.g0.cover | 229 | 229 | 0.08 |
| k4_hard.curr_k4h_train.g0.complete_p | 246 | 246 | 0.08 |
| k4_hard.curr_k4h_train.g0.entered_p | 246 | 246 | 0.08 |
| k4_hard.curr_k4h_train.g0.progress_p | 246 | 246 | 0.08 |
| k4_hard.curr_k4h_train.g1.complete_p | 256 | 256 | 0.08 |
| k4_hard.curr_k4h_train.g1.cover | 256 | 256 | 0.08 |
| k4_hard.curr_k4h_train.g1.entered_p | 256 | 256 | 0.08 |
| k4_hard.curr_k4h_train.g1.progress_p | 256 | 256 | 0.08 |

First-solved (arm `blind`): k1.curr_k1_train.g0.nofault, k1.curr_k1_train.isolation, k2.curr_k2_train.g0.nofault, k2.curr_k2_train.g1.nofault, k2.curr_k2_train.isolation, k3.curr_k3_train.g0.nofault, k3.curr_k3_train.g1.nofault, k3.curr_k3_train.g2.nofault.

First-solved (arm `lexicase_dataflow`): k1.curr_k1_train.g0.nofault, k1.curr_k1_train.isolation, k2.curr_k2_train.g0.nofault, k2.curr_k2_train.g1.nofault, k2.curr_k2_train.isolation, k3.curr_k3_train.g0.nofault, k3.curr_k3_train.g1.nofault, k3.curr_k3_train.g2.nofault.

Cover case ever solved (any individual, any stage): lexicase_dataflow 0/6, blind 1/6.
No 4/4 elites, so no non-RR/RTC discovery to report.

## Findings

Isolation and `nofault` are solved at generation 0 in every seed and stage (idle programs do not fault guests they never run). Lexicase therefore has **no distinguishing cases** until someone covers a window. That event happened in **1/12** runs: blind seed 99 at `k4_hard` gen 229 (`g0.cover`), then `entered_p`/`progress_p`/`complete_p` at gen 246, then guest 1 at gen 256. Final 4-guest eval is 1/4, label `starving`, archive 4. Disassembly: [`examples/bare_curr_gated_blind_s99.txt`](examples/bare_curr_gated_blind_s99.txt). Lexicase+dataflow seed 99 stayed `no-dispatch` (R=0, archive 1).

k1 elites never entered or covered (0/12), so the 1/1 promotion rule did not fire. Curriculum still spent 1000 gens on later stages with the idle population; the seed-99 stone appears only on `k4_hard`.

Wall **43.4 min** vs round 5 **254.1 min** at the same 320000 evals/seed: correctness mode no longer scores unused novelty k-NN. That is parent overhead, not fewer VM steps. Cartesian GP was not given a sweep arm (`--cgp-seeds 0`).

