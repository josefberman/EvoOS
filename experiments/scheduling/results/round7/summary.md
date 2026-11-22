# Round 7: close bare-bits; Condition C-hw

Wall time **11891.7s** (198.2 min), eval_workers=4.
ISA: opcodes 0–31 unchanged; 32–34 are BLKRD/BLKWR/SVC from the persist-blk boot track. No pager, pipe, or schedule_process opcode.
Opcode count: **35** (`HALT`=0 … `SVC`=34).

Bare-line close: [`why_bare_fails.md`](why_bare_fails.md).

Command:

```text
python -m evoos.experiments.scheduling.sweep_round7 \
  --generations 4000 --population 64 --eval-workers 4
```

**256000** evals/seed (4000 × 64), 6 seeds `{1, 7, 13, 42, 99, 2024}`, pack `hw`, QD + epsilon-lexicase + dataflow, seeded from 6 round-2 gadget elites. Checkpointed under `checkpoints/`.

## Condition A / A-hw

| suite | name | train completed | held completed | 4/4 | held R |
| --- | --- | --- | --- | --- | --- |
| old4 | run_to_completion | [4, 4] | [4, 4] | True | 1.000 |
| old4 | round_robin_q16 | [4, 4] | [4, 4] | True | 1.000 |
| old4 | selfish_first | [1, 1] | [1, 1] | False | 0.400 |
| hw | fifo_swap_rr | [8, 8] | [8, 6] | True | 1.000 |
| hw | direct_copy_router | [8, 8] | [8, 6] | True | 1.000 |
| hw | place_first_frames_rr | [4, 4] | [4, 4] | False | 0.667 |
| hw | fifo_swap_deaf | [8, 4] | [8, 3] | False | 0.840 |

`fifo_swap_rr` is 4 RAM frames, FIFO victim via BLKWR/BLKRD, RR `RUNCTXI`, and a PUT→GET copy through `LAST_SVC`. `direct_copy_router` is the same program (routing is that copy, not a pipe opcode). `place_first_frames_rr` never evicts: 4/8 pressure. `fifo_swap_deaf` swaps but ignores SVC: 8/8 pressure, producers halt, consumers empty.

## Condition C-hw (hardware-manual pack, pressure+route)

Pack `hw` = gadgets + ctx-scan + BLKRD/BLKWR + window CTXSET + SVC-status snippets. Honestly framed as the hardware manual, not bare bits. Environment: 8 guests / 4 RAM frames + block device; 4 producer/consumer pairs via SVC PUT/GET. QD MAP-Elites cell `(completed, blk_writes, svc+delivered, switch_bin, isolation)` + epsilon-lexicase.

| arm | search R mean ± std (best) | 4/4 train+held | mean n_eval |
| --- | --- | --- | --- |
| hw_lexicase | 0.530 ± 0.040 (0.596) | **0/6** | 256000 |

Route `delivered` below is consumers with the expected token (re-evaluated on `route_4_train`), not the pressure-workload column.

| seed | R | train completed | held | org (pressure) | org (route) | blk_w | route delivered | isol | archive |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.472 | [2, 5] | [2, 3] | fifo-swap-like+rr | fifo-swap-like+router | 57 | 1/4 | pass | 2379 |
| 7 | 0.553 | [1, 5] | [1, 4] | fifo-swap-like+rr | fifo-swap-like+router | 122 | 1/4 | **fail** | 2888 |
| 13 | 0.507 | [3, 5] | [3, 4] | fifo-swap-like+rr | fifo-swap-like+router | 39 | 1/4 | pass | 2445 |
| 42 | 0.509 | [4, 4] | [4, 4] | blk-user | blk-user+router | 2 | 1/4 | pass | 3855 |
| 99 | 0.596 | [4, 5] | [4, 4] | fifo-swap-like+rr | fifo-swap-like+router | 7 | 1/4 | pass | 3724 |
| 2024 | 0.545 | [3, 4] | [3, 3] | fifo-swap-like+rr | fifo-swap-like+rr | 210 | 0/4 | pass | 2126 |

Organizations observed: `fifo-swap-like+rr` (5/6) and `blk-user` (seed 42).

### Versus hand-written baselines

| | pressure train | route train delivered | isolation |
| --- | --- | --- | --- |
| fifo_swap_rr / direct_copy | 8/8 | 4/4 | pass |
| place_first_frames_rr | 4/8 | 2/4 | pass |
| fifo_swap_deaf | 8/8 | 0/4 | pass |
| best evolved (seed 99) | 4/8 | 1/4 | pass |
| seed 42 (blk-user) | 4/8 | 1/4 | pass |
| seed 2024 (heavy swap) | 3/8 | 0/4 | pass |

Search invents **placement** (CTXSET DATA_BASE/BOUND + ADDPROT + BLKRD) and two eviction styles: many BLKWR (seeds 1, 7, 13, 2024) vs almost none (seed 42, same 4/8 ceiling as place-first). It does not invent a complete FIFO clock: nobody finishes guests 5–8 on pressure except by starving others. Routing is a **single-pair** copy (`route_4_train.delivered` first solved gens 2–57) vs the baseline's four pairs.

Cases solved first (every seed, gen 0): `isolation`, `nofault`, `blk`, and several `.cover`. `complete_p` on pressure guests appears by gen 2–73 for the first guest; later guests stretch to hundreds of generations (seed 13 g2 at 541, seed 2024 g3 at 1395).

### Elite disassembly pointers

- `hw_lex_s99` org=`fifo-swap-like+rr` origin=`modify_imm` → [`examples/hw_lex_s99.txt`](examples/hw_lex_s99.txt)
- `hw_lex_s42` org=`blk-user` origin=`modify_op` → [`examples/hw_lex_s42.txt`](examples/hw_lex_s42.txt)
- others: [`examples/`](examples/)

Best elite `hw_lex_s99` (4/8 pressure, 5/8 route, 8/8 resident, 7 BLKWR). Window installs at 256/288/320/352, BLKRD/BLKWR, RUNCTXI 27 and 127:

```
   0:  CTXSET   a=1 b=5 c=0 imm=5
   1:  ADDPROT  a=1 b=3 c=5 imm=0
   2:  BLKWR    a=4 b=3 c=4 imm=320
   3:  MOVI     a=4 b=0 c=0 imm=3
   6:  MOVI     a=5 b=0 c=0 imm=352
   7:  CTXSET   a=3 b=5 c=0 imm=4
   8:  MOVI     a=4 b=0 c=0 imm=384
   9:  CTXSET   a=3 b=4 c=0 imm=5
  10:  ADDPROT  a=3 b=5 c=4 imm=0
  15:  MOVI     a=4 b=0 c=0 imm=256
  16:  CTXSET   a=5 b=4 c=0 imm=4
  28:  BLKRD    a=0 b=2 c=1 imm=0
  41:  BLKRD    a=0 b=4 c=3 imm=14
  61:  RUNCTXI  a=1 b=1 c=5 imm=27
  68:  RUNCTXI  a=7 b=0 c=0 imm=127
```

Seed 42 (`blk-user`) starts with `CTXGET LAST_SVC` / `LAST_SVC_CTX` (svc-status gadget) and almost no BLKWR — place-first organization with a weak router.

## Close

Bare-bits is closed on density grounds (see `why_bare_fails.md`). C-hw moves the question to organization given the hardware manual. At 256k evals/seed the archive fills with swap-like and place-first cells; 4/4 against the FIFO/copy baselines is still open. Next lever is the remaining policy (victim selection, PUT/GET copy for all pairs), not a new opcode.
