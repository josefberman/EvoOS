# Round 3: bare ISA, curriculum, shaping, substrate

Wall time **1415.1s** (23.6 min), 4 processes.
Search: gens/stage=100 pop=36 seeds `[1, 7, 13, 42, 99, 2024]`.
Operator pack is **bare** (no MMU/RUNCTX gadgets, no ctx-scan).

## Shaping terms

```
Behavioral shaping terms. None of these matches an instruction sequence.

Each term is an *observable* of the machine after a run. A supervisor can
satisfy them with any bytecode that produces that machine state. They are
not `schedule_process`, not `ADDPROT` opcode bonuses, not run-queue hints.

| Term | Observation | Why it is not an architectural prior |
| --- | --- | --- |
| entered | Fraction of guests that retired ≥1 instruction | Any context-switch primitive posts this; we do not care *how* RUNCTX was reached |
| prot_cover | Guest's assigned data window is inside some installed MMU range | Hardware protection *state*, not “emit ADDPROT in this order” |
| progress | Guest executed >0 cycles (same as entered here, kept distinct for heartbeats later) | Work happened |
| complete | Guest halted with expected outputs | The original behavioral objective |

Weights sum to 1 and replace the sparse F = 0.8·complete + 0.2·progress
when shaping is on. Isolation remains a hard constraint.
```

Weights: `{'entered': 0.15, 'prot_cover': 0.2, 'progress': 0.25, 'complete': 0.4}`

## Substrate flags (generic hardware, not OS)

| flag | hostile (round 2) | lenient | why it is hardware |
| --- | --- | --- | --- |
| `sticky_guest_fault` | guest dead after PROT/MEM until CTXSET | RUNCTX retries the faulting instruction | resume-after-abort, like a CPU restarting the faulting load |
| `supervisor_halt_on_mem` | supervisor halt | sticky error, skip access, continue | imprecise abort vs machine check |
| `runctx_posts_status` | off | LAST_RUN_STEPS/FAULT/CTX CSRs | result of the last context-switch instruction |

## Condition A

| name | held complete | 4/4 | held R | label |
| --- | --- | --- | --- | --- |
| run_to_completion | [4, 4] | True | 1.000 | run-to-completion-like |
| round_robin_q16 | [4, 4] | True | 1.000 | round-robin-like |
| selfish_first | [1, 1] | False | 0.400 | starving |

## Condition B (efficiency in the objective)

| seed | q | B-train R | E | held complete | 4/4 |
| --- | --- | --- | --- | --- | --- |
| 7 | 192 | 0.923 | 0.781 | [4, 4] | True |
| 13 | 192 | 0.923 | 0.781 | [4, 4] | True |
| 42 | 192 | 0.923 | 0.781 | [4, 4] | True |
| 1 | 192 | 0.923 | 0.781 | [4, 4] | True |
| 99 | 192 | 0.923 | 0.781 | [4, 4] | True |
| 2024 | 192 | 0.923 | 0.781 | [4, 4] | True |

Grid best q=**128** trainR=0.923 E=0.781 switches=[4, 4].

## Condition C bare — 4/4 on the *full* 4-guest train+held-out

| curriculum | shaping | substrate | n | search R mean | std | best | 4/4 train+held | stall stages | labels |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| flat | shape | lenient | 6 | 0.607 | 0.285 | 1.000 | 1/6 | — | batched/priority-like, other, round-robin-like, starving |
| curr | noshape | lenient | 6 | 0.307 | 0.050 | 0.360 | 0/6 | k1,k1,k1,k1,k1,k1 | batched/priority-like, other, round-robin-like, starving |
| curr | shape | hostile | 6 | 0.427 | 0.126 | 0.600 | 0/6 | k1,k1,k1,k1,k1,k1 | other, round-robin-like, starving |
| curr | shape | lenient | 6 | 0.533 | 0.142 | 0.760 | 0/6 | k1,k1,k1,k1,k1,k1 | batched/priority-like, other, round-robin-like, starving |
| curr | shape | lenient_no_memerr | 6 | 0.440 | 0.146 | 0.680 | 0/6 | k1,k1,k1,k1,k1,k1 | batched/priority-like, other, round-robin-like, starving |
| curr | shape | lenient_no_resume | 6 | 0.487 | 0.139 | 0.720 | 0/6 | k1,k1,k1,k1,k1,k1 | other, round-robin-like, starving |
| curr | shape | lenient_no_status | 6 | 0.530 | 0.153 | 0.760 | 0/6 | k1,k1,k1,k1,k1,k1 | other, round-robin-like, starving |

