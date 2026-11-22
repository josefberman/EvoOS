# On-device online self-evolution

Default ISO is a self-evolving build seeded with **s99**. The evolution engine is firmware (`native/evoos_meta.c`): it is not OS policy.

## What is real

- Live supervisor is **interpreted bytecode** calling the same ring-0 trampolines (`RUNCTX`/`ADDPROT`/`BLK*`) as AOT. Guest contexts are real x86 + MMU; they survive a supervisor swap (PC/regs of the supervisor reset; `hw.ctx[]` does not).
- Mutations match the Python gadgets pack: `modify_imm` / `modify_operand` / insert/delete / crossover / MMU and RUNCTX gadgets.
- Shadow eval is the **C interpreter** (`evoos_vm.c`) on a private `EvoOSMachine`. It never calls live `evoos_hw_runctx`. A broken candidate cannot touch live page tables.
- Reward is graded (F/S/E + no-halt + switch events + deliveries + fairness). Isolation / supervisor fault is still hard 0. Never-halt pressure/spin guests score on progress, not HALT.
- Parent selection is lexicase over four cases: open-arrival, pressure, route+PUT, live-train traces. Swap gate is held-out recent live traces + margin, plus safety on the regression suite.
- MAP-Elites 4×4 (switch bins × fitness band) is persisted with the incumbent (archive version 2).
- Rollback if live `isolation_n` rises, live fitness drops, or the supervisor HALTs after a swap. Key `d` injects a HALT degrader for the QEMU session.
- Archive is **firmware ATA LBAs 64+** (full 512-byte sectors), not the 32-block guest device. Reboot reloads incumbent, generation, lineage ids, and occupied map cells.

## What is simulated / honest limits

- Shadow guests are EvoOS **bytecode** catalogue entries, not the native x86 images. Traces record spawn kind + SVC; replay is a model of the live mix, not cycle-accurate.
- There is no runtime gcc. “AOT or interpreter” on device is the interpreter. Seed AOT `supervisor_main` remains in the ISO for persist_storage_main only.
- MAP-Elites is 4×4 cells. Two mutants per tick plus seeded challengers, lexicase parents. Not a datacenter search.
- persist_rr is a **seeded challenger**, not discovered. If the user’s mix includes PUT, it can beat s99 on the persist bonus — that is a real swap of scheduling+storage policy, still human-shaped.
- Round 7/8 C-hw research guests were not in this tree; pressure (window store storm) and route (SVC 3) are the on-ISO stand-ins.

## Tests

`make test-qemu` boots BIOS+UEFI with a disk, runs #PF/preempt, PUT + route (so held-out traces have deliveries), waits for a real hot-swap, then pressure, injects a HALT degrader (`d`) to force rollback, screenshots, quits, reboots, and checks `archive loaded gen=`. Isolation must stay 0.

Graded fitness + mixed traces are what make the incumbent’s score non-zero; persist_rr can beat s99 on PUT held-out by the persist bonus. That is a real swap of seeded policy, not a scripted inject. The degrader is test-only.

Round 7 C-hw fifo-swap / place-first binaries are **not in this tree**. `c_hw_fifo_swap_like` and `c_hw_place_first` are ISA-identical stand-ins (FIFO walk; new-arrivals-first).

### Session numbers (BIOS)

| event | gen | dfit | dheld | n | via |
| --- | --- | --- | --- | --- | --- |
| s99 → persist_rr | 1 | +166 | +273 | 62→50 | challenger |
| persist_rr → mutant | 23 | +46 | +32 | 50→17 | modify_operand |
| live-fitness rollback | 25 | — | — | → persist_rr | pressure starved later slots |
| degrader HALT + rollback | 25 | — | — | 50→1→50 | test-degrade |

`isolation_n=0` throughout. Inspect after swaps still shows guest contexts (spin/pressure running). Reboot: `archive loaded gen=40 inc=2` persist_rr, `map=6`, lineage `1,2,48,55`. UEFI matched (challenger + `insert_runctx_gadget` mutant, same two rollbacks). Serial: `artifacts/serial-bios.log`. Screendump: `artifacts/dashboard.png`.
