"""Failure events and disruption scenarios.

Pack 1 models disruption as *asset unavailability only*. No adversary
behaviour, no targeting, no offensive action is represented. A failure event
says nothing about why an asset stopped working.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mission_machine.assets.base import FailureState


@dataclass
class FailureEvent:
    """One asset changing state at one point in time."""

    event_id: str
    asset_id: str
    hour: float
    state: FailureState = FailureState.UNAVAILABLE
    cause: str = "UNSPECIFIED"
    description: str = ""
    restored_hour: float | None = None
    data_labels: tuple[str, ...] = ("SYNTHETIC", "ASSUMED", "UNVALIDATED")

    def active_at(self, hour: float) -> bool:
        if hour < self.hour:
            return False
        if self.restored_hour is not None and hour >= self.restored_hour:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "asset_id": self.asset_id,
            "hour": self.hour,
            "state": str(self.state),
            "cause": self.cause,
            "description": self.description,
            "restored_hour": self.restored_hour,
            "data_labels": list(self.data_labels),
        }


@dataclass
class Scenario:
    """A named set of failure events, used for planning and for the demo."""

    scenario_id: str
    name: str = ""
    description: str = ""
    events: list[FailureEvent] = field(default_factory=list)
    data_labels: tuple[str, ...] = ("SYNTHETIC", "ASSUMED", "UNVALIDATED")

    def states_at(self, hour: float) -> dict[str, FailureState]:
        """Asset states implied by this scenario at ``hour``."""

        states: dict[str, FailureState] = {}
        for event in sorted(self.events, key=lambda e: e.hour):
            if event.active_at(hour):
                states[event.asset_id] = event.state
            elif event.restored_hour is not None and hour >= event.restored_hour:
                states[event.asset_id] = FailureState.NOMINAL
        return states

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "name": self.name,
            "description": self.description,
            "events": [e.to_dict() for e in self.events],
            "data_labels": list(self.data_labels),
        }


#: The Pack 1 mandated disruption: Generator B becomes unavailable mid-mission.
GENERATOR_B_UNAVAILABLE = Scenario(
    scenario_id="SC-DEGRADED-001",
    name="Generator B unavailable",
    description=(
        "At H+30 Generator B stops and cannot be restarted within the mission. "
        "Cause is deliberately unspecified: Pack 1 models the consequence, not the cause."
    ),
    events=[
        FailureEvent(
            event_id="EV-001",
            asset_id="GEN-B",
            hour=30.0,
            state=FailureState.UNAVAILABLE,
            cause="UNSPECIFIED_LOSS_OF_ASSET",
            description="Generator B unavailable for the remainder of the mission.",
        )
    ],
)


#: A compound disruption: the generator loss above, plus the host-nation supply
#: never coming back. Used to show a case where critical functions cannot all be
#: held and something has to be given up.
GENERATOR_B_AND_GRID_LOSS = Scenario(
    scenario_id="SC-DEGRADED-002",
    name="Generator B unavailable and grid not restored",
    description=(
        "At H+30 Generator B stops, and the host-nation supply that the plan expected "
        "from H+30 does not return for the rest of the mission."
    ),
    events=[
        FailureEvent(
            event_id="EV-001",
            asset_id="GEN-B",
            hour=30.0,
            state=FailureState.UNAVAILABLE,
            cause="UNSPECIFIED_LOSS_OF_ASSET",
            description="Generator B unavailable for the remainder of the mission.",
        ),
        FailureEvent(
            event_id="EV-002",
            asset_id="GRID-01",
            hour=30.0,
            state=FailureState.UNAVAILABLE,
            cause="UNSPECIFIED_LOSS_OF_SUPPLY",
            description="Host-nation supply does not return as planned at H+30.",
        ),
    ],
)