### Per-seed

| seed | curr | shape | substrate | search R | train | held | 4/4 | stall | label |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | curr | shape | hostile | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 7 | curr | shape | hostile | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 13 | curr | shape | hostile | 0.600 | [1, 1] | [1, 1] | no | k1 | round-robin-like |
| 42 | curr | shape | hostile | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |
| 99 | curr | shape | hostile | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |
| 2024 | curr | shape | hostile | 0.360 | [0, 0] | [0, 0] | no | k1 | round-robin-like |
| 1 | flat | shape | lenient | 0.360 | [0, 0] | [0, 0] | no | — | starving |
| 7 | flat | shape | lenient | 1.000 | [4, 4] | [4, 4] | yes | — | round-robin-like |
| 13 | flat | shape | lenient | 0.320 | [0, 0] | [0, 0] | no | — | starving |
| 42 | flat | shape | lenient | 0.520 | [0, 0] | [0, 0] | no | — | batched/priority-like |
| 99 | flat | shape | lenient | 0.440 | [0, 0] | [0, 0] | no | — | starving |
| 2024 | flat | shape | lenient | 1.000 | [4, 4] | [4, 3] | no | — | other |
| 1 | curr | noshape | lenient | 0.360 | [0, 0] | [0, 0] | no | k1 | other |
| 1 | curr | shape | lenient | 0.600 | [2, 2] | [2, 2] | no | k1 | batched/priority-like |
| 7 | curr | shape | lenient | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 7 | curr | noshape | lenient | 0.320 | [0, 0] | [0, 0] | no | k1 | starving |
| 13 | curr | shape | lenient | 0.760 | [2, 2] | [2, 2] | no | k1 | other |
| 13 | curr | noshape | lenient | 0.320 | [0, 0] | [0, 0] | no | k1 | batched/priority-like |
| 42 | curr | shape | lenient | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 42 | curr | noshape | lenient | 0.360 | [0, 0] | [0, 0] | no | k1 | round-robin-like |
| 99 | curr | shape | lenient | 0.520 | [0, 0] | [0, 0] | no | k1 | round-robin-like |
| 99 | curr | noshape | lenient | 0.240 | [0, 0] | [0, 0] | no | k1 | starving |
| 2024 | curr | shape | lenient | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |
| 2024 | curr | noshape | lenient | 0.240 | [0, 0] | [0, 0] | no | k1 | starving |
| 1 | curr | shape | lenient_no_memerr | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 7 | curr | shape | lenient_no_memerr | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 13 | curr | shape | lenient_no_memerr | 0.680 | [1, 1] | [1, 1] | no | k1 | round-robin-like |
| 42 | curr | shape | lenient_no_memerr | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |
| 99 | curr | shape | lenient_no_memerr | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |
| 2024 | curr | shape | lenient_no_memerr | 0.360 | [0, 0] | [0, 0] | no | k1 | batched/priority-like |
| 1 | curr | shape | lenient_no_resume | 0.720 | [1, 1] | [1, 1] | no | k1 | round-robin-like |
| 7 | curr | shape | lenient_no_resume | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 13 | curr | shape | lenient_no_resume | 0.520 | [0, 0] | [0, 0] | no | k1 | round-robin-like |
| 42 | curr | shape | lenient_no_resume | 0.360 | [0, 0] | [0, 0] | no | k1 | starving |
| 99 | curr | shape | lenient_no_resume | 0.520 | [0, 0] | [0, 0] | no | k1 | round-robin-like |
| 2024 | curr | shape | lenient_no_resume | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |
| 1 | curr | shape | lenient_no_status | 0.760 | [2, 2] | [2, 2] | no | k1 | other |
| 7 | curr | shape | lenient_no_status | 0.520 | [0, 0] | [0, 0] | no | k1 | other |
| 13 | curr | shape | lenient_no_status | 0.660 | [2, 2] | [2, 2] | no | k1 | other |
| 42 | curr | shape | lenient_no_status | 0.440 | [0, 0] | [0, 0] | no | k1 | starving |
| 99 | curr | shape | lenient_no_status | 0.520 | [0, 0] | [0, 0] | no | k1 | round-robin-like |
| 2024 | curr | shape | lenient_no_status | 0.280 | [0, 0] | [0, 0] | no | k1 | starving |

