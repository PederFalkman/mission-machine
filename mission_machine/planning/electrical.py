"""A port for asking whether a configuration's power flows are realisable -
and an honest declaration that nothing here can answer that yet.

Checking a schedule's energy balance is ``planning/milp.py``'s job, and it is
done: every option the engine offers is verified against a declared
constraint set before it is shown. What that verifier cannot say is whether
the same schedule is *electrically* realisable - whether the currents implied
by a set of simultaneous injections keep every bus inside its voltage band on
the physical network those assets sit on. That is a different physics, with a
different owner.

The owner is ato-energy-platform's ``PowerSystemSolver`` (frozen at
``power_system_solver/1.0`` - see its
``docs/platform-status/SOLIDS_POWERSYSTEMSOLVER_V1_FREEZE_DECISION.md``), and
Mission Machine's cross-repo governance is explicit that this demonstrator
"may consume it through a versioned provider/service contract" and "must not
implement its own 'good enough' power-flow solver." So unlike
``resilience.dependencies.GraphPropagationProvider`` - a real, working,
in-process default for a question that *is* this demonstrator's own subject
matter - there is deliberately no in-process default here. Building one would
mean two things: a second, uncoordinated implementation of a physics this
repository does not own, and a confident number next to a real solver's
answer with no way for a reader to tell which is which.

The port is declared anyway, for the same reason ``OrToolsCpSatProvider`` and
``RodotPropagationProvider`` are: so that "we did not ask" stays visible and
never reads as "we asked and found nothing." Its one backend reports itself
unwired for two independent reasons, not one:

1. **No boundary exists to call.** Every current caller of
   ``PowerSystemSolver`` is in-process Python inside ato-energy-platform; no
   service in that platform exposes it over the network yet (confirmed by
   inspection during the freeze decision: no consumer, HTTP or otherwise,
   calls it in production). A cross-repo integration through a source import
   is exactly what this demonstrator's contract rules forbid.
2. **Mission Machine could not fill in the request even if the boundary
   existed.** A ``PowerSystemRequest`` needs a ``topology_ref`` and per-node
   ``injections_kw``/``injections_kvar`` against a concrete network. Every
   asset in this demonstrator's own inventory carries one ``location`` string
   (default ``"SUPPORT_NODE"``, see ``assets/base.py``) and nothing else -
   no bus, no line, no impedance, no per-node identity. There is exactly one
   node here today, in the sense the solver means the word.

Both are named because they are independent blockers: fixing the first alone
would still leave nothing to ask it, and fixing the second alone would still
leave nowhere to send the question. See ``docs/research/questions.md`` RQ-024.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Sequence


class ElectricalFeasibilityStatus(str, Enum):
    """How an assessment ended. Every value is distinguishable from every other."""

    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    NOT_ASSESSED = "NOT_ASSESSED"  # no backend could take the question
    ERROR = "ERROR"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @property
    def is_answer(self) -> bool:
        return self in (ElectricalFeasibilityStatus.FEASIBLE, ElectricalFeasibilityStatus.INFEASIBLE)


@dataclass(frozen=True)
class ElectricalFeasibilityDescriptor:
    """What a backend is, and whether it is actually here."""

    name: str
    kind: str
    available: bool
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "available": self.available,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ElectricalFeasibilityRequest:
    """What would have to be known to ask the question.

    This shape exists so a real backend has somewhere to be handed a topology
    and per-node injections once one is modelled (a Pack 2+ decision, not this
    one). It cannot be constructed from today's inventory - see the module
    docstring - which is why no provider below can be given a real request
    yet; ``mission_id`` and a description of what could not be built are all
    a Pack 1 caller has to offer.
    """

    mission_id: str
    topology_ref: str = ""
    injections_kw: Mapping[str, float] = field(default_factory=dict)
    injections_kvar: Mapping[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "topology_ref": self.topology_ref,
            "injections_kw": dict(self.injections_kw),
            "injections_kvar": dict(self.injections_kvar),
        }


@dataclass
class ElectricalFeasibilityResult:
    """An assessment, with its provenance attached."""

    status: ElectricalFeasibilityStatus
    backend: str
    detail: str = ""
    violations: tuple[str, ...] = ()
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def is_answer(self) -> bool:
        return self.status.is_answer

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "backend": self.backend,
            "detail": self.detail,
            "violations": list(self.violations),
            "data_labels": list(self.data_labels),
        }


class ElectricalFeasibilityProvider:
    """A backend for the question. Read-only: it answers, it commands nothing.

    Same shape as ``planning.providers.OptimisationProvider`` and
    ``resilience.dependencies.PropagationProvider``, and for the same reason:
    a declared port that reports itself whether or not anybody wired it.
    """

    name = "provider"

    def describe(self) -> ElectricalFeasibilityDescriptor:  # pragma: no cover - interface
        raise NotImplementedError

    def assess(
        self, request: ElectricalFeasibilityRequest
    ) -> ElectricalFeasibilityResult:  # pragma: no cover - interface
        raise NotImplementedError


class PowerSystemSolverProvider(ElectricalFeasibilityProvider):
    """Declared, unwired, and saying why - twice over. See the module docstring."""

    name = "power-system-solver"

    def describe(self) -> ElectricalFeasibilityDescriptor:
        return ElectricalFeasibilityDescriptor(
            name=self.name,
            kind="service",
            available=False,
            detail=(
                "Not wired. ato-energy-platform's PowerSystemSolver (frozen "
                "power_system_solver/1.0) answers this question but has no HTTP "
                "boundary yet - every current caller is in-process inside that "
                "repository, and a source import across repositories is not a "
                "contract this demonstrator is permitted to take. Separately, "
                "Mission Machine's own asset model has no topology (every asset "
                "carries one `location` string, not a bus/line graph), so no "
                "request could be built even if the boundary existed. See "
                "docs/research/questions.md RQ-024."
            ),
        )

    def assess(self, request: ElectricalFeasibilityRequest) -> ElectricalFeasibilityResult:
        raise RuntimeError(
            "the power-system-solver electrical feasibility provider is declared but not "
            "wired; use ElectricalFeasibilityRegistry.assess(), which answers NOT_ASSESSED "
            "rather than raising"
        )


class ElectricalFeasibilityRegistry:
    """Every declared backend, wired or not - and an honest answer when none is."""

    def __init__(self, providers: Sequence[ElectricalFeasibilityProvider] | None = None) -> None:
        self._providers: list[ElectricalFeasibilityProvider] = (
            list(providers) if providers is not None else [PowerSystemSolverProvider()]
        )

    def register(self, provider: ElectricalFeasibilityProvider) -> None:
        self._providers.append(provider)

    def status_report(self) -> tuple[ElectricalFeasibilityDescriptor, ...]:
        return tuple(provider.describe() for provider in self._providers)

    def available(self) -> list[ElectricalFeasibilityProvider]:
        return [provider for provider in self._providers if provider.describe().available]

    def assess(self, request: ElectricalFeasibilityRequest) -> ElectricalFeasibilityResult:
        """Assess with the first backend that can take it, or say plainly that none can.

        Mirrors ``planning.providers.ProviderRegistry.solve``: absence is
        survivable, so this returns an honest ``NOT_ASSESSED`` result rather
        than raising or fabricating a pass.
        """

        for provider in self.available():
            return provider.assess(request)
        declared = ", ".join(descriptor.name for descriptor in self.status_report())
        return ElectricalFeasibilityResult(
            status=ElectricalFeasibilityStatus.NOT_ASSESSED,
            backend="none",
            detail=(
                f"No backend can assess electrical feasibility for {request.mission_id}. "
                f"Declared backends: {declared}. Mission Machine's planner does not check "
                "network physics for a configuration in Pack 1 - the MILP verifier checks "
                "energy balance, and the dependency graph reports topology-free consequences "
                "of an unmet dependency; neither is this question. See "
                "docs/research/questions.md RQ-024."
            ),
        )


#: The default registry. Import and pass a different one to test in isolation.
DEFAULT_ELECTRICAL_FEASIBILITY_REGISTRY = ElectricalFeasibilityRegistry()
