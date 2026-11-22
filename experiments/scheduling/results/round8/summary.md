# Round 8: C-hw routing curriculum

Wall time **1798.7s** (30.0 min), eval_workers=4.
ISA: opcodes 0–31 unchanged; 32–34 are BLKRD/BLKWR/SVC. No pipe / message-queue opcode.
Opcode count: **35**.
gens_per_stage=2500 pop=48 seeds=[1, 7, 13, 42, 99, 2024].

Boot-track embed: [`embed/`](embed/) + [`evoos/export/FORMAT.md`](../../../../evoos/export/FORMAT.md).

## Baselines

| stage | name | train completed | delivered | msg_ok | style | 4/4 |
| --- | --- | --- | --- | --- | --- | --- |
| r1_pair | payload_copy_rr | [2] | 1 | [True] | copy-supervisor | True |
| r1_pair | fifo_swap_rr | [2] | 1 | [True] | copy-supervisor | True |
| r2_order | payload_copy_rr | [2] | 1 | [True, True, True] | copy-supervisor | True |
| r2_order | fifo_swap_rr | [1] | 0 | [True, False, False] | copy-supervisor | False |
| r3_pairs | payload_copy_rr | [6] | 3 | [True, True, True] | copy-supervisor | True |
| r3_pairs | fifo_swap_rr | [6] | 3 | [True, True, True] | disk-buffer | True |
| r4_pressure | payload_copy_rr | [8, 8] | 4 | [True, True, True, True] | copy-supervisor | True |
| r4_pressure | fifo_swap_rr | [8, 8] | 4 | [True, True, True, True] | disk-buffer | True |

## Condition C-hw routing search

| arm | search R mean ± std (best) | 4/4 | r4 delivered mean (best) | mean n_eval |
| --- | --- | --- | --- | --- |
| route_curriculum | 0.912 ± 0.055 (0.960) | **0/6** | 2.83 (4) | 185912 |

| seed | R | r1 | r2 n_ok | r3 | r4 train/held | mix | style | shared | stall |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.960 | 0 | 1 | 3 | 4/3 | 2 | shared-window | True | None |
| 7 | 0.840 | 1 | 1 | 2 | 2/2 | 1 | shared-window | True | None |
| 13 | 0.940 | 1 | 1 | 3 | 3/3 | 2 | shared-window | True | None |
| 42 | 0.960 | 1 | 1 | 3 | 4/3 | 2 | shared-window | True | None |
| 99 | 0.830 | 1 | 3 | 2 | 2/2 | 1 | shared-window | True | None |
| 2024 | 0.940 | 1 | 3 | 2 | 2/2 | 1 | shared-window | True | None |

Styles: ['shared-window']. Orgs: ['blk-user+shared-window', 'fifo-swap-like+shared-window']. Shared-window elites: **6/6**. Non-copy styles: **6/6**.

Search evals: **1115472** total, mean 185912/seed. r1/r2 early-stopped once a pair completed (12–167 gens); r4 always ran the 2500-gen cap. Correctness mode skipped novelty k-NN. Isolation: **6/6**.

## Delivered vs baseline

payload_copy_rr (direct copy through a supervisor mailbox): r1=1/1, r2=3/3, r3=3/3, r4=4/4, style=`copy-supervisor`.
fifo_swap_rr (1-word mailbox + swap): r1=1/1, r2=1/3 (word0 only), r3=3/3, r4=4/4. Style is `copy-supervisor` when the consumer stays mapped, `disk-buffer` on r3/r4 because the consumer's payload is observed on `home_block` after a FIFO evict — not a dedicated message queue.
Round-7 evolved best: **1/4** delivered on four pairs.

Evolved (final elite, not in-stage): r1 **5/6**, r2 order **2/6** at 3/3, r3 **3/6** at 3/3, r4 train **2/6** at 4/4 (mean 2.83), r4≥3 **3/6**.
`four_of_four` here is evaluate_split on `[route_4, mix]` train **and** held (all guests complete + isolation), not “4 messages delivered”. Mix guests do not all finish, so **0/6** even when r4 messages are 4/4.

## Curriculum stages (in-search, before the final elite specialized)

