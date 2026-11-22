"""Stage 3 placeholder: persistence / a storage device.

A block device with read/write of fixed-size sectors is acceptable hardware.
A `create_file` or VFS opcode is not. Guests would request durable writes
through memory-mapped device registers; the supervisor would have to discover
buffering, naming, and crash-consistent order.
"""

STAGE = 3
STATUS = "placeholder"
