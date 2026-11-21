"""Canonical binary image for the frozen EvoOS ISA.

A program is five integer words per instruction (op, a, b, c, imm). A machine
image is that encoding plus firmware memory, guest code, and config flags.
Nothing in this module changes interpreter semantics.
"""

from __future__ import annotations

import struct
from typing import Sequence

from evoos.vm.instruction_set import Instr
from evoos.vm.machine import Machine, MachineConfig

IMAGE_MAGIC = 0x30535645  # 'EVS0' little-endian
IMAGE_VERSION = 1
IMAGE_HEADER_BYTES = 160

FLAG_STICKY_GUEST_FAULT = 1 << 0
FLAG_SUPERVISOR_HALT_ON_MEM = 1 << 1
FLAG_RUNCTX_POSTS_STATUS = 1 << 2

IO_BASE_DEFAULT = 1920
IO_BOUND_DEFAULT = 1984

_U32 = struct.Struct("<I")
_I32 = struct.Struct("<i")
_HDR = struct.Struct("<20I32s32s16s")
_CODE_ENT = struct.Struct("<IIII")  # ctx, n_instrs, offset, reserved
_MEM_INIT = struct.Struct("<ii")  # addr, value
_LEGAL = struct.Struct("<Iii")  # ctx, lo, hi
_EXPECT = struct.Struct("<IiiI")  # ctx, addr, value, reserved
_INSTR = struct.Struct("<iiiii")
_NAME = 32


def config_flags(cfg: MachineConfig) -> int:
    f = 0
    if cfg.sticky_guest_fault:
        f |= FLAG_STICKY_GUEST_FAULT
    if cfg.supervisor_halt_on_mem:
        f |= FLAG_SUPERVISOR_HALT_ON_MEM
    if cfg.runctx_posts_status:
        f |= FLAG_RUNCTX_POSTS_STATUS
    return f


def flags_to_config(flags: int, **kwargs) -> MachineConfig:
    kwargs.setdefault("sticky_guest_fault", bool(flags & FLAG_STICKY_GUEST_FAULT))
    kwargs.setdefault("supervisor_halt_on_mem", bool(flags & FLAG_SUPERVISOR_HALT_ON_MEM))
    kwargs.setdefault("runctx_posts_status", bool(flags & FLAG_RUNCTX_POSTS_STATUS))
    return MachineConfig(**kwargs)


def _pad_name(s: str) -> bytes:
    b = s.encode("ascii", "replace")[: _NAME - 1]
    return b + b"\x00" * (_NAME - len(b))