| seed | stage | gens | delivered | n_msg_ok | style | promoted | isol |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | r1_pair | 16 | 1.0 | 1 | shared-window | True | True |
| 1 | r2_order | 2 | 1.0 | 3 | shared-window | True | True |
| 1 | r3_pairs | 307 | 3.0 | 3 | shared-window | True | True |
| 1 | r4_pressure | 2500 | 4.0 | 4 | shared-window | True | True |
| 7 | r1_pair | 13 | 1.0 | 1 | shared-window | True | True |
| 7 | r2_order | 6 | 1.0 | 3 | shared-window | True | True |
| 7 | r3_pairs | 2500 | 2.0 | 2 | shared-window | True | True |
| 7 | r4_pressure | 2500 | 2.0 | 3 | shared-window | True | True |
| 13 | r1_pair | 17 | 1.0 | 1 | shared-window | True | True |
| 13 | r2_order | 1 | 1.0 | 3 | shared-window | True | True |
| 13 | r3_pairs | 2500 | 2.0 | 2 | shared-window | True | True |
| 13 | r4_pressure | 2500 | 3.0 | 3 | shared-window | True | True |
| 42 | r1_pair | 16 | 1.0 | 1 | shared-window | True | True |
| 42 | r2_order | 3 | 1.0 | 3 | shared-window | True | True |
| 42 | r3_pairs | 46 | 3.0 | 3 | shared-window | True | True |
| 42 | r4_pressure | 2500 | 4.0 | 4 | shared-window | True | True |
| 99 | r1_pair | 85 | 1.0 | 1 | shared-window | True | True |
| 99 | r2_order | 167 | 1.0 | 3 | shared-window | True | True |
| 99 | r3_pairs | 2500 | 2.0 | 2 | shared-window | True | True |
| 99 | r4_pressure | 2500 | 2.0 | 2 | shared-window | True | True |
| 2024 | r1_pair | 12 | 1.0 | 1 | shared-window | True | True |
| 2024 | r2_order | 8 | 1.0 | 3 | shared-window | True | True |
| 2024 | r3_pairs | 40 | 3.0 | 3 | shared-window | True | True |
| 2024 | r4_pressure | 2500 | 3.0 | 3 | shared-window | True | True |

Every seed promoted all four stages. In-search r2 was 3/3 messages for all six; the r4 elite kept that ordering on seeds 99 and 2024 only. Seed 1's r4 elite scores r1=0: it is specialized to 8 contexts (2-guest sandbox does not map the same windows).

## Did anything unlike direct copy emerge?

**Yes. All six elites are `shared-window`**, not copy-through-supervisor. They `CTXSET` overlapping `[DATA_BASE, DATA_BOUND)` and `ADDPROT` the same range to two guest ids. The sandbox treats dual-granted overlap as a share, not a leak. MAP-Elites `route_cell` keeps style as an axis; equal-R replacement prefers non-copy.

Organizations:

- `blk-user+shared-window` — 5/6 (BLKRD into frames, little/no eviction, dual ADDPROT).
- `fifo-swap-like+shared-window` — 1/6 (seed 99; BLKWR plus shared windows; only seed that keeps r2=3/3 and a swap-like label).

No evolved elite labeled `copy-supervisor` or `batch`. Disk-buffer appears on the fifo **baseline** under pressure, not on search elites.

### Best elite `route_s42` style=`shared-window` org=`blk-user+shared-window`

r1=1 r2=[True, False, False] r3=3 r4=4/3 mix=2 shared=True origin=modify_imm len=128.

Dual ADDPROT on the same frame (ctx 2 and ctx 3, words 288–320):

```
  67:  MOVI     a=5 b=0 c=0 imm=288
  68:  CTXSET   a=2 b=5 c=0 imm=4
  69:  MOVI     a=6 b=0 c=0 imm=320
  70:  CTXSET   a=2 b=6 c=0 imm=5
  71:  ADDPROT  a=2 b=5 c=6 imm=0
  ...
  79:  CTXSET   a=3 b=5 c=0 imm=4
  80:  MOVI     a=6 b=0 c=0 imm=320
  81:  CTXSET   a=3 b=6 c=0 imm=5
  82:  ADDPROT  a=3 b=5 c=6 imm=0
```

Full disassembly:

