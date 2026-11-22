# Round 9: shared-window generalization + leak audit

Wall time **5370.9s** (89.5 min), eval_workers=4.
ISA: opcodes 0–31 unchanged; 32–34 are BLKRD/BLKWR/SVC. No pipe / message-queue opcode.
Opcode count: **35**.
generations=4000 pop=40 seeds=[1, 7, 13, 42, 99, 2024] n_seeds_in=6.

Boot-track embed: [`embed/`](embed/) + [`evoos/export/FORMAT.md`](../../../../evoos/export/FORMAT.md).

## Baselines (official 4/4 split = route_4 + mix, train AND held)

| name | 4/4 | r7 4/4 | train completed | held completed | audit pass | style |
| --- | --- | --- | --- | --- | --- | --- |
| payload_copy_rr | True | True | [8, 8] | [6, 8] | True | copy-supervisor |
| fifo_swap_rr | True | True | [8, 8] | [6, 8] | True | disk-buffer |

## Condition C-hw generalization search

| arm | search R mean ± std (best) | 4/4 split | r7 4/4 | audit pass | mean n_eval |
| --- | --- | --- | --- | --- | --- |
| gen_lexicase | 0.769 ± 0.057 (0.853) | **0/6** | **0/6** | **3/6** | 160000 |

| seed | R | 4/4 | r7 | r4 deliv t/h | mix t/h | shuf | style | org | audit | leak | revoked |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 0.683 | False | False | 1/0 | 5/5 | 4 | shared-window | blk-user+shared-window | True | False | False |
| 7 | 0.853 | False | False | 3/2 | 4/4 | 4 | disk-buffer | fifo-swap-like+disk-buffer | False | False | True |
| 13 | 0.768 | False | False | 1/1 | 3/3 | 4 | shared-window | blk-user+shared-window | True | False | False |
| 42 | 0.784 | False | False | 0/0 | 2/2 | 4 | none | blk-user+rr | False | False | False |
| 99 | 0.715 | False | False | 2/2 | 3/3 | 4 | shared-window | fifo-swap-like+shared-window | False | False | False |
| 2024 | 0.811 | False | False | 3/3 | 3/3 | 6 | shared-window | fifo-swap-like+shared-window | True | False | False |

Styles: ['disk-buffer', 'none', 'shared-window']. Orgs: ['blk-user+rr', 'blk-user+shared-window', 'fifo-swap-like+disk-buffer', 'fifo-swap-like+shared-window']. Shared-window: **4/6**. Official 4/4: **0/6**. Round-7 pressure+route 4/4: **0/6**. Audit pass (no third-guest grant, attackers contained): **3/6**. Any leak on official or audit: **3/6**.

4/4 split = route_4 + mix, train AND held, all guests complete + isolation. Round-7 4/4 = pressure_8 + route_4 train AND pressure_8 + route_3 held.
Leak = a context that is not the designated pair partner is granted the pair window (ADDPROT or overlapping DATA_*). Revocation (CLRPROT after delivery) is scored, not a hard fail.

Round-8 evolved 4/4 messages on two seeds but failed this 4/4 split and (under the new rule) leaked the window to extra ctx ids. Round 9 seeds those elites, adds shuffled/offset/order lexicase cases, and hard-fails third-guest grants.
Finding: **0/6** still on the all-guests 4/4 split. Leak-safe shared-window (audit **3/6**: seeds 1, 13, 2024) delivers fewer messages than leaky round-8 (1–3/4 vs 4/4). Seed 2024 is the strongest leak-safe router (r4 **3/3** train/held, shuffled **6/8** complete, mix 3/8). Seed 7 has the highest R (0.853) via fifo-swap-like+disk-buffer (r4 3/2, mix 4/8, CLRPROT=30, revoked) but fails the mix-store attacker case. Nobody completes the 8 compute guests under pressure (r7 4/4 **0/6**). Baselines still **1/1** on both 4/4 splits and the audit.

### Best elite `gen_s2024` 4/4=`False` style=`shared-window` org=`fifo-swap-like+shared-window`

diagnose: train completed [7, 3] held [6, 3]; near-zero-cycle guests (train idx) [[7], [7]]; label=round-robin-like

