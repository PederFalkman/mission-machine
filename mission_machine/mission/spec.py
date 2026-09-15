"""MissionSpec - the machine-readable statement of what must keep running.

This is the contract between the operator's intent and everything downstream.
Nothing in the planner reads free text: if a requirement is not in the
MissionSpec, the planner cannot honour it, and if it is in the MissionSpec, it
is visible to the operator in the MISSION screen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mission_machine.assets.base import Mobility
from mission_machine.assets.inventory import AssetInventory
from mission_machine.assets.loads import Load, LoadCriticality, LoadProfile
from mission_machine.environment.model import Environment, GridAvailability, WeatherProfile


class MissionSpecError(ValueError):
    """Raised when a MissionSpec is internally inconsistent."""


@dataclass
class ReserveRequirement:
    """How much untouched energy must remain at all times.

    Semantics are explicit rather than a bare number: Pack 1 refuses to ship
    quantities whose meaning has to be guessed.

    ``hours_of_critical_load`` - reserve must cover the critical load for this
    many hours, using stored energy plus unburned fuel.
    ``fraction_of_storage``    - reserve must be this fraction of nameplate
    storage energy.
    """

    type: str = "hours_of_critical_load"
    value: float = 6.0

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value}


@dataclass
class MobilityRequirement:
    relocation_required: bool = False
    max_displacement_time_min: float | None = None
    max_mobility_class: str = "TRAILER"
    note: str = ""

    def allows(self, mobility: Mobility) -> bool:
        order = [
            Mobility.MAN_PORTABLE,
            Mobility.PALLETISED,
            Mobility.TRAILER,
            Mobility.FIXED,
        ]
        limit = Mobility(self.max_mobility_class)
        return order.index(mobility) <= order.index(limit)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relocation_required": self.relocation_required,
            "max_displacement_time_min": self.max_displacement_time_min,
            "max_mobility_class": self.max_mobility_class,
            "note": self.note,
        }


@dataclass
class OperatorPriority:
    """One line of operator intent, ranked. Rank 1 outranks rank 2."""

    rank: int
    statement: str
    applies_to: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "statement": self.statement, "applies_to": list(self.applies_to)}


@dataclass
class MissionSpec:
    """Machine-readable mission definition."""

    mission_id: str
    name: str = ""
    mission_duration_h: float = 72.0
    time_step_h: float = 1.0
    location_context: str = ""
    critical_loads: list[str] = field(default_factory=list)
    secondary_loads: list[str] = field(default_factory=list)
    load_profiles: dict[str, dict[str, Any]] = field(default_factory=dict)
    required_availability: float = 0.99
    minimum_reserve: ReserveRequirement = field(default_factory=ReserveRequirement)
    fuel_limit_l: float = 0.0
    grid_availability: GridAvailability = field(default_factory=GridAvailability)
    weather_profile: WeatherProfile = field(default_factory=WeatherProfile)
    deployment_time_limit_min: float = 240.0
    mobility_requirement: MobilityRequirement = field(default_factory=MobilityRequirement)
    operator_priorities: list[OperatorPriority] = field(default_factory=list)
    asset_inventory_ref: str = ""
    inventory: AssetInventory = field(default_factory=AssetInventory)
    data_labels: list[str] = field(default_factory=lambda: ["SYNTHETIC", "UNVALIDATED"])
    source: str = "mission-machine/pack-1 synthetic mission set"
    notes: list[str] = field(default_factory=list)

    # --- derived views ------------------------------------------------------

    @property
    def step_count(self) -> int:
        return int(round(self.mission_duration_h / self.time_step_h))

    @property
    def hours(self) -> list[float]:
        return [i * self.time_step_h for i in range(self.step_count)]

    @property
    def minimum_reserve_hours(self) -> float:
        if self.minimum_reserve.type == "hours_of_critical_load":
            return self.minimum_reserve.value
        return 0.0

    def critical_load_assets(self) -> list[Load]:
        return [self._load(load_id) for load_id in self.critical_loads]

    def secondary_load_assets(self) -> list[Load]:
        return [self._load(load_id) for load_id in self.secondary_loads]

    def all_load_assets(self) -> list[Load]:
        return self.critical_load_assets() + self.secondary_load_assets()

    def _load(self, load_id: str) -> Load:
        asset = self.inventory.get(load_id)
        if not isinstance(asset, Load):
            raise MissionSpecError(f"{load_id!r} is referenced as a load but is {asset.kind}")
        return asset

    def critical_demand_kw(self, hour: float, ambient_c: float) -> float:
        return sum(load.demand_kw(hour, ambient_c) for load in self.critical_load_assets())

    def mean_critical_demand_kw(self, environment: Environment) -> float:
        hours = self.hours
        if not hours:
            return 0.0
        total = sum(
            self.critical_demand_kw(hour, environment.temperature_c(hour)) for hour in hours
        )
        return total / len(hours)

    def critical_energy_demand_kwh(self, environment: Environment) -> float:
        return sum(
            self.critical_demand_kw(hour, environment.temperature_c(hour)) * self.time_step_h
            for hour in self.hours
        )

    def build_environment(self) -> Environment:
        """Assemble the Environment implied by this MissionSpec."""

        return Environment(
            environment_id=f"ENV-{self.mission_id}",
            location_context=self.location_context,
            weather=self.weather_profile,
            grid=self.grid_availability,
        )

    # --- validation ---------------------------------------------------------

    def validate(self) -> list[str]:
        """Return a list of problems. Empty list means the spec is usable.

        Validation is returned rather than raised so the MISSION screen can show
        the operator exactly what is wrong with their mission definition.
        """

        problems: list[str] = []
        if self.mission_duration_h <= 0:
            problems.append("mission_duration_h must be positive")
        if self.time_step_h <= 0 or self.time_step_h > self.mission_duration_h:
            problems.append("time_step_h must be positive and no longer than the mission")
        if not 0.0 < self.required_availability <= 1.0:
            problems.append("required_availability must be in (0, 1]")
        if not self.critical_loads:
            problems.append("a mission with no critical loads has nothing to assure")

        seen = set()
        for load_id in self.critical_loads + self.secondary_loads:
            if load_id in seen:
                problems.append(f"load {load_id!r} listed twice")
            seen.add(load_id)
            asset = self.inventory.find(load_id)
            if asset is None:
                problems.append(f"load {load_id!r} is not in asset inventory")
                continue
            if not isinstance(asset, Load):
                problems.append(f"load {load_id!r} is not a load asset (kind={asset.kind})")

        for load_id in self.critical_loads:
            asset = self.inventory.find(load_id)
            if isinstance(asset, Load) and asset.criticality is not LoadCriticality.CRITICAL:
                problems.append(
                    f"{load_id!r} is listed as critical in the mission but marked "
                    f"{asset.criticality} in the inventory"
                )
        for load_id in self.secondary_loads:
            asset = self.inventory.find(load_id)
            if isinstance(asset, Load) and asset.criticality is LoadCriticality.CRITICAL:
                problems.append(
                    f"{load_id!r} is listed as secondary in the mission but marked "
                    f"CRITICAL in the inventory"
                )

        for load_id in self.load_profiles:
            if load_id not in seen:
                problems.append(f"load_profiles has an entry for unlisted load {load_id!r}")

        if self.fuel_limit_l < 0:
            problems.append("fuel_limit_l cannot be negative")

        # Mobility only constrains the plan when the node is actually required
        # to be able to move; a fixed grid feeder is not a defect otherwise.
        if self.mobility_requirement.relocation_required:
            for asset in self.inventory:
                if not self.mobility_requirement.allows(asset.mobility):
                    problems.append(
                        f"asset {asset.asset_id!r} mobility {asset.mobility} exceeds mission "
                        f"mobility limit {self.mobility_requirement.max_mobility_class}"
                    )
        return problems

    def mobility_excluded_assets(self) -> list[str]:
        """Assets that could not move with the node if relocation were ordered.

        Reported to the operator rather than silently filtered out: whether a
        fixed asset is acceptable is a command decision, not a solver decision.
        """

        return [
            asset.asset_id
            for asset in self.inventory
            if not self.mobility_requirement.allows(asset.mobility)
        ]

    def require_valid(self) -> "MissionSpec":
        problems = self.validate()
        if problems:
            raise MissionSpecError("; ".join(problems))
        return self

    # --- serialisation ------------------------------------------------------

    @classmethod
    def from_dict(
        cls,
        payload: dict[str, Any],
        *,
        base_path: Path | None = None,
        inventory: AssetInventory | None = None,
    ) -> "MissionSpec":
        base_path = base_path or Path.cwd()

        inventory_ref = payload.get("asset_inventory", "")
        if inventory is not None:
            resolved_inventory = inventory
        elif isinstance(inventory_ref, dict):
            resolved_inventory = AssetInventory.from_dict(inventory_ref)
            inventory_ref = resolved_inventory.inventory_id
        elif inventory_ref:
            resolved_inventory = AssetInventory.from_file((base_path / inventory_ref).resolve())
        else:
            resolved_inventory = AssetInventory()

        spec = cls(
            mission_id=payload["mission_id"],
            name=payload.get("name", ""),
            mission_duration_h=float(payload.get("mission_duration", {}).get("value", 72.0))
            if isinstance(payload.get("mission_duration"), dict)
            else float(payload.get("mission_duration_h", payload.get("mission_duration", 72.0))),
            time_step_h=float(payload.get("time_step_h", 1.0)),
            location_context=payload.get("location_context", ""),
            critical_loads=list(payload.get("critical_loads", [])),
            secondary_loads=list(payload.get("secondary_loads", [])),
            load_profiles=dict(payload.get("load_profiles", {})),
            required_availability=float(payload.get("required_availability", 0.99)),
            minimum_reserve=ReserveRequirement(**payload["minimum_reserve"])
            if "minimum_reserve" in payload
            else ReserveRequirement(),
            fuel_limit_l=float(payload.get("fuel_limit", payload.get("fuel_limit_l", 0.0))),
            grid_availability=GridAvailability(**payload["grid_availability"])
            if "grid_availability" in payload
            else GridAvailability(),
            weather_profile=WeatherProfile(**payload["weather_profile"])
            if "weather_profile" in payload
            else WeatherProfile(),
            deployment_time_limit_min=float(
                payload.get("deployment_time_limit", payload.get("deployment_time_limit_min", 240.0))
            ),
            mobility_requirement=MobilityRequirement(**payload["mobility_requirement"])
            if "mobility_requirement" in payload
            else MobilityRequirement(),
            operator_priorities=[
                OperatorPriority(**item) for item in payload.get("operator_priorities", [])
            ],
            asset_inventory_ref=str(inventory_ref),
            inventory=resolved_inventory,
            data_labels=payload.get("data_labels", ["SYNTHETIC", "UNVALIDATED"]),
            source=payload.get("source", "mission-machine/pack-1 synthetic mission set"),
            notes=list(payload.get("notes", [])),
        )
        spec._apply_load_profile_overrides()
        return spec

    @classmethod
    def from_file(cls, path: str | Path, inventory: AssetInventory | None = None) -> "MissionSpec":
        path = Path(path)
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return cls.from_dict(payload, base_path=path.parent, inventory=inventory)

    def _apply_load_profile_overrides(self) -> None:
        """Mission-level demand profiles override inventory defaults."""

        for load_id, profile in self.load_profiles.items():
            asset = self.inventory.find(load_id)
            if isinstance(asset, Load):
                asset.profile = LoadProfile(**profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "name": self.name,
            "mission_duration_h": self.mission_duration_h,
            "time_step_h": self.time_step_h,
            "location_context": self.location_context,
            "critical_loads": list(self.critical_loads),
            "secondary_loads": list(self.secondary_loads),
            "load_profiles": {k: dict(v) for k, v in self.load_profiles.items()},
            "required_availability": self.required_availability,
            "minimum_reserve": self.minimum_reserve.to_dict(),
            "fuel_limit_l": self.fuel_limit_l,
            "grid_availability": self.grid_availability.to_dict(),
            "weather_profile": self.weather_profile.to_dict(),
            "deployment_time_limit_min": self.deployment_time_limit_min,
            "mobility_requirement": self.mobility_requirement.to_dict(),
            "operator_priorities": [p.to_dict() for p in self.operator_priorities],
            "asset_inventory": self.asset_inventory_ref,
            "data_labels": list(self.data_labels),
            "source": self.source,
            "notes": list(self.notes),
        }
