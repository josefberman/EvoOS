# Persistence curriculum search

Wall ~3300 s, 4 workers, 8 seeds, gadgets pack, MAP-Elites (layout × gated-correct+stage) with curiosity (`1/sqrt(visits)`). Checkpointed. Gated GET: payload must already be on the device after PUT, and GET must `BLKRD`.

## Condition A (persist_rr)

Reward **1.60**, stage D, gated 2/2, layout `sparse-hash-like`, 2 writes.
Human formula `block = (ctx-1)*8 + (key & 7)` → keys land on blocks 1 and 9. Not evolved.

## Search

| | |
| --- | --- |
| evals | 804697 |
| elapsed | 3300 s |
| archive cells | 25 |
| best evolved | crossover, R=1.5375, D gated 2/2, layout `other`, 91 instrs, 6 writes |

Learning curve: `persist_curriculum_curve.json` (every 50 gens). Best evolved rose 1.24 → 1.35 → 1.5375 and never caught persist_rr’s 1.60 (extra writes).

## What organization emerged

The D 2/2 crossover is **persist_rr spliced with a second copy of its own handler**. Occupied blocks: **1 (42), 9 (99)** — the human stride table — plus extra copies of 99 at 17, 22, 27. Same namespace, more `BLKWR`. Classified `other` because more than four occupied blocks.

Append-log-like cells reached D at 1/2 (isolation-preserving miss after a random cut). Dense-table cells peaked at D 1/2. Empty cells never recalled from the device.

Nothing log-structured or hash-like was *discovered*. The only 2/2 policies are the human table and a worse duplicate of it.

## Failures

- Hardcoded GET (MOVI payload, no `BLKRD`) scores 0 under the gate.
- Mutating the stride (`modify_imm` / `modify_operand`) collapses two guests or drops a key; those stepping stones stayed in the archive (append-log 1/2).
- s99 never `BLKRD`s. Default ISO scheduling stays s99.
- The crossover elite is **not** the live scheduler: it is RR + the human table, not s99, and it writes more. Its SVC/`BLK*` prefix is AOT’d as `persist_storage_main` for live `w`/`r`/`z`.

## HITL / ISO

Crossover elite **passed** QEMU ATA kill/reboot (`recall=123`). persist_rr also passed.

Default live ISO remains **s99** (`live_ok=false` for prefix-compose): the elite never-halts, so prefixing it would replace s99’s scheduler with RR. AOT still emits the elite’s oneshot storage handler on the s99 ISO (`storage=evolved_persist_crossover`). `make test-persist` packs `evolved_persist` (the crossover as supervisor) and then re-runs the ATA reboot test on the restored s99 ISO. `LIVE_SUPERVISOR=persist_rr` is Condition A.

**Default ISO storage today:** s99 schedules; evolved persist oneshot handles PUT/GET after guest `SVC`. The oneshot is persist_rr’s stride table plus extra copies, not a new layout.
