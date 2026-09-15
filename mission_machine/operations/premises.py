"""Checking the plan's premises against what the node has actually seen.

Everything else in this repository improves how well the machine plans. This
module asks a different question: is it still planning against the right world?

A MissionSpec asserts things - host-nation supply returns at H+30, the command
post draws this much, the array yields that much. The planner takes them as
given, and a rolling controller will replan diligently and optimally against a
premise that stopped being true hours ago. RQ-014 measured what that costs: in
the severe case, a controller told the truth completed the mission while the
same controller told the original premise failed, gaining one hour over doing
nothing at all. Past a certain size of disturbance, noticing beats optimising.

So this module compares what the node observed with what the mission said would
happen, and where the two have parted company it says so, quantifies what it
means, and offers a revised premise - *and stops there*. Revising a mission
assumption is an operator's decision, not a planner's. The machine may notice; it
may not quietly start planning against a world nobody agreed to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from mission_machine.assets.inventory import AssetInventory
from mission_machine.assets.loads import Load, scale_profile
from mission_machine.environment.model import Environment
from mission_machine.mission.spec import MissionSpec
from mission_machine.simulation.state import StepRecord

#: The three thresholds below decide when a difference becomes a contradiction.
#: They are chosen, not derived, and are registered as AS-022.
#:
#: Consecutive hours of missing host-nation supply, inside a window the mission
#: says is available, before the window is treated as contradicted rather than
#: as a glitch.
GRID_BREACH_HOURS = 2.0

#: Fractional deviation in observed energy before a profile is called wrong.
#: Below it, the difference is within what a synthetic profile was ever going to
#: get right; above it, the plan is working from the wrong number.
PROFILE_BREACH_FRACTION = 0.10

#: Observed yield below this share of the forecast is a contradicted weather
#: premise rather than a cloudy afternoon.
YIELD_BREACH_FRACTION = 0.70


@dataclass(frozen=True)
class Premise:
    """Something the mission asserts, which the planner takes as given."""

    key: str
    statement: str
    source: str
    assumption_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "statement": self.statement,
            "source": self.source,
            "assumption_id": self.assumption_id,
        }


@dataclass
class PremiseBreach:
    """A premise the node's own observations contradict, and the revision on offer."""

    premise: Premise
    detected_at_hour: float
    severity: str                 # CONTRADICTED | DRIFTING
    evidence: list[str] = field(default_factory=list)
    revision_statement: str = ""
    revised_environment: Environment | None = None
    revised_inventory: AssetInventory | None = None
    conservative: bool = True
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def has_revision(self) -> bool:
        return self.revised_environment is not None or self.revised_inventory is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "premise": self.premise.to_dict(),
            "detected_at_hour": self.detected_at_hour,
            "severity": self.severity,
            "evidence": list(self.evidence),
            "revision_statement": self.revision_statement,
            "conservative": self.conservative,
            "has_revision": self.has_revision,
            "data_labels": list(self.data_labels),
        }


# --------------------------------------------------------------------------
# detectors
# --------------------------------------------------------------------------


def _grid_breach(
    mission: MissionSpec,
    planned: Environment,
    steps: Sequence[StepRecord],
    at_hour: float,
) -> PremiseBreach | None:
    """Was supply there in the hours the mission said it would be?"""

    missing = [
        step.hour
        for step in steps
        if planned.grid_available_at(step.hour) and not step.grid_available
    ]
    if len(missing) < GRID_BREACH_HOURS:
        return None

    windows = planned.grid.available_windows
    first = min(missing)
    premise = Premise(
        key="GRID_AVAILABILITY",
        statement=(
            "Host-nation supply is available in the windows "
            + ", ".join(f"H+{w[0]:.0f} to H+{w[1]:.0f}" for w in windows)
            + "."
        ),
        source="MissionSpec.grid_availability",
        assumption_id="AS-008",
    )
    # The conservative revision: supply that has not appeared when it was due is
    # not assumed to appear later. It is conservative on purpose, and labelled
    # as such, because the alternative is a plan that keeps waiting for it.
    surviving = [list(w) for w in windows if w[1] <= first]
    revised = planned.with_grid_windows(
        surviving,
        name="observed_grid",
        note=f"Revised premise: no host-nation supply from H+{first:.0f} onwards (observed).",
    )
    return PremiseBreach(
        premise=premise,
        detected_at_hour=at_hour,
        severity="CONTRADICTED",
        evidence=[
            f"Supply was expected and absent for {len(missing):.0f} h: "
            + ", ".join(f"H+{hour:.0f}" for hour in missing[:8])
            + ("..." if len(missing) > 8 else ""),
        ],
        revision_statement=(
            f"Plan on no host-nation supply from H+{first:.0f} to the end of the mission."
        ),
        revised_environment=revised,
    )


