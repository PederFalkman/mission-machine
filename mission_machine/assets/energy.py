"""Supply-side and conversion assets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mission_machine.assets.base import Asset, AssetKind


@dataclass
class Generator(Asset):
    """Fuel-burning generator set.

    Fuel use is modelled with the standard two-parameter affine curve

        litres/h = no_load_l_per_h + marginal_l_per_kwh * kW_output

    which is transparent, monotonic and linear - so the same coefficients are
    reusable directly in the MILP formulation (see ``planning/milp.py``).
    """

    kind: AssetKind = AssetKind.GENERATOR
    no_load_l_per_h: float = 0.0
    marginal_l_per_kwh: float = 0.30
    tank_l: float = 0.0
    mean_time_between_failures_h: float | None = None

    def fuel_for(self, output_kw: float, hours: float) -> float:
        """Litres burned producing ``output_kw`` for ``hours`` (0 if stopped)."""

        if output_kw <= 0.0 or hours <= 0.0:
            return 0.0
        return self.no_load_l_per_h * hours + self.marginal_l_per_kwh * output_kw * hours

    def min_loading_kw(self, ambient_c: float = 15.0) -> float:
        return self.minimum_loading_fraction * self.max_output_kw(ambient_c)

    def max_output_kw(self, ambient_c: float = 15.0) -> float:
        return self.capacity_kw * self.derating_factor(ambient_c)

    def specific_fuel_l_per_kwh(self, loading_fraction: float = 0.75) -> float:
        """Fuel intensity at a given loading - used for merit ordering."""

        output = max(1e-6, self.capacity_kw * loading_fraction)
        return self.fuel_for(output, 1.0) / output

    def extra_dict(self) -> dict[str, Any]:
        return {
            "no_load_l_per_h": self.no_load_l_per_h,
            "marginal_l_per_kwh": self.marginal_l_per_kwh,
            "tank_l": self.tank_l,
            "mean_time_between_failures_h": self.mean_time_between_failures_h,
            "specific_fuel_l_per_kwh_at_75pct": round(self.specific_fuel_l_per_kwh(), 4),
        }


@dataclass
class Battery(Asset):
    """Battery energy storage system (BESS)."""

    kind: AssetKind = AssetKind.BATTERY
    max_charge_kw: float = 0.0
    max_discharge_kw: float = 0.0
    round_trip_efficiency: float = 0.92
    min_state_of_charge: float = 0.20
    max_state_of_charge: float = 1.0
    initial_state_of_charge: float = 0.90
    self_discharge_per_h: float = 0.0002

    def __post_init__(self) -> None:
        if self.max_charge_kw <= 0.0:
            self.max_charge_kw = self.capacity_kw
        if self.max_discharge_kw <= 0.0:
            self.max_discharge_kw = self.capacity_kw

    @property
    def one_way_efficiency(self) -> float:
        """ASSUMED: round-trip losses split evenly between charge and discharge."""

        return self.round_trip_efficiency ** 0.5

    @property
    def usable_kwh(self) -> float:
        return self.energy_capacity_kwh * (self.max_state_of_charge - self.min_state_of_charge)

    def reserve_floor_kwh(self) -> float:
        return self.energy_capacity_kwh * self.min_state_of_charge

    def extra_dict(self) -> dict[str, Any]:
        return {
            "max_charge_kw": self.max_charge_kw,
            "max_discharge_kw": self.max_discharge_kw,
            "round_trip_efficiency": self.round_trip_efficiency,
            "min_state_of_charge": self.min_state_of_charge,
            "max_state_of_charge": self.max_state_of_charge,
            "initial_state_of_charge": self.initial_state_of_charge,
            "self_discharge_per_h": self.self_discharge_per_h,
            "usable_kwh": round(self.usable_kwh, 2),
        }


@dataclass
class GridConnection(Asset):
    """Connection to host-nation / civil supply.

    Availability is an hour-by-hour profile because the interesting research
    question is what happens when it goes away, not what happens when it is
    perfect.
    """

    kind: AssetKind = AssetKind.GRID_CONNECTION
    available_hours: list[list[int]] = field(default_factory=list)  # [[start, end), ...]
    reliability: float = 0.9
    reconnect_time_min: float = 15.0

    def is_available_at(self, hour: float) -> bool:
        if not self.is_available:
            return False
        if not self.available_hours:
            return True
        return any(start <= hour < end for start, end in self.available_hours)

    def extra_dict(self) -> dict[str, Any]:
        return {
            "available_hours": [list(w) for w in self.available_hours],
            "reliability": self.reliability,
            "reconnect_time_min": self.reconnect_time_min,
        }


@dataclass
class SolarPV(Asset):
    """Optional mobile photovoltaic array."""

    kind: AssetKind = AssetKind.SOLAR_PV
    peak_kw: float = 0.0
    derating: float = 0.85
    temperature_coefficient_per_c: float = -0.004
    reference_temperature_c: float = 25.0

    def __post_init__(self) -> None:
        if self.peak_kw <= 0.0:
            self.peak_kw = self.capacity_kw
        if self.capacity_kw <= 0.0:
            self.capacity_kw = self.peak_kw

    def output_kw(self, solar_fraction: float, ambient_c: float) -> float:
        """Output at a given irradiance fraction (0..1) and ambient temperature."""

        if solar_fraction <= 0.0 or not self.is_available:
            return 0.0
        temp_factor = 1.0 + self.temperature_coefficient_per_c * (
            ambient_c - self.reference_temperature_c
        )
        return max(0.0, self.peak_kw * self.derating * solar_fraction * temp_factor)

    def extra_dict(self) -> dict[str, Any]:
        return {
            "peak_kw": self.peak_kw,
            "derating": self.derating,
            "temperature_coefficient_per_c": self.temperature_coefficient_per_c,
            "reference_temperature_c": self.reference_temperature_c,
        }


@dataclass
class PowerConversion(Asset):
    """Bidirectional power conversion equipment / distribution limit."""

    kind: AssetKind = AssetKind.POWER_CONVERSION
    bidirectional: bool = True

    def extra_dict(self) -> dict[str, Any]:
        return {"bidirectional": self.bidirectional}