## Diagnosis

1/42 bare runs reached 4/4 on the full 4-guest split.
k1 elites: entered a guest in **24/36**, prot_cover in **0/36**, completed in **0/36**.
Stall histogram: k1=36, none=6

Curriculum **never promoted**: 100 generations at k1 were not enough to complete the single guest. The recurring search-R of **0.52** is the shaped local optimum `entered+progress` without `prot_cover` (blend 0.40, S=1 → R=0.80×0.40+0.20=0.52). RUNCTX without an MPU window lets the guest retire 1–2 instructions then fault; that scores better than ADDPROT without RUNCTX (blend 0.20, S=0). Flat search (500 gens on 4 guests, no promotion gate) is the only cell that reached 4/4.

Ablation of search-R vs curr+shape+lenient (mean 0.533): no curriculum 0.607; no shaping 0.307; hostile 0.427; no-resume 0.487; no-memerr 0.440; no-status 0.530.
Leave-one-out: the RUNCTX status CSRs are not load-bearing; resume-after-abort and imprecise supervisor mem abort each move the mean a little. None of them unblocks k1.

## Path to a bootable x86_64 ISO

The end artifact is not a Python research VM. It is a **bootable ISO** whose runtime *is* the evolved bytecode, hosted by a tiny stub, not a Linux clone.

1. **Freeze the ISA as a byte encoding** (already a 5-tuple of ints; pack as 32- or 64-bit words). No Python objects at runtime. A C or Rust interpreter/JIT of that encoding is the host.
2. **Boot shim (BIOS first, UEFI next):** Limine or a ~few-hundred-line custom Multiboot2/BIOS `boot.asm` that enters long mode, identity-maps a few GiB, loads a ramdisk blob `supervisor.bin` + guest images, and jumps to `evoos_interp`. QEMU: `qemu-system-x86_64 -cdrom evoos.iso -nographic -serial mon:stdio`. VirtualBox: the same ISO, I/O APIC on, EFI optional once UEFI Limine works.
3. **Embed the evolved program** as a flat binary in the ISO (xoriso/grub-mkrescue or Limine `limine-deploy`). The stub does not contain a scheduler, heap, or VFS — it exposes the same primitives as the research VM: physical memory, contexts as register+PC frames, RUNCTX as “run this frame for N instructions”, ADDPROT as a software MPU over the physical map, a PIT/APIC timer as RDTIME, VGA/serial as a STORE window.
4. **What the user sees:** serial/VGA text from guest STORE-mapped output (later a framebuffer range). Milestone-1 analogue: four demo tasks printing completion lines. Later: a prompt that is itself guest bytecode, not bash-on-Linux.
5. **Test ladder:** (a) host interpreter on Linux matching the Python VM on golden traces, (b) QEMU ISO serial log vs the same traces, (c) VirtualBox screenshot/serial, BIOS then UEFI.
6. **Non-conventional target, not xv6-in-a-trenchcoat:** do *not* grow a Unix process table, page allocator, or POSIX VFS as privileged code. Aim for a **single address-space capability machine**: guest “programs” are contexts with MPU ranges; naming and persistence are discovered as STORE protocols against a block window; multiplexing stays whatever bytecode evolution found (RR-like, batched, or stranger). The stub is firmware; the OS *is* the supervisor blob. If we need Linux syscalls, they are a translation layer in bytecode under differential tests — never the native architecture.

