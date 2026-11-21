# EvoOS

**Research question:** can an RL / evolutionary agent discover a *functional operating system* from behavioral objectives alone, without being taught conventional OS architecture?

The artifact being evolved is a **supervisor program** in a small, typed bytecode. The agent must learn an *implementation* (how to multiplex a machine among competing guest programs, isolate their memory, and keep them making progress). It is not being trained to operate Linux, xv6, or any other existing kernel.

## The no-architectural-priors rule

The instruction set is a virtual *computer*, not a virtual *operating system*.

Allowed hardware:

- load / store, arithmetic, compare / branch
- `RUNCTX` / `RUNCTXI` — run a hardware context for N steps and return
- `CTXGET` / `CTXSET` — read or poke context metadata (PC, halt, fault, data window, …)
- `ADDPROT` / `CLRPROT` — range-based memory-protection registers (an MMU-like mechanism)
- a cycle timer (`RDTIME`)

**Forbidden in the ISA**, on purpose: `schedule_process`, `yield_to_next`, `allocate_page`, run queues, priority bands, wait queues, or any other traditional OS component as a primitive. If round-robin, priorities, or something stranger appear, they must appear as *patterns in bytecode*.

Three experimental conditions, same guests and reward:

| | What is given | What is searched |
| --- | --- | --- |
| **A** | A human-written supervisor in this ISA (RTC, round-robin, selfish) | nothing |
| **B** | The human round-robin *structure*; UCB1 tunes only the `RUNCTXI` quantum | one immediate |
| **C** | Random bytecode + mutation operators (see priors below) | the whole program |

## Priors that are *not* in the ISA but *are* in the search

`RUNCTX` and `ADDPROT` are hardware. The **gadgets** are mutation operators that insert the *calling convention* for those CSRs, not new opcodes:

| Operator pack | Operators beyond insert/delete/tweak | Encodes | Does not encode |
| --- | --- | --- | --- |
| `bare` | none | raw ISA | how to call MMU/RUNCTX |
| `gadgets` | `insert_mmu_gadget`, `insert_runctx_gadget` | `MOVI ctx; CTXGET DATA_*; ADDPROT` and `MOVI ctx; RUNCTXI n` for **one** random context id | round-robin, quanta, skip-halted, until-idle, run queues |
| `full` | gadgets + `insert_ctx_scan` | “the machine reports `N_CONTEXTS`; you can walk ids 1..N-1” with a one-instruction body | skip-if-halted, time-slice policy, outer until-idle loop |

Invalid context ids are **nops** (a trap that halted the supervisor made search degenerate). That is a machine-semantics choice, not an OS.

**Implication for the minimal-priors claim:** Condition C with `gadgets` or `full` is “discover organization given the ISA manual’s examples,” not “discover a kernel from a bag of bits.” The `bare` pack is the closer test of the latter. At the budget below, **bare never reaches 4/4**; gadgets often do. Treat C+gadgets as a positive result about *organization*, and treat C+bare as a negative result about *undirected bytecode search*.

## Milestone 1

Four guest programs share finite data memory and a global instruction budget. Reward

```
R = 0.45F + 0.20S + 0.15E + 0.10G + 0.10N
```

(functional correctness, stability/isolation, efficiency, generalization, novelty). Isolation / memory-corruption failures are a **hard constraint**. Invalid bytecode scores `-1`.

- `--mode correctness` — F and S only
- `--mode qd` — full weights + MAP-Elites

## Setup and commands

Python 3.11+. PyTorch optional.

```bash
pip install -e ".[dev]"
pytest
python train.py --generations 40 --population 24 --seed 42 --mode correctness --operator-pack gadgets
python -m evoos.experiments.scheduling.analyze --db runs/correctness/evoos.sqlite
# Round 2: gadgets vs bare (minutes–tens of minutes):
python -m evoos.experiments.scheduling.sweep --out experiments/scheduling/results
# Round 3: bare-only curriculum + shaping + substrate ablations:
python -m evoos.experiments.scheduling.sweep_round3 --out experiments/scheduling/results/round3
```

