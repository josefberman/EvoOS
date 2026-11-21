"""Typed, verifiable bytecode for the EvoOS virtual computer.

Design rule (no architectural priors)
-------------------------------------
The ISA is a small *machine*. It is not an operating system and must not
become one by smuggling OS policy into the opcode names.

Included: load/store, arithmetic, compare/branch, a primitive that runs a
hardware context for N steps and returns, a cycle timer, and range-based
memory-protection registers (an MMU-like mechanism, not `allocate_page`).

Excluded, on purpose: `schedule_process`, `yield_to_next`, `allocate_page`,
run queues, priority bands, wait queues, address-space identifiers as
first-class process objects, or any other traditional OS component.
Those organizations, if they appear, must be *discovered* as patterns
in ordinary bytecode.

Encoding
--------
Each instruction is five integer words ``(op, a, b, c, imm)``. No Python
objects are required at rest or at runtime. The frozen on-disk form is
little-endian signed 32-bit words; see ``docs/isa.md`` and `dumps_program`.
`encode_word` packs into 32 bits with a 12-bit immediate and is **lossy**
(dumps only). Programs are Harvard-style: each context has its own
instruction list; data lives in a shared word memory.
"""

from __future__ import annotations

import re
import struct
from enum import IntEnum
from typing import Iterable, NamedTuple, Sequence


N_REGS = 8
IMM_MIN = -(1 << 20)
IMM_MAX = (1 << 20) - 1
MAGIC = 0x0E2015

# Firmware / info-block layout in data memory (word addresses).
FW_MAGIC = 0
FW_N_GUESTS = 1
FW_MEM_WORDS = 2
FW_MAX_STEPS = 3
FW_GUEST_STRIDE = 4
FW_GUEST_TABLE = 16  # ctx, data_base, data_bound, reserved


class Op(IntEnum):
    HALT = 0
    NOP = 1
    MOV = 2
    MOVI = 3
    ADD = 4
    SUB = 5
    MUL = 6
    AND = 7
    OR = 8
    XOR = 9
    SHL = 10
    SHR = 11
    NOT = 12
    ADDI = 13
    LOAD = 14
    STORE = 15
    CMP = 16
    BEQ = 17
    BNE = 18
    BLT = 19
    BGE = 20
    JMP = 21
    JAL = 22
    JR = 23
    # Hardware primitives — not OS policy.
    RUNCTX = 24  # run context regs[a] for regs[b] steps, then return (invalid id = nop)
    RUNCTXI = 25  # run context regs[a] for `imm` steps, then return (invalid id = nop)
    CTXGET = 26  # rd = context[regs[b]].field[imm]
    CTXSET = 27  # context[regs[a]].field[imm] = regs[b]
    ADDPROT = 28  # add [regs[b], regs[c]) RW range on context regs[a]
    CLRPROT = 29  # clear protection ranges on context regs[a]
    RDTIME = 30  # rd = global cycle counter
    RDFAULT = 31  # rd = last fault code of current context
    # Encoding version 2 (ISA profile 3). Opcodes 0–31 are unchanged.
    BLKRD = 32  # rd = status; copy device block regs[b] into mem[regs[c]..]
    BLKWR = 33  # write mem[regs[b]..] to device block regs[a]
    SVC = 34  # unprivileged trap; request code in regs[a]; yield to supervisor


PRIVILEGED = frozenset(
    {
        Op.RUNCTX,
        Op.RUNCTXI,
        Op.CTXGET,
        Op.CTXSET,
        Op.ADDPROT,
        Op.CLRPROT,
        Op.BLKRD,
        Op.BLKWR,
    }
)


class Svc(IntEnum):
    """Guest trap codes. Not opcodes. Hardware int 0x80 uses the same numbers in rax."""

    NONE = 0
    PUT = 1
    GET = 2


class BlkStatus(IntEnum):
    OK = 0
    BAD = 1
    OOB = 2


class CtxField(IntEnum):
    PC = 0
    HALTED = 1
    FAULT = 2
    CYCLES = 3
    DATA_BASE = 4
    DATA_BOUND = 5
    N_CONTEXTS = 6
    STATUS = 7
    # Machine-wide result of the last RUNCTX (readable on any context id).
    LAST_RUN_STEPS = 8
    LAST_RUN_FAULT = 9
    LAST_RUN_CTX = 10
    LAST_BLK_STATUS = 11
    N_BLOCKS = 12
    BLK_WORDS = 13
    LAST_SVC = 14
    LAST_SVC_CTX = 15


class Fault(IntEnum):
    NONE = 0
    PROT = 1
    MEM = 2
    PC = 3
    CTX = 4
    PRIV = 5
    PROT_FULL = 6
    BAD_OP = 7
    BUDGET = 8


