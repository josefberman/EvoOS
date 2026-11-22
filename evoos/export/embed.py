"""Embed format for the boot track: C-hw elites + evaluator cases.

Canonical encoding is lossless 5-tuples (op, a, b, c, imm). A packed 32-bit
word is provided for dumps only (12-bit immediate, same as `encode_word`).

The ISO stub should:
  1. parse `evoos-embed-v1` JSON, or
  2. mmap the `.bin` quintuple stream (little-endian i32 × 5 per instruction).

It must not invent opcodes. Interpreter/JIT the 5-tuple ISA (0–34).
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any, Iterable, Sequence

from evoos.evaluator.hw_workloads import routing_stages
from evoos.evaluator.workloads import heldout_workloads, training_workloads
from evoos.vm.instruction_set import (
    MAGIC,
    Op,
    Instr,
    encode_word,
    instrs_from_jsonable,
    instrs_to_jsonable,
)
from evoos.vm.sandbox import GuestImage, Workload

FORMAT_VERSION = 1
FORMAT_NAME = "evoos-embed-v1"
BIN_MAGIC = 0x45564F53  # 'EVOS'
TUPLE_STRUCT = struct.Struct("<5i")  # op, a, b, c, imm
HEADER_STRUCT = struct.Struct("<IHH")  # magic, version, n_instr


def isa_manifest() -> dict[str, Any]:
    names = [op.name for op in Op]
    return {
        "n_op": len(names),
        "names": names,
        "fields": ["op", "a", "b", "c", "imm"],
        "encode_word": "op:8 a:4 b:4 c:4 imm:12 (lossy; JSON 5-tuples are canonical)",
        "privileged": [op.name for op in Op if int(op) >= 24 and op.name != "SVC"],
        "magic": MAGIC,
    }


def guest_to_json(g: GuestImage) -> dict[str, Any]:
    return {
        "name": g.name,
        "role": g.role,
        "code": instrs_to_jsonable(g.code),
        "data_base": g.data_base,
        "data_bound": g.data_bound,
        "init": {str(k): int(v) for k, v in g.init.items()},
        "expected": {str(k): int(v) for k, v in g.expected.items()},
        "expected_off": {str(k): int(v) for k, v in (g.expected_off or {}).items()},
        "home_block": g.home_block,
        "token": g.token,
        "pair": g.pair,
        "messages": list(g.messages or ()),
        "needed_cycles": g.needed_cycles,
    }


def workload_to_json(w: Workload) -> dict[str, Any]:
    return {
        "name": w.name,
        "split": w.split,
        "kind": w.kind,
        "guests": [guest_to_json(g) for g in w.guests],
    }


def pack_bin(instrs: Sequence[Instr]) -> bytes:
    header = HEADER_STRUCT.pack(BIN_MAGIC, FORMAT_VERSION, len(instrs))
    body = b"".join(TUPLE_STRUCT.pack(ins.op, ins.a, ins.b, ins.c, ins.imm) for ins in instrs)
    return header + body


def unpack_bin(blob: bytes) -> tuple[Instr, ...]:
    magic, ver, n = HEADER_STRUCT.unpack_from(blob, 0)
    if magic != BIN_MAGIC:
        raise ValueError(f"bad bin magic {magic:#x}")
    if ver != FORMAT_VERSION:
        raise ValueError(f"unsupported embed version {ver}")
    off = HEADER_STRUCT.size
    out = []
    for _ in range(n):
        op, a, b, c, imm = TUPLE_STRUCT.unpack_from(blob, off)
        out.append(Instr(op, a, b, c, imm))
        off += TUPLE_STRUCT.size
    return tuple(out)


def embed_document(
    *,
    supervisor: Sequence[Instr],
    origin: str = "",
    label: str = "",
    cases: Iterable[Workload] | None = None,
    machine: dict[str, Any] | None = None,
    notes: str = "",
) -> dict[str, Any]:
    instrs = tuple(supervisor)
    return {
        "format": FORMAT_NAME,
        "version": FORMAT_VERSION,
        "isa": isa_manifest(),
        "supervisor": {
            "origin": origin,
            "label": label,
            "instrs": instrs_to_jsonable(instrs),
            "n_instr": len(instrs),
            "words_lossy": [encode_word(ins) for ins in instrs],
        },
        "machine": machine
        or {
            "n_contexts": 9,
            "n_blocks": 16,
            "block_words": 32,
            "blk_latency": 2,
            "max_steps": 8000,
            "mem_words": 2048,
            "lenient": True,
        },
        "cases": [workload_to_json(w) for w in (cases or ())],
        "notes": notes,
    }


def default_boot_cases() -> list[Workload]:
    cases: list[Workload] = []
    for st in routing_stages():
        cases.extend(st["train"])
        cases.extend(st["heldout"])
    # Keep the original 4-guest mix for the boot-track smoke path.
    cases.extend(training_workloads())
    cases.extend(heldout_workloads())
    return cases


def write_embed(path: Path, doc: dict[str, Any], instrs: Sequence[Instr]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2))
    path.with_suffix(".bin").write_bytes(pack_bin(instrs))


def read_embed(path: Path) -> tuple[dict[str, Any], tuple[Instr, ...]]:
    doc = json.loads(Path(path).read_text())
    instrs = instrs_from_jsonable(doc["supervisor"]["instrs"])
    return doc, instrs