```
   0:  MOVI     a=4 b=0 c=0 imm=320
   1:  RUNCTXI  a=4 b=0 c=0 imm=63
   2:  MOVI     a=2 b=0 c=0 imm=6
   3:  MOVI     a=3 b=0 c=0 imm=320
   4:  CTXSET   a=2 b=3 c=0 imm=16
   5:  MOVI     a=6 b=0 c=0 imm=352
   6:  CTXSET   a=2 b=6 c=0 imm=5
   7:  ADDPROT  a=2 b=3 c=6 imm=640
   8:  MOVI     a=7 b=0 c=0 imm=2
   9:  MOVI     a=1 b=0 c=0 imm=352
  10:  CTXSET   a=7 b=1 c=0 imm=4
  11:  MOVI     a=3 b=0 c=0 imm=384
  12:  CTXSET   a=7 b=3 c=0 imm=5
  13:  ADDPROT  a=7 b=1 c=3 imm=0
  14:  RUNCTXI  a=2 b=0 c=0 imm=257
  15:  MOVI     a=7 b=0 c=0 imm=5
  16:  MOVI     a=1 b=0 c=0 imm=320
  17:  BLKRD    a=0 b=7 c=1 imm=1
  18:  RUNCTXI  a=4 b=0 c=0 imm=64
  19:  MOVI     a=2 b=0 c=0 imm=5
  20:  MOVI     a=3 b=0 c=0 imm=320
  21:  CTXSET   a=2 b=3 c=0 imm=4
  22:  MOVI     a=6 b=0 c=0 imm=351
  23:  CTXSET   a=2 b=6 c=0 imm=5
  24:  ADDPROT  a=2 b=3 c=6 imm=0
  25:  MOVI     a=7 b=0 c=0 imm=1
  26:  MOVI     a=1 b=0 c=0 imm=352
  27:  CTXSET   a=7 b=1 c=0 imm=4
  28:  MOVI     a=5 b=0 c=0 imm=384
  29:  RUNCTXI  a=5 b=0 c=0 imm=32
  30:  MOVI     a=3 b=0 c=0 imm=384
  31:  CTXSET   a=7 b=3 c=0 imm=5
  32:  ADDPROT  a=7 b=1 c=3 imm=0
  33:  RUNCTXI  a=2 b=0 c=0 imm=256
  34:  MOVI     a=6 b=0 c=0 imm=8
  35:  CTXGET   a=4 b=6 c=0 imm=128
  36:  CTXGET   a=3 b=6 c=0 imm=5
  37:  MOVI     a=6 b=0 c=0 imm=6
  38:  MOVI     a=4 b=0 c=0 imm=353
  39:  BLKRD    a=0 b=6 c=4 imm=9
  40:  ADDPROT  a=6 b=4 c=3 imm=0
  41:  MOVI     a=2 b=0 c=0 imm=1
  42:  NOP      a=0 b=0 c=0 imm=0
  43:  ADDI     a=2 b=2 c=0 imm=1
  44:  CTXGET   a=7 b=0 c=0 imm=6
  45:  MOVI     a=2 b=0 c=0 imm=320
  46:  CTXSET   a=6 b=2 c=0 imm=4
  47:  MOVI     a=4 b=0 c=0 imm=14
  48:  CTXSET   a=6 b=4 c=0 imm=15
  49:  ADDPROT  a=6 b=2 c=4 imm=0
  50:  BLT      a=2 b=7 c=0 imm=11  # -> 62
  51:  MOVI     a=3 b=0 c=0 imm=6
  52:  RUNCTXI  a=3 b=0 c=0 imm=4
  53:  BLKRD    a=0 b=1 c=4 imm=0
  54:  MOVI     a=5 b=0 c=0 imm=2
  55:  MOVI     a=3 b=0 c=0 imm=5
  56:  MOVI     a=2 b=0 c=0 imm=-2
  57:  RUNCTXI  a=2 b=0 c=0 imm=128
  58:  MOVI     a=4 b=0 c=0 imm=320
  59:  BLKWR    a=3 b=4 c=0 imm=387
  60:  RUNCTXI  a=5 b=0 c=0 imm=256
  61:  ADDPROT  a=2 b=5 c=6 imm=1
  62:  CTXGET   a=3 b=0 c=0 imm=14
  63:  MOVI     a=3 b=0 c=0 imm=4
  64:  CTXGET   a=3 b=0 c=0 imm=14
  65:  MOVI     a=3 b=0 c=0 imm=7
  66:  MOVI     a=5 b=0 c=0 imm=288
  67:  MOVI     a=7 b=0 c=0 imm=1
  68:  RUNCTXI  a=7 b=0 c=0 imm=256
  69:  CTXSET   a=3 b=5 c=0 imm=4
  70:  MOVI     a=6 b=0 c=0 imm=320
  71:  CTXSET   a=3 b=6 c=0 imm=5
  72:  ADDPROT  a=3 b=5 c=6 imm=-4
  73:  CTXGET   a=4 b=0 c=0 imm=15
  74:  RUNCTXI  a=3 b=0 c=0 imm=16
  75:  RUNCTXI  a=2 b=0 c=0 imm=32
  76:  RUNCTXI  a=3 b=0 c=0 imm=64
  77:  RUNCTXI  a=4 b=3 c=1 imm=256
  78:  ADDI     a=7 b=1 c=5 imm=17
  79:  MOVI     a=2 b=0 c=0 imm=1
  80:  MOVI     a=6 b=0 c=0 imm=2
  81:  RUNCTXI  a=6 b=0 c=0 imm=128
  82:  CTXGET   a=1 b=2 c=0 imm=4
  83:  CTXGET   a=6 b=2 c=0 imm=5
  84:  MOVI     a=3 b=0 c=0 imm=4
  85:  RUNCTXI  a=3 b=0 c=0 imm=256
  86:  MOVI     a=7 b=0 c=0 imm=3
  87:  CTXGET   a=3 b=0 c=0 imm=14
  88:  CTXGET   a=6 b=0 c=0 imm=15
  89:  MOVI     a=6 b=0 c=0 imm=257
  90:  BLKRD    a=0 b=7 c=6 imm=-4
  91:  BEQ      a=5 b=2 c=2 imm=2  # -> 94
  92:  CTXGET   a=6 b=7 c=7 imm=27
  93:  MOVI     a=6 b=0 c=0 imm=3
  94:  MOVI     a=3 b=0 c=0 imm=256
  95:  CTXSET   a=6 b=3 c=0 imm=4
  96:  MOVI     a=2 b=0 c=0 imm=288
  97:  CTXSET   a=6 b=2 c=0 imm=6
  98:  ADDPROT  a=6 b=3 c=2 imm=0
  99:  CTXSET   a=7 b=5 c=0 imm=5
 100:  MOVI     a=4 b=0 c=0 imm=7
 101:  RUNCTXI  a=4 b=0 c=0 imm=8
 102:  MOVI     a=4 b=0 c=0 imm=4
 103:  MOVI     a=2 b=0 c=0 imm=252
 104:  CTXSET   a=4 b=2 c=0 imm=4
 105:  MOVI     a=6 b=0 c=0 imm=288
 106:  CTXSET   a=4 b=6 c=0 imm=5
 107:  ADDPROT  a=4 b=2 c=6 imm=1
 108:  NOP      a=0 b=6 c=6 imm=128
 109:  BEQ      a=4 b=3 c=4 imm=11  # -> 121
 110:  RUNCTXI  a=7 b=3 c=1 imm=10
 111:  RUNCTXI  a=3 b=0 c=0 imm=16
 112:  BLKRD    a=0 b=3 c=5 imm=388
 113:  MOVI     a=4 b=0 c=0 imm=4
 114:  MOVI     a=2 b=0 c=0 imm=256
 115:  CTXSET   a=4 b=2 c=0 imm=4
 116:  MOVI     a=6 b=0 c=0 imm=288
 117:  CTXSET   a=4 b=6 c=0 imm=5
 118:  ADDPROT  a=4 b=2 c=6 imm=0
 119:  NOP      a=0 b=6 c=6 imm=4
 120:  BEQ      a=4 b=3 c=4 imm=11  # -> 132
 121:  RUNCTXI  a=7 b=3 c=1 imm=0
 122:  JR       a=0 b=1 c=2 imm=-4
 123:  BLT      a=5 b=4 c=4 imm=640  # -> 764
 124:  MOV      a=5 b=6 c=3 imm=128
 125:  RUNCTXI  a=5 b=5 c=0 imm=25
 126:  RUNCTXI  a=3 b=3 c=3 imm=640
 127:  CTXSET   a=0 b=6 c=2 imm=925717
```

