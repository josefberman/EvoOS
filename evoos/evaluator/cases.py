"""Fine-grained behavioral test cases for lexicase selection.

Each case is one guest on one workload times one milestone. Isolation is a
separate case and a hard filter in the search loop. These are observables of
the machine after a run, not opcode bonuses.
"""

from __future__ import annotations

from evoos.vm.sandbox import EvalResult

MILESTONES = ("cover", "entered_p", "progress_p", "complete_p", "nofault")


def case_vector(
    results: list[EvalResult],
    *,
    gated: bool = True,
) -> tuple[list[str], list[float]]:
    names: list[str] = []
    vals: list[float] = []
    for r in results:
        w = r.workload or "wl"
        names.append(f"{w}.isolation")
        vals.append(1.0 if r.isolation_ok else 0.0)
        names.append(f"{w}.blk")
        vals.append(1.0 if (r.n_blk_read or 0) > 0 else 0.0)
        names.append(f"{w}.delivered")
        vals.append(1.0 if (r.delivered or 0) > 0 else 0.0)
        n = r.n_guests
        for g in range(n):
            cover = 1.0 if (r.prot_cover and g < len(r.prot_cover) and r.prot_cover[g]) else 0.0
            entered_p = (
                1.0 if (r.ran_protected and g < len(r.ran_protected) and r.ran_protected[g]) else 0.0
            )
            progressed = False
            completed = False
            nofault = False
            if r.outcomes and g < len(r.outcomes):
                o = r.outcomes[g]
                progressed = bool(o.progressed)
                completed = bool(o.correct)
                nofault = o.fault == 0
            if gated:
                progressed = progressed and entered_p >= 1.0
                completed = completed and entered_p >= 1.0
            prefix = f"{w}.g{g}"
            names.extend(
                [
                    f"{prefix}.cover",
                    f"{prefix}.entered_p",
                    f"{prefix}.progress_p",
                    f"{prefix}.complete_p",
                    f"{prefix}.nofault",
                ]
            )
            vals.extend(
                [
                    cover,
                    entered_p,
                    1.0 if progressed else 0.0,
                    1.0 if completed else 0.0,
                    1.0 if nofault else 0.0,
                ]
            )
    return names, vals


def cases_solved(names: list[str], vec: list[float]) -> list[str]:
    return [n for n, v in zip(names, vec) if v >= 1.0]


def summarize_cases(names: list[str], vec: list[float]) -> dict[str, int]:
    """Count solved cases by milestone suffix."""
    out = {m: 0 for m in ("isolation",) + MILESTONES}
    for n, v in zip(names, vec):
        if v < 1.0:
            continue
        tail = n.rsplit(".", 1)[-1]
        if tail in out:
            out[tail] += 1
    out["total"] = sum(1 for v in vec if v >= 1.0)
    out["n_cases"] = len(vec)
    return out
