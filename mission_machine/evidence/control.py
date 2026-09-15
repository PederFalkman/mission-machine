"""The control-path rule, stated where a test can check it.

Mission Machine is an **assessment** product. It plans, simulates, compares and
recommends. It does not start a generator, close a breaker, dispatch a battery
or command anything at all, and it holds no credential that would let it.

That is a claim about the whole codebase, so it is enforced by
``tests/test_no_control_path.py`` rather than by this docstring: no public
callable anywhere in the package may carry a name that reads as an actuation,
and :data:`CONTROL_PATH_ENABLED` must stay False. If somebody later adds
``dispatch_generator`` to a module, the build fails before the code ships.

Adapted from the same rule in capacity-machine (``providers/base.py``,
``tests/test_no_write_methods.py``). See ``docs/reuse-assessment.md``.
"""

from __future__ import annotations

#: Pack 1 has no control path, no activation endpoint and no write credential.
#: Flipping this to True is a product decision with a different risk profile,
#: and it would have to be argued for, not merged.
CONTROL_PATH_ENABLED = False

#: Name fragments that read as commanding a physical asset. A public callable
#: containing one of these is treated as a control path until proven otherwise.
WRITE_METHOD_MARKERS: tuple[str, ...] = (
    "activate",
    "actuate",
    "close_breaker",
    "command",
    "control",
    "curtail_asset",
    "dispatch",
    "energise",
    "energize",
    "open_breaker",
    "set_output",
    "set_setpoint",
    "shed_load",
    "shutdown",
    "start_generator",
    "stop_generator",
    "switch",
    "trip_breaker",
)

#: Simulation and planning legitimately talk about dispatch - the simulator
#: models it. These names are internal to the model and reachable only from
#: inside the package, which is why they are exempt. The exemption is a list, so
#: that adding to it is a visible act.
SIMULATION_ONLY_EXEMPTIONS: tuple[str, ...] = (
    "mission_machine.simulation.simulator.Simulator._dispatch_generators",
)


def reads_as_actuation(name: str) -> str | None:
    """Return the marker a name matches, or None.

    Matching is on whole words, not substrings: ``round_trip_efficiency`` is a
    battery property, not a breaker trip, and a rule that cannot tell the
    difference gets switched off the first time it cries wolf. Markers that
    contain an underscore are matched as a phrase anywhere in the name.
    """

    lowered = name.lower()
    tokens = set(lowered.replace("-", "_").split("_"))
    for marker in WRITE_METHOD_MARKERS:
        if "_" in marker:
            if marker in lowered:
                return marker
        elif marker in tokens:
            return marker
    return None
