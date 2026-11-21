"""Stage 5 is implemented on the boot track: see native/ and docs/isa.md.

The stub is firmware, not a kernel. `make iso` builds evoos.iso with Limine
(BIOS + UEFI) around a C interpreter of the frozen 5-word ISA.
"""

STAGE = 5
STATUS = "boot-track"
ISO = "artifacts/evoos.iso"