## Scaled results (measured)

Machine: Cloud Agent VM, CPython 3.12, 4 cores, no GPU. **24.1 min** wall.

```text
python -m evoos.experiments.scheduling.sweep \
  --generations 400 --population 40 --workers 4
```

6 seeds `{1, 7, 13, 42, 99, 2024}` × 2 modes × 3 operator packs = 36 Condition C jobs. Tables: [`experiments/scheduling/results/summary.md`](experiments/scheduling/results/summary.md).

**Condition A (held-out):** RTC and round-robin q=16 complete 4/4, isolation pass, correctness R = 1.000. Selfish: 1/4, R = 0.400.

**Condition B:** UCB1 over 19 quanta. Every quantum that finishes all guests scores correctness R = 1.0, so the bandit keeps **q=1** (first perfect arm). 6/6 seeds 4/4, label `round-robin-like`. Efficiency is *not* in this objective; switch counts still fall as q grows (`q=1` hundreds of switches, `q≥128` four switches ≈ RTC).

**Condition C, 4/4 on train AND held-out with isolation:**

| mode | pack | search R mean ± std (best) | seeds 4/4 | held-out labels of elites |
| --- | --- | --- | --- | --- |
| correctness | bare | 0.347 ± 0.038 (0.400) | **0/6** | (no 4/4) |
| correctness | gadgets | 1.000 ± 0.000 (1.000) | **6/6** | mostly `round-robin-like`, one `other` |
| correctness | full | 1.000 ± 0.000 (1.000) | **5/6** | RR-like / batched / other; one seed held `[4, 3]` |
| qd | bare | 0.305 ± 0.023 (0.332) | **0/6** | (no 4/4) |
| qd | gadgets | 0.857 ± 0.039 (0.879) | **5/6** | other / RR-like; one seed stuck at 3/4 |
| qd | full | 0.878 ± 0.007 (0.892) | **5/6** | other / RTC-like; one seed held `[4, 3]` |

**21/36** C runs hit 4/4. **0/12** bare. The N_CONTEXTS scan did not beat gadgets. QD search rewards are lower than correctness because E/G/N share the weight; several QD elites still 4/4 on held-out.

**A vs B vs C (best held-out correctness R):** all three can reach 1.000 and 4/4. A and B do it with a human RR/RTC; C does it only when MMU/RUNCTX gadgets are enabled, and the resulting programs are messy bytecode whose *behavior* is often RR-like or RTC-like rather than a copy of the baseline source.

## Round 3 (bare ISA: curriculum, shaping, substrate)

Same machine, **23.6 min**, 6 seeds, **bare pack only**. Tables: [`experiments/scheduling/results/round3/summary.md`](experiments/scheduling/results/round3/summary.md).

```text
python -m evoos.experiments.scheduling.sweep_round3 \
  --gens-per-stage 100 --population 36 --workers 4
```

Curriculum stages: 1 → 2 → 3 → 4 guests, then a harder 4-guest mix. Promote when the stage's train guests all complete with isolation. Shaping replaces sparse F with behavioral observables (`entered`, `prot_cover`, `progress`, `complete`) — not opcode bonuses. Lenient substrate: RUNCTX retries a faulting guest instruction, supervisor mem errors post a CSR instead of halting, RUNCTX writes LAST_RUN_* CSRs.

**Condition B with efficiency.** R = 0.45F + 0.20S + 0.35E. Grid E rises with quantum (`q=1` E=0.444 / 228+322 switches → `q=128` E=0.781 / 4+4 switches). UCB1 6/6 seeds pick **q=192** (tied with the large-q plateau; 4/4, held R≈0.93). The identifiable quantum is “almost RTC.”

**Condition C, 4/4 on full 4-guest train AND held-out:**

