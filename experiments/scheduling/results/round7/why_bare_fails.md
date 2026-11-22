# Why bare-bits discovery fails

Rounds 3–6 searched random 5-tuples (bare pack) with four methods: UCB1, curriculum+shaping, learned GRU edits, and lexicase+dataflow. Combined 4/4 count on the gated 4-guest mix: **1/42** (round 3, ungated flat, did not replicate) then **0/24 + 0/12 + 0/12**. The bottleneck is the **cover** event: `ADDPROT` must contain a guest's `[DATA_BASE, DATA_BOUND)`.

## Analytic density

Calling-convention family size (length-4 MMU gadget): **840** (4 ctx ids × P(7,3) distinct regs). Guest windows: `[[256, 384], [384, 512], [512, 640], [640, 768]]`.

Effective per-instruction alphabet used by `random_instr` ≈ `6.35e+05` (31 ops × 8³ regs × 40 immediates). Four-instruction space ≈ `1.62e+23`.

P(a uniform length-4 block **is** the gadget) ≈ `5.17e-21`.
P(a length-24 random program contains that block) ≈ `1.09e-19`.
Expected evals to first such block: **9.21e+18**.
If four independent copies were required for 4/4: **7.2e+75** evals.

A loop over `N_CONTEXTS` (the `insert_ctx_scan` gadget) is **not** in the bare pack. Bare search must invent that five-instruction scan as well as the MMU calling convention.

## Empirical density (this run)

Random bare length-24 on k1 (20000 programs): cover hits **0**, p̂=`0`, E[evals]=`inf` (cover+run **0**).

Random bare length-24 on 4-guest train (10000 programs): cover hits **0**, p̂=`0`, E[evals]=`inf`.

Single-lineage bare mutate (10000 evals): cover hits **0**, first hit at eval `None`, p̂=`0`.

Restricted length-4 `MOVI|CTXGET|ADDPROT` (20000 programs): cover hits **1**, p̂=`5e-05`, E[evals]=`20000.0`. Even inside this three-opcode subset, cover needs the exact CTXGET field pair (`DATA_BASE` then `DATA_BOUND`) and matching registers — the 840-member family.

## Observed search (round 6, real numbers)

12 jobs × 320000 evals = **3840000**. Cover events: **1** (blind seed 99, k4_hard gen 229, ≈270656 evals into that seed). Lexicase cover events: **0**. 4/4: **0**.
Sweep p̂ = `2.6e-07` (1/3840000).

Budget for 50% chance of a 4/4 if four covers were independent at that p̂: **1.51e+26** evals. They are not independent — 4/4 also needs a scan/unroll and RUNCTX after cover — so this is a **lower** bound on the remaining search. Round-2 gadgets reach 4/4 because `insert_mmu_gadget` *is* the 840-member family.

## Close

Bare-substrate discovery is closed. Further budget on random 5-tuples does not create a gradient on cover (lexicase cases are 0 for everyone until the event). The main line moves to Condition **C-hw**: the hardware-manual (gadget) pack, with a richer environment so *organization* (placement, eviction, routing) is not given.
