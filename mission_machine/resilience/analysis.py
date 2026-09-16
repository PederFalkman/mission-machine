"""Resilience analysis: single points of failure and recovery options.

Both analyses are done the same way - by re-running the deterministic
simulator with one thing changed - so that every claim on the screen is backed
by a schedule the operator can open and read.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Sequence

from mission_machine.assets.base import AssetKind, FailureState
from mission_machine.planning.configuration import Configuration, SecondaryPolicy
from mission_machine.planning.engine import PlanningEngine
from mission_machine.planning.metrics import ConfigurationMetrics, compute_metrics
from mission_machine.resilience.failures import FailureEvent
from mission_machine.simulation.simulator import Simulator, effective_state
from mission_machine.simulation.state import NodeState

EPS = 1e-9

#: Asset kinds that are part of the power system rather than the mission load.
_INFRASTRUCTURE = {
    AssetKind.GENERATOR,
    AssetKind.BATTERY,
    AssetKind.GRID_CONNECTION,
    AssetKind.SOLAR_PV,
    AssetKind.POWER_CONVERSION,
}


@dataclass
class FailureImpact:
    """What the loss of one asset would do to this configuration."""

    asset_id: str
    asset_name: str
    kind: str
    is_single_point_of_failure: bool
    endurance_hours: float
    endurance_hours_baseline: float
    critical_load_coverage: float
    secondary_load_coverage: float
    reserve_hours_min: float
    functions_at_risk: list[str] = field(default_factory=list)
    first_shortfall_hour: float | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "asset_name": self.asset_name,
            "kind": self.kind,
            "is_single_point_of_failure": self.is_single_point_of_failure,
            "endurance_hours": round(self.endurance_hours, 2),
            "endurance_hours_baseline": round(self.endurance_hours_baseline, 2),
            "endurance_delta_h": round(self.endurance_hours - self.endurance_hours_baseline, 2),
            "critical_load_coverage": round(self.critical_load_coverage, 4),
            "secondary_load_coverage": round(self.secondary_load_coverage, 4),
            "reserve_hours_min": round(self.reserve_hours_min, 2),
            "functions_at_risk": list(self.functions_at_risk),
            "first_shortfall_hour": self.first_shortfall_hour,
            "summary": self.summary,
        }


@dataclass
class RecoveryOption:
    """An action the operator could take, with its simulated effect."""

    option_id: str
    action: str
    description: str
    requires: list[str] = field(default_factory=list)
    time_to_effect_min: float = 0.0
    endurance_delta_h: float = 0.0
    projected_endurance_delta_h: float = 0.0
    fuel_delta_l: float = 0.0
    reserve_delta_h: float = 0.0
    secondary_coverage_delta: float = 0.0
    restores_critical_assurance: bool = False
    available: bool = True
    cost_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "option_id": self.option_id,
            "action": self.action,
            "description": self.description,
            "requires": list(self.requires),
            "time_to_effect_min": self.time_to_effect_min,
            "endurance_delta_h": round(self.endurance_delta_h, 2),
            "projected_endurance_delta_h": round(self.projected_endurance_delta_h, 2),
            "fuel_delta_l": round(self.fuel_delta_l, 1),
            "reserve_delta_h": round(self.reserve_delta_h, 2),
            "secondary_coverage_delta": round(self.secondary_coverage_delta, 4),
            "restores_critical_assurance": self.restores_critical_assurance,
            "available": self.available,
            "cost_note": self.cost_note,
        }


class ResilienceAnalyst:
    """Runs the what-if simulations behind the resilience metrics."""

    def __init__(self, engine: PlanningEngine) -> None:
        self.engine = engine
        self.mission = engine.mission
        self.inventory = engine.inventory
        self.environment = engine.environment

    # -- helpers ------------------------------------------------------------

    def _evaluate(
        self,
        configuration: Configuration,
        *,
        start_hour: float,
        end_hour: float | None,
        initial_state: NodeState | None,
        events: Sequence[FailureEvent],
        inventory=None,
    ) -> ConfigurationMetrics:
        simulator = Simulator(self.mission, self.environment, inventory or self.inventory)
        result = simulator.run(
            configuration,
            start_hour=start_hour,
            end_hour=end_hour,
            initial_state=initial_state,
            events=list(events),
        )
        return compute_metrics(self.mission, configuration, result, inventory or self.inventory)

    # -- single points of failure -------------------------------------------

    def single_points_of_failure(
        self,
        configuration: Configuration,
        *,
        start_hour: float = 0.0,
        end_hour: float | None = None,
        initial_state: NodeState | None = None,
        events: Sequence[FailureEvent] = (),
        baseline: ConfigurationMetrics | None = None,
    ) -> list[FailureImpact]:
        """Lose one active asset at a time and see what the mission loses.

        An asset is a single point of failure when losing it means the critical
        functions can no longer be assured for the rest of the mission.
        """

        events = list(events)
        baseline = baseline or self._evaluate(
            configuration,
            start_hour=start_hour,
            end_hour=end_hour,
            initial_state=initial_state,
            events=events,
        )
        impacts: list[FailureImpact] = []
        for asset_id in configuration.active_asset_ids:
            asset = self.inventory.find(asset_id)
            if asset is None or asset.kind not in _INFRASTRUCTURE:
                continue
            if effective_state(asset, start_hour, events) is FailureState.UNAVAILABLE:
                continue
            probe = list(events) + [
                FailureEvent(
                    event_id=f"SPOF-{asset_id}",
                    asset_id=asset_id,
                    hour=start_hour,
                    state=FailureState.UNAVAILABLE,
                    cause="ANALYSIS_PROBE",
                    description=f"What-if probe: {asset_id} unavailable from H+{start_hour:.0f}.",
                )
            ]
            metrics = self._evaluate(
                configuration,
                start_hour=start_hour,
                end_hour=end_hour,
                initial_state=initial_state,
                events=probe,
            )
            is_spof = not (metrics.availability_met and metrics.completes_mission)
            at_risk = self._functions_at_risk(
                configuration, start_hour, end_hour, initial_state, probe
            )
            summary = (
                f"Losing {asset.name} ends assured critical support after "
                f"{metrics.endurance_hours:.0f} h."
                if is_spof
                else (
                    f"Critical functions survive the loss of {asset.name}; "
                    f"minimum reserve falls to {metrics.energy_reserve_hours_min:.1f} h."
                )
            )
            impacts.append(
                FailureImpact(
                    asset_id=asset_id,
                    asset_name=asset.name,
                    kind=str(asset.kind),
                    is_single_point_of_failure=is_spof,
                    endurance_hours=metrics.endurance_hours,
                    endurance_hours_baseline=baseline.endurance_hours,
                    critical_load_coverage=metrics.critical_load_coverage,
                    secondary_load_coverage=metrics.secondary_load_coverage,
                    reserve_hours_min=metrics.energy_reserve_hours_min,
                    functions_at_risk=at_risk,
                    summary=summary,
                )
            )
        impacts.sort(key=lambda i: (not i.is_single_point_of_failure, i.endurance_hours))
        return impacts

    def _functions_at_risk(
        self,
        configuration: Configuration,
        start_hour: float,
        end_hour: float | None,
        initial_state: NodeState | None,
        events: Sequence[FailureEvent],
    ) -> list[str]:
        simulator = Simulator(self.mission, self.environment, self.inventory)
        result = simulator.run(
            configuration,
            start_hour=start_hour,
            end_hour=end_hour,
            initial_state=initial_state,
            events=list(events),
        )
        # Only count functions this configuration was trying to deliver. A
        # discretionary load the operator already agreed to leave off is not
        # "at risk" - it is already not running.
        intended = set(self.mission.critical_loads)
        intended.update(
            load.asset_id
            for load in self.mission.secondary_load_assets()
            if configuration.policy.attempts(load, self.mission)
        )
        at_risk: list[str] = []
        for step in result.steps:
            for load_id, demand in step.load_demand_kw.items():
                if load_id not in intended:
                    continue
                served = step.load_served_kw.get(load_id, 0.0)
                if demand - served > 1e-3 and load_id not in at_risk:
                    at_risk.append(load_id)
        return at_risk

    # -- recovery options ---------------------------------------------------

    def _commit_requirements(self, asset: Any) -> list[str]:
        """What committing this asset takes, including a requirement it breaks.

        Found by MM-DEMO-002 (RQ-001): where the mission must be able to
        displace within the hour, the machine offered "bring the trailer-mounted
        set on line" as a recovery from a generator failure, saying nothing
        about the requirement that action breaks. The action is still offered -
        lifting the relocation requirement is a command decision - but it is no
        longer offered as though it were free.
        """

        requires = [f"{asset.asset_id} serviceable", "fuel available"]
        if asset.asset_id in self.mission.mobility_excluded_assets_if_required():
            limit = self.mission.mobility_requirement.max_displacement_time_min
            requires.append(
                f"the relocation requirement lifted: {asset.asset_id} cannot displace"
                + (f" inside {limit:.0f} min" if limit else "")
            )
        return requires

    def _commit_cost_note(self, asset: Any) -> str:
        note = "Adds an asset to supervise and burns additional fuel."
        if asset.asset_id in self.mission.mobility_excluded_assets_if_required():
            note += (
                f" The node could no longer displace within the mission's limit while "
                f"{asset.asset_id} is relied on."
            )
        return note

    def recovery_options(
        self,
        configuration: Configuration,
        *,
        start_hour: float = 0.0,
        end_hour: float | None = None,
        initial_state: NodeState | None = None,
        events: Sequence[FailureEvent] = (),
        baseline: ConfigurationMetrics | None = None,
    ) -> list[RecoveryOption]:
        """Actions that could be taken from here, each with a simulated effect."""

        events = list(events)
        policy = configuration.policy
        inv = self.inventory
        baseline = baseline or self._evaluate(
            configuration,
            start_hour=start_hour,
            end_hour=end_hour,
            initial_state=initial_state,
            events=events,
        )

        def measure(new_configuration: Configuration, inventory=None) -> ConfigurationMetrics:
            return self._evaluate(
                new_configuration,
                start_hour=start_hour,
                end_hour=end_hour,
                initial_state=initial_state,
                events=events,
                inventory=inventory,
            )

        def delta(option: RecoveryOption, metrics: ConfigurationMetrics) -> RecoveryOption:
            option.endurance_delta_h = metrics.endurance_hours - baseline.endurance_hours
            option.projected_endurance_delta_h = (
                metrics.projected_endurance_hours - baseline.projected_endurance_hours
            )
            option.fuel_delta_l = metrics.fuel_consumption_l - baseline.fuel_consumption_l
            option.reserve_delta_h = (
                metrics.energy_reserve_hours_min - baseline.energy_reserve_hours_min
            )
            option.secondary_coverage_delta = (
                metrics.secondary_load_coverage - baseline.secondary_load_coverage
            )
            option.restores_critical_assurance = (
                metrics.availability_met
                and metrics.completes_mission
                and not (baseline.availability_met and baseline.completes_mission)
            )
            return option

        options: list[RecoveryOption] = []
        engine = self.engine

        # Start a generator that is not committed.
        for gen in inv.generators:
            if gen.asset_id in policy.generator_ids:
                continue
            if effective_state(gen, start_hour, events) is FailureState.UNAVAILABLE:
                continue
            candidate = configuration.with_policy(
                generator_ids=tuple(policy.generator_ids) + (gen.asset_id,)
            )
            candidate.active_asset_ids = engine.active_assets_for(candidate.policy)
            options.append(
                delta(
                    RecoveryOption(
                        option_id=f"REC-COMMIT-{gen.asset_id}",
                        action=f"Commit {gen.asset_id}",
                        description=(
                            f"Bring {gen.name} on line ({gen.capacity_kw:.0f} kW) to restore "
                            "generation margin."
                        ),
                        requires=self._commit_requirements(gen),
                        time_to_effect_min=gen.setup_time_min + gen.startup_time_min,
                        cost_note=self._commit_cost_note(gen),
                    ),
                    measure(candidate),
                )
            )

        # Deploy the optional PV array.
        for pv in inv.pv_arrays:
            if policy.deploy_pv or effective_state(pv, start_hour, events) is FailureState.UNAVAILABLE:
                continue
            candidate = configuration.with_policy(deploy_pv=True)
            candidate.active_asset_ids = engine.active_assets_for(candidate.policy)
            options.append(
                delta(
                    RecoveryOption(
                        option_id="REC-DEPLOY-PV",
                        action=f"Deploy {pv.asset_id}",
                        description=(
                            f"Set up the {pv.peak_kw:.0f} kWp mobile array to displace generator "
                            "fuel during daylight."
                        ),
                        requires=[f"{pv.setup_crew} personnel", f"{pv.setup_time_min:.0f} min setup"],
                        time_to_effect_min=pv.setup_time_min,
                        cost_note=(
                            "Increases physical footprint. Output depends on weather; "
                            "contributes nothing at night."
                        ),
                    ),
                    measure(candidate),
                )
            )

        # Use the grid when it is available.
        for grid in inv.grid_connections:
            if policy.use_grid or effective_state(grid, start_hour, events) is FailureState.UNAVAILABLE:
                continue
            candidate = configuration.with_policy(use_grid=True)
            candidate.active_asset_ids = engine.active_assets_for(candidate.policy)
            options.append(
                delta(
                    RecoveryOption(
                        option_id="REC-USE-GRID",
                        action=f"Connect {grid.asset_id}",
                        description="Take host-nation supply during its available windows.",
                        requires=["grid present", "connection authorised"],
                        time_to_effect_min=grid.reconnect_time_min,
                        cost_note="Creates a dependency on a supply outside the node's control.",
                    ),
                    measure(candidate),
                )
            )

        # Shed discretionary load.
        if policy.secondary_policy is not SecondaryPolicy.CRITICAL_ONLY:
            next_policy = (
                SecondaryPolicy.AS_PRIORITISED
                if policy.secondary_policy is SecondaryPolicy.FULL
                else SecondaryPolicy.CRITICAL_ONLY
            )
            candidate = configuration.with_policy(secondary_policy=next_policy)
            candidate.active_asset_ids = engine.active_assets_for(candidate.policy)
            dropped = [
                load.asset_id
                for load in self.mission.secondary_load_assets()
                if policy.attempts(load, self.mission)
                and not candidate.policy.attempts(load, self.mission)
            ]
            options.append(
                delta(
                    RecoveryOption(
                        option_id=f"REC-SHED-{next_policy}",
                        action=f"Shed discretionary load ({', '.join(dropped) or 'remaining'})",
                        description=(
                            "Stop serving discretionary functions to protect fuel and reserve."
                        ),
                        requires=["operator authority to stop the function"],
                        time_to_effect_min=5.0,
                        cost_note="The functions listed stop until the operator restores them.",
                    ),
                    measure(candidate),
                )
            )

        # Accept a degraded environmental-control setpoint.
        cooling = [
            load
            for load in self.mission.critical_load_assets()
            if load.kind is AssetKind.COOLING_SYSTEM and load.min_service_fraction < 1.0
        ]
        for load in cooling:
            degraded_inventory = self.mission.degraded_inventory([load.asset_id])
            scale = max(0.1, load.min_service_fraction)
            options.append(
                delta(
                    RecoveryOption(
                        option_id=f"REC-DEGRADE-{load.asset_id}",
                        action=f"Accept degraded setpoint on {load.asset_id}",
                        description=(
                            f"Run {load.name} at {scale:.0%} of demand "
                            f"(setpoint {getattr(load, 'degraded_setpoint_c', 0.0):.0f} degC "
                            "instead of nominal)."
                        ),
                        requires=["equipment thermal limits accepted by the operator"],
                        time_to_effect_min=10.0,
                        cost_note=(
                            "Shelter temperature rises. Equipment stays inside its stated limits "
                            "but with less margin - this is a command decision, not a settings change."
                        ),
                    ),
                    measure(configuration, inventory=degraded_inventory),
                )
            )

        # Lower the battery reserve floor.
        if policy.use_battery and policy.battery_reserve_soc > 0.16:
            candidate = configuration.with_policy(battery_reserve_soc=0.15)
            options.append(
                delta(
                    RecoveryOption(
                        option_id="REC-LOWER-RESERVE",
                        action="Lower the battery reserve floor to 15 %",
                        description=(
                            "Make more stored energy available for discretionary use, at the cost "
                            "of ride-through if a generator stops."
                        ),
                        requires=["operator accepts reduced ride-through"],
                        time_to_effect_min=1.0,
                        cost_note="Less stored energy is held back for the next failure.",
                    ),
                    measure(candidate),
                )
            )

        # Fuel resupply, when the environment says it is possible.
        if self.environment.resupply_available:
            options.append(
                RecoveryOption(
                    option_id="REC-RESUPPLY",
                    action="Request fuel resupply",
                    description=(
                        f"Request bulk fuel; assumed lead time "
                        f"{self.environment.resupply_lead_time_h:.0f} h."
                    ),
                    requires=["route open", "resupply asset available"],
                    time_to_effect_min=self.environment.resupply_lead_time_h * 60.0,
                    cost_note=(
                        "Exposes a vehicle and crew on the route. Effect is not simulated in "
                        "Pack 1 - the lead time is ASSUMED."
                    ),
                    available=True,
                )
            )

        options.sort(
            key=lambda o: (
                -o.restores_critical_assurance,
                -o.endurance_delta_h,
                -o.projected_endurance_delta_h,
                o.fuel_delta_l,
            )
        )
        return options
