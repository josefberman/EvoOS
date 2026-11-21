"""Stage 5: bootable x86_64 ISO whose runtime is the evolved bytecode.

The stub is firmware, not a kernel: a C/Rust interpreter or JIT of the
frozen ISA, plus a Limine or Multiboot2 shim (BIOS first, UEFI next).
No Linux, no process table, no VFS in the stub. Guest STORE windows map
to serial/VGA. Test ladder: host interpreter vs Python VM golden traces,
then `qemu-system-x86_64 -cdrom evoos.iso`, then VirtualBox.

See experiments/scheduling/results/round3/summary.md (Path to a bootable ISO).
Not used in milestone 1.
"""

STAGE = 5
STATUS = "placeholder"
