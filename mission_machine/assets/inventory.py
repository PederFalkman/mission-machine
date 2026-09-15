"""Asset inventory: deserialisation, lookup and grouping."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Iterator

from mission_machine.assets.base import (
    Asset,
    AssetKind,
    FailureState,
    Mobility,
    OperatingConstraints,
)
from mission_machine.assets.energy import (
    Battery,
    Generator,
    GridConnection,
    PowerConversion,
    SolarPV,
)
from mission_machine.assets.loads import (
    ChargingSystem,
    CommunicationsLoad,
    CoolingSystem,
    Load,
    LoadCriticality,
    LoadProfile,
)

_CLASS_BY_KIND: dict[AssetKind, type[Asset]] = {
    AssetKind.GENERATOR: Generator,
    AssetKind.BATTERY: Battery,
    AssetKind.GRID_CONNECTION: GridConnection,
    AssetKind.SOLAR_PV: SolarPV,
    AssetKind.POWER_CONVERSION: PowerConversion,
    AssetKind.LOAD: Load,
    AssetKind.COOLING_SYSTEM: CoolingSystem,
    AssetKind.CHARGING_SYSTEM: ChargingSystem,
    AssetKind.COMMUNICATIONS_LOAD: CommunicationsLoad,
}


def asset_from_dict(payload: dict[str, Any]) -> Asset:
    """Build a typed asset from a plain dict (as stored in ``data/assets``)."""

    data = dict(payload)
    try:
        kind = AssetKind(data.pop("kind"))
    except KeyError as exc:  # pragma: no cover - defensive
        raise ValueError(f"asset {data.get('asset_id')!r} has no 'kind'") from exc
    cls = _CLASS_BY_KIND[kind]

    if "mobility" in data:
        data["mobility"] = Mobility(data["mobility"])
    if "failure_state" in data:
        data["failure_state"] = FailureState(data["failure_state"])
    if "criticality" in data:
        data["criticality"] = LoadCriticality(data["criticality"])
    if "operating_constraints" in data:
        data["operating_constraints"] = OperatingConstraints(**data["operating_constraints"])
    if "profile" in data:
        data["profile"] = LoadProfile(**data["profile"])

    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(
            f"asset {data.get('asset_id')!r} ({kind}) has unsupported fields: "
            + ", ".join(sorted(unknown))
        )
    return cls(kind=kind, **data)


@dataclass
class AssetInventory:
    """The set of assets a mission can plan with."""

    inventory_id: str = "SYNTHETIC-ASSET-SET-001"
    description: str = ""
    assets: list[Asset] = field(default_factory=list)
    data_labels: list[str] = field(default_factory=lambda: ["SYNTHETIC", "UNVALIDATED"])
    source: str = "mission-machine/pack-1 synthetic asset set"

    # --- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AssetInventory":
        return cls(
            inventory_id=payload.get("inventory_id", "SYNTHETIC-ASSET-SET-001"),
            description=payload.get("description", ""),
            assets=[asset_from_dict(item) for item in payload.get("assets", [])],
            data_labels=payload.get("data_labels", ["SYNTHETIC", "UNVALIDATED"]),
            source=payload.get("source", "mission-machine/pack-1 synthetic asset set"),
        )

    @classmethod
    def from_file(cls, path: str | Path) -> "AssetInventory":
        with open(path, "r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def copy(self) -> "AssetInventory":
        return copy.deepcopy(self)

    # --- lookup -------------------------------------------------------------

    def __iter__(self) -> Iterator[Asset]:
        return iter(self.assets)

    def __len__(self) -> int:
        return len(self.assets)

    def get(self, asset_id: str) -> Asset:
        for asset in self.assets:
            if asset.asset_id == asset_id:
                return asset
        raise KeyError(f"unknown asset: {asset_id!r}")

    def find(self, asset_id: str) -> Asset | None:
        try:
            return self.get(asset_id)
        except KeyError:
            return None

    def of_kind(self, *kinds: AssetKind) -> list[Asset]:
        wanted = set(kinds)
        return [a for a in self.assets if a.kind in wanted]

    def subset(self, asset_ids: Iterable[str]) -> list[Asset]:
        wanted = list(asset_ids)
        return [self.get(aid) for aid in wanted]

    @property
    def generators(self) -> list[Generator]:
        return [a for a in self.assets if isinstance(a, Generator)]

    @property
    def batteries(self) -> list[Battery]:
        return [a for a in self.assets if isinstance(a, Battery)]

    @property
    def grid_connections(self) -> list[GridConnection]:
        return [a for a in self.assets if isinstance(a, GridConnection)]

    @property
    def pv_arrays(self) -> list[SolarPV]:
        return [a for a in self.assets if isinstance(a, SolarPV)]

    @property
    def power_conversion(self) -> list[PowerConversion]:
        return [a for a in self.assets if isinstance(a, PowerConversion)]

    @property
    def loads(self) -> list[Load]:
        return [a for a in self.assets if isinstance(a, Load)]

    @property
    def critical_loads(self) -> list[Load]:
        return [load for load in self.loads if load.is_critical]

    @property
    def secondary_loads(self) -> list[Load]:
        return [load for load in self.loads if not load.is_critical]

    def supply_assets(self) -> list[Asset]:
        return [a for a in self.assets if a.is_supply]

    # --- mutation (used by failure scenarios) -------------------------------

    def with_failure(self, asset_id: str, state: FailureState) -> "AssetInventory":
        """Return a copy where ``asset_id`` is in ``state``."""

        clone = self.copy()
        clone.get(asset_id).failure_state = state
        return clone

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_id": self.inventory_id,
            "description": self.description,
            "data_labels": list(self.data_labels),
            "source": self.source,
            "assets": [a.to_dict() for a in self.assets],
        }