class Perm(IntEnum):
    R = 1
    W = 2
    RW = 3


class Instr(NamedTuple):
    op: int
    a: int = 0
    b: int = 0
    c: int = 0
    imm: int = 0

    def pretty(self) -> str:
        name = Op(self.op).name if self.op in Op._value2member_map_ else f"OP{self.op}"
        return f"{name:8} a={self.a} b={self.b} c={self.c} imm={self.imm}"


def privileged_ops() -> frozenset[Op]:
    return PRIVILEGED


_REG_NAMES = {f"r{i}": i for i in range(N_REGS)}
_REG_NAMES.update({f"R{i}": i for i in range(N_REGS)})


def _parse_reg(tok: str) -> int:
    t = tok.strip().rstrip(",").lower()
    if t not in _REG_NAMES:
        raise ValueError(f"unknown register {tok!r}")
    return _REG_NAMES[t]


def _parse_int(tok: str, labels: dict[str, int] | None = None) -> int:
    t = tok.strip().rstrip(",")
    if labels is not None and t in labels:
        return labels[t]
    try:
        return int(t, 0)
    except ValueError as exc:
        raise ValueError(f"not an integer or label: {tok!r}") from exc


def assemble(source: str) -> tuple[Instr, ...]:
    """Two-pass assembler. Labels, `#` comments, comma-separated operands.

    Branch / JMP / JAL immediates are PC-relative to the *next* instruction:
    `target = pc + 1 + imm`. A label used as the last operand of those ops
    is converted automatically.
    """
    raw_lines: list[str] = []
    for line in source.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            raw_lines.append(stripped)

    labels: dict[str, int] = {}
    bodies: list[str] = []
    pc = 0
    for line in raw_lines:
        while True:
            if ":" not in line:
                bodies.append(line)
                pc += 1
                break
            lab, rest = line.split(":", 1)
            lab = lab.strip()
            if not lab or " " in lab or lab.lower() in _OP_ALIASES:
                bodies.append(line)
                pc += 1
                break
            if lab in labels:
                raise ValueError(f"duplicate label {lab!r}")
            labels[lab] = pc
            line = rest.strip()
            if not line:
                break

    out: list[Instr] = []
    for pc, line in enumerate(bodies):
        parts = [p for p in line.replace(",", " ").split() if p]
        if not parts:
            continue
        op = _OP_ALIASES.get(parts[0].upper())
        if op is None:
            raise ValueError(f"unknown opcode {parts[0]!r} at pc={pc}")
        args = parts[1:]
        a = b = c = imm = 0

        def rel(tok: str) -> int:
            t = tok.strip().rstrip(",")
            if t in labels:
                return labels[t] - (pc + 1)
            return _parse_int(t)

        if op in (Op.HALT, Op.NOP):
            pass
        elif op in (Op.RDTIME, Op.RDFAULT, Op.CLRPROT, Op.SVC):
            a = _parse_reg(args[0])
        elif op == Op.MOV:
            a, b = _parse_reg(args[0]), _parse_reg(args[1])
        elif op == Op.MOVI:
            a, imm = _parse_reg(args[0]), _parse_int(args[1], labels)
        elif op in (
            Op.ADD,
            Op.SUB,
            Op.MUL,
            Op.AND,
            Op.OR,
            Op.XOR,
            Op.SHL,
            Op.SHR,
            Op.CMP,
            Op.ADDPROT,
            Op.BLKRD,
        ):
            a, b, c = _parse_reg(args[0]), _parse_reg(args[1]), _parse_reg(args[2])
        elif op in (Op.NOT, Op.RUNCTX, Op.BLKWR):
            a, b = _parse_reg(args[0]), _parse_reg(args[1])
        elif op in (Op.ADDI, Op.LOAD, Op.STORE):
            a, b, imm = _parse_reg(args[0]), _parse_reg(args[1]), _parse_int(args[2], labels)
        elif op in (Op.BEQ, Op.BNE, Op.BLT, Op.BGE):
            a, b = _parse_reg(args[0]), _parse_reg(args[1])
            imm = rel(args[2])
        elif op == Op.JMP:
            imm = rel(args[0])
        elif op == Op.JAL:
            a = _parse_reg(args[0])
            imm = rel(args[1])
        elif op == Op.JR:
            a = _parse_reg(args[0])
        elif op == Op.RUNCTXI:
            a, imm = _parse_reg(args[0]), _parse_int(args[1], labels)
        elif op in (Op.CTXGET, Op.CTXSET):
            a, b = _parse_reg(args[0]), _parse_reg(args[1])
            imm = _parse_field(args[2])
        else:
            raise ValueError(f"unhandled op {op}")
        out.append(Instr(int(op), a, b, c, imm))
    return tuple(out)


