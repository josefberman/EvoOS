"""Deterministic virtual computer: memory, contexts, execution, I/O primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from evoos.vm.instruction_set import (
    N_REGS,
    CtxField,
    Fault,
    Instr,
    Op,
    Perm,
    PRIVILEGED,
)


MASK32 = 0xFFFFFFFF
SIGN32 = 0x80000000
MAX_PROT_REGIONS = 4


def wrap32(x: int) -> int:
    x &= MASK32
    if x & SIGN32:
        return x - 0x100000000
    return x


def as_u32(x: int) -> int:
    return x & MASK32


@dataclass
class MachineConfig:
    mem_words: int = 2048
    n_regs: int = N_REGS
    n_contexts: int = 5  # supervisor + 4 guests
    max_steps: int = 8000
    max_supervisor_len: int = 256
    max_guest_len: int = 96
    max_prot_regions: int = MAX_PROT_REGIONS
    # Safety cap so a runaway interpreter cannot hang a training loop.
    wall_steps: int = 12_000
    # Generic hardware semantics (not OS conventions). Round-2 default is hostile.
    # sticky_guest_fault: once a guest faults, further RUNCTX is a no-op until CTXSET.
    # False = RUNCTX retries the faulting instruction (standard "resume after abort").
    sticky_guest_fault: bool = True
    # supervisor_halt_on_mem: out-of-range supervisor load/store halts the machine.
    # False = post a sticky error code and skip the access (imprecise abort).
    supervisor_halt_on_mem: bool = True
    # runctx_posts_status: last RUNCTX step-count and guest fault are readable CSRs.
    runctx_posts_status: bool = False

    def substrate_name(self) -> str:
        if self.sticky_guest_fault and self.supervisor_halt_on_mem and not self.runctx_posts_status:
            return "hostile"
        parts = []
        if not self.sticky_guest_fault:
            parts.append("resume")
        if not self.supervisor_halt_on_mem:
            parts.append("memerr")
        if self.runctx_posts_status:
            parts.append("status")
        return "+".join(parts) or "hostile"


def lenient_config(**kwargs) -> MachineConfig:
    """All three generic-hardware relaxations on."""
    kwargs.setdefault("sticky_guest_fault", False)
    kwargs.setdefault("supervisor_halt_on_mem", False)
    kwargs.setdefault("runctx_posts_status", True)
    return MachineConfig(**kwargs)


@dataclass
class Context:
    code: list[Instr] = field(default_factory=list)
    pc: int = 0
    regs: list[int] = field(default_factory=lambda: [0] * N_REGS)
    halted: bool = False
    fault: int = 0
    cycles: int = 0
    halt_cycle: int | None = None
    data_base: int = 0
    data_bound: int = 0
    prot: list[tuple[int, int, int]] = field(default_factory=list)
    # Evaluator telemetry only (not an ISA CSR). Host stubs may ignore these.
    protected_cycles: int = 0
    ran_protected: bool = False

    def clone(self) -> "Context":
        c = Context(
            code=list(self.code),
            pc=self.pc,
            regs=self.regs[:],
            halted=self.halted,
            fault=self.fault,
            cycles=self.cycles,
            halt_cycle=self.halt_cycle,
            data_base=self.data_base,
            data_bound=self.data_bound,
            prot=list(self.prot),
            protected_cycles=self.protected_cycles,
            ran_protected=self.ran_protected,
        )
        return c


@dataclass
class Snapshot:
    mem: list[int]
    last_writer: list[int]
    contexts: list[Context]
    cycles: int
    halted: bool
    supervisor_fault: int
    switch_count: int
    isolation_hits: list[tuple[int, int]]
    runctx_log: list[tuple[int, int, int]]
    last_run_steps: int = 0
    last_run_fault: int = 0
    last_run_ctx: int = 0


class Machine:
    """Word-addressed machine with hardware contexts and range protection."""

    def __init__(self, config: MachineConfig | None = None) -> None:
        self.config = config or MachineConfig()
        n = self.config.mem_words
        self.mem = [0] * n
        self.last_writer = [255] * n  # 255 = never written
        self.contexts = [Context() for _ in range(self.config.n_contexts)]
        self.cycles = 0
        self.halted = False
        self.supervisor_fault = 0
        self.switch_count = 0
        self.isolation_hits: list[tuple[int, int]] = []
        self.runctx_log: list[tuple[int, int, int]] = []
        # Ground-truth store ranges for isolation detection (evaluator-owned).
        self.legal_store: list[tuple[int, int] | None] = [None] * self.config.n_contexts
        self._log_runctx = True
        self.last_run_steps = 0
        self.last_run_fault = 0
        self.last_run_ctx = 0
        # Evaluator telemetry: values written by CTXGET / RDTIME / RDFAULT.
        self.observed_consts: list[int] = []

    def snapshot(self) -> Snapshot:
        return Snapshot(
            mem=self.mem[:],
            last_writer=self.last_writer[:],
            contexts=[c.clone() for c in self.contexts],
            cycles=self.cycles,
            halted=self.halted,
            supervisor_fault=self.supervisor_fault,
            switch_count=self.switch_count,
            isolation_hits=list(self.isolation_hits),
            runctx_log=list(self.runctx_log),
            last_run_steps=self.last_run_steps,
            last_run_fault=self.last_run_fault,
            last_run_ctx=self.last_run_ctx,
        )

    def restore(self, snap: Snapshot) -> None:
        self.mem = snap.mem[:]
        self.last_writer = snap.last_writer[:]
        self.contexts = [c.clone() for c in snap.contexts]
        self.cycles = snap.cycles
        self.halted = snap.halted
        self.supervisor_fault = snap.supervisor_fault
        self.switch_count = snap.switch_count
        self.isolation_hits = list(snap.isolation_hits)
        self.runctx_log = list(snap.runctx_log)
        self.last_run_steps = snap.last_run_steps
        self.last_run_fault = snap.last_run_fault
        self.last_run_ctx = snap.last_run_ctx
        self.observed_consts = []

    def state_hash(self) -> tuple:
        ctx_part = []
        for c in self.contexts:
            ctx_part.append(
                (
                    tuple(c.regs),
                    c.pc,
                    c.halted,
                    c.fault,
                    c.cycles,
                    tuple(c.prot),
                    tuple(c.code),
                )
            )
        return (tuple(self.mem), tuple(ctx_part), self.cycles, self.halted, self.switch_count)

    def _write_reg(self, ctx: Context, rd: int, val: int) -> None:
        if rd == 0:
            return
        ctx.regs[rd] = wrap32(val)

    def _fault(self, cid: int, code: int) -> None:
        if (
            cid == 0
            and (not self.config.supervisor_halt_on_mem)
            and code in (Fault.MEM, Fault.PROT, Fault.CTX)
        ):
            # Sticky error CSR; do not latch context.fault or halt.
            self.supervisor_fault = int(code)
            return
        ctx = self.contexts[cid]
        ctx.fault = int(code)
        if cid == 0:
            self.supervisor_fault = int(code)
            self.halted = True

    def _window_covered(self, guest: Context) -> bool:
        """True if some MPU range fully contains the guest's assigned data window.

        Evaluator-only. Does not change load/store faults or any opcode.
        """
        if guest.data_bound <= guest.data_base:
            return False
        for lo, hi, _flags in guest.prot:
            if lo <= guest.data_base and hi >= guest.data_bound:
                return True
        return False

    def _prot_allows(self, cid: int, addr: int, perm: int) -> bool:
        if cid == 0:
            return True
        for lo, hi, flags in self.contexts[cid].prot:
            if lo <= addr < hi and (flags & perm):
                return True
        return False

    def _mem_load(self, cid: int, addr: int) -> int | None:
        if addr < 0 or addr >= self.config.mem_words:
            self._fault(cid, Fault.MEM)
            return None
        if not self._prot_allows(cid, addr, Perm.R):
            self._fault(cid, Fault.PROT)
            return None
        return self.mem[addr]

    def _mem_store(self, cid: int, addr: int, val: int) -> None:
        if addr < 0 or addr >= self.config.mem_words:
            self._fault(cid, Fault.MEM)
            return
        if not self._prot_allows(cid, addr, Perm.W):
            self._fault(cid, Fault.PROT)
            return
        legal = self.legal_store[cid]
        if cid != 0 and legal is not None:
            lo, hi = legal
            if not (lo <= addr < hi):
                self.isolation_hits.append((cid, addr))
        self.mem[addr] = wrap32(val)
        self.last_writer[addr] = cid

    def _ctx_field_get(self, target: Context, field: int, self_cid: int) -> int:
        if field == CtxField.PC:
            return target.pc
        if field == CtxField.HALTED:
            return int(target.halted)
        if field == CtxField.FAULT:
            return target.fault
        if field == CtxField.CYCLES:
            return target.cycles
        if field == CtxField.DATA_BASE:
            return target.data_base
        if field == CtxField.DATA_BOUND:
            return target.data_bound
        if field == CtxField.N_CONTEXTS:
            return self.config.n_contexts
        if field == CtxField.STATUS:
            return (int(target.halted)) | (int(target.fault != 0) << 1)
        if field == CtxField.LAST_RUN_STEPS:
            return self.last_run_steps if self.config.runctx_posts_status else 0
        if field == CtxField.LAST_RUN_FAULT:
            return self.last_run_fault if self.config.runctx_posts_status else 0
        if field == CtxField.LAST_RUN_CTX:
            return self.last_run_ctx if self.config.runctx_posts_status else 0
        return 0

    def _ctx_field_set(self, target: Context, field: int, val: int) -> None:
        val = wrap32(val)
        if field == CtxField.PC:
            target.pc = val
        elif field == CtxField.HALTED:
            target.halted = bool(val)
        elif field == CtxField.FAULT:
            target.fault = int(val)

    def _run_context(self, cid: int, n_steps: int) -> int:
        if cid <= 0 or cid >= self.config.n_contexts:
            self._fault(0, Fault.CTX)
            return 0
        n_steps = max(0, min(int(n_steps), self.config.max_steps))
        ran = 0
        guest = self.contexts[cid]
        if (not self.config.sticky_guest_fault) and guest.fault and not guest.halted:
            # Resume: retry the faulting instruction after the supervisor returns.
            guest.fault = 0
        for _ in range(n_steps):
            if self.halted or self.cycles >= self.config.max_steps:
                break
            if guest.halted:
                break
            if guest.fault:
                break
            covered = self._window_covered(guest)
            self._execute(cid)
            ran += 1
            if covered:
                guest.protected_cycles += 1
                guest.ran_protected = True
        self.switch_count += 1
        self.last_run_steps = ran
        self.last_run_fault = guest.fault
        self.last_run_ctx = cid
        if self._log_runctx and len(self.runctx_log) < 4096:
            self.runctx_log.append((self.cycles, cid, ran))
        return ran

    def _execute(self, cid: int) -> None:
        ctx = self.contexts[cid]
        if ctx.halted or ctx.fault:
            return
        if ctx.pc < 0 or ctx.pc >= len(ctx.code):
            # Falling off the program ends the context; it is not a machine crash.
            ctx.halted = True
            ctx.halt_cycle = self.cycles
            if cid == 0:
                self.halted = True
            return
        ins = ctx.code[ctx.pc]
        op = ins.op
        self.cycles += 1
        ctx.cycles += 1
        if self.cycles > self.config.wall_steps:
            self.halted = True
            self._fault(cid, Fault.BUDGET)
            return

        regs = ctx.regs
        a, b, c, imm = ins.a, ins.b, ins.c, ins.imm
        next_pc = ctx.pc + 1

        if op in PRIVILEGED and cid != 0:
            self._fault(cid, Fault.PRIV)
            return

        if op == Op.HALT:
            ctx.halted = True
            ctx.halt_cycle = self.cycles
            ctx.pc = next_pc
            if cid == 0:
                self.halted = True
            return
        if op == Op.NOP:
            ctx.pc = next_pc
            return
        if op == Op.MOV:
            self._write_reg(ctx, a, regs[b])
            ctx.pc = next_pc
            return
        if op == Op.MOVI:
            self._write_reg(ctx, a, imm)
            ctx.pc = next_pc
            return
        if op == Op.ADD:
            self._write_reg(ctx, a, wrap32(regs[b] + regs[c]))
            ctx.pc = next_pc
            return
        if op == Op.SUB:
            self._write_reg(ctx, a, wrap32(regs[b] - regs[c]))
            ctx.pc = next_pc
            return
        if op == Op.MUL:
            self._write_reg(ctx, a, wrap32(regs[b] * regs[c]))
            ctx.pc = next_pc
            return
        if op == Op.AND:
            self._write_reg(ctx, a, regs[b] & regs[c])
            ctx.pc = next_pc
            return
        if op == Op.OR:
            self._write_reg(ctx, a, regs[b] | regs[c])
            ctx.pc = next_pc
            return
        if op == Op.XOR:
            self._write_reg(ctx, a, regs[b] ^ regs[c])
            ctx.pc = next_pc
            return
        if op == Op.SHL:
            self._write_reg(ctx, a, wrap32(regs[b] << (as_u32(regs[c]) & 31)))
            ctx.pc = next_pc
            return
        if op == Op.SHR:
            self._write_reg(ctx, a, wrap32(as_u32(regs[b]) >> (as_u32(regs[c]) & 31)))
            ctx.pc = next_pc
            return
        if op == Op.NOT:
            self._write_reg(ctx, a, wrap32(~regs[b]))
            ctx.pc = next_pc
            return
        if op == Op.ADDI:
            self._write_reg(ctx, a, wrap32(regs[b] + imm))
            ctx.pc = next_pc
            return
        if op == Op.LOAD:
            addr = wrap32(regs[b] + imm)
            val = self._mem_load(cid, addr)
            if ctx.fault:
                if cid == 0 and not self.halted:
                    ctx.pc = next_pc
                return
            self._write_reg(ctx, a, val if val is not None else 0)
            ctx.pc = next_pc
            return
        if op == Op.STORE:
            addr = wrap32(regs[a] + imm)
            self._mem_store(cid, addr, regs[b])
            if ctx.fault:
                if cid == 0 and not self.halted:
                    ctx.pc = next_pc
                return
            ctx.pc = next_pc
            return
        if op == Op.CMP:
            rb, rc = regs[b], regs[c]
            if rb < rc:
                self._write_reg(ctx, a, -1)
            elif rb > rc:
                self._write_reg(ctx, a, 1)
            else:
                self._write_reg(ctx, a, 0)
            ctx.pc = next_pc
            return
        if op == Op.BEQ:
            ctx.pc = next_pc + imm if regs[a] == regs[b] else next_pc
            return
        if op == Op.BNE:
            ctx.pc = next_pc + imm if regs[a] != regs[b] else next_pc
            return
        if op == Op.BLT:
            ctx.pc = next_pc + imm if regs[a] < regs[b] else next_pc
            return
        if op == Op.BGE:
            ctx.pc = next_pc + imm if regs[a] >= regs[b] else next_pc
            return
        if op == Op.JMP:
            ctx.pc = next_pc + imm
            return
        if op == Op.JAL:
            self._write_reg(ctx, a, next_pc)
            ctx.pc = next_pc + imm
            return
        if op == Op.JR:
            ctx.pc = regs[a]
            return
        if op == Op.RUNCTX:
            ctx.pc = next_pc
            tid = regs[a]
            if 0 < tid < self.config.n_contexts:
                self._run_context(tid, regs[b])
            return
        if op == Op.RUNCTXI:
            ctx.pc = next_pc
            tid = regs[a]
            if 0 < tid < self.config.n_contexts:
                self._run_context(tid, imm)
            return
        if op == Op.CTXGET:
            tid = regs[b]
            if 0 <= tid < self.config.n_contexts:
                val = self._ctx_field_get(self.contexts[tid], imm, cid)
                self._write_reg(ctx, a, val)
                if cid == 0:
                    self.observed_consts.append(int(val))
            ctx.pc = next_pc
            return
        if op == Op.CTXSET:
            tid = regs[a]
            if 0 <= tid < self.config.n_contexts:
                self._ctx_field_set(self.contexts[tid], imm, regs[b])
            ctx.pc = next_pc
            return
        if op == Op.ADDPROT:
            tid = regs[a]
            if 0 < tid < self.config.n_contexts:
                target = self.contexts[tid]
                if len(target.prot) < self.config.max_prot_regions:
                    lo, hi = wrap32(regs[b]), wrap32(regs[c])
                    if hi > lo:
                        target.prot.append((lo, hi, int(Perm.RW)))
            ctx.pc = next_pc
            return
        if op == Op.CLRPROT:
            tid = regs[a]
            if 0 < tid < self.config.n_contexts:
                self.contexts[tid].prot.clear()
            ctx.pc = next_pc
            return
        if op == Op.RDTIME:
            self._write_reg(ctx, a, self.cycles)
            ctx.pc = next_pc
            return
        if op == Op.RDFAULT:
            self._write_reg(ctx, a, ctx.fault)
            ctx.pc = next_pc
            return

        self._fault(cid, Fault.BAD_OP)

    def run(self, max_steps: int | None = None) -> None:
        limit = self.config.max_steps if max_steps is None else max_steps
        while not self.halted and self.cycles < limit:
            sup = self.contexts[0]
            if sup.halted or sup.fault:
                self.halted = True
                break
            self._execute(0)
        if self.cycles >= limit and not self.halted:
            # Budget exhausted: stop, not a supervisor crash.
            self.halted = True

    def install_supervisor(self, code: Iterable[Instr]) -> None:
        self.contexts[0].code = list(code)
        self.contexts[0].pc = 0
        self.contexts[0].regs = [0] * self.config.n_regs
        self.contexts[0].halted = False
        self.contexts[0].fault = 0
        self.contexts[0].cycles = 0
        self.contexts[0].prot = []
        self.contexts[0].data_base = 0
        self.contexts[0].data_bound = self.config.mem_words
        self.legal_store[0] = (0, self.config.mem_words)
