"""Re-audit round-8/9 elites under the on-device 8-ctx third-guest rule."""

from __future__ import annotations

import json
from pathlib import Path

from evoos.agent.program import Candidate
from evoos.evaluator.share_audit import admission_audit_candidate
from evoos.experiments.scheduling.hw_baselines import fifo_swap_rr, payload_copy_rr
from evoos.vm.instruction_set import parse_disassembly
from evoos.vm.sandbox import ON_DEVICE_N_CONTEXTS, ON_DEVICE_PAIR_WINDOW


def _load_dir(root: Path) -> list[tuple[str, Candidate]]:
    out = []
    if not root.exists():
        return out
    for p in sorted(root.glob("*.txt")):
        try:
            instrs = parse_disassembly(p.read_text())
        except ValueError:
            continue
        out.append((p.stem, Candidate(instrs=instrs, origin=p.stem, id=0)))
    return out


def main() -> int:
    rows = []
    for name, cand in (
        [("payload_copy_rr", payload_copy_rr(32)), ("fifo_swap_rr", fifo_swap_rr(32))]
        + _load_dir(Path("experiments/scheduling/results/round8/examples"))
        + _load_dir(Path("experiments/scheduling/results/round9/examples"))
    ):
        adm = admission_audit_candidate(cand)
        leak_ctx = sorted(
            {
                hit.get("name")
                for hit in adm.get("cases") or []
                if hit.get("share_leak") or not hit.get("attacker_contained")
            }
        )
        rows.append(
            {
                "name": name,
                "origin": cand.origin,
                "n_instr": len(cand.instrs),
                "admission_pass": adm["pass"],
                "admission_n_leak": adm["n_leak"],
                "admission_n_attacker_fail": adm["n_attacker_fail"],
                "fail_cases": leak_ctx,
                "n_contexts": ON_DEVICE_N_CONTEXTS,
                "pair_window": list(ON_DEVICE_PAIR_WINDOW),
                "cases": adm["cases"],
            }
        )
        print(
            f"{name:20} admission={adm['pass']} leak={adm['n_leak']} "
            f"atk_fail={adm['n_attacker_fail']} fail={leak_ctx}"
        )
    out = Path("experiments/scheduling/results/round10")
    out.mkdir(parents=True, exist_ok=True)
    (out / "reaudit_r8_r9.json").write_text(json.dumps(rows, indent=2, default=str))
    n_pass = sum(1 for r in rows if r["admission_pass"])
    lines = [
        "# Round 8/9 re-audit under on-device 8-ctx rule",
        "",
        f"n_contexts=**{ON_DEVICE_N_CONTEXTS}**. Pair window `{ON_DEVICE_PAIR_WINDOW}`. "
        "Live HALT guests fill ctx ids above n_guests. "
        "Any grant of a pair window or subrange to a non-participant is a leak.",
        "",
        f"Admission pass: **{n_pass}/{len(rows)}**.",
        "",
        "| name | admission | n_leak | attacker_fail | failing cases |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        lines.append(
            f"| {r['name']} | {r['admission_pass']} | {r['admission_n_leak']} | "
            f"{r['admission_n_attacker_fail']} | {', '.join(r['fail_cases']) or '—'} |"
        )
    lines.append("")
    (out / "reaudit_r8_r9.md").write_text("\n".join(lines) + "\n")
    print(f"wrote {out / 'reaudit_r8_r9.md'} pass={n_pass}/{len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
