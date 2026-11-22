# On-device online self-evolution

Default ISO is a self-evolving build seeded with **s99**. The evolution engine is firmware (`native/evoos_meta.c`): it is not OS policy.

## What is real

- Live supervisor is **interpreted bytecode** calling the same ring-0 trampolines (`RUNCTX`/`ADDPROT`/`BLK*`/`CTXSET`) as AOT. Guest contexts are real x86 + MMU; they survive a supervisor swap (PC/regs of the supervisor reset; `hw.ctx[]` does not).
- Round-9 leak-safe *candidates* (`r9_gen_s1`, `r9_gen_s13`, `r9_gen_s2024`) are imported via `evoos-embed-v1`. Round-8 128-instr elites and round-9 seeds 7/42/99 are **not** incumbents (quarantined).
- Third-guest is a **hard gate**: extra ctx covering the pair window `[256,384)` fails shadow `eval_audit`, `map_add`, and `try_swap`. Archive load re-audits the incumbent and every MAP cell and quarantines leaks (restore s99). Live `share_mask[0]` bits outside ctx 1+2 roll back a swapped supervisor.
- Key `a` spawns native `atk_store` / `atk_load` against the pair window after share. Ungranted STORE/LOAD take `#PF`. `isolation_n` stays 0 (fault before a successful out-of-slot write).
- Mutations match the Python gadgets pack. Shadow eval is the C interpreter on a private `EvoOSMachine`.
- Reward is graded (F/S/E + no-halt + deliveries + fairness). Isolation / supervisor fault / leak is hard 0.
- Rollback if live `isolation_n` rises, live fitness drops, supervisor HALTs, or a third guest is granted the pair window. Key `d` injects a HALT degrader.

## What is simulated / honest limits

- Shadow guests are EvoOS **bytecode** catalogue entries, not the native x86 images.
- There is no runtime gcc. On-device policy is the interpreter.
- Round-9 research (`results/round9`) marked seeds 1/13/2024 as audit-pass on a 3-guest pair + attacker split that **skips** ctx ids above `n_guests`. On-device audit uses 8 contexts and treats any extra grant of `[256,384)` as a leak. Those 128-instr blobs ADDPROT ctx 4/5/6 with pair subranges, so they **fail** the hard gate (`leak=1 contain=0 pass=0`) and are not admitted to MAP or the live challenger slot.
- `r8_share_4g` is a seeded 4-guest projection (ADDPROT ctx 1 own window, then the same range to ctx 2 only). It **passes** on-device audit. It is the leak-safe challenger / live default after gen-1.

## Tests

`make test-qemu` boots BIOS+UEFI, waits `audit done`, runs malicious-read `#PF`, PUT, shared-window route (`u`), third-guest STORE/LOAD (`a`), waits for a real hot-swap, pressure, degrader (`d`), screenshots, quits, reboots, checks `archive loaded gen=`. Isolation must stay 0. Live third-guest extra bits must stay 0.

### On-device audit (every boot)

| origin | leak | contain | pass |
| --- | --- | --- | --- |
| evolved_open_arrival_gadgets_s99 | 0 | 1 | 1 |
| baseline_persist_rr_q32 | 0 | 1 | 1 |
| r8_share_4g | 0 | 1 | 1 |
| r9_gen_s1 | 1 | 0 | 0 |
| r9_gen_s13 | 1 | 0 | 0 |
| r9_gen_s2024 | 1 | 0 | 0 |

Quarantined (not in ISO pop/MAP): `r8_route_*`, `r7_hw_lex_*`, `r9_gen_s7`, `r9_gen_s42`, `r9_gen_s99`. Fallback challenger: `r8_share_4g`. Boot incumbent remains **s99**.

### Session numbers (BIOS)

| event | gen | dfit | dheld | dpress | n | via |
| --- | --- | --- | --- | --- | --- | --- |
| s99 → r8_share_4g | 1 | +197 | +238 | +228 | 62→22 | challenger (seeded) |
| r8_share_4g → insert_mmu_gadget | 4 | +18 | +56 | −3 | 22→26 | shadow (mutant) |
| live-fitness rollback | ~33 | — | — | — | → id=2 r8_share_4g | route mix |
| insert_mmu_gadget again | 33 | +40 | +66 | −3 | 22→26 | seed label, same mutant |
| live-fitness rollback | ~42 | — | — | — | → id=2 | pressure |
| degrader HALT + rollback | — | — | — | — | 22→1→22 | test-degrade |

Deliveries: `route delivered=77` after `shared-window slot=0 ctx=1+2`. Live audit after `a`: `audit live leak=0 extra=0 isolation_n=0`. `atk_store` / `atk_load` both `#PF isolation_n=0`.

`isolation_n=0` throughout. Evolved swap survived without rollback: **no**. Real pre-degrade swaps: **3** (1 seeded, 2 mutant). Rollbacks: **3** (2 live-fitness + degrader halt). Reboot: `archive loaded gen=64 inc=2` origin=`r8_share_4g`, `map=7`, lineage `1,2,36,36,111`. Loaded MAP cells re-audited `pass=1`.

UEFI matched (same audit table, same seeded swap, same mutant + live-fitness rollback, `route delivered=77`, `audit live leak=0 extra=0`, atk `#PF`, reboot `gen=68 inc=2 map=7`). Serial: `artifacts/serial-bios.log`. Screendump: `artifacts/dashboard.png`.
