"""Generic asset interface.

Every physical thing Mission Machine can plan with is an :class:`Asset`.
The base class carries the properties the planner needs regardless of what
the asset does (can it be moved, is it working, how long does it take to set
up, what does it need to be connected to). Energy behaviour lives in the
subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AssetKind(str, Enum):
    GENERATOR = "GENERATOR"
    BATTERY = "BATTERY"
    GRID_CONNECTION = "GRID_CONNECTION"
    SOLAR_PV = "SOLAR_PV"
    POWER_CONVERSION = "POWER_CONVERSION"
    LOAD = "LOAD"
    COOLING_SYSTEM = "COOLING_SYSTEM"
    CHARGING_SYSTEM = "CHARGING_SYSTEM"
    COMMUNICATIONS_LOAD = "COMMUNICATIONS_LOAD"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Mobility(str, Enum):
    FIXED = "FIXED"                 # cannot be relocated within the mission
    TRAILER = "TRAILER"             # towable, needs a vehicle
    PALLETISED = "PALLETISED"       # needs handling equipment
    MAN_PORTABLE = "MAN_PORTABLE"   # two-person lift

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class FailureState(str, Enum):
    NOMINAL = "NOMINAL"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass
class OperatingConstraints:
    """Constraints that are not captured by a single scalar rating."""

    min_ambient_c: float = -30.0
    max_ambient_c: float = 45.0
    max_continuous_hours: float | None = None
    requires_operator_attendance: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "min_ambient_c": self.min_ambient_c,
            "max_ambient_c": self.max_ambient_c,
            "max_continuous_hours": self.max_continuous_hours,
            "requires_operator_attendance": self.requires_operator_attendance,
            "notes": list(self.notes),
        }


@dataclass
class Asset:
    """Base asset.

    Attributes are deliberately generic so that the planning engine can reason
    about an inventory it has never seen before.
    """

    asset_id: str
    name: str
    kind: AssetKind
    capacity_kw: float = 0.0
    energy_capacity_kwh: float = 0.0
    availability: float = 1.0               # fraction of time expected serviceable
    efficiency: float = 1.0
    ramp_rate_kw_per_min: float | None = None
    minimum_loading_fraction: float = 0.0
    startup_time_min: float = 0.0
    mobility: Mobility = Mobility.PALLETISED
    location: str = "SUPPORT_NODE"
    failure_state: FailureState = FailureState.NOMINAL
    interface_requirements: list[str] = field(default_factory=list)
    operating_constraints: OperatingConstraints = field(default_factory=OperatingConstraints)
    setup_time_min: float = 0.0
    setup_crew: int = 1
    optional: bool = False                  # not required to be deployed
    data_labels: list[str] = field(default_factory=lambda: ["SYNTHETIC", "UNVALIDATED"])
    source: str = "mission-machine/pack-1 synthetic asset set"

    # --- capability queries -------------------------------------------------

    @property
    def is_supply(self) -> bool:
        return self.kind in {
            AssetKind.GENERATOR,
            AssetKind.BATTERY,
            AssetKind.GRID_CONNECTION,
            AssetKind.SOLAR_PV,
        }

    @property
    def is_load(self) -> bool:
        return self.kind in {
            AssetKind.LOAD,
            AssetKind.COOLING_SYSTEM,
            AssetKind.CHARGING_SYSTEM,
            AssetKind.COMMUNICATIONS_LOAD,
        }

    @property
    def is_available(self) -> bool:
        return self.failure_state is not FailureState.UNAVAILABLE

    def derating_factor(self, ambient_c: float) -> float:
        """Fraction of rated capacity usable at ``ambient_c``.

        ASSUMED: linear derate of 1 %/degC above 25 degC, floor 0.8. Applied to
        thermal machines only; subclasses may override.
        """

        if ambient_c <= 25.0:
            return 1.0
        return max(0.8, 1.0 - 0.01 * (ambient_c - 25.0))

    # --- serialisation ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        out = {
            "asset_id": self.asset_id,
            "name": self.name,
            "kind": str(self.kind),
            "capacity_kw": self.capacity_kw,
            "energy_capacity_kwh": self.energy_capacity_kwh,
            "availability": self.availability,
            "efficiency": self.efficiency,
            "ramp_rate_kw_per_min": self.ramp_rate_kw_per_min,
            "minimum_loading_fraction": self.minimum_loading_fraction,
            "startup_time_min": self.startup_time_min,
            "mobility": str(self.mobility),
            "location": self.location,
            "failure_state": str(self.failure_state),
            "interface_requirements": list(self.interface_requirements),
            "operating_constraints": self.operating_constraints.to_dict(),
            "setup_time_min": self.setup_time_min,
            "setup_crew": self.setup_crew,
            "optional": self.optional,
            "data_labels": list(self.data_labels),
            "source": self.source,
        }
        out.update(self.extra_dict())
        return out

    def extra_dict(self) -> dict[str, Any]:
        """Subclass-specific fields for serialisation."""

        return {}
