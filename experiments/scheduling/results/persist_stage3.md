# Stage 3 — what landed on the device

## Hardware added (encoding v2)

Opcodes 0–31 unchanged. New:

| op | name | role |
| --- | --- | --- |
| 32 | BLKRD | privileged copy of one device block into memory |
| 33 | BLKWR | privileged copy of memory onto one device block |
| 34 | SVC | unprivileged yield; request code in a register |

Context fields 11–15: LAST_BLK_STATUS, N_BLOCKS, BLK_WORDS, LAST_SVC, LAST_SVC_CTX.
Guest contract: PUT=1 / GET=2 with window `[key, n, payload…]`. Isolation is a
hard fail. Firmware on QEMU plumbs ATA PIO (`0x1F0`); it does not choose layout.

`insert_blk_gadget` is a calling-convention prior (MOVI block; MOVI addr;
BLKRD/BLKWR), documented as such.

## Search (gadgets pack, persist_rr + s99 + s42 + random)

Best reward **1.15** is still **persist_rr** (human Condition A).

Human layout: `block = (ctx-1)*8 + (key & 7)`. Two guests storing key `1`
land on blocks **1** and **9**. `classify_disk` labels that **sparse-hash-like**
because the occupied indices are few and far apart. It is not a hash table
discovered by search. It is a strided slot table a human wrote.

That blob was **not** copied to `persist_gadgets_elite.txt`. Shipping it as
“the evolved OS” would repeat the rr_live mistake.

QD cells:

| layout | correct | origin | note |
| --- | --- | --- | --- |
| sparse-hash-like | 2/2 | persist_rr | human seed; isolation ok |
| append-log-like | 1/2 | modify_operand on persist_rr | broke the stride; one guest’s value lost |
| empty | 0/2 | random | wrote a block, recalled nothing |

rr_live: 0/2, empty device, no leak (also no store).

Crash (drop next write): persist_rr isolation still holds; one of two values
missing. Corrupt block 0: persist_rr still 2/2 (it never used block 0 for
these keys).

## Failures

- No evolved program beat persist_rr on this reward. Mutating the slot formula
  tended to collapse two guests onto adjacent blocks (`append-log-like`) and
  lose isolation or a value.
- s99 does not handle LAST_SVC. Default ISO stays s99 so `make test-qemu`
  remains a live scheduler + `#PF` test.
- Persist HITL uses `LIVE_SUPERVISOR=persist_rr` until search produces a
  non-human elite that is 2/2 after restart **and** still RUNCTXs a
  keyboard-injected `#PF` guest.
- Python device is 16 words; ATA sectors are 512 bytes (first 64 bytes used).

## Honest label

Nothing log-structured emerged as a working OS. The only 2/2 organization is
the human stride table, which a heuristic calls sparse-hash-like. Stage 3
delivered the substrate, the contract, the reward, HITL reboot, and a QD
archive of failures. The OS of record for the default ISO is still s99.
