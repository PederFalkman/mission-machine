"""Demand-side assets (mission functions that consume power)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mission_machine.assets.base import Asset, AssetKind


class LoadCriticality(str, Enum):
    CRITICAL = "CRITICAL"
    SECONDARY = "SECONDARY"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass
class LoadProfile:
    """Machine-readable demand profile.

    Supported types (all transparent, all linear in the MILP):

    ``constant``     - flat ``kw``
    ``hourly``       - explicit ``values`` list, one entry per mission hour
    ``diurnal``      - ``base_kw`` plus ``swing_kw`` peaking at ``peak_hour``
    ``temperature``  - ``base_kw`` plus ``kw_per_degc`` above ``reference_c``
    ``schedule``     - ``windows`` of ``[start_hour, end_hour, kw]``
    """

    type: str = "constant"
    kw: float = 0.0
    values: list[float] = field(default_factory=list)
    base_kw: float = 0.0
    swing_kw: float = 0.0
    peak_hour: float = 15.0
    kw_per_degc: float = 0.0
    reference_c: float = 15.0
    windows: list[list[float]] = field(default_factory=list)
    max_kw: float | None = None

    def demand_kw(self, hour: float, ambient_c: float) -> float:
        import math

        if self.type == "constant":
            value = self.kw
        elif self.type == "hourly":
            if not self.values:
                value = 0.0
            else:
                idx = int(hour) % len(self.values)
                value = self.values[idx]
        elif self.type == "diurnal":
            phase = 2.0 * math.pi * (hour - self.peak_hour) / 24.0
            value = self.base_kw + self.swing_kw * max(0.0, math.cos(phase))
        elif self.type == "temperature":
            value = self.base_kw + self.kw_per_degc * max(0.0, ambient_c - self.reference_c)
        elif self.type == "schedule":
            value = 0.0
            for window in self.windows:
                start, end, kw = window[0], window[1], window[2]
                if start <= (hour % 24.0) < end or start <= hour < end:
                    value = max(value, kw)
        else:
            raise ValueError(f"unknown load profile type: {self.type!r}")
        if self.max_kw is not None:
            value = min(value, self.max_kw)
        return max(0.0, value)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        for key in (
            "kw",
            "values",
            "base_kw",
            "swing_kw",
            "peak_hour",
            "kw_per_degc",
            "reference_c",
            "windows",
            "max_kw",
        ):
            value = getattr(self, key)
            if value not in (0.0, [], None):
                out[key] = value
        return out


def scale_profile(profile: LoadProfile, scale: float) -> LoadProfile:
    """A copy of ``profile`` with every demand term scaled.

    Used where a function is run in a degraded mode: the shape of the demand is
    unchanged, the magnitude is not.
    """

    return LoadProfile(
        type=profile.type,
        kw=profile.kw * scale,
        values=[value * scale for value in profile.values],
        base_kw=profile.base_kw * scale,
        swing_kw=profile.swing_kw * scale,
        peak_hour=profile.peak_hour,
        kw_per_degc=profile.kw_per_degc * scale,
        reference_c=profile.reference_c,
        windows=[[w[0], w[1], w[2] * scale] for w in profile.windows],
        max_kw=profile.max_kw,
    )


@dataclass
class Load(Asset):
    """A mission function expressed as an electrical demand."""

    kind: AssetKind = AssetKind.LOAD
    function: str = ""                       # what the load is FOR, in operator language
    criticality: LoadCriticality = LoadCriticality.SECONDARY
    profile: LoadProfile = field(default_factory=LoadProfile)
    shed_priority: int = 5                   # 1 = shed last, 9 = shed first
    deferrable: bool = False                 # may be moved in time without loss of function
    min_service_fraction: float = 0.0        # partial service that still delivers the function

    def demand_kw(self, hour: float, ambient_c: float) -> float:
        if not self.is_available:
            return 0.0
        return self.profile.demand_kw(hour, ambient_c)

    @property
    def is_critical(self) -> bool:
        return self.criticality is LoadCriticality.CRITICAL

    def extra_dict(self) -> dict[str, Any]:
        return {
            "function": self.function,
            "criticality": str(self.criticality),
            "profile": self.profile.to_dict(),
            "shed_priority": self.shed_priority,
            "deferrable": self.deferrable,
            "min_service_fraction": self.min_service_fraction,
        }


@dataclass
class CommunicationsLoad(Load):
    """Communications support load (radio, SATCOM, network)."""

    kind: AssetKind = AssetKind.COMMUNICATIONS_LOAD
    redundant_feed_required: bool = True

    def extra_dict(self) -> dict[str, Any]:
        out = super().extra_dict()
        out["redundant_feed_required"] = self.redundant_feed_required
        return out


@dataclass
class CoolingSystem(Load):
    """Environmental control / cooling load."""

    kind: AssetKind = AssetKind.COOLING_SYSTEM
    coefficient_of_performance: float = 2.8
    setpoint_c: float = 22.0
    degraded_setpoint_c: float = 27.0

    def extra_dict(self) -> dict[str, Any]:
        out = super().extra_dict()
        out.update(
            {
                "coefficient_of_performance": self.coefficient_of_performance,
                "setpoint_c": self.setpoint_c,
                "degraded_setpoint_c": self.degraded_setpoint_c,
            }
        )
        return out


@dataclass
class ChargingSystem(Load):
    """Vehicle / UAS charging load."""

    kind: AssetKind = AssetKind.CHARGING_SYSTEM
    energy_per_cycle_kwh: float = 0.0
    cycles_required: int = 0

    def extra_dict(self) -> dict[str, Any]:
        out = super().extra_dict()
        out.update(
            {
                "energy_per_cycle_kwh": self.energy_per_cycle_kwh,
                "cycles_required": self.cycles_required,
            }
        )
        return out
