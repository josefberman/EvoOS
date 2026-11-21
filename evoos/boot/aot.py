"""AOT-compile EvoOS supervisor bytecode to native C (ring 0).

The generated function is straight-line x86_64 after gcc. Privileged
ops become trampoline calls (RUNCTX, ADDPROT, CTX*). No policy lives
in those helpers.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from evoos.boot.pack import (
    PERSIST_ELITE_PATH,
    persist_iso_kind,
    persist_oneshot_storage,
    select_supervisor,
)
from evoos.vm.instruction_set import Instr, Op, parse_disassembly

_R = ["0u", "r1", "r2", "r3", "r4", "r5", "r6", "r7"]


def _reg(i: int) -> str:
    if i <= 0 or i > 7:
        return "0u"
    return _R[i]


def _wreg(i: int, expr: str) -> str:
    if i <= 0 or i > 7:
        return "/* r0 */ (void)0"
    return f"{_R[i]} = (uint32_t)({expr})"


def _emit_fn(instrs: tuple[Instr, ...] | list[Instr], fn: str) -> list[str]:
    n = len(instrs)
    lines: list[str] = [
        f"void {fn}(void) {{",
        "    uint32_t r1 = 0, r2 = 0, r3 = 0, r4 = 0, r5 = 0, r6 = 0, r7 = 0;",
        "    uint32_t tmp;",
        "    (void)tmp; (void)r1; (void)r2; (void)r3; (void)r4; (void)r5; (void)r6; (void)r7;",
        "    static void *disptab[] = {",
    ]
    for pc in range(n):
        lines.append(f"        &&L{pc},")
    lines.append("    };")
    lines.append("    (void)disptab;")
    if n == 0:
        lines.append("    return;")
        lines.append("}")
        lines.append("")
        return lines

    for pc, ins in enumerate(instrs):
        op, a, b, c, imm = ins.op, ins.a, ins.b, ins.c, ins.imm
        lines.append(f"L{pc}:")
        ra, rb, rc = _reg(a), _reg(b), _reg(c)
        tgt = pc + 1 + imm
        nxt = f"goto L{pc + 1};" if pc + 1 < n else "return;"

        if op == Op.HALT:
            lines.append("    return;")
        elif op == Op.NOP:
            lines.append(f"    {nxt}")
        elif op == Op.MOV:
            lines.append(f"    {_wreg(a, rb)};")
            lines.append(f"    {nxt}")
        elif op == Op.MOVI:
            lines.append(f"    {_wreg(a, f'(uint32_t){imm}')};")
            lines.append(f"    {nxt}")
        elif op == Op.ADD:
            lines.append(f"    {_wreg(a, f'{rb} + {rc}')};")
            lines.append(f"    {nxt}")
        elif op == Op.SUB:
            lines.append(f"    {_wreg(a, f'{rb} - {rc}')};")
            lines.append(f"    {nxt}")
        elif op == Op.MUL:
            lines.append(f"    {_wreg(a, f'{rb} * {rc}')};")
            lines.append(f"    {nxt}")
        elif op == Op.AND:
            lines.append(f"    {_wreg(a, f'{rb} & {rc}')};")
            lines.append(f"    {nxt}")
        elif op == Op.OR:
            lines.append(f"    {_wreg(a, f'{rb} | {rc}')};")
            lines.append(f"    {nxt}")
        elif op == Op.XOR:
            lines.append(f"    {_wreg(a, f'{rb} ^ {rc}')};")
            lines.append(f"    {nxt}")
        elif op == Op.SHL:
            lines.append(f"    {_wreg(a, f'{rb} << ({rc} & 31u)')};")
            lines.append(f"    {nxt}")
        elif op == Op.SHR:
            lines.append(f"    {_wreg(a, f'(uint32_t){rb} >> ({rc} & 31u)')};")
            lines.append(f"    {nxt}")
        elif op == Op.NOT:
            lines.append(f"    {_wreg(a, f'~{rb}')};")
            lines.append(f"    {nxt}")
        elif op == Op.ADDI:
            lines.append(f"    {_wreg(a, f'{rb} + (uint32_t){imm}')};")
            lines.append(f"    {nxt}")
        elif op == Op.LOAD:
            lines.append(f"    tmp = (uint32_t)({rb} + (uint32_t){imm});")
            lines.append(f"    {_wreg(a, 'evoos_hw_load((int32_t)tmp)')};")
            lines.append(f"    {nxt}")
        elif op == Op.STORE:
            lines.append(f"    evoos_hw_store((int32_t)({ra} + (uint32_t){imm}), (int32_t){rb});")
            lines.append(f"    {nxt}")
        elif op == Op.CMP:
            lines.append(f"    tmp = ((int32_t){rb} < (int32_t){rc}) ? (uint32_t)-1 : ((int32_t){rb} > (int32_t){rc}) ? 1u : 0u;")
            lines.append(f"    {_wreg(a, 'tmp')};")
            lines.append(f"    {nxt}")
        elif op == Op.BEQ:
            if 0 <= tgt < n:
                lines.append(f"    if ({ra} == {rb}) goto L{tgt};")
            else:
                lines.append(f"    if ({ra} == {rb}) return;")
            lines.append(f"    {nxt}")
        elif op == Op.BNE:
            if 0 <= tgt < n:
                lines.append(f"    if ({ra} != {rb}) goto L{tgt};")
            else:
                lines.append(f"    if ({ra} != {rb}) return;")
            lines.append(f"    {nxt}")
        elif op == Op.BLT:
            if 0 <= tgt < n:
                lines.append(f"    if ((int32_t){ra} < (int32_t){rb}) goto L{tgt};")
            else:
                lines.append(f"    if ((int32_t){ra} < (int32_t){rb}) return;")
            lines.append(f"    {nxt}")
        elif op == Op.BGE:
            if 0 <= tgt < n:
                lines.append(f"    if ((int32_t){ra} >= (int32_t){rb}) goto L{tgt};")
            else:
                lines.append(f"    if ((int32_t){ra} >= (int32_t){rb}) return;")
            lines.append(f"    {nxt}")
        elif op == Op.JMP:
            if 0 <= tgt < n:
                lines.append(f"    goto L{tgt};")
            else:
                lines.append("    return;")
        elif op == Op.JAL:
            lines.append(f"    {_wreg(a, str(pc + 1))};")
            if 0 <= tgt < n:
                lines.append(f"    goto L{tgt};")
            else:
                lines.append("    return;")
        elif op == Op.JR:
            lines.append(f"    tmp = {ra};")
            lines.append(f"    if (tmp < {n}u) goto *disptab[tmp];")
            lines.append("    return;")
        elif op == Op.RUNCTX:
            lines.append(f"    evoos_hw_runctx((int32_t){ra}, (int32_t){rb});")
            lines.append(f"    {nxt}")
        elif op == Op.RUNCTXI:
            lines.append(f"    evoos_hw_runctx((int32_t){ra}, {imm});")
            lines.append(f"    {nxt}")
        elif op == Op.CTXGET:
            lines.append(f"    {_wreg(a, f'(uint32_t)evoos_hw_ctxget((int32_t){rb}, {imm})')};")
            lines.append(f"    {nxt}")
        elif op == Op.CTXSET:
            lines.append(f"    evoos_hw_ctxset((int32_t){ra}, {imm}, (int32_t){rb});")
            lines.append(f"    {nxt}")
        elif op == Op.ADDPROT:
            lines.append(f"    evoos_hw_addprot((int32_t){ra}, (int32_t){rb}, (int32_t){rc});")
            lines.append(f"    {nxt}")
        elif op == Op.CLRPROT:
            lines.append(f"    evoos_hw_clrprot((int32_t){ra});")
            lines.append(f"    {nxt}")
        elif op == Op.RDTIME:
            lines.append(f"    {_wreg(a, '(uint32_t)hw.cycles')};")
            lines.append(f"    {nxt}")
        elif op == Op.RDFAULT:
            lines.append(f"    {_wreg(a, '0')};")
            lines.append(f"    {nxt}")
        elif op == Op.BLKRD:
            lines.append(f"    {_wreg(a, f'(uint32_t)evoos_hw_blkrd((int32_t){rb}, (int32_t){rc})')};")
            lines.append(f"    {nxt}")
        elif op == Op.BLKWR:
            lines.append(f"    evoos_hw_blkwr((int32_t){ra}, (int32_t){rb});")
            lines.append(f"    {nxt}")
        elif op == Op.SVC:
            lines.append(f"    evoos_hw_svc((int32_t){ra});")
            lines.append(f"    {nxt}")
        else:
            lines.append("    return;")

    lines.append("}")
    lines.append("")
    return lines


def emit_c(
    instrs: tuple[Instr, ...] | list[Instr],
    origin: str = "",
    note: str = "",
    persist_handler: tuple[Instr, ...] | list[Instr] | None = None,
    persist_origin: str = "none",
) -> str:
    lines: list[str] = [
        "/* Generated by python -m evoos.boot.aot — do not edit. */",
        '#include "hw.h"',
        "",
        f'const char supervisor_origin[] = "{origin or "aot"}";',
        f'const char supervisor_note[] = "{note or "isa-aot"}";',
        f'const char persist_storage_origin[] = "{persist_origin or "none"}";',
        "",
    ]
    lines.extend(_emit_fn(instrs, "supervisor_main"))
    if persist_handler:
        lines.extend(_emit_fn(persist_handler, "persist_storage_main"))
    else:
        lines.append("void persist_storage_main(void) {}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="AOT-compile a supervisor to native C")
    p.add_argument("--supervisor", default="evolved_open")
    p.add_argument("--from-file", default="", help="disassembly file instead of a named supervisor")
    p.add_argument("--origin", default="")
    p.add_argument("-o", "--output", required=True)
    args = p.parse_args(argv)
    if args.from_file:
        instrs = parse_disassembly(Path(args.from_file).read_text())
        origin = args.origin or Path(args.from_file).stem
    else:
        instrs, origin = select_supervisor(args.supervisor)
        if args.origin:
            origin = args.origin
    note = "aot-native-ring0"
    persist_handler = None
    persist_origin = "none"
    kind = (args.supervisor or "").lower().replace("-", "_")
    # Persist-only supervisors already handle LAST_SVC in-loop. Attach the
    # oneshot handler only to s99-style live ISOs so w/r/z use evolved storage
    # without replacing the scheduler.
    if (
        not args.from_file
        and kind in ("evolved_open", "open", "live", "evolved_open_persist")
        and persist_iso_kind() == "evolved_persist"
        and PERSIST_ELITE_PATH.exists()
    ):
        persist_handler = persist_oneshot_storage(
            parse_disassembly(PERSIST_ELITE_PATH.read_text())
        )
        header = PERSIST_ELITE_PATH.read_text().splitlines()[0].lstrip("# ").split()
        persist_origin = header[0] if header else "evolved_persist"
    Path(args.output).write_text(
        emit_c(
            instrs,
            origin=origin,
            note=note,
            persist_handler=persist_handler,
            persist_origin=persist_origin,
        )
    )
    print(
        f"wrote {args.output} origin={origin} n={len(instrs)} storage={persist_origin}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