def _is_int(tok: str) -> bool:
    t = tok.strip().rstrip(",")
    try:
        int(t, 0)
        return True
    except ValueError:
        return False


def _parse_field(tok: str) -> int:
    t = tok.strip().rstrip(",").upper()
    if t in CtxField.__members__:
        return int(CtxField[t])
    return int(t, 0)


_OP_ALIASES = {op.name: op for op in Op}


def disassemble(instrs: Sequence[Instr] | Iterable[Instr], labels: bool = True) -> str:
    items = list(instrs)
    lines: list[str] = []
    for pc, ins in enumerate(items):
        try:
            op = Op(ins.op)
        except ValueError:
            lines.append(f"{pc:4d}:  {ins.pretty()}")
            continue
        extra = ""
        if op in (Op.BEQ, Op.BNE, Op.BLT, Op.BGE, Op.JMP, Op.JAL) and labels:
            extra = f"  # -> {pc + 1 + ins.imm}"
        lines.append(f"{pc:4d}:  {ins.pretty()}{extra}")
    return "\n".join(lines)


def encode_word(ins: Instr) -> int:
    """Pack into a 32-bit word (lossy on large immediates). For dumps only."""
    imm = ins.imm & 0xFFF
    return (ins.op & 0xFF) | ((ins.a & 0xF) << 8) | ((ins.b & 0xF) << 12) | ((ins.c & 0xF) << 16) | (imm << 20)


def instrs_to_jsonable(instrs: Sequence[Instr]) -> list[list[int]]:
    return [[ins.op, ins.a, ins.b, ins.c, ins.imm] for ins in instrs]


def instrs_from_jsonable(rows: Sequence[Sequence[int]]) -> tuple[Instr, ...]:
    return tuple(Instr(*row) for row in rows)


# Frozen program blob: little-endian u32 header then 5×i32 per instruction.
# Layout: magic=MAGIC, version, n_instrs, reserved, then n_instrs*(op,a,b,c,imm).
# version 1: opcodes 0–31. version 2: may include BLKRD/BLKWR/SVC (32–34).
PROG_VERSION = 1
PROG_VERSION_MAX = 2
_PROG_HDR = struct.Struct("<IIII")
_INSTR_LE = struct.Struct("<iiiii")
INSTR_WORDS = 5  # op, a, b, c, imm


def program_version(instrs: Sequence[Instr]) -> int:
    return 2 if any(ins.op >= 32 for ins in instrs) else 1


def dumps_program(instrs: Sequence[Instr]) -> bytes:
    """Serialize a program as integer words. Inverse of `loads_program`."""
    body = b"".join(_INSTR_LE.pack(ins.op, ins.a, ins.b, ins.c, ins.imm) for ins in instrs)
    return _PROG_HDR.pack(MAGIC, program_version(instrs), len(instrs), 0) + body


def loads_program(data: bytes) -> tuple[Instr, ...]:
    """Deserialize a `dumps_program` blob. Raises ValueError on a bad header."""
    if len(data) < _PROG_HDR.size:
        raise ValueError("program blob too short")
    magic, version, n, _res = _PROG_HDR.unpack_from(data, 0)
    if magic != MAGIC:
        raise ValueError(f"bad program magic {magic:#x}")
    if version < 1 or version > PROG_VERSION_MAX:
        raise ValueError(f"unsupported program version {version}")
    need = _PROG_HDR.size + n * _INSTR_LE.size
    if len(data) < need:
        raise ValueError("program blob truncated")
    out: list[Instr] = []
    off = _PROG_HDR.size
    for _ in range(n):
        out.append(Instr(*_INSTR_LE.unpack_from(data, off)))
        off += _INSTR_LE.size
    return tuple(out)


_DISASM_LINE = re.compile(
    r"^\s*\d+:\s+(\S+)\s+a=(-?\d+)\s+b=(-?\d+)\s+c=(-?\d+)\s+imm=(-?\d+)"
)


def parse_disassembly(text: str) -> tuple[Instr, ...]:
    """Parse `disassemble()` / elite example text back into instructions."""
    out: list[Instr] = []
    for line in text.splitlines():
        m = _DISASM_LINE.match(line)
        if not m:
            continue
        name = m.group(1).upper()
        if name not in _OP_ALIASES:
            raise ValueError(f"unknown opcode in disassembly: {name}")
        out.append(
            Instr(
                int(_OP_ALIASES[name]),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                int(m.group(5)),
            )
        )
    if not out:
        raise ValueError("no instructions in disassembly")
    return tuple(out)
