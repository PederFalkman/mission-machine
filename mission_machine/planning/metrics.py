"""Output metrics.

Pack 1 deliberately reports the dimensions **separately**. There is no
composite "mission assurance score": a single number would hide the trade-off
the operator is being asked to make, and its weights would be invisible. See
``docs/architecture.md`` for the conditions under which a composite score could
be added later.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from mission_machine.assets.base import Asset, AssetKind
from mission_machine.assets.inventory import AssetInventory
from mission_machine.mission.spec import MissionSpec
from mission_machine.planning.configuration import Configuration
from mission_machine.assets.base import FailureState
from mission_machine.evidence.questions import OpenQuestion, merge as merge_questions
from mission_machine.simulation.simulator import SimulationResult, effective_state

EPS = 1e-9

#: A function counts as served when it gets at least this share of the energy it
#: asked for over the horizon. Below it, the operator did not get the function
#: they asked for, whatever the average says.
SERVED_COVERAGE_THRESHOLD = 0.95


@dataclass
class DeploymentComplexity:
    """How much work the configuration is to stand up.

    The index is *not* a hidden weighting: it is

        COMPLEXITY_POINTS = assets_to_deploy
                          + distinct_interface_types
                          + ceil(setup_critical_path_min / 30)

    and every component is reported alongside it.
    """

    assets_to_deploy: int = 0
    setup_critical_path_min: float = 0.0
    total_crew_minutes: float = 0.0
    distinct_interface_types: int = 0
    connections: int = 0
    deployment_time_limit_min: float = 0.0
    within_time_limit: bool = True

    @property
    def points(self) -> int:
        return (
            self.assets_to_deploy
            + self.distinct_interface_types
            + math.ceil(self.setup_critical_path_min / 30.0)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "assets_to_deploy": self.assets_to_deploy,
            "setup_critical_path_min": round(self.setup_critical_path_min, 1),
            "total_crew_minutes": round(self.total_crew_minutes, 1),
            "distinct_interface_types": self.distinct_interface_types,
            "connections": self.connections,
            "deployment_time_limit_min": self.deployment_time_limit_min,
            "within_time_limit": self.within_time_limit,
            "points": self.points,
            "formula": "assets + interface types + ceil(setup critical path / 30 min)",
        }


@dataclass
class ConfigurationMetrics:
    """Everything the COMPARE screen shows, plus what OPERATE needs."""

    configuration_id: str
    mission_id: str
    horizon_h: float = 0.0
    mission_duration_h: float = 0.0

    # endurance
    endurance_hours: float = 0.0
    projected_endurance_hours: float = 0.0
    endurance_limited_by: str = ""
    completes_mission: bool = False

    # coverage
    critical_load_coverage: float = 0.0
    critical_energy_demand_kwh: float = 0.0
    critical_energy_served_kwh: float = 0.0
    critical_hours_degraded: int = 0
    required_availability: float = 0.0
    availability_met: bool = False
    secondary_load_coverage: float = 0.0
    secondary_energy_demand_kwh: float = 0.0
    secondary_energy_served_kwh: float = 0.0
    shed_load_ids: list[str] = field(default_factory=list)
    load_coverage: dict[str, float] = field(default_factory=dict)
    """Energy served divided by energy demanded, per load, over the horizon."""
    unhonoured_priorities: list[str] = field(default_factory=list)
    """Functions the operator marked SERVE_IF_AFFORDABLE that this option drops."""

    # energy and fuel
    fuel_consumption_l: float = 0.0
    fuel_remaining_l: float = 0.0
    fuel_limit_l: float = 0.0
    energy_by_source_kwh: dict[str, float] = field(default_factory=dict)
    total_energy_kwh: float = 0.0
    grid_dependence: float = 0.0
    grid_energy_kwh: float = 0.0

    # storage
    battery_min_soc: float = 0.0
    battery_end_soc: float = 0.0
    battery_throughput_kwh: float = 0.0
    battery_equivalent_full_cycles: float = 0.0

    # reserve
    energy_reserve_kwh_min: float = 0.0
    energy_reserve_hours_min: float = 0.0
    energy_reserve_kwh_end: float = 0.0
    energy_reserve_hours_end: float = 0.0
    reserve_requirement_hours: float = 0.0
    reserve_requirement_met: bool = False
    energy_reserve_withheld_kwh: float = 0.0
    open_questions: list[dict[str, Any]] = field(default_factory=list)

    # assets
    number_of_active_assets: int = 0
    active_asset_ids: list[str] = field(default_factory=list)
    generator_run_hours: dict[str, float] = field(default_factory=dict)
    generator_starts: dict[str, int] = field(default_factory=dict)
    peak_critical_demand_kw: float = 0.0
    installed_generation_kw: float = 0.0
    standby_generation_kw: float = 0.0
    n_minus_1_generation_ok: bool = False
    n_minus_1_ride_through_h: float = 0.0

    # resilience (filled in by the resilience module)
    single_points_of_failure: list[dict[str, Any]] = field(default_factory=list)
    recovery_options: list[dict[str, Any]] = field(default_factory=list)

    deployment: DeploymentComplexity = field(default_factory=DeploymentComplexity)
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def feasible(self) -> bool:
        """Feasible = critical functions assured for the whole horizon."""

        return self.availability_met and self.completes_mission

    @property
    def deployment_setup_minutes(self) -> float:
        return self.deployment.setup_critical_path_min

    @property
    def single_point_of_failure_count(self) -> int:
        return len(self.single_points_of_failure)

    @property
    def recovery_option_count(self) -> int:
        return len(self.recovery_options)

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "mission_id": self.mission_id,
            "horizon_h": self.horizon_h,
            "mission_duration_h": self.mission_duration_h,
            "ENDURANCE_HOURS": round(self.endurance_hours, 2),
            "projected_endurance_hours": round(self.projected_endurance_hours, 2),
            "endurance_limited_by": self.endurance_limited_by,
            "completes_mission": self.completes_mission,
            "CRITICAL_LOAD_COVERAGE": round(self.critical_load_coverage, 4),
            "critical_energy_demand_kwh": round(self.critical_energy_demand_kwh, 1),
            "critical_energy_served_kwh": round(self.critical_energy_served_kwh, 1),
            "critical_hours_degraded": self.critical_hours_degraded,
            "required_availability": self.required_availability,
            "availability_met": self.availability_met,
            "SECONDARY_LOAD_COVERAGE": round(self.secondary_load_coverage, 4),
            "secondary_energy_demand_kwh": round(self.secondary_energy_demand_kwh, 1),
            "secondary_energy_served_kwh": round(self.secondary_energy_served_kwh, 1),
            "shed_load_ids": list(self.shed_load_ids),
            "load_coverage": {k: round(v, 4) for k, v in self.load_coverage.items()},
            "unhonoured_priorities": list(self.unhonoured_priorities),
            "FUEL_CONSUMPTION": round(self.fuel_consumption_l, 1),
            "fuel_remaining_l": round(self.fuel_remaining_l, 1),
            "fuel_limit_l": self.fuel_limit_l,
            "energy_by_source_kwh": {k: round(v, 1) for k, v in self.energy_by_source_kwh.items()},
            "total_energy_kwh": round(self.total_energy_kwh, 1),
            "GRID_DEPENDENCE": round(self.grid_dependence, 4),
            "grid_energy_kwh": round(self.grid_energy_kwh, 1),
            "battery_min_soc": round(self.battery_min_soc, 4),
            "battery_end_soc": round(self.battery_end_soc, 4),
            "battery_throughput_kwh": round(self.battery_throughput_kwh, 1),
            "battery_equivalent_full_cycles": round(self.battery_equivalent_full_cycles, 2),
            "ENERGY_RESERVE": round(self.energy_reserve_kwh_min, 1),
            "energy_reserve_hours_min": round(self.energy_reserve_hours_min, 2),
            "energy_reserve_kwh_end": round(self.energy_reserve_kwh_end, 1),
            "energy_reserve_hours_end": round(self.energy_reserve_hours_end, 2),
            "reserve_requirement_hours": self.reserve_requirement_hours,
            "reserve_requirement_met": self.reserve_requirement_met,
            "ENERGY_RESERVE_WITHHELD": round(self.energy_reserve_withheld_kwh, 1),
            "OPEN_QUESTIONS": list(self.open_questions),
            "NUMBER_OF_ACTIVE_ASSETS": self.number_of_active_assets,
            "active_asset_ids": list(self.active_asset_ids),
            "generator_run_hours": {k: round(v, 1) for k, v in self.generator_run_hours.items()},
            "generator_starts": dict(self.generator_starts),
            "peak_critical_demand_kw": round(self.peak_critical_demand_kw, 2),
            "installed_generation_kw": round(self.installed_generation_kw, 1),
            "standby_generation_kw": round(self.standby_generation_kw, 1),
            "n_minus_1_generation_ok": self.n_minus_1_generation_ok,
            "n_minus_1_ride_through_h": round(self.n_minus_1_ride_through_h, 2),
            "SINGLE_POINTS_OF_FAILURE": self.single_points_of_failure,
            "single_point_of_failure_count": self.single_point_of_failure_count,
            "RECOVERY_OPTIONS": self.recovery_options,
            "recovery_option_count": self.recovery_option_count,
            "DEPLOYMENT_COMPLEXITY": self.deployment.to_dict(),
            "feasible": self.feasible,
            "data_labels": list(self.data_labels),
        }


def deployment_complexity(
    mission: MissionSpec, configuration: Configuration, inventory: AssetInventory
) -> DeploymentComplexity:
    """Setup burden of the configuration's active assets.

    ASSUMED: crews work in parallel, one crew per asset, so the critical path is
    the longest single setup, not the sum.
    """

    assets = [inventory.get(aid) for aid in configuration.active_asset_ids]
    deployable = [a for a in assets if a.setup_time_min > 0]
    interfaces: set[str] = set()
    connections = 0
    for asset in assets:
        interfaces.update(asset.interface_requirements)
        connections += len(asset.interface_requirements)
    critical_path = max((a.setup_time_min for a in deployable), default=0.0)
    return DeploymentComplexity(
        assets_to_deploy=len(deployable),
        setup_critical_path_min=critical_path,
        total_crew_minutes=sum(a.setup_time_min * a.setup_crew for a in deployable),
        distinct_interface_types=len(interfaces),
        connections=connections,
        deployment_time_limit_min=mission.deployment_time_limit_min,
        within_time_limit=critical_path <= mission.deployment_time_limit_min,
    )


def compute_metrics(
    mission: MissionSpec,
    configuration: Configuration,
    result: SimulationResult,
    inventory: AssetInventory | None = None,
) -> ConfigurationMetrics:
    """Turn a simulation record into the reported output metrics."""

    inventory = inventory or mission.inventory
    dt = result.time_step_h
    steps = result.steps

    critical_demand = sum(s.critical_demand_kw * s.duration_h for s in steps)
    critical_served = sum(s.critical_served_kw * s.duration_h for s in steps)
    secondary_demand = sum(s.secondary_demand_kw * s.duration_h for s in steps)
    secondary_served = sum(s.secondary_served_kw * s.duration_h for s in steps)

    grid_kwh = sum(s.grid_kw * s.duration_h for s in steps)
    pv_kwh = sum(s.pv_kw * s.duration_h for s in steps)
    gen_kwh = sum(s.generator_total_kw * s.duration_h for s in steps)
    discharge_kwh = sum(s.battery_discharge_kw * s.duration_h for s in steps)
    charge_kwh = sum(s.battery_charge_kw * s.duration_h for s in steps)
    curtailed_kwh = sum(s.curtailed_kw * s.duration_h for s in steps)
    primary_kwh = grid_kwh + pv_kwh + gen_kwh

    battery = inventory.batteries[0] if inventory.batteries else None
    usable = battery.usable_kwh if battery else 0.0

    demand_by_load: dict[str, float] = {}
    served_by_load: dict[str, float] = {}
    for step in steps:
        for load_id, kw in step.load_demand_kw.items():
            demand_by_load[load_id] = demand_by_load.get(load_id, 0.0) + kw * step.duration_h
        for load_id, kw in step.load_served_kw.items():
            served_by_load[load_id] = served_by_load.get(load_id, 0.0) + kw * step.duration_h
    load_coverage = {
        load_id: (served_by_load.get(load_id, 0.0) / demand if demand > EPS else 1.0)
        for load_id, demand in demand_by_load.items()
    }

    shed: list[str] = []
    for step in steps:
        for load_id in step.shed_load_ids:
            if load_id not in shed:
                shed.append(load_id)

    coverage_critical = critical_served / critical_demand if critical_demand > EPS else 1.0
    coverage_secondary = secondary_served / secondary_demand if secondary_demand > EPS else 1.0

    reserve_values = [(s.reserve_kwh, s.reserve_hours) for s in steps] or [(0.0, 0.0)]
    reserve_min_kwh = min(v[0] for v in reserve_values)
    reserve_min_hours = min(v[1] for v in reserve_values)

    # Energy the node holds but this configuration cannot reach, reported next to
    # the reserve rather than folded into it or silently dropped. Reported at its
    # worst, to match the open questions that explain it.
    reserve_withheld_kwh = max((s.reserve_withheld_kwh for s in steps), default=0.0)
    open_questions = merge_questions(
        question for step in steps for question in step.reserve_questions
    )

    metrics = ConfigurationMetrics(
        configuration_id=configuration.configuration_id,
        mission_id=mission.mission_id,
        horizon_h=result.horizon_h,
        mission_duration_h=mission.mission_duration_h,
        endurance_hours=result.endurance_hours,
        endurance_limited_by=result.endurance_limited_by,
        completes_mission=result.completed_mission,
        critical_load_coverage=coverage_critical,
        critical_energy_demand_kwh=critical_demand,
        critical_energy_served_kwh=critical_served,
        critical_hours_degraded=sum(1 for s in steps if s.unserved_critical_kw > EPS),
        required_availability=mission.required_availability,
        availability_met=coverage_critical >= mission.required_availability - 1e-6,
        secondary_load_coverage=coverage_secondary,
        secondary_energy_demand_kwh=secondary_demand,
        secondary_energy_served_kwh=secondary_served,
        shed_load_ids=shed,
        load_coverage=load_coverage,
        unhonoured_priorities=[
            load_id
            for load_id in mission.serve_if_affordable_loads()
            if load_coverage.get(load_id, 0.0) < SERVED_COVERAGE_THRESHOLD
        ],
        fuel_consumption_l=sum(s.fuel_used_l for s in steps),
        fuel_remaining_l=result.final_state.fuel_remaining_l,
        fuel_limit_l=mission.fuel_limit_l,
        energy_by_source_kwh={
            "grid": grid_kwh,
            "pv": pv_kwh,
            "generator": gen_kwh,
            "battery_discharge": discharge_kwh,
            "battery_charge": charge_kwh,
            "curtailed": curtailed_kwh,
        },
        total_energy_kwh=primary_kwh,
        grid_dependence=grid_kwh / primary_kwh if primary_kwh > EPS else 0.0,
        grid_energy_kwh=grid_kwh,
        battery_min_soc=min((s.battery_soc for s in steps), default=0.0),
        battery_end_soc=result.final_state.battery_soc,
        battery_throughput_kwh=discharge_kwh,
        battery_equivalent_full_cycles=discharge_kwh / usable if usable > EPS else 0.0,
        energy_reserve_kwh_min=reserve_min_kwh,
        energy_reserve_hours_min=reserve_min_hours,
        energy_reserve_kwh_end=steps[-1].reserve_kwh if steps else 0.0,
        energy_reserve_hours_end=steps[-1].reserve_hours if steps else 0.0,
        reserve_requirement_hours=mission.minimum_reserve_hours,
        reserve_requirement_met=reserve_min_hours >= mission.minimum_reserve_hours - 1e-6,
        energy_reserve_withheld_kwh=reserve_withheld_kwh,
        open_questions=[question.to_dict() for question in open_questions],
        number_of_active_assets=len(configuration.active_asset_ids),
        active_asset_ids=list(configuration.active_asset_ids),
        generator_run_hours=dict(result.final_state.generator_run_hours),
        generator_starts=dict(result.final_state.generator_starts),
        deployment=deployment_complexity(mission, configuration, inventory),
    )

    # Endurance beyond the mission horizon: how much longer the node could hold
    # the critical load on what is left in the battery and the tanks.
    # ASSUMED extrapolation at the final step's critical demand.
    metrics.projected_endurance_hours = metrics.endurance_hours + (
        metrics.energy_reserve_hours_end if result.completed_mission else 0.0
    )

    # N-1 on generation: can the committed generators still carry the peak
    # critical load after the largest of them is lost? Grid supply is
    # deliberately not counted - it is the least dependable source in the model.
    # Only generators that are still serviceable at the end of the horizon count
    # towards redundancy: a set that has failed is not a reserve.
    reference_hour = max(result.start_hour, result.end_hour - dt)
    committed = [
        asset
        for asset in (inventory.find(aid) for aid in configuration.policy.generator_ids)
        if asset is not None
        and effective_state(asset, reference_hour, result.events) is not FailureState.UNAVAILABLE
    ]
    capacities = sorted((gen.capacity_kw for gen in committed), reverse=True)
    peak_critical = max((s.critical_demand_kw for s in steps), default=0.0)
    metrics.peak_critical_demand_kw = peak_critical
    metrics.installed_generation_kw = sum(capacities)
    metrics.standby_generation_kw = max(
        0.0, sum(capacities) - max((s.generator_total_kw for s in steps), default=0.0)
    )
    metrics.n_minus_1_generation_ok = sum(capacities[1:]) >= peak_critical - EPS

    # How long the node could hold its peak critical load after losing its
    # largest committed generator. Generation covers what it can; the battery
    # covers the gap until its usable energy runs out. Capped at the mission
    # duration - beyond that the number stops being useful.
    remaining_gen_kw = sum(capacities[1:])
    gap_kw = max(0.0, peak_critical - remaining_gen_kw)
    if gap_kw <= EPS:
        metrics.n_minus_1_ride_through_h = mission.mission_duration_h
    elif battery is not None and configuration.policy.use_battery:
        metrics.n_minus_1_ride_through_h = min(
            mission.mission_duration_h, usable * battery.one_way_efficiency / gap_kw
        )
    else:
        metrics.n_minus_1_ride_through_h = 0.0
    return metrics