def _read_name(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("ascii", "replace")


class ImageMeta:
    """Firmware-side labels and expected stores. Not ISA state."""

    def __init__(
        self,
        origin: str = "",
        workload: str = "",
        guest_names: Sequence[str] = (),
        expected: Sequence[tuple[int, int, int]] = (),  # ctx, addr, value
        io_base: int = IO_BASE_DEFAULT,
        io_bound: int = IO_BOUND_DEFAULT,
    ) -> None:
        self.origin = origin
        self.workload = workload
        self.guest_names = tuple(guest_names)
        self.expected = tuple(expected)
        self.io_base = io_base
        self.io_bound = io_bound


def dumps_image(machine: Machine, meta: ImageMeta | None = None) -> bytes:
    """Serialize initial machine state (pre-run) as an EVS0 image."""
    meta = meta or ImageMeta()
    cfg = machine.config
    code_sections: list[tuple[int, tuple[Instr, ...]]] = []
    for cid, ctx in enumerate(machine.contexts):
        if ctx.code:
            code_sections.append((cid, tuple(ctx.code)))
    mem_inits = [(i, v) for i, v in enumerate(machine.mem) if v != 0]
    legal: list[tuple[int, int, int]] = []
    for cid, rng in enumerate(machine.legal_store):
        if rng is not None:
            legal.append((cid, rng[0], rng[1]))
    expected = list(meta.expected)
    n_guests = max(0, cfg.n_contexts - 1)
    names = list(meta.guest_names)
    while len(names) < n_guests:
        names.append(f"guest{len(names) + 1}")
    names = names[:n_guests]

    header_off = 0
    table_off = IMAGE_HEADER_BYTES
    mem_off = table_off + _CODE_ENT.size * len(code_sections)
    legal_off = mem_off + _MEM_INIT.size * len(mem_inits)
    exp_off = legal_off + _LEGAL.size * len(legal)
    names_off = exp_off + _EXPECT.size * len(expected)
    blob_off = names_off + _NAME * n_guests

    code_blobs = []
    ents = []
    cursor = blob_off
    for cid, instrs in code_sections:
        blob = b"".join(_INSTR.pack(i.op, i.a, i.b, i.c, i.imm) for i in instrs)
        ents.append((cid, len(instrs), cursor, 0))
        code_blobs.append(blob)
        cursor += len(blob)

    origin = _pad_name(meta.origin)
    workload = _pad_name(meta.workload)
    reserved = b"\x00" * 16
    words = (
        IMAGE_MAGIC,
        IMAGE_VERSION,
        IMAGE_HEADER_BYTES,
        cfg.n_contexts,
        cfg.mem_words,
        cfg.max_steps,
        cfg.wall_steps,
        cfg.max_prot_regions,
        cfg.n_regs,
        config_flags(cfg),
        len(code_sections),
        len(mem_inits),
        len(legal),
        len(expected),
        n_guests,
        meta.io_base,
        meta.io_bound,
        0,
        0,
        0,
    )
    buf = bytearray(_HDR.pack(*words, origin, workload, reserved))
    assert len(buf) == IMAGE_HEADER_BYTES
    for ent in ents:
        buf += _CODE_ENT.pack(*ent)
    for addr, val in mem_inits:
        buf += _MEM_INIT.pack(addr, val)
    for row in legal:
        buf += _LEGAL.pack(*row)
    for row in expected:
        buf += _EXPECT.pack(row[0], row[1], row[2], 0)
    for nm in names:
        buf += _pad_name(nm)
    for blob in code_blobs:
        buf += blob
    _ = (header_off, mem_off, legal_off, exp_off, names_off)
    return bytes(buf)


def loads_image(data: bytes) -> tuple[Machine, ImageMeta]:
    """Load an EVS0 image into a Machine at reset (before `run`)."""
    if len(data) < IMAGE_HEADER_BYTES:
        raise ValueError("image too short")
    parsed = _HDR.unpack_from(data, 0)
    (
        magic,
        version,
        header_bytes,
        n_contexts,
        mem_words,
        max_steps,
        wall_steps,
        max_prot,
        n_regs,
        flags,
        n_code,
        n_mem,
        n_legal,
        n_exp,
        n_guests,
        io_base,
        io_bound,
        *_rest_u32,
    ) = parsed[:20]
    origin_b = parsed[20]
    workload_b = parsed[21]
    if magic != IMAGE_MAGIC:
        raise ValueError(f"bad image magic {magic:#x}")
    if version != IMAGE_VERSION:
        raise ValueError(f"unsupported image version {version}")
    if header_bytes != IMAGE_HEADER_BYTES:
        raise ValueError(f"unexpected header size {header_bytes}")
    cfg = flags_to_config(
        flags,
        mem_words=mem_words,
        n_regs=n_regs,
        n_contexts=n_contexts,
        max_steps=max_steps,
        wall_steps=wall_steps,
        max_prot_regions=max_prot,
    )
    m = Machine(cfg)
    off = IMAGE_HEADER_BYTES
    ents = []
    for _ in range(n_code):
        ents.append(_CODE_ENT.unpack_from(data, off))
        off += _CODE_ENT.size
    for _ in range(n_mem):
        addr, val = _MEM_INIT.unpack_from(data, off)
        if 0 <= addr < mem_words:
            m.mem[addr] = val
            m.last_writer[addr] = 0
        off += _MEM_INIT.size
    m.legal_store = [None] * n_contexts
    for _ in range(n_legal):
        cid, lo, hi = _LEGAL.unpack_from(data, off)
        if 0 <= cid < n_contexts:
            m.legal_store[cid] = (lo, hi)
        off += _LEGAL.size
    expected: list[tuple[int, int, int]] = []
    for _ in range(n_exp):
        cid, addr, val, _r = _EXPECT.unpack_from(data, off)
        expected.append((cid, addr, val))
        off += _EXPECT.size
    names: list[str] = []
    for _ in range(n_guests):
        names.append(_read_name(data[off : off + _NAME]))
        off += _NAME
    for cid, n_instrs, blob_off, _r in ents:
        instrs = []
        p = blob_off
        for _ in range(n_instrs):
            instrs.append(Instr(*_INSTR.unpack_from(data, p)))
            p += _INSTR.size
        if 0 <= cid < n_contexts:
            ctx = m.contexts[cid]
            ctx.code = list(instrs)
            ctx.pc = 0
            ctx.regs = [0] * n_regs
            ctx.halted = False
            ctx.fault = 0
            ctx.cycles = 0
            ctx.halt_cycle = None
            ctx.prot = []
            if cid == 0:
                ctx.data_base = 0
                ctx.data_bound = mem_words
            else:
                legal = m.legal_store[cid]
                if legal is not None:
                    ctx.data_base, ctx.data_bound = legal
                    ctx.regs[1] = legal[0]
                    ctx.regs[2] = legal[1]
    meta = ImageMeta(
        origin=_read_name(origin_b),
        workload=_read_name(workload_b),
        guest_names=names,
        expected=expected,
        io_base=io_base,
        io_bound=io_bound,
    )
    return m, meta


def mem_fingerprint(mem: Sequence[int]) -> int:
    h = 2166136261
    for w in mem:
        h ^= w & 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    return h


def format_trace(machine: Machine) -> str:
    """Canonical trace text. The C interpreter emits the same lines."""
    lines = ["# evoos-trace v1"]
    lines.append(f"cycles={machine.cycles}")
    lines.append(f"switch_count={machine.switch_count}")
    lines.append(f"halted={int(machine.halted)}")
    lines.append(f"supervisor_fault={int(machine.supervisor_fault)}")
    lines.append(f"last_run_steps={int(machine.last_run_steps)}")
    lines.append(f"last_run_fault={int(machine.last_run_fault)}")
    lines.append(f"last_run_ctx={int(machine.last_run_ctx)}")
    hits = machine.isolation_hits
    lines.append(f"isolation_n={len(hits)}")
    for cid, addr in hits:
        lines.append(f"isolation {cid},{addr}")
    for i, ctx in enumerate(machine.contexts):
        hc = -1 if ctx.halt_cycle is None else int(ctx.halt_cycle)
        regs = ",".join(str(int(r)) for r in ctx.regs)
        prot = ",".join(f"{lo}:{hi}:{fl}" for lo, hi, fl in ctx.prot)
        lines.append(
            f"ctx {i} pc={int(ctx.pc)} halted={int(ctx.halted)} "
            f"fault={int(ctx.fault)} cycles={int(ctx.cycles)} "
            f"halt_cycle={hc} base={int(ctx.data_base)} bound={int(ctx.data_bound)} "
            f"regs={regs} prot={prot}"
        )
    lines.append(f"memfp={mem_fingerprint(machine.mem)}")
    log = machine.runctx_log
    lines.append(f"runctx_n={len(log)}")
    for cyc, cid, ran in log:
        lines.append(f"runctx {cyc},{cid},{ran}")
    return "\n".join(lines) + "\n"


__all__ = [
    "IMAGE_MAGIC",
    "IMAGE_VERSION",
    "ImageMeta",
    "dumps_image",
    "format_trace",
    "loads_image",
    "mem_fingerprint",
]
