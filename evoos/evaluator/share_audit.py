"""Adversarial shared-window audit: third-guest grant is a hard leak."""

from __future__ import annotations

from evoos.agent.program import Candidate
from evoos.evaluator.hw_workloads import audit_workloads, heldout_audit_workloads, on_device_audit_workloads
from evoos.vm.machine import lenient_config
from evoos.vm.sandbox import ON_DEVICE_N_CONTEXTS, Sandbox, Workload


def admission_sandbox(max_steps: int = 4000) -> Sandbox:
    """On-device 8-context machine used for audit_pass == boot admission."""
    return Sandbox(
        lenient_config(
            n_contexts=ON_DEVICE_N_CONTEXTS,
            n_blocks=16,
            block_words=32,
            blk_latency=2,
            max_steps=max_steps,
            max_supervisor_len=192,
            wall_steps=max(max_steps + 4000, 12000),
        )
    )


def audit_candidate(sandbox: Sandbox, cand: Candidate, workloads: tuple[Workload, ...] | None = None) -> dict:
    """Run malicious-guest workloads. Any share_leak is a hard failure."""
    rows = []
    n_leak = 0
    n_attacker_fail = 0
    n_revoked = 0
    n_stale = 0
    n_clrprot = 0
    isol = True
    for w in workloads if workloads is not None else audit_workloads():
        r = sandbox.evaluate(cand.instrs, w)
        leak = bool(r.share_leak) or (not r.isolation_ok)
        if leak:
            n_leak += 1
        if not r.attacker_contained:
            n_attacker_fail += 1
        if r.revoked:
            n_revoked += 1
        n_stale += int(getattr(r, "n_stale", 0) or 0)
        n_clrprot += int(getattr(r, "n_clrprot", 0) or 0)
        isol = isol and r.isolation_ok
        rows.append(
            {
                "name": w.name,
                "share_leak": r.share_leak,
                "attacker_contained": r.attacker_contained,
                "revoked": r.revoked,
                "n_clrprot": r.n_clrprot,
                "n_stale": getattr(r, "n_stale", 0),
                "n_attackers": getattr(r, "n_attackers", 0),
                "isolation_ok": r.isolation_ok,
                "delivered": r.delivered,
                "completed": r.completed,
                "n_guests": r.n_guests,
                "style": r.route_style,
            }
        )
    return {
        "n_cases": len(rows),
        "n_leak": n_leak,
        "n_attacker_fail": n_attacker_fail,
        "n_revoked": n_revoked,
        "n_stale": n_stale,
        "n_clrprot": n_clrprot,
        "pass": n_leak == 0 and n_attacker_fail == 0,
        "isolation_ok": isol,
        "cases": rows,
    }


def heldout_audit_candidate(sandbox: Sandbox, cand: Candidate) -> dict:
    """Never-trained leak audit (disjoint from the rotating train bank)."""
    return audit_candidate(sandbox, cand, heldout_audit_workloads())


def admission_audit_candidate(cand: Candidate, max_steps: int = 4000) -> dict:
    """On-device 8-ctx audit. Research audit_pass must match this gate."""
    sb = admission_sandbox(max_steps=max_steps)
    result = audit_candidate(sb, cand, on_device_audit_workloads())
    result["n_contexts"] = ON_DEVICE_N_CONTEXTS
    return result
