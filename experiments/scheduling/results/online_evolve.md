# On-device online self-evolution

Default ISO is a self-evolving build seeded with **s99**. The evolution engine is firmware (`native/evoos_meta.c`): it is not OS policy.

## What is real

- Live supervisor is **interpreted bytecode** calling the same ring-0 trampolines (`RUNCTX`/`ADDPROT`/`BLK*`/`CTXSET`) as AOT. Guest contexts are real x86 + MMU; they survive a supervisor swap (PC/regs of the supervisor reset; `hw.ctx[]` does not).
- Round-7 C-hw and round-8 shared-window elites are imported as `evoos-embed-v1` (`evoos/export/FORMAT.md`) and seed the on-device MAP-Elites archive. ISA stand-ins (`fifo_swap_like` / `place_first`) are gone.
- Mutations match the Python gadgets pack: `modify_imm` / `modify_operand` / insert/delete / crossover / MMU and RUNCTX gadgets.
- Shadow eval is the **C interpreter** (`evoos_vm.c`) on a private `EvoOSMachine`. It never calls live `evoos_hw_runctx`. A broken candidate cannot touch live page tables.
- Reward is graded (F/S/E + no-halt + switch events + deliveries + fairness). Isolation / supervisor fault is still hard 0. Never-halt pressure/spin guests score on progress, not HALT.
- Parent selection is lexicase over four cases: open-arrival, pressure, shared-window route, live-train traces. Recent live traces weigh more. Held-out always folds in the pressure case if the window itself has none. Swap also refuses a challenger that regresses the pressure case by more than the margin (the gen-23 failure mode).
- MAP-Elites 4×4 (switch bins × fitness band) is persisted with the incumbent (archive version 2).
- Rollback if live `isolation_n` rises, live fitness drops, or the supervisor HALTs after a swap. Key `d` injects a HALT degrader for the QEMU session.
- Archive is **firmware ATA LBAs 64+** (full 512-byte sectors), not the 32-block guest device. Reboot reloads incumbent, generation, lineage ids, and occupied map cells.
- Key `u` loads a producer/consumer pair. ADDPROT of the **same word range** to two guest ids maps the **same physical frame** into both ring-3 address spaces (`HW_DATA_VA_SLOT`). The producer stores; the consumer loads. A malicious guest (`m`) that reads the window without that grant takes `#PF`. The dashboard `SHARE` line lists which slots are shared between which ctx ids.

## What is simulated / honest limits

- Shadow guests are EvoOS **bytecode** catalogue entries, not the native x86 images. Traces record spawn kind + SVC; replay is a model of the live mix, not cycle-accurate.
- There is no runtime gcc. “AOT or interpreter” on device is the interpreter. Seed AOT `supervisor_main` remains in the ISO for persist_storage_main only.
- MAP-Elites is 4×4 cells. Two mutants per tick plus seeded challengers, lexicase parents. Not a datacenter search.
- `r8_share_4g` is a **seeded** 4-guest projection of the round-8 organization (ADDPROT the same range to ctx 1 and 2, never-halt RR). It is not a 128-instr round-8 elite running as live policy. The real `route_s42` / `hw_lex_s*` blobs sit in the MAP; this session they did not beat s99 on the pressure-inclusive held-out gate.
- An evolved `modify_op` mutant did hot-swap (dpress=0, so the pressure-case gate allowed it) and was **rolled back** on live fitness under the route/pressure mix. No evolved swap survived without rollback.

## Tests

`make test-qemu` boots BIOS+UEFI with a disk, runs malicious-read `#PF`, PUT, shared-window route (`u`), waits for a real hot-swap, then pressure, injects a HALT degrader (`d`) to force rollback, screenshots, quits, reboots, and checks `archive loaded gen=`. Isolation must stay 0.

### Session numbers (BIOS)

| event | gen | dfit | dheld | dpress | n | via |
| --- | --- | --- | --- | --- | --- | --- |
| s99 → r8_share_4g | 1 | +197 | +238 | +228 | 62→22 | seed (seeded) |
| r8_share_4g → modify_op | 13 | +45 | +102 | 0 | 22→22 | shadow (mutant) |
| live-fitness rollback | 35 | — | — | — | → id=4 r8_share_4g | route mix |
| modify_op swap again | 35 | +101 | +122 | 0 | 22→22 | seed label, same mutant |
| live-fitness rollback | 37 | — | — | — | → id=4 | pressure |
| degrader HALT + rollback | 40 | — | — | — | 22→1→22 | test-degrade |

Deliveries: `route delivered=77` after `shared-window slot=0 ctx=1+2 phys=…` (same frame in both guests). Malicious read of the ungranted window: `#PF isolation_n=0`.

`isolation_n=0` throughout. Evolved swap survived without rollback: **no**. Reboot: `archive loaded gen=60 inc=4` origin=`r8_share_4g`, `map=9`, lineage `1,4,55,55,104`. UEFI matched (same seeded swap, same mutant + live-fitness rollback, `route delivered=77`, reboot `gen=64 inc=4 map=10`). Serial: `artifacts/serial-bios.log`. Screendump: `artifacts/dashboard.png`.
