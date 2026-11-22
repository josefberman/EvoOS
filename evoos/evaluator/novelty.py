"""Behavioral and structural descriptors for novelty / MAP-Elites."""

from __future__ import annotations

from dataclasses import dataclass, field

from evoos.vm.instruction_set import Instr, Op
from evoos.vm.sandbox import EvalResult


def structural_descriptor(instrs: tuple[Instr, ...] | list[Instr]) -> tuple[int, int, int, int]:
    n_run = n_prot = n_back = 0
    for i, ins in enumerate(instrs):
        if ins.op in (Op.RUNCTX, Op.RUNCTXI):
            n_run += 1
        elif ins.op in (Op.ADDPROT, Op.CLRPROT):
            n_prot += 1
        elif ins.op in (Op.BEQ, Op.BNE, Op.BLT, Op.BGE, Op.JMP, Op.JAL) and ins.imm < 0:
            n_back += 1
    length_bin = min(len(instrs) // 16, 8)
    return (length_bin, min(n_run, 15), min(n_prot, 8), min(n_back, 8))


def opcode_histogram(instrs: tuple[Instr, ...] | list[Instr]) -> list[float]:
    n = max(len(instrs), 1)
    counts = [0.0] * (max(Op) + 1)
    for ins in instrs:
        if 0 <= ins.op < len(counts):
            counts[ins.op] += 1.0
    return [c / n for c in counts]


def behavioral_descriptor(result: EvalResult) -> tuple[float, ...]:
    n = max(result.n_guests, 1)
    total_g = sum(result.guest_cycles) or 1
    shares = tuple(c / total_g for c in result.guest_cycles)
    switch_n = min(result.switch_count / 50.0, 1.0)
    complete = result.completed / n
    return shares + (switch_n, complete, 1.0 if result.isolation_ok else 0.0)


def elites_cell(result: EvalResult) -> tuple[int, int]:
    """2-D MAP-Elites grid: context-switch pressure × completion count."""
    x = min(result.switch_count // 4, 7)
    y = min(result.completed, 4)
    return (x, y)


STYLE_CODE = {
    "none": 0,
    "copy-supervisor": 1,
    "shared-window": 2,
    "disk-buffer": 3,
    "batch": 4,
}

# copy-supervisor is the hand-written direct-copy style. Prefer 2–4 at equal correctness.
NON_BASELINE_STYLES = frozenset({"shared-window", "disk-buffer", "batch"})


def hw_cell(result: EvalResult) -> tuple[int, int, int, int, int]:
    """C-hw MAP-Elites: completion × eviction × routing × switch rhythm × isolation."""
    n_done = min(int(result.completed), 8)
    n_blk = min(int(result.n_blk_write or 0), 7)
    n_svc = min(int(result.n_svc or 0) + int(result.delivered or 0), 7)
    sw = min(int(result.switch_count) // 16, 7)
    isol = 1 if result.isolation_ok else 0
    return (n_done, n_blk, n_svc, sw, isol)


def route_cell(result: EvalResult) -> tuple[int, int, int, int, int]:
    """Routing MAP-Elites: deliveries × payload slots × style × switches × isolation."""
    n_del = min(int(result.delivered or 0), 8)
    n_msg = min(sum(1 for x in (result.msg_ok or []) if x), 8)
    style = STYLE_CODE.get(str(result.route_style or "none"), 0)
    sw = min(int(result.switch_count) // 4, 7)
    isol = 1 if result.isolation_ok else 0
    return (n_del, n_msg, style, sw, isol)


def org_label(result: EvalResult) -> str:
    """Behavioral organization label for C-hw traces."""
    if result.completed == 0 and result.switch_count == 0:
        return "no-dispatch"
    style = str(result.route_style or "none")
    if (result.n_blk_write or 0) >= max(1, result.n_guests - 4) and result.completed > 0:
        base = "fifo-swap-like"
    elif (result.n_blk_read or 0) > 0:
        base = "blk-user"
    else:
        base = "no-swap"
    if (result.delivered or 0) > 0 or style not in ("none", ""):
        tag = style if style not in ("none", "copy-supervisor") else "router"
        if style == "copy-supervisor":
            tag = "copy"
        elif style == "shared-window":
            tag = "shared-window"
        elif style == "disk-buffer":
            tag = "disk-buffer"
        elif style == "batch":
            tag = "batch"
        else:
            tag = "router"
        return base + "+" + tag
    if result.switch_count > result.n_guests * 3:
        return base + "+rr"
    return base


def is_non_baseline_route(result: EvalResult) -> bool:
    return str(result.route_style or "") in NON_BASELINE_STYLES and (result.delivered or 0) > 0


def milestone_cell(result: EvalResult) -> tuple[int, int, int, int, int]:
    """Stepping-stone grid: cover / entered / progressed / completed / n_guests.

    Counts are isolation-gated (protected-run) so a RUNCTX-without-MPU elite
    does not occupy the same cell as a cover+entered elite.
    """
    n_prot = sum(1 for c in (result.prot_cover or []) if c)
    n_ent = int(result.entered_protected)
    n_prog = int(result.progressed_protected)
    n_done = int(result.completed_protected)
    n_g = int(result.n_guests)
    return (n_prot, n_ent, n_prog, n_done, n_g)


def _euclid(a: tuple[float, ...] | list[float], b: tuple[float, ...] | list[float]) -> float:
    n = min(len(a), len(b))
    return sum((float(a[i]) - float(b[i])) ** 2 for i in range(n)) ** 0.5


@dataclass
class NoveltyArchive:
    vectors: list[tuple[float, ...]] = field(default_factory=list)
    k: int = 5
    cap: int = 2048

    def add(self, vec: tuple[float, ...]) -> None:
        self.vectors.append(vec)
        if len(self.vectors) > self.cap:
            self.vectors = self.vectors[-self.cap :]

    def score(self, vec: tuple[float, ...]) -> float:
        if not self.vectors:
            return 1.0
        dists = sorted(_euclid(vec, x) for x in self.vectors)
        take = dists[: min(self.k, len(dists))]
        mean = sum(take) / len(take)
        return mean / (mean + 1.0)