| curriculum | shaping | substrate | search R mean ± std (best) | 4/4 |
| --- | --- | --- | --- | --- |
| flat | yes | lenient | 0.607 ± 0.285 (1.000) | **1/6** (seed 7, RR-like). Seed 2024: train 4/4, held `[4, 3]` |
| curr | yes | lenient | 0.533 ± 0.142 (0.760) | **0/6** |
| curr | no | lenient | 0.307 ± 0.050 (0.360) | **0/6** |
| curr | yes | hostile | 0.427 ± 0.126 (0.600) | **0/6** |
| curr | yes | lenient, no resume | 0.487 ± 0.139 (0.720) | **0/6** |
| curr | yes | lenient, no memerr | 0.440 ± 0.146 (0.680) | **0/6** |
| curr | yes | lenient, no status | 0.530 ± 0.153 (0.760) | **0/6** |

**1/42** bare runs hit 4/4. That one is **flat + shaping + lenient**, not the curriculum. All 36 curriculum jobs **stalled at k1** (0/36 completed the single guest; 0/36 had `prot_cover`; 24/36 entered). Recurring R=**0.52** is `entered+progress` without an MPU window: RUNCTX lets the guest retire 1–2 ops then fault, which outscores ADDPROT without RUNCTX. Later stages of a few shaped+lenient seeds still reach 2/4 on the 4-guest eval (prot_cover appears after k1). Status CSRs are not load-bearing; resume and imprecise mem abort move the mean a little and do not unblock k1.

Round 2 said bare is 0/12 at 400×40 with gadgets off. Round 3 shows a **single** bare 4/4 when the substrate is lenient, shaping is on, and the 500-gen budget is spent on four guests rather than gated by a 1-guest promotion. That is not a reliable discovery procedure. The ISA is still integer 5-tuples (`op,a,b,c,imm`); nothing Python-only is required to interpret it on a later boot stub.

## Path to a bootable x86_64 ISO

The evolved supervisor *is* the OS. A tiny host stub (C interpreter of the frozen word encoding) plus Limine (BIOS + UEFI) is firmware, not a kernel. See **Booting EvoOS** below.

## Booting EvoOS

The OS is the evolved bytecode blob. Firmware in `native/` interprets the frozen 5-word ISA (`docs/isa.md`). It is not a kernel: it does device I/O (PS/2, serial, GOP framebuffer) and a Harvard loader for a built-in guest catalogue. Who runs, and which protection windows apply, stay in supervisor bytecode (`RUNCTX` / `ADDPROT`).

The ISO is a **live** system. The supervisor is sliced forever; guests halt, get replaced from the catalogue, and isolation is demonstrated while it is running. There is no `[halt]` after the first workload.

**Build the ISO** (needs `gcc`, `xorriso`, Python 3.11+):

```bash
pip install -e ".[dev]"
make iso                          # live edition of gadgets elite s42
make iso LIVE_SUPERVISOR=rr_live  # never-HALT human round-robin
```

Outputs: `build/evoos.iso` and `artifacts/evoos.iso`. Docker: `docker build -t evoos . && docker run --rm evoos make iso test-qemu`.

**Keys** (PS/2 scancode set 1, or a serial byte — not a Unix tty):

| key | action |
| --- | --- |
| `1`–`4` | install next catalogue kind into that slot |
| `s` `f` `x` `c` `m` | sum / fill / xor / copyinc / **malicious** into the next slot |
| space | next kind into the next slot |
| `i` | inspect contexts on serial |
| `h` | help |
| `q` | quit (isa-debug-exit; isolation_n==0 → QEMU status 1) |

`m` installs a guest that stores into another slot. The evolved ADDPROT window should fault it (`fault=1`, `isolation_n=0`).

**QEMU live test** (BIOS + UEFI, QMP `send-key`, screendump):

```bash
make test-qemu
```

Success: serial contains `EvoOS live`, a `malicious` spawn, `inspect isolation_n=`, `EvoOS quit`. Screenshot: `artifacts/dashboard.png`.

**QEMU interactive:**

```bash
qemu-system-x86_64 -cdrom artifacts/evoos.iso -m 128M -serial mon:stdio \
  -device isa-debug-exit,iobase=0xf4,iosize=0x04
```

