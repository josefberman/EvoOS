# Round 5: learned edit policy vs blind mutation

Wall time **15246.5s** (254.1 min), eval_workers=4.
Search: gens/stage=1000 pop=64 seeds `[1, 7, 13, 42, 99, 2024]`. Operator pack **bare**. Substrate **lenient**. Gated curriculum + milestone MAP-Elites. k1 promotes on a 1/1 protected-complete stone.
Target evals/seed ≈ 320000 (5 stages, before early-stop). Round 4 was 120×56×5 = 33600; this run is ~9.5× that budget.

## ISA freeze

ISA freeze: opcodes HALT..RDFAULT remain 0..31; CTXGET fields including LAST_RUN_* are unchanged. Round 5 trains an edit policy over existing 5-tuples. A C interpreter/boot stub must match the existing encoding only.
Opcode count: **32** (`HALT`=0 … `RDFAULT`=31).

## Policy

Learned arm: GRU over instruction tokens (op,a,b,c,imm) plus a 12-d telemetry/milestone vector. Heads: edit kind, position, opcode, registers, immediate table. REINFORCE with a value baseline on (child−parent) reward, plus a new-cell bonus from the MAP-Elites archive. ε-greedy mixes a UCB1 operator arm. One network is shared across seeds (online). Blind arm: UCB1 over the same bare operators, independent per seed.

Shaping weights: `{'entered': 0.15, 'prot_cover': 0.2, 'progress': 0.25, 'complete': 0.4}`

```
Behavioral shaping terms. None of these matches an instruction sequence.

Each term is an *observable* of the machine after a run. A supervisor can
satisfy them with any bytecode that produces that machine state. They are
not `schedule_process`, not `ADDPROT` opcode bonuses, not run-queue hints.

| Term | Observation | Why it is not an architectural prior |
| --- | --- | --- |
| entered | Fraction of guests that retired ≥1 instruction | Any context-switch primitive posts this; we do not care *how* RUNCTX was reached |
| prot_cover | Guest's assigned data window is inside some installed MMU range | Hardware protection *state*, not “emit ADDPROT in this order” |
| progress | Guest executed >0 cycles (same as entered here, kept distinct for heartbeats later) | Work happened |
| complete | Gue
```

## Condition A (lenient, handwritten)

| name | held complete | 4/4 | held R | label |
| --- | --- | --- | --- | --- |
| run_to_completion | [4, 4] | True | 1.000 | run-to-completion-like |
| round_robin_q16 | [4, 4] | True | 1.000 | round-robin-like |
| selfish_first | [1, 1] | False | 0.400 | starving |

## Condition C — learned vs blind, equal eval budget

| arm | n | search R mean | std | best | 4/4 train+held | mean n_eval | stall stages | labels |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blind | 6 | 0.067 | 0.149 | 0.400 | 0/6 | 320000 | k1,k1,k1,k1,k1,k1 | no-dispatch, starving |
| learned | 6 | 0.000 | 0.000 | 0.000 | 0/6 | 320000 | k1,k1,k1,k1,k1,k1 | no-dispatch |

### Per-seed

| arm | seed | search R | train | held | 4/4 | stall | label | archive | n_eval |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| blind | 1 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| blind | 7 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| blind | 13 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| blind | 42 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| blind | 99 | 0.400 | [1, 1] | [1, 1] | no | k1 | starving | 6 | 320000 |
| blind | 2024 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| learned | 1 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| learned | 7 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| learned | 13 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| learned | 42 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| learned | 99 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |
| learned | 2024 | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 | 320000 |

## Edits the learned policy preferred

Visit counts (shared net, after last learned seed):

| edit | count |
| --- | --- |
| modify_imm | 313282 |
| modify_operand | 219052 |
| crossover | 203382 |
| control_flow | 196942 |
| remove | 184956 |
| insert_gadget | 184637 |
| modify_op | 184111 |
| swap | 154741 |
| insert | 140258 |
| duplicate_block | 138639 |
| revert_mutate | 0 |

Mean advantage EMA:

| edit | adv |
| --- | --- |
| insert | 0.0000 |
| remove | 0.0000 |
| modify_op | 0.0000 |
| modify_operand | 0.0000 |
| modify_imm | 0.0000 |
| duplicate_block | 0.0000 |
| swap | 0.0000 |
| insert_gadget | 0.0000 |
| crossover | 0.0000 |
| control_flow | 0.0000 |
| revert_mutate | 0.0000 |

## Learning curves

Per-generation `best_reward` vs cumulative evaluations: [`learning_curves.csv`](learning_curves.csv). Stage-level promotion: [`curriculum_curves.csv`](curriculum_curves.csv).

- **blind**: final search R mean 0.067 over 1920000 total evals (320000/seed).
- **learned**: final search R mean 0.000 over 1920000 total evals (320000/seed).

## Diagnosis

**0/12** bare runs reached 4/4 on full train+held-out (0/6 learned, 0/6 blind).
k1 curriculum stage: entered 0/12, entered_protected 0/12, prot_cover 0/12, protected-complete 0/12, promoted 0/12.
Stall histogram: k1=12
No 4/4 elites, so no non-RR/RTC discovery to report.

Equal evals: every seed of both arms ran the full 5×1000×64 = **320000** evaluations (no early-stop). Search `max_len=64`, `max_steps=4000`; the 4/4 split is scored at 8000 steps.

Learned vs blind: the GRU never left the idle cell (search R **0.000** on all 6 seeds, archive size 1). Advantages stayed 0 because child−parent deltas were 0 — there was no reward gradient to reinforce. Visit counts still skewed toward `modify_imm` / `modify_operand` / `crossover` (not `revert_mutate`, which stayed at 0). Blind UCB1 matched round 4 on seed 99: k1–k3 idle, then k4 found a **1/4** protected-complete elite (guest slot 1 covered, `entered_protected=1`, R=0.400, archive 6 cells including `(1,1,1,1,4)`, label `starving`). That stone is a 4-guest cell, so the **k1** 1/1 promotion rule never fired. The shared net, trained only on idle deltas, did not rediscover seed 99.

Nothing structurally non-RR/RTC at 4/4 because nothing reached 4/4. Blind is strictly better at this budget (1/6 nonzero vs 0/6). Covering a guest window from random 5-tuples remains the bottleneck; a learned edit policy does not substitute for that event.