```
   0:  MOVI     a=4 b=0 c=0 imm=5
   1:  RUNCTXI  a=4 b=0 c=0 imm=64
   2:  MOVI     a=2 b=0 c=0 imm=5
   3:  MOVI     a=3 b=0 c=0 imm=320
   4:  CTXSET   a=2 b=3 c=0 imm=4
   5:  MOVI     a=6 b=0 c=0 imm=352
   6:  CTXSET   a=2 b=6 c=0 imm=5
   7:  ADDPROT  a=2 b=3 c=6 imm=0
   8:  MOVI     a=2 b=0 c=0 imm=3
   9:  MOVI     a=7 b=0 c=0 imm=2
  10:  MOVI     a=1 b=0 c=0 imm=352
  11:  CTXSET   a=7 b=1 c=0 imm=4
  12:  MOVI     a=3 b=0 c=0 imm=384
  13:  CTXSET   a=7 b=3 c=0 imm=5
  14:  ADDPROT  a=7 b=1 c=3 imm=0
  15:  RUNCTXI  a=2 b=0 c=0 imm=256
  16:  MOVI     a=2 b=0 c=0 imm=1
  17:  NOP      a=0 b=0 c=0 imm=0
  18:  ADDI     a=2 b=2 c=0 imm=1
  19:  CTXGET   a=7 b=0 c=0 imm=6
  20:  MOVI     a=6 b=0 c=0 imm=6
  21:  MOVI     a=2 b=0 c=0 imm=320
  22:  CTXSET   a=6 b=2 c=0 imm=4
  23:  MOVI     a=4 b=0 c=0 imm=352
  24:  CTXSET   a=6 b=4 c=0 imm=5
  25:  ADDPROT  a=6 b=2 c=4 imm=0
  26:  BLT      a=2 b=7 c=0 imm=-4  # -> 23
  27:  BLKRD    a=0 b=1 c=4 imm=0
  28:  MOVI     a=5 b=0 c=0 imm=1
  29:  RUNCTXI  a=5 b=0 c=0 imm=12
  30:  ADDI     a=5 b=5 c=0 imm=1
  31:  CTXGET   a=7 b=0 c=0 imm=6
  32:  BLT      a=5 b=7 c=0 imm=-4  # -> 29
  33:  MOVI     a=3 b=0 c=0 imm=5
  34:  MOVI     a=5 b=0 c=0 imm=256
  35:  MOVI     a=1 b=0 c=0 imm=6
  36:  RUNCTXI  a=1 b=0 c=0 imm=128
  37:  BLKRD    a=0 b=3 c=5 imm=0
  38:  MOVI     a=4 b=0 c=0 imm=256
  39:  MOVI     a=1 b=0 c=0 imm=1
  40:  MOVI     a=4 b=0 c=0 imm=0
  41:  MOVI     a=7 b=0 c=0 imm=288
  42:  BLKWR    a=4 b=7 c=0 imm=925717
  43:  MOVI     a=2 b=0 c=0 imm=5
  44:  RUNCTXI  a=2 b=0 c=0 imm=64
  45:  MOVI     a=5 b=0 c=0 imm=4
  46:  CTXSET   a=5 b=2 c=0 imm=4
  47:  MOVI     a=1 b=0 c=0 imm=384
  48:  CTXSET   a=5 b=1 c=0 imm=5
  49:  ADDPROT  a=5 b=2 c=1 imm=0
  50:  MOVI     a=7 b=0 c=0 imm=1
  51:  MOVI     a=6 b=0 c=0 imm=352
  52:  CTXSET   a=7 b=6 c=0 imm=4
  53:  MOVI     a=5 b=0 c=0 imm=384
  54:  CTXSET   a=7 b=5 c=0 imm=5
  55:  BLKRD    a=0 b=7 c=6 imm=0
  56:  BEQ      a=5 b=2 c=2 imm=2  # -> 59
  57:  CTXGET   a=6 b=7 c=7 imm=27
  58:  MOVI     a=6 b=0 c=0 imm=3
  59:  RUNCTX   a=7 b=6 c=5 imm=0
  60:  CTXGET   a=3 b=0 c=0 imm=14
  61:  MOVI     a=5 b=0 c=0 imm=384
  62:  MOVI     a=1 b=0 c=0 imm=4
  63:  RUNCTXI  a=1 b=0 c=0 imm=64
  64:  ADDI     a=4 b=3 c=4 imm=2
  65:  ADDPROT  a=7 b=6 c=5 imm=0
  66:  MOVI     a=2 b=0 c=0 imm=8
  67:  MOVI     a=5 b=0 c=0 imm=288
  68:  CTXSET   a=2 b=5 c=0 imm=4
  69:  MOVI     a=6 b=0 c=0 imm=320
  70:  CTXSET   a=2 b=6 c=0 imm=5
  71:  ADDPROT  a=2 b=5 c=6 imm=0
  72:  CTXGET   a=3 b=0 c=0 imm=14
  73:  MOVI     a=3 b=0 c=0 imm=4
  74:  CTXGET   a=3 b=0 c=0 imm=14
  75:  MOVI     a=3 b=0 c=0 imm=7
  76:  MOVI     a=5 b=0 c=0 imm=288
  77:  MOVI     a=7 b=0 c=0 imm=1
  78:  RUNCTXI  a=7 b=0 c=0 imm=256
  79:  CTXSET   a=3 b=5 c=0 imm=4
  80:  MOVI     a=6 b=0 c=0 imm=320
  81:  CTXSET   a=3 b=6 c=0 imm=5
  82:  ADDPROT  a=3 b=5 c=6 imm=0
  83:  CTXGET   a=4 b=0 c=0 imm=15
  84:  RUNCTXI  a=3 b=0 c=0 imm=16
  85:  RUNCTXI  a=2 b=0 c=0 imm=32
  86:  RUNCTXI  a=3 b=0 c=0 imm=64
  87:  RUNCTXI  a=4 b=3 c=1 imm=-2
  88:  ADDI     a=7 b=1 c=5 imm=17
  89:  MOVI     a=2 b=0 c=0 imm=1
  90:  MOVI     a=6 b=0 c=0 imm=2
  91:  RUNCTXI  a=6 b=0 c=0 imm=128
  92:  CTXGET   a=1 b=2 c=0 imm=4
  93:  CTXGET   a=6 b=2 c=0 imm=5
  94:  MOVI     a=3 b=0 c=0 imm=1
  95:  RUNCTXI  a=3 b=0 c=0 imm=256
  96:  MOVI     a=7 b=0 c=0 imm=3
  97:  CTXGET   a=3 b=0 c=0 imm=14
  98:  CTXGET   a=6 b=0 c=0 imm=15
  99:  MOVI     a=6 b=0 c=0 imm=256
 100:  BLKRD    a=0 b=7 c=6 imm=0
 101:  BEQ      a=5 b=2 c=2 imm=2  # -> 104
 102:  CTXGET   a=6 b=7 c=7 imm=27
 103:  MOVI     a=6 b=0 c=0 imm=3
 104:  MOVI     a=3 b=0 c=0 imm=256
 105:  CTXSET   a=6 b=3 c=0 imm=4
 106:  MOVI     a=2 b=0 c=0 imm=288
 107:  CTXSET   a=6 b=2 c=0 imm=5
 108:  ADDPROT  a=6 b=3 c=2 imm=0
 109:  CTXSET   a=7 b=5 c=0 imm=5
 110:  MOVI     a=4 b=0 c=0 imm=6
 111:  RUNCTXI  a=4 b=0 c=0 imm=8
 112:  MOVI     a=4 b=0 c=0 imm=4
 113:  MOVI     a=2 b=0 c=0 imm=256
 114:  CTXSET   a=4 b=2 c=0 imm=4
 115:  MOVI     a=6 b=0 c=0 imm=288
 116:  CTXSET   a=4 b=6 c=0 imm=5
 117:  ADDPROT  a=4 b=2 c=6 imm=0
 118:  MOVI     a=2 b=0 c=0 imm=3
 119:  CTXGET   a=1 b=2 c=0 imm=4
 120:  CTXGET   a=5 b=2 c=0 imm=5
 121:  ADDPROT  a=2 b=1 c=5 imm=0
 122:  SHL      a=5 b=1 c=6 imm=640
 123:  RUNCTXI  a=2 b=7 c=7 imm=32
 124:  ADDPROT  a=0 b=1 c=1 imm=5
 125:  AND      a=7 b=6 c=2 imm=0
 126:  BLKRD    a=0 b=4 c=2 imm=0
 127:  RUNCTXI  a=2 b=0 c=0 imm=256
```

## Boot-track embed

Format `evoos-embed-v1`: lossless JSON 5-tuples plus a `.bin` quintuple stream (`u32 magic=0x45564F53`, `u16 version`, `u16 n_instr`, then `i32×5` per instruction). Cases are the routing curriculum workloads plus the original 4-guest mix. Spec: [`evoos/export/FORMAT.md`](../../../../evoos/export/FORMAT.md). Elites: [`embed/route_s{seed}.json`](embed/) and `.bin`.

