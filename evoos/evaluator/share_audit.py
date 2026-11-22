"""Adversarial shared-window audit: third-guest grant is a hard leak."""

from __future__ import annotations

from evoos.agent.program import Candidate
from evoos.evaluator.hw_workloads import audit_workloads
from evoos.vm.sandbox import Sandbox


def audit_candidate(sandbox: Sandbox, cand: Candidate) -> dict:
    """Run malicious-guest workloads. Any share_leak is a hard failure."""
    rows = []
    n_leak = 0
    n_attacker_fail = 0
    n_revoked = 0
    isol = True
    for w in audit_workloads():
        r = sandbox.evaluate(cand.instrs, w)
        leak = bool(r.share_leak) or (not r.isolation_ok)
        if leak:
            n_leak += 1
        if not r.attacker_contained:
            n_attacker_fail += 1
        if r.revoked:
            n_revoked += 1
        isol = isol and r.isolation_ok
        rows.append(
            {
                "name": w.name,
                "share_leak": r.share_leak,
                "attacker_contained": r.attacker_contained,
                "revoked": r.revoked,
                "n_clrprot": r.n_clrprot,
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
        "pass": n_leak == 0 and n_attacker_fail == 0,
        "isolation_ok": isol,
        "cases": rows,
    }
