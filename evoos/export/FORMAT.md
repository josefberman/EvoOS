# EvoOS embed format (`evoos-embed-v1`)

For the boot-track ISO stub: load a supervisor elite and the evaluator cases
without inventing opcodes.

## Canonical program

JSON field `supervisor.instrs` is a list of 5-tuples `[op, a, b, c, imm]`.
Integers are signed 32-bit. This is **lossless**. Do not use `words_lossy`
(12-bit immediate pack) as the source of truth.

Opcode names: `isa.names[op]`. Current freeze: `HALT=0` … `RDFAULT=31`,
`BLKRD=32`, `BLKWR=33`, `SVC=34`. Privileged: RUNCTX/I, CTXGET/SET, ADDPROT,
CLRPROT, BLKRD, BLKWR. `SVC` is unprivileged.

## Binary companion (`.bin`)

Little-endian:

```
u32 magic = 0x45564F53  /* 'EVOS' */
u16 version = 1
u16 n_instr
repeat n_instr:
    i32 op, a, b, c, imm
```

`python -c "from evoos.export.embed import unpack_bin; unpack_bin(open('elite.bin','rb').read())"`

## Machine block

The sandbox the elite was scored in. Boot stub should match:

- `n_contexts`, `mem_words`, `max_steps`
- `n_blocks`, `block_words`, `blk_latency`
- `lenient`: sticky_guest_fault=false, supervisor_halt_on_mem=false, runctx_posts_status=true

Firmware words 0..3 and guest table at 16 are the same as `evoos/vm/instruction_set.py`.

## Cases

Each case is a `Workload`: `kind` (`route` | `pressure` | `mix` | `compute`),
`guests[]` with `code` 5-tuples, `role` (`producer` | `consumer` | `compute`),
`messages`, `expected_off` (relative to `DATA_BASE`), `home_block`.

Delivery: consumer `expected_off[i] == mem[DATA_BASE+i]` (or disk `home_block`
if unmapped). Isolation is a hard fail.

## What the stub must not add

No `pipe`, `message_queue`, `allocate_page`, or `schedule_process` opcode.
Routing and swap, if present, are patterns in the 5-tuples.
