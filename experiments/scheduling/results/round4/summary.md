# Round 4: gated shaping, milestone MAP-Elites, gated vs ungated

Wall time **1466.9s** (24.4 min), 4 processes.
Search: gens/stage=120 flat gens=500 pop=56 seeds `[1, 7, 13, 42, 99, 2024]`.
Operator pack **bare**. Substrate **lenient**. Stepping-stones: milestone MAP-Elites + curiosity + elite crossover.

## ISA freeze

ISA freeze: opcodes HALT..RDFAULT remain 0..31; CTXGET fields including LAST_RUN_* are unchanged. Round 4 adds evaluator-only `ran_protected` / `protected_cycles` on the Python Context object — not a CSR, not an opcode. A C interpreter/boot stub must match the existing 5-tuple encoding only.
Opcode count: **32** (`HALT`=0 … `RDFAULT`=31).

## Gated shaping

Progress/entered/complete for a guest count only if that guest retired ≥1 instruction while its assigned data window was already covered. `prot_cover` is on the path, not a competing 0.20 term. Ungated is the round-3 mix (the R=0.52 trap).

Weights: `{'entered': 0.15, 'prot_cover': 0.2, 'progress': 0.25, 'complete': 0.4}`

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
| complete | Guest halted with expected outputs | The original behavioral objective |

Weights sum to 1 and replace the sparse F = 0.8·complete + 0.2·progress
when shaping is on. Isolation remains a hard constraint.

Round 4 gated mode (isolation-gated / lexicographic):
entered, progress, and complete for a guest count **only if** that guest
retired at least one instruction while its data window was already cover
```

## Condition A (lenient substrate, handwritten)

| name | held complete | 4/4 | held R | label |
| --- | --- | --- | --- | --- |
| run_to_completion | [4, 4] | True | 1.000 | run-to-completion-like |
| round_robin_q16 | [4, 4] | True | 1.000 | round-robin-like |
| selfish_first | [1, 1] | False | 0.400 | starving |

## Condition C bare — 4/4 on the *full* 4-guest train+held-out

| curriculum | gate | n | search R mean | std | best | 4/4 train+held | stall stages | labels |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flat | ungated | 6 | 0.387 | 0.100 | 0.520 | 0/6 | — | batched/priority-like, other, starving |
| flat | gated | 6 | 0.000 | 0.000 | 0.000 | 0/6 | — | no-dispatch |
| curr | ungated | 6 | 0.523 | 0.170 | 0.760 | 0/6 | k1,k1,k1,k1,k1,k1 | batched/priority-like, other, round-robin-like, starving |
| curr | gated | 6 | 0.067 | 0.149 | 0.400 | 0/6 | k1,k1,k1,k1,k1,k1 | no-dispatch, starving |

### Per-seed

| seed | curr | gate | search R | train | held | 4/4 | stall | label | archive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | curr | gated | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 |
| 7 | curr | gated | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 |
| 13 | curr | gated | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 |
| 42 | curr | gated | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 |
| 99 | curr | gated | 0.400 | [1, 1] | [1, 1] | no | k1 | starving | 6 |
| 2024 | curr | gated | 0.000 | [0, 0] | [0, 0] | no | k1 | no-dispatch | 1 |
| 1 | flat | gated | 0.000 | [0, 0] | [0, 0] | no | — | no-dispatch | 1 |
| 7 | flat | gated | 0.000 | [0, 0] | [0, 0] | no | — | no-dispatch | 1 |
| 13 | flat | gated | 0.000 | [0, 0] | [0, 0] | no | — | no-dispatch | 1 |
| 42 | flat | gated | 0.000 | [0, 0] | [0, 0] | no | — | no-dispatch | 1 |
| 99 | flat | gated | 0.000 | [0, 0] | [0, 0] | no | — | no-dispatch | 1 |
| 2024 | flat | gated | 0.000 | [0, 0] | [0, 0] | no | — | no-dispatch | 1 |
| 1 | curr | ungated | 0.520 | [0, 0] | [0, 0] | no | k1 | other | 1 |
| 7 | curr | ungated | 0.520 | [0, 0] | [0, 0] | no | k1 | round-robin-like | 1 |
| 13 | curr | ungated | 0.760 | [2, 2] | [2, 2] | no | k1 | batched/priority-like | 18 |
| 42 | curr | ungated | 0.360 | [0, 0] | [0, 0] | no | k1 | starving | 1 |
| 99 | curr | ungated | 0.280 | [0, 0] | [0, 0] | no | k1 | starving | 1 |
| 2024 | curr | ungated | 0.700 | [2, 2] | [2, 2] | no | k1 | batched/priority-like | 18 |
| 1 | flat | ungated | 0.520 | [0, 0] | [0, 0] | no | — | other | 1 |
| 7 | flat | ungated | 0.360 | [0, 0] | [0, 0] | no | — | starving | 1 |
| 13 | flat | ungated | 0.280 | [0, 0] | [0, 0] | no | — | batched/priority-like | 1 |
| 42 | flat | ungated | 0.280 | [0, 0] | [0, 0] | no | — | starving | 1 |
| 99 | flat | ungated | 0.360 | [0, 0] | [0, 0] | no | — | starving | 1 |
| 2024 | flat | ungated | 0.520 | [0, 0] | [0, 0] | no | — | other | 1 |

## Diagnosis

**0/24** bare runs reached 4/4 on full train+held-out.
k1 elites: entered 2/12, entered_protected 0/12, prot_cover 0/12, completed 0/12.
Curriculum jobs that promoted at least one stage: 0.
Stall histogram: k1=12, none=12
No 4/4 elites, so no non-RR/RTC discovery to report.

Gated vs ungated: ungated still sits on the round-3 **R=0.52** RUNCTX-without-MPU plateau (curr mean 0.523); two ungated curriculum seeds reach 2/4 complete on the 4-guest split but never promote k1. Gated **removes that plateau** (flat gated 0/6 at R=0.000, all `no-dispatch`). The only gated nonzero is curr seed 99 (R=0.400, 1/4 complete, `entered_protected=1`, prot_cover on one slot, archive size 6) — cover then run, the intended stepping stone, not 4/4. Most gated archives have size 1 (idle cell): random bytecode almost never installs a covering MPU range, and once unprotected RUNCTX scores 0 there is no remaining gradient except cover.

