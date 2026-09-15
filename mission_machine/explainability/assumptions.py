"""Assumption register.

Every assumption that materially affects a number on screen is registered here,
in code, so that it can be shown next to the result rather than buried in a
document nobody opens. ``docs/assumptions.md`` is written from this register.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Assumption:
    assumption_id: str
    statement: str
    category: str
    label: str                 # SYNTHETIC | ASSUMED | SIMULATED | UNVALIDATED
    impact_if_wrong: str
    where: str                 # module that relies on it

    def to_dict(self) -> dict[str, Any]:
        return {
            "assumption_id": self.assumption_id,
            "statement": self.statement,
            "category": self.category,
            "label": self.label,
            "impact_if_wrong": self.impact_if_wrong,
            "where": self.where,
        }


ASSUMPTIONS: tuple[Assumption, ...] = (
    Assumption(
        "AS-001",
        "All asset ratings, fuel curves, load profiles and weather data are invented for this "
        "demonstrator. None of it comes from the Swedish Armed Forces or any other organisation.",
        "data",
        "SYNTHETIC",
        "Every quantitative result changes. No result should be read as a performance claim.",
        "data/assets, data/missions",
    ),
    Assumption(
        "AS-002",
        "Generator fuel consumption is affine in output: litres/h = no-load + marginal x kW.",
        "model",
        "ASSUMED",
        "Fuel figures drift at very low and very high loading; the merit order between similar "
        "sets could change.",
        "assets/energy.py, planning/milp.py",
    ),
    Assumption(
        "AS-003",
        "A one-hour time step is fine enough to plan a 72-hour mission.",
        "model",
        "ASSUMED",
        "Ramp rates, transfer breaks and short peaks are invisible at this resolution. The UPS "
        "behaviour that protects communications during a source change is not modelled.",
        "simulation/simulator.py",
    ),
    Assumption(
        "AS-004",
        "Round-trip battery losses are split evenly between charging and discharging, and "
        "self-discharge is a fixed fraction of nameplate energy per hour.",
        "model",
        "ASSUMED",
        "State of charge drifts from reality over long missions; reserve estimates shift.",
        "assets/energy.py, simulation/simulator.py",
    ),
    Assumption(
        "AS-005",
        "Unburned fuel is converted to an energy reserve using the best available generator's "
        "specific fuel consumption at 75 % loading.",
        "metric",
        "ASSUMED",
        "Reserve in hours is optimistic when the remaining set ends up lightly loaded.",
        "simulation/simulator.py",
    ),
    Assumption(
        "AS-006",
        "All generators draw from one shared bulk fuel supply; individual tank capacities are "
        "recorded but not enforced as a constraint.",
        "model",
        "ASSUMED",
        "A configuration may be reported as feasible when it would in practice require refuelling "
        "runs between tanks.",
        "simulation/simulator.py",
    ),
    Assumption(
        "AS-007",
        "Deployment crews work in parallel, one crew per asset, so setup time is the longest "
        "single setup rather than the sum.",
        "metric",
        "ASSUMED",
        "Deployment complexity is understated when personnel are the binding constraint.",
        "planning/metrics.py",
    ),
    Assumption(
        "AS-008",
        "Grid availability follows a fixed, known schedule of windows.",
        "scenario",
        "ASSUMED",
        "Real supply fails without warning. Options that lean on the grid would be riskier than "
        "the comparison suggests.",
        "environment/model.py, data/missions",
    ),
    Assumption(
        "AS-009",
        "Thermal derating of generators is linear at 1 %/degC above 25 degC, floored at 80 %.",
        "model",
        "ASSUMED",
        "Capacity margins in hot conditions are approximate.",
        "assets/base.py",
    ),
    Assumption(
        "AS-010",
        "Disruption is modelled only as asset unavailability. No adversary behaviour, no "
        "targeting, no cascading physical damage, no signature or detectability effects.",
        "scope",
        "ASSUMED",
        "Resilience results describe equipment failures, not contested-environment survivability.",
        "resilience/",
    ),
    Assumption(
        "AS-011",
        "Cooling demand is a linear function of ambient temperature above a reference point; "
        "shelter thermal mass is ignored.",
        "model",
        "ASSUMED",
        "Short-term cooling peaks and the ride-through that thermal mass provides are not "
        "represented.",
        "assets/loads.py",
    ),
    Assumption(
        "AS-012",
        "A generator started within a time step delivers only for the remainder of that step; "
        "ramp rates are otherwise non-binding at one-hour resolution.",
        "model",
        "ASSUMED",
        "Stop/start cycling looks cheaper than it is; maintenance impact of cycling is not costed.",
        "simulation/simulator.py",
    ),
    Assumption(
        "AS-013",
        "No fuel resupply arrives during the mission unless the environment explicitly allows it.",
        "scenario",
        "ASSUMED",
        "Endurance limits are conservative where resupply is in fact available.",
        "data/missions, resilience/analysis.py",
    ),
    Assumption(
        "AS-014",
        "Projected endurance beyond the mission horizon extrapolates the final step's critical "
        "demand at constant load.",
        "metric",
        "ASSUMED",
        "Projected endurance is indicative only; it is not a mission-planning figure.",
        "planning/metrics.py",
    ),
    Assumption(
        "AS-015",
        "Energy counts towards the reserve only when the configuration being assessed can "
        "actually deliver it: the battery must be deployed, and a generator must be committed "
        "to burn the fuel. Energy the node holds but cannot reach is reported as a withheld "
        "quantity with an open question, never as part of the reserve and never as zero.",
        "metric",
        "ASSUMED",
        "A configuration that could reach the energy quickly - by starting a generator that is "
        "already on site - looks worse than one that has it connected, which is the intended "
        "bias but is a judgement, not a fact.",
        "simulation/simulator.py, planning/metrics.py",
    ),
    Assumption(
        "AS-016",
        "A function the operator marks NEVER_INTERRUPT is treated as requiring the node to hold "
        "it through the loss of its largest generator for at least as long as the mission's own "
        "deployment time limit. The word 'never' is read as covering a single failure, not only "
        "the plan as drawn.",
        "interpretation",
        "ASSUMED",
        "If the operator meant only 'do not plan to interrupt it', the planner is buying "
        "redundancy they did not ask for and paying fuel for it. The cost is reported as a "
        "trade-off, so the choice stays visible.",
        "mission/spec.py, explainability/explain.py",
    ),
    Assumption(
        "AS-017",
        "A function counts as served when it receives at least 95 % of the energy it asked for "
        "over the horizon. Below that the operator did not get the function, whatever the "
        "average says.",
        "metric",
        "ASSUMED",
        "A function delivered at 90 % might be perfectly usable, or useless, depending on what "
        "it is; one threshold cannot tell the difference.",
        "planning/metrics.py",
    ),
    Assumption(
        "AS-018",
        "A degraded mode is applied by scaling a load's demand profile to its "
        "min_service_fraction, and only for loads the operator pre-authorised with "
        "DEGRADE_ACCEPTABLE.",
        "model",
        "ASSUMED",
        "Real degradation is not a uniform scaling - a cooling system at 60 % of demand does not "
        "hold 60 % of its setpoint margin. The energy figures are indicative; the decision they "
        "inform is the operator's.",
        "mission/spec.py, assets/loads.py",
    ),
)


def assumptions_for(category: str | None = None) -> list[Assumption]:
    if category is None:
        return list(ASSUMPTIONS)
    return [a for a in ASSUMPTIONS if a.category == category]


def as_dicts() -> list[dict[str, Any]]:
    return [a.to_dict() for a in ASSUMPTIONS]
