"""How good is the rule-based dispatch?

The planning engine chooses a configuration by enumerating candidates and
simulating each one under transparent dispatch rules. That answers "which
configuration", but it leaves a question open: for the configuration it picked,
how much better could the hour-by-hour dispatch have been?

Nothing in Pack 1 could answer that, because there was no optimum to compare
against - only the rules and their output. This module puts the same
configuration to a real MILP solver and reports the difference, with the
caveats that make the number mean something.

The solver's answer is checked against the declared constraint set before it is
believed, by the same verifier that checks the simulator's schedules. A solver
that returns an infeasible assignment, or a model that maps the problem wrongly,
fails here rather than producing a confident wrong number.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mission_machine.assets.inventory import AssetInventory
from mission_machine.environment.model import Environment
from mission_machine.mission.spec import MissionSpec
from mission_machine.planning.configuration import Configuration
from mission_machine.planning.milp import MilpModel, build_model
from mission_machine.planning.providers import (
    DEFAULT_REGISTRY,
    DISPATCH_MILP,
    ProviderRegistry,
    SolverOutcome,
    SolverStatus,
)
from mission_machine.simulation.simulator import SimulationResult

#: Said every time a solver number is put next to a simulated one.
PERFECT_FORESIGHT_CAVEAT = (
    "The solver knows the whole mission in advance - every hour of load, weather and grid "
    "availability. The dispatch rules do not, so this is an upper bound on what a causal "
    "controller could recover. It turns out to be a tight one: twelve hours of lookahead "
    "recovers 96-100 % of it (RQ-012, `mission-machine foresight`), so what the rules give up "
    "is commitment logic rather than foresight."
)

SERVICE_FLOOR_CAVEAT = (
    "The solver was required to deliver at least as much energy to each discretionary function "
    "as the simulated schedule did, so the saving is not won by serving less. It may still move "
    "*when* that energy is delivered, which is legitimate for a deferrable load and an "
    "assumption for anything else."
)


@dataclass
class DispatchComparison:
    """One configuration's rule-based dispatch against the best possible one."""

    configuration_id: str
    label: str
    backend: str
    status: SolverStatus
    baseline_fuel_l: float
    optimal_fuel_l: float | None = None
    wall_time_s: float = 0.0
    verified: bool = False
    violations: list[dict[str, Any]] = field(default_factory=list)
    model_size: dict[str, int] = field(default_factory=dict)
    caveats: list[str] = field(default_factory=list)
    detail: str = ""
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def saving_l(self) -> float | None:
        if self.optimal_fuel_l is None:
            return None
        return self.baseline_fuel_l - self.optimal_fuel_l

    @property
    def saving_fraction(self) -> float | None:
        saving = self.saving_l
        if saving is None or self.baseline_fuel_l <= 0:
            return None
        return saving / self.baseline_fuel_l

    @property
    def trustworthy(self) -> bool:
        """Only a verified answer from a real solve is worth quoting."""

        return self.status.is_answer and self.verified

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "label": self.label,
            "backend": self.backend,
            "status": str(self.status),
            "baseline_fuel_l": round(self.baseline_fuel_l, 1),
            "optimal_fuel_l": (
                round(self.optimal_fuel_l, 1) if self.optimal_fuel_l is not None else None
            ),
            "saving_l": round(self.saving_l, 1) if self.saving_l is not None else None,
            "saving_fraction": (
                round(self.saving_fraction, 4) if self.saving_fraction is not None else None
            ),
            "wall_time_s": round(self.wall_time_s, 2),
            "verified": self.verified,
            "violations": list(self.violations[:5]),
            "model_size": dict(self.model_size),
            "caveats": list(self.caveats),
            "detail": self.detail,
            "trustworthy": self.trustworthy,
            "data_labels": list(self.data_labels),
        }


def compare_with_optimum(
    mission: MissionSpec,
    configuration: Configuration,
    simulation: SimulationResult,
    *,
    environment: Environment | None = None,
    inventory: AssetInventory | None = None,
    registry: ProviderRegistry | None = None,
    time_budget_s: float = 60.0,
    baseline_fuel_l: float | None = None,
) -> DispatchComparison:
    """Solve the same configuration's dispatch exactly, and report the difference."""

    registry = registry or DEFAULT_REGISTRY
    model: MilpModel = build_model(
        mission,
        environment,
        inventory,
        configuration=configuration,
        service_floor=simulation,
    )
    baseline = (
        baseline_fuel_l
        if baseline_fuel_l is not None
        else sum(step.fuel_used_l for step in simulation.steps)
    )
    outcome: SolverOutcome = registry.solve(
        model, problem_class=DISPATCH_MILP, time_budget_s=time_budget_s
    )

    comparison = DispatchComparison(
        configuration_id=configuration.configuration_id,
        label=configuration.label or configuration.configuration_id,
        backend=outcome.backend,
        status=outcome.status,
        baseline_fuel_l=baseline,
        wall_time_s=outcome.wall_time_s,
        model_size=model.size(),
        detail=outcome.detail,
    )

    if not outcome.is_answer:
        comparison.caveats.append(
            "No optimum was obtained, so nothing is claimed about how good the dispatch rules "
            "are. The rules' own output stands on its own."
        )
        return comparison

    violations = model.verify(outcome.assignment, tolerance=1e-3)
    comparison.verified = not violations
    comparison.violations = [violation.to_dict() for violation in violations]
    comparison.optimal_fuel_l = outcome.objective

    if violations:
        comparison.caveats.append(
            "The solver's own answer does not satisfy the declared constraint set, so it is "
            "reported and not used. That is a bug in the model or the mapping to the solver, "
            "not a result."
        )
        return comparison

    comparison.caveats.append(PERFECT_FORESIGHT_CAVEAT)
    comparison.caveats.append(SERVICE_FLOOR_CAVEAT)
    if outcome.status is SolverStatus.FEASIBLE:
        comparison.caveats.append(
            f"The solve stopped at its {time_budget_s:.0f} s budget with a solution it had not "
            "proved optimal, so the true optimum is no higher than this and the gap shown is a "
            "lower bound on what the rules give up."
        )
    return comparison
