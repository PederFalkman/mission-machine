"""Simulation state and time-series records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mission_machine.evidence.questions import OpenQuestion

#: Energy below which an unmet demand is treated as rounding, not a failure.
UNSERVED_TOLERANCE_KWH = 0.05


@dataclass
class ReserveBreakdown:
    """The energy reserve, split by whether the configuration can reach it.

    ``reserve_kwh`` counts only energy this configuration can actually deliver.
    Energy that exists on the node but cannot be reached - a battery that is not
    deployed, fuel with no generator committed - is reported as
    ``withheld_kwh`` with an :class:`~mission_machine.evidence.OpenQuestion`
    saying why, never as zero and never folded into the total.
    """

    reserve_kwh: float = 0.0
    reserve_hours: float = 0.0
    reserve_withheld_kwh: float = 0.0
    reserve_questions: list[OpenQuestion] = field(default_factory=list)
    stored_kwh: float = 0.0
    fuel_kwh: float = 0.0
    withheld_kwh: float = 0.0
    questions: tuple[OpenQuestion, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reserve_kwh": round(self.reserve_kwh, 2),
            "reserve_hours": round(self.reserve_hours, 2),
            "reserve_withheld_kwh": round(self.reserve_withheld_kwh, 2),
            "reserve_questions": [q.to_dict() for q in self.reserve_questions],
            "stored_kwh": round(self.stored_kwh, 2),
            "fuel_kwh": round(self.fuel_kwh, 2),
            "withheld_kwh": round(self.withheld_kwh, 2),
            "questions": [q.to_dict() for q in self.questions],
        }


@dataclass
class NodeState:
    """Everything that carries over from one time step to the next."""

    hour: float = 0.0
    battery_soc: float = 1.0
    fuel_remaining_l: float = 0.0
    generator_running: dict[str, bool] = field(default_factory=dict)
    generator_output_kw: dict[str, float] = field(default_factory=dict)
    generator_run_hours: dict[str, float] = field(default_factory=dict)
    generator_starts: dict[str, int] = field(default_factory=dict)

    def copy(self) -> "NodeState":
        return NodeState(
            hour=self.hour,
            battery_soc=self.battery_soc,
            fuel_remaining_l=self.fuel_remaining_l,
            generator_running=dict(self.generator_running),
            generator_output_kw=dict(self.generator_output_kw),
            generator_run_hours=dict(self.generator_run_hours),
            generator_starts=dict(self.generator_starts),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "hour": self.hour,
            "battery_soc": round(self.battery_soc, 4),
            "fuel_remaining_l": round(self.fuel_remaining_l, 2),
            "generator_running": dict(self.generator_running),
            "generator_output_kw": {k: round(v, 2) for k, v in self.generator_output_kw.items()},
            "generator_run_hours": {k: round(v, 2) for k, v in self.generator_run_hours.items()},
            "generator_starts": dict(self.generator_starts),
        }


@dataclass
class StepRecord:
    """One time step of the dispatch, in full. Nothing is hidden from the operator."""

    hour: float
    duration_h: float
    ambient_c: float
    solar_fraction: float
    grid_available: bool

    critical_demand_kw: float = 0.0
    critical_served_kw: float = 0.0
    secondary_demand_kw: float = 0.0          # every secondary load, whether attempted or not
    secondary_attempted_kw: float = 0.0       # what the policy tried to serve
    secondary_served_kw: float = 0.0

    pv_kw: float = 0.0
    grid_kw: float = 0.0
    generator_kw: dict[str, float] = field(default_factory=dict)
    battery_discharge_kw: float = 0.0
    battery_charge_kw: float = 0.0
    curtailed_kw: float = 0.0

    battery_soc: float = 0.0
    battery_energy_kwh: float = 0.0
    fuel_used_l: float = 0.0
    fuel_remaining_l: float = 0.0

    reserve_kwh: float = 0.0
    reserve_hours: float = 0.0
    reserve_withheld_kwh: float = 0.0
    reserve_questions: list[OpenQuestion] = field(default_factory=list)
    shed_load_ids: list[str] = field(default_factory=list)
    unserved_critical_kw: float = 0.0
    load_demand_kw: dict[str, float] = field(default_factory=dict)
    load_served_kw: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def generator_total_kw(self) -> float:
        return sum(self.generator_kw.values())

    @property
    def critical_shortfall_kwh(self) -> float:
        return self.unserved_critical_kw * self.duration_h

    def to_dict(self) -> dict[str, Any]:
        return {
            "hour": self.hour,
            "duration_h": self.duration_h,
            "ambient_c": round(self.ambient_c, 2),
            "solar_fraction": round(self.solar_fraction, 3),
            "grid_available": self.grid_available,
            "critical_demand_kw": round(self.critical_demand_kw, 2),
            "critical_served_kw": round(self.critical_served_kw, 2),
            "secondary_demand_kw": round(self.secondary_demand_kw, 2),
            "secondary_attempted_kw": round(self.secondary_attempted_kw, 2),
            "secondary_served_kw": round(self.secondary_served_kw, 2),
            "pv_kw": round(self.pv_kw, 2),
            "grid_kw": round(self.grid_kw, 2),
            "generator_kw": {k: round(v, 2) for k, v in self.generator_kw.items()},
            "generator_total_kw": round(self.generator_total_kw, 2),
            "battery_discharge_kw": round(self.battery_discharge_kw, 2),
            "battery_charge_kw": round(self.battery_charge_kw, 2),
            "curtailed_kw": round(self.curtailed_kw, 2),
            "battery_soc": round(self.battery_soc, 4),
            "battery_energy_kwh": round(self.battery_energy_kwh, 2),
            "fuel_used_l": round(self.fuel_used_l, 3),
            "fuel_remaining_l": round(self.fuel_remaining_l, 2),
            "reserve_kwh": round(self.reserve_kwh, 2),
            "reserve_hours": round(self.reserve_hours, 2),
            "reserve_withheld_kwh": round(self.reserve_withheld_kwh, 2),
            "reserve_questions": [q.to_dict() for q in self.reserve_questions],
            "shed_load_ids": list(self.shed_load_ids),
            "load_demand_kw": {k: round(v, 2) for k, v in self.load_demand_kw.items()},
            "load_served_kw": {k: round(v, 2) for k, v in self.load_served_kw.items()},
            "unserved_critical_kw": round(self.unserved_critical_kw, 3),
            "notes": list(self.notes),
        }