**Live supervisor.** The round-2 elite HALTs at instruction 70 (fine for one-shot eval). The live ISO embeds `evolved_gadgets_s42_live`: that HALT is JMP back into the evolved RUNCTX loop. Same opcodes, same bytes 0..69. Open-arrival eval (`evoos/evaluator/open_arrival.py`) injects guests after t=0 without new opcodes; RR-live (`JMP main`) is the human never-HALT baseline. `--supervisor evolved` still packs the original HALT blob for differential tests.

**VirtualBox**

1. New VM → **Other** → **Other/Unknown (64-bit)** (or BSD 64-bit if Other is hidden).
2. RAM **128 MB** minimum, **256 MB** comfortable. 1 CPU is enough. Chipset PIIX3 or ICH9. Enable I/O APIC.
3. Storage: add the ISO as an optical drive (IDE or SATA). Do not attach a virtual HDD; this firmware does not install.
4. System → Boot order: **Optical** first. Uncheck Floppy.
5. For BIOS: System → uncheck Enable EFI. For UEFI: check Enable EFI (the ISO is a hybrid Limine image).
6. Ports → Serial Ports → Port 1: enable, port mode **Raw file**, path `evoos-serial.log`.
7. Display: 16 MB VRAM, VMSVGA or VBoxVGA. Limine hands the firmware a framebuffer (not BIOS text at 0xB8000). You should see **EVOOS LIVE**, the elite name, four context rows, and a RUNCTX heatmap.
8. Start. Type `m` to inject a malicious guest; the FAULT row should go red and isolation stay 0. Type `q` does nothing useful in VirtualBox (no isa-debug-exit); reset the VM to stop.

Headless attempt on this Cloud Agent VM: `VBoxManage` 7.0.16 installed from Ubuntu noble, the VM registered, `startvm --type headless` failed with `NS_ERROR_FAILURE`. Cause: `/dev/vboxdrv` does not exist (`modprobe vboxdrv`: module not found under `/lib/modules/6.12.113+`). Matching `linux-headers-6.12.113+` are not in the archive (dkms built headers for 6.8.0-151 instead). **VirtualBox did not boot here.** QEMU BIOS and OVMF UEFI did.

**Differential tests** (Python VM vs hosted C interpreter, one-shot image, max_steps=8000):

```bash
make test-diff
pytest tests/test_codec.py tests/test_differential.py tests/test_open_arrival.py
```

**Select supervisor at pack time:** `--supervisor evolved` / `evolved_live` / `rr` / `rr_live`. `--live` raises max_steps so firmware can slice indefinitely.

## Layout

```
evoos/vm/           instruction_set, codec (frozen 5-word encoding), machine, sandbox, verifier
evoos/evaluator/    workloads, correctness, reward, novelty
evoos/agent/        mutations (packs), policy, MAP-Elites, search
evoos/experiments/scheduling/  baselines, Condition B, sweep, results/
evoos/boot/         pack EVS0 images, catalogue, Python vs C traces
evoos/evaluator/    workloads, correctness, open_arrival, reward, novelty
evoos/placeholders/ stages 2–4 stubs
native/             freestanding C interpreter, Limine firmware, live ISO recipe
docs/isa.md         frozen binary ISA + live IO window
artifacts/          evoos.iso, dashboard.png, serial-bios.log
train.py
```

## Roadmap

| Stage | Question | Status |
| --- | --- | --- |
| 1 | Discover multiplexing + isolation for four competing guests | done (search) |
| boot-1 | Frozen ISA + C interpreter + one-shot Limine ISO | `native/`, `docs/isa.md` |
| boot-2 | Live ISO: open arrivals, PS/2+serial events, framebuffer dashboard | this milestone |
| 2 | Memory management from behavioral pressure (no `allocate_page` opcode) | `evoos/placeholders/memory.py` |
| 3 | Persistence / a block device, not a VFS opcode | `evoos/placeholders/persistence.py` |
| 4 | Linux syscall slice via differential tests against Linux | `evoos/placeholders/linux_syscalls.py` |

Next: break the k1 `entered`-without-`prot_cover` local optimum (shaping weights or a cover-before-progress gate that is still behavioral).
