"""Configuration - one candidate way of standing up and running the node.

A Configuration is two things:

1. **Which assets are in use** (what the deployment team physically sets up).
2. **How they are operated** (the dispatch policy the node runs under).

Both are explicit, small and human-readable. An operator has to be able to read
a configuration out loud and understand what they are being asked to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from mission_machine.assets.loads import Load


class SecondaryPolicy(str, Enum):
    """How much discretionary load the node attempts to serve."""

    FULL = "FULL"                    # attempt every secondary load
    PRIORITY_ONLY = "PRIORITY_ONLY"  # attempt only the high-priority secondary loads
    CRITICAL_ONLY = "CRITICAL_ONLY"  # serve nothing discretionary

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class GeneratorMode(str, Enum):
    """How generators are committed."""

    #: Start a generator only when needed, run it hard, and use the surplus to
    #: recharge the battery so it can be stopped again. Fewer, higher-loaded
    #: running hours; better specific fuel consumption; more start/stop cycles.
    CYCLED = "CYCLED"

    #: Keep the committed generator(s) running for the whole mission. Simpler to
    #: supervise, more tolerant of sudden load steps, burns no-load fuel.
    CONTINUOUS = "CONTINUOUS"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Secondary loads with a shed priority at or below this number are attempted
#: under :attr:`SecondaryPolicy.PRIORITY_ONLY`.
PRIORITY_SECONDARY_THRESHOLD = 5


@dataclass
class DispatchPolicy:
    """The operating rules the node runs under. Deterministic and inspectable."""

    generator_ids: tuple[str, ...] = ()
    use_grid: bool = True
    deploy_pv: bool = False
    use_battery: bool = True
    battery_reserve_soc: float = 0.25
    secondary_policy: SecondaryPolicy = SecondaryPolicy.PRIORITY_ONLY
    generator_mode: GeneratorMode = GeneratorMode.CYCLED
    battery_charge_target_soc: float = 0.95

    def attempts(self, load: Load) -> bool:
        """Does this policy attempt to serve ``load`` at all?"""

        if load.is_critical:
            return True
        if self.secondary_policy is SecondaryPolicy.CRITICAL_ONLY:
            return False
        if self.secondary_policy is SecondaryPolicy.FULL:
            return True
        return load.shed_priority <= PRIORITY_SECONDARY_THRESHOLD

    def describe(self) -> list[str]:
        lines = []
        if self.generator_ids:
            mode = "cycled with the battery" if self.generator_mode is GeneratorMode.CYCLED else "run continuously"
            lines.append(f"Generators {', '.join(self.generator_ids)} {mode}.")
        else:
            lines.append("No generator committed.")
        lines.append(
            "Grid used whenever available." if self.use_grid else "Grid not used (node runs islanded)."
        )
        lines.append("Mobile PV deployed." if self.deploy_pv else "Mobile PV not deployed.")
        if self.use_battery:
            lines.append(
                f"Battery held at or above {self.battery_reserve_soc:.0%} state of charge for "
                f"discretionary use, recharged towards {self.battery_charge_target_soc:.0%}."
            )
        else:
            lines.append("Battery not used.")
        lines.append(
            {
                SecondaryPolicy.FULL: "All secondary loads attempted.",
                SecondaryPolicy.PRIORITY_ONLY: (
                    f"Only secondary loads with shed priority <= {PRIORITY_SECONDARY_THRESHOLD} attempted."
                ),
                SecondaryPolicy.CRITICAL_ONLY: "Secondary loads not served.",
            }[self.secondary_policy]
        )
        return lines

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_ids": list(self.generator_ids),
            "use_grid": self.use_grid,
            "deploy_pv": self.deploy_pv,
            "use_battery": self.use_battery,
            "battery_reserve_soc": self.battery_reserve_soc,
            "secondary_policy": str(self.secondary_policy),
            "generator_mode": str(self.generator_mode),
            "battery_charge_target_soc": self.battery_charge_target_soc,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DispatchPolicy":
        return cls(
            generator_ids=tuple(payload.get("generator_ids", ())),
            use_grid=bool(payload.get("use_grid", True)),
            deploy_pv=bool(payload.get("deploy_pv", False)),
            use_battery=bool(payload.get("use_battery", True)),
            battery_reserve_soc=float(payload.get("battery_reserve_soc", 0.25)),
            secondary_policy=SecondaryPolicy(payload.get("secondary_policy", "PRIORITY_ONLY")),
            generator_mode=GeneratorMode(payload.get("generator_mode", "CYCLED")),
            battery_charge_target_soc=float(payload.get("battery_charge_target_soc", 0.95)),
        )


@dataclass
class Configuration:
    """A candidate infrastructure configuration (a Course of Action)."""

    configuration_id: str
    label: str = ""
    strategy: str = ""
    intent: str = ""
    policy: DispatchPolicy = field(default_factory=DispatchPolicy)
    active_asset_ids: tuple[str, ...] = ()
    start_hour: float = 0.0
    derived_from: str | None = None
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def with_policy(self, **changes: Any) -> "Configuration":
        return replace(self, policy=replace(self.policy, **changes))

    def describe(self) -> list[str]:
        return self.policy.describe()

    def to_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "label": self.label,
            "strategy": self.strategy,
            "intent": self.intent,
            "policy": self.policy.to_dict(),
            "active_asset_ids": list(self.active_asset_ids),
            "start_hour": self.start_hour,
            "derived_from": self.derived_from,
            "data_labels": list(self.data_labels),
            "description": self.describe(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Configuration":
        return cls(
            configuration_id=payload["configuration_id"],
            label=payload.get("label", ""),
            strategy=payload.get("strategy", ""),
            intent=payload.get("intent", ""),
            policy=DispatchPolicy.from_dict(payload.get("policy", {})),
            active_asset_ids=tuple(payload.get("active_asset_ids", ())),
            start_hour=float(payload.get("start_hour", 0.0)),
            derived_from=payload.get("derived_from"),
        )
