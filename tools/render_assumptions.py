#!/usr/bin/env python3
"""Write docs/assumptions.md from the assumption register in code.

The register in ``mission_machine/explainability/assumptions.py`` is the single
source of truth: it is what the UI shows next to the results. This script keeps
the document in step with it. ``tests/test_evidence_and_cli.py`` fails if they
drift apart.

    python3 tools/render_assumptions.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mission_machine.explainability.assumptions import ASSUMPTIONS  # noqa: E402

HEADER = """# Assumption register

Generated from `mission_machine/explainability/assumptions.py` by
`tools/render_assumptions.py`. Do not edit by hand - change the register and
re-run the script.

Every assumption below materially affects a number the demonstrator puts on
screen. The UI shows this register on the MISSION screen so that a reader can
see what the results rest on without leaving the tool.

Labels: **SYNTHETIC** invented input data; **ASSUMED** a modelling choice;
**SIMULATED** produced by the model; **UNVALIDATED** not checked against field
data or an external reference.

| Id | Label | Category | Assumption | If it is wrong | Where it is used |
| --- | --- | --- | --- | --- | --- |
"""

FOOTER = """
## What is deliberately not modelled in Pack 1

* Adversary behaviour, targeting, offensive action, or contested-environment
  survivability. Disruption appears only as asset unavailability.
* Signature, emissions and detectability of any configuration.
* Personnel availability as a scheduling constraint (only setup crew size is
  recorded).
* Cyber dependencies, control-system availability and communications bearer
  behaviour beyond the electrical load they draw.
* Maintenance intervals, spares and consumables other than fuel.
* Financial cost of any kind.

These are exclusions, not oversights. Each one is a candidate for a later pack
and is listed in `docs/pack1-deliverables.md` under the Pack 2 recommendation.
"""


def render() -> str:
    rows = []
    for assumption in ASSUMPTIONS:
        statement = assumption.statement.replace("|", "\\|")
        impact = assumption.impact_if_wrong.replace("|", "\\|")
        rows.append(
            f"| {assumption.assumption_id} | {assumption.label} | {assumption.category} "
            f"| {statement} | {impact} | `{assumption.where}` |"
        )
    return HEADER + "\n".join(rows) + "\n" + FOOTER


def main() -> int:
    target = ROOT / "docs" / "assumptions.md"
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target.relative_to(ROOT)} ({len(ASSUMPTIONS)} assumptions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
