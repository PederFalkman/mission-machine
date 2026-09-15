"""Guardrail 2 - Mission Machine has no control path.

This is the test that keeps the demonstrator an assessment product. It plans,
simulates, compares and recommends; it never starts a generator, closes a
breaker or commands anything. That is a claim about the whole codebase, so it is
checked here rather than asserted in a document: if somebody later adds
``dispatch_generator`` to a module or a mutating route to the API, this fails
before the code ships.

Ported from capacity-machine's ``tests/test_no_write_methods.py``; see
``docs/reuse-assessment.md``.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import unittest

import mission_machine
from mission_machine.evidence.control import (
    CONTROL_PATH_ENABLED,
    SIMULATION_ONLY_EXEMPTIONS,
    reads_as_actuation,
)
from mission_machine.ui.server import MUTATING_ROUTES

#: The routes the API is allowed to expose. Every one of them plans, assesses or
#: records an operator decision.
EXPECTED_MUTATING_ROUTES = {
    "/api/configure",
    "/api/select",
    "/api/advance",
    "/api/degrade",
    "/api/reset",
}


#: ``__main__`` runs the CLI on import, so it is imported by running the tool,
#: not by this scan. Everything it contains is one line calling ``cli.main``.
UNIMPORTABLE = {"mission_machine.__main__"}


def all_modules() -> list:
    modules = [mission_machine]
    for info in pkgutil.walk_packages(mission_machine.__path__, "mission_machine."):
        if info.name in UNIMPORTABLE:
            continue
        modules.append(importlib.import_module(info.name))
    return modules


def public_callables(module) -> list[tuple[str, object]]:
    """Every callable reachable from outside the package, with its qualified name."""

    found: list[tuple[str, object]] = []
    for name, member in vars(module).items():
        if name.startswith("_"):
            continue
        if getattr(member, "__module__", None) != module.__name__:
            continue
        if inspect.isfunction(member):
            found.append((f"{module.__name__}.{name}", member))
        elif inspect.isclass(member):
            for attribute, value in vars(member).items():
                if attribute.startswith("_"):
                    continue
                if inspect.isfunction(value) or isinstance(value, (staticmethod, classmethod)):
                    found.append((f"{module.__name__}.{name}.{attribute}", value))
    return found


class ControlPathTests(unittest.TestCase):
    def test_the_control_path_is_disabled(self) -> None:
        self.assertFalse(
            CONTROL_PATH_ENABLED,
            "Pack 1 has no control path. Enabling one is a product decision with a "
            "different risk profile, not a merge.",
        )

    def test_no_public_callable_commands_an_asset(self) -> None:
        offenders: list[str] = []
        for module in all_modules():
            for qualified_name, _member in public_callables(module):
                if qualified_name in SIMULATION_ONLY_EXEMPTIONS:
                    continue
                marker = reads_as_actuation(qualified_name.rsplit(".", 1)[-1])
                if marker is not None:
                    offenders.append(f"{qualified_name} (matches {marker!r})")
        self.assertEqual(
            offenders,
            [],
            "Mission Machine must not expose a way to command an asset: " + str(offenders),
        )

    def test_the_simulation_exemptions_still_exist_and_stay_private(self) -> None:
        """An exemption that has gone stale would silently weaken the rule."""

        for qualified_name in SIMULATION_ONLY_EXEMPTIONS:
            parts = qualified_name.split(".")
            method = parts[-1]
            self.assertTrue(
                method.startswith("_"),
                f"{qualified_name} is exempt but public; an exemption may only cover "
                "something unreachable from outside the package",
            )
            module = importlib.import_module(".".join(parts[:-2]))
            owner = getattr(module, parts[-2])
            self.assertTrue(
                hasattr(owner, method), f"stale exemption: {qualified_name} no longer exists"
            )

    def test_the_api_exposes_only_planning_and_assessment_routes(self) -> None:
        self.assertEqual(set(MUTATING_ROUTES), EXPECTED_MUTATING_ROUTES)

    def test_no_asset_can_be_commanded_through_the_asset_model(self) -> None:
        """Assets describe capability; they do not carry actuation."""

        from mission_machine.assets.base import Asset
        from mission_machine.assets.energy import Battery, Generator, GridConnection, SolarPV

        for cls in (Asset, Generator, Battery, GridConnection, SolarPV):
            for attribute in dir(cls):
                if attribute.startswith("_"):
                    continue
                self.assertIsNone(
                    reads_as_actuation(attribute),
                    f"{cls.__name__}.{attribute} reads as an actuation",
                )

    def test_failure_events_describe_state_and_never_cause_it(self) -> None:
        """A FailureEvent is an observation the planner reacts to, not an instruction."""

        from mission_machine.resilience.failures import FailureEvent

        event = FailureEvent(event_id="E", asset_id="GEN-A", hour=1.0)
        offenders = [name for name in dir(event) if reads_as_actuation(name)]
        self.assertEqual(offenders, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