def _load_breach(
    mission: MissionSpec,
    planned: Environment,
    steps: Sequence[StepRecord],
    at_hour: float,
) -> PremiseBreach | None:
    """Is the critical load drawing what the mission said it would?"""

    observed = sum(step.critical_demand_kw * step.duration_h for step in steps)
    expected = sum(
        mission.critical_demand_kw(step.hour, planned.temperature_c(step.hour)) * step.duration_h
        for step in steps
    )
    if expected <= 1e-6:
        return None
    ratio = observed / expected
    if abs(ratio - 1.0) < PROFILE_BREACH_FRACTION:
        return None

    inventory = mission.inventory.copy()
    for load_id in mission.critical_loads:
        asset = inventory.find(load_id)
        if isinstance(asset, Load):
            asset.profile = scale_profile(asset.profile, ratio)

    return PremiseBreach(
        premise=Premise(
            key="CRITICAL_LOAD_PROFILE",
            statement="The critical functions draw the demand stated in the mission's load profiles.",
            source="MissionSpec.load_profiles",
            assumption_id="AS-001",
        ),
        detected_at_hour=at_hour,
        severity="CONTRADICTED" if ratio > 1.0 else "DRIFTING",
        evidence=[
            f"Observed {observed:.0f} kWh of critical demand over the first "
            f"{len(steps)} h against {expected:.0f} kWh planned - {ratio - 1.0:+.0%}.",
        ],
        revision_statement=(
            f"Plan on critical demand {ratio - 1.0:+.0%} against the stated profiles."
        ),
        revised_inventory=inventory,
        conservative=ratio > 1.0,
    )


def _yield_breach(
    mission: MissionSpec,
    planned: Environment,
    steps: Sequence[StepRecord],
    at_hour: float,
) -> PremiseBreach | None:
    """Is the array yielding what the weather profile said it would?"""

    inventory = mission.inventory
    if not inventory.pv_arrays:
        return None
    array = inventory.pv_arrays[0]
    observed = sum(step.pv_kw * step.duration_h for step in steps)
    expected = sum(
        array.output_kw(planned.solar_fraction(step.hour), planned.temperature_c(step.hour))
        * step.duration_h
        for step in steps
    )
    if expected <= 1.0 or observed >= expected * YIELD_BREACH_FRACTION:
        return None

    ratio = observed / expected
    return PremiseBreach(
        premise=Premise(
            key="WEATHER_YIELD",
            statement=(
                f"The weather follows the mission's profile: {planned.weather.description}."
            ),
            source="MissionSpec.weather_profile",
            assumption_id="AS-008",
        ),
        detected_at_hour=at_hour,
        severity="CONTRADICTED",
        evidence=[
            f"The array yielded {observed:.0f} kWh against {expected:.0f} kWh forecast over the "
            f"first {len(steps)} h - {ratio:.0%} of what was planned.",
        ],
        revision_statement=f"Plan on solar yield at {ratio:.0%} of the stated profile.",
        revised_environment=planned.variant(name="observed_cloud", cloud_scale=ratio),
    )


DETECTORS = (_grid_breach, _load_breach, _yield_breach)


def check_premises(
    mission: MissionSpec,
    planned_environment: Environment,
    steps: Sequence[StepRecord],
    at_hour: float,
) -> list[PremiseBreach]:
    """Compare what the node saw with what the mission said, and report the gaps.

    Only observations are used. Nothing here looks at the future, because the
    operator cannot either - the question is whether what has *already happened*
    contradicts what the plan is still assuming.
    """

    if not steps:
        return []
    breaches = [
        detector(mission, planned_environment, steps, at_hour) for detector in DETECTORS
    ]
    return [breach for breach in breaches if breach is not None]
