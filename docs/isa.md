# EvoOS ISA — frozen binary format

The research VM in `evoos/vm/` is the specification. This document freezes the
**on-disk encoding**. Semantics are unchanged: the same five integer fields
the Python `Instr` tuple already used.

The OS is a supervisor program in this ISA. On the search VM a C/Python
interpreter executes these words. On the bootable ISO the same words are
**AOT-compiled to native x86_64** and run in ring 0; firmware is only boot
plus a trap trampoline.

## Version

Encoding version **1** (five `i32` words, opcodes 0–31) is unchanged.
**ISA profile 2** (this document's hardware section) grounds privileged
ops in real x86-64. Search still mutates version-1 programs; the AOT
compiler and HITL evaluator consume the same blob.

## Instruction

Each instruction is five little-endian signed 32-bit integers:

| word | name | meaning |
| --- | --- | --- |
| 0 | `op` | opcode, `0..31` |
| 1 | `a` | register 0..7 |
| 2 | `b` | register 0..7 |
| 3 | `c` | register 0..7 |
| 4 | `imm` | signed immediate, `-(2^20) .. 2^20-1` |

No Python objects. JSON `[[op,a,b,c,imm], ...]` is a debug view of the same
integers. `encode_word()` in Python packs a *lossy* 32-bit dump (12-bit imm)
and is not the frozen format.

Registers are 32-bit two's complement. `r0` ignores writes. Data memory is
word-addressed signed 32-bit cells.

## Opcodes

| op | name | form |
| --- | --- | --- |
| 0 | HALT | — |
| 1 | NOP | — |
| 2 | MOV | rd, rs |
| 3 | MOVI | rd, imm |
| 4 | ADD | rd, rs, rt |
| 5 | SUB | rd, rs, rt |
| 6 | MUL | rd, rs, rt |
| 7 | AND | rd, rs, rt |
| 8 | OR | rd, rs, rt |
| 9 | XOR | rd, rs, rt |
| 10 | SHL | rd, rs, rt (shift `rt & 31`, logical) |
| 11 | SHR | rd, rs, rt (logical) |
| 12 | NOT | rd, rs |
| 13 | ADDI | rd, rs, imm |
| 14 | LOAD | rd, rb, imm ; addr = rb+imm |
| 15 | STORE | ra, rb, imm ; addr = ra+imm, val = rb |
| 16 | CMP | rd, rs, rt ; -1 / 0 / 1 signed |
| 17 | BEQ | ra, rb, imm ; pc = next+imm if equal |
| 18 | BNE | |
| 19 | BLT | signed |
| 20 | BGE | signed |
| 21 | JMP | imm |
| 22 | JAL | rd, imm ; rd = next pc |
| 23 | JR | ra |
| 24 | RUNCTX | ra, rb ; run context `ra` for `rb` steps (invalid id = nop) |
| 25 | RUNCTXI | ra, imm |
| 26 | CTXGET | rd, rb, field |
| 27 | CTXSET | ra, rb, field |
| 28 | ADDPROT | ra, rb, rc ; RW range [rb, rc) on context ra |
| 29 | CLRPROT | ra |
| 30 | RDTIME | rd |
| 31 | RDFAULT | rd |

Privileged (supervisor only): RUNCTX, RUNCTXI, CTXGET, CTXSET, ADDPROT, CLRPROT.
Guests that execute them take `PRIV` and do not advance PC.

Branches and JMP/JAL are PC-relative to the **next** instruction:
`target = pc + 1 + imm`.

## Context fields (`imm` of CTXGET/CTXSET)

| id | name | notes |
| --- | --- | --- |
| 0 | PC | |
| 1 | HALTED | |
| 2 | FAULT | |
| 3 | CYCLES | get only |
| 4 | DATA_BASE | get only |
| 5 | DATA_BOUND | get only |
| 6 | N_CONTEXTS | get only, machine-wide |
| 7 | STATUS | halted \| (faulted<<1) |
| 8 | LAST_RUN_STEPS | 0 unless `runctx_posts_status` |
| 9 | LAST_RUN_FAULT | |
| 10 | LAST_RUN_CTX | |

CTXSET only writes PC, HALTED, FAULT. Other fields are ignored.

## Fault codes

NONE=0, PROT=1, MEM=2, PC=3, CTX=4, PRIV=5, PROT_FULL=6, BAD_OP=7, BUDGET=8.

## Substrate flags (generic hardware, not OS policy)

Round-2 default is **hostile**: sticky guest fault, supervisor halt on mem
error, no LAST_RUN_* CSRs. Packed in the image `flags` word:

- bit 0: `sticky_guest_fault`
- bit 1: `supervisor_halt_on_mem`
- bit 2: `runctx_posts_status`

## Program blob (`supervisor.bin`)

Little-endian:

```
u32 magic = 0x0E2015
u32 version = 1
u32 n_instrs
u32 reserved = 0
n_instrs × (i32 op, i32 a, i32 b, i32 c, i32 imm)
```

Python: `evoos.vm.instruction_set.dumps_program` / `loads_program`.

## Machine image (`*.img`, boot ramdisk)

Magic `0x30535645` (`EVS0`). Header is 160 bytes; see `evoos/vm/codec.py`.
Contains config, firmware/info-block memory inits, per-context code, legal
store ranges (evaluator isolation), and expected cells for the boot banner.

Python: `evoos.vm.codec.dumps_image` / `loads_image`.

## STORE / IO window

ISA STORE always writes data memory. The default IO window is words
`[1920, 1984)`. Guest slots stay 256..768; the info block stays at words 0..31.

Firmware (not bytecode) posts low-level input events into the window so a
supervisor *could* LOAD them. Offsets from `io_base`:

| off | name | writer |
| --- | --- | --- |
| 0 | KEY_ASCII | firmware (PS/2 scancode set 1 or serial byte, lowercased) |
| 1 | KEY_SEQ | firmware, monotonic counter |
| 2 | KEY_SCAN | firmware, raw PS/2 make code (0 if the event was serial) |
| 3 | SPAWNS | firmware, count of catalogue installs |
| 4 | LAST_SLOT | firmware, last installed slot 0..3 |
| 5 | LAST_FAULT | firmware, isolation/fault event count |

These are memory words, not a Unix tty. Policy (which context RUNCTX runs,
ADDPROT windows) stays in supervisor bytecode. Installing a catalogue guest
is firmware because the machine is Harvard: bytecode cannot write another
context's code list.

Live images raise `max_steps` / `wall_steps` so firmware can slice forever.
The one-shot HALT elite is unchanged (`--supervisor evolved`). The live ISO
embeds a one-instruction edition (`--supervisor evolved_live`): instruction
70 HALT is JMP back into the evolved RUNCTX loop. No new opcodes.

## Arithmetic

All ALU results wrap to 32-bit two's complement (Python `wrap32`). SHR is
logical. CMP/BLT/BGE are signed.

## Hardware profile (ISA v2)

Privileged ops on the ISO are not interpreted. They are trampoline calls
from AOT-compiled supervisor code:

| op | Hardware |
| --- | --- |
| ADDPROT / CLRPROT | Per-context page tables. Each guest slot is a 4 KiB user page. ADDPROT `[lo,hi)` maps any slot whose word window overlaps that range (U+RW, NX). CLRPROT unmaps those data pages. Code and stack pages stay mapped. Region cap is still 4 (extra ADDPROT is a no-op, not `PROT_FULL` halt). |
| RUNCTX / RUNCTXI | `iretq` into ring 3 with that context's CR3. PIT channel 0 mode-0 is armed for `N * 80` counts (~1–17 ms). Return to the supervisor on IRQ0 (preempt), `int $0x80` (halt), `#PF`, or `#GP`. |
| CTXGET / CTXSET | Saved host register state (RIP/RSP/RFLAGS + GPRs) plus HALTED/FAULT/DATA_*/N_CONTEXTS. `PC` is the low 32 bits of RIP; guests live at `0x400000` so CTXSET PC is not useful. |

**Guests are real x86-64 user code**, not ISA bytecode. They signal completion
with `int $0x80`. Isolation is a `#PF` on an unmapped user page, never a
store check in C.

### Divergences from the Python VM (documented, search still matches on outcomes)

1. **Quantum.** Python `RUNCTX N` counts ISA guest instructions. Hardware `N`
   is a PIT one-shot. HITL agreement is completed/isolation, not switch
   counts or cycle counts.
2. **Guest language.** Python guests are ISA programs with the same
   algorithms (sum/fill/xor/copyinc). Hardware guests are `.S` user
   programs with RDI = data window. The supervisor bytecode is identical.
3. **PROT_FULL.** Python latches supervisor fault. Hardware drops extra
   regions so a live elite cannot kill itself with redundant ADDPROT.
4. **Invalid RUNCTX id.** Python may CTX-fault the supervisor. Hardware
   returns 0 (nop), matching `docs/isa.md` opcode table.
5. **Sticky #PF.** Further RUNCTX of a faulted guest is a nop until CTXSET
   FAULT=0 (hostile substrate).

Still simulated / not claimed as hardware: the PIT quantum vs retirement
count, QEMU as the machine, firmware-drawn dashboard, serial as a debug
console, no disk, no SMP, `int $0x80` as the only "syscall".
