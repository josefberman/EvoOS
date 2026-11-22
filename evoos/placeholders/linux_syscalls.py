"""Stage 4 placeholder: Linux syscall compatibility via differential tests.

Proposed method: run the same guest binaries under (a) a discovered supervisor
that implements a syscall gate in bytecode and (b) Linux. Compare traces for
a small ABI slice (read/write/brk/exit). Do not bake Linux's scheduler or
mm into the ISA; only expose a trap-to-supervisor instruction if needed.
"""

STAGE = 4
STATUS = "placeholder"
