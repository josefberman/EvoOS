# On-device online self-evolution

Default ISO is a self-evolving build seeded with **s99**. The evolution engine is firmware (`native/evoos_meta.c`): it is not OS policy.

## What is real

- Live supervisor is **interpreted bytecode** calling the same ring-0 trampolines (`RUNCTX`/`ADDPROT`/`BLK*`) as AOT. Guest contexts are real x86 + MMU; they survive a supervisor swap (PC/regs of the supervisor reset; `hw.ctx[]` does not).
- Mutations match the Python gadgets pack: `modify_imm` / `modify_operand` / insert/delete / crossover / MMU and RUNCTX gadgets.
- Shadow eval is the **C interpreter** (`evoos_vm.c`) on a private `EvoOSMachine`. It never calls live `evoos_hw_runctx`. A broken candidate cannot touch live page tables.
- Reward is the same shape (F/S/E, isolation hard 0). PUT traces add a persist bonus if the payload is on the shadow disk. Route SVC 3 adds a smaller bonus if dest window holds 77.
- Hot-swap when a candidate beats the incumbent by 0.03 and passes safety (no isolation, supervisor not halted early, every shadow guest progressed). Rollback if live `isolation_n` rises after a swap.
- Archive is **firmware ATA LBAs 64+** (full 512-byte sectors), not the 32-block guest device. Reboot reloads incumbent, generation, lineage ids.

## What is simulated / honest limits

- Shadow guests are EvoOS **bytecode** catalogue entries, not the native x86 images. Traces record spawn kind + SVC; replay is a model of the live mix, not cycle-accurate.
- There is no runtime gcc. “AOT or interpreter” on device is the interpreter. Seed AOT `supervisor_main` remains in the ISO for persist_storage_main only.
- Population is 6. One mutant (plus incumbent + persist_rr challenger) per tick. Not a datacenter search.
- persist_rr is a **seeded challenger**, not discovered. If the user’s mix includes PUT, it can beat s99 on the persist bonus — that is a real swap of scheduling+storage policy, still human-shaped.
- Round 7/8 C-hw research guests were not in this tree; pressure (window store storm) and route (SVC 3) are the on-ISO stand-ins.

## Tests

`make test-qemu` boots BIOS+UEFI with a disk, runs the existing #PF/preempt session, then PUT + route + pressure, waits 8 generations, screenshots, quits, reboots, and checks `archive loaded gen=`. Isolation must stay 0. Swap is required only if something beats s99; otherwise the log says so.

Session (BIOS, 8 gens): `isolation_n=0` throughout, `#PF` on malicious, IRQ0 preempt, `put=123`. Shadow scored the incumbent 0 on the pressure-heavy replay (never-halt guests fail the “all shadow guests progress/complete” safety gate as written), so **no hot-swap** — serial `evo noswap gen=8`. Reboot: `archive loaded gen=8 inc=1`. UEFI matched. Persist HITL (`make test-persist`) uses a fresh disk so the live-test archive cannot replace the persist elite.
