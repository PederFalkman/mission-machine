"""Solver backends behind one interface.

Pack 1 declared the planning problem as a MILP and used it only to *check*
schedules; the ranking was done by enumerating configurations and simulating
each one. This module is the seam a real solver plugs into, so that the
demonstrator can use one when it is installed and say so, and work without one
and say that too.

Three rules, taken from the same seam in capacity-machine (see
``docs/reuse-assessment.md``):

* **Absence is survivable.** No solver is a dependency. The deterministic
  baseline is itself a provider, always available, always the default.
* **The answer names its source.** Every :class:`SolverOutcome` carries the
  backend and version that produced it. A result that came from CBC and a result
  that came from the rule-based dispatch are different claims and must never be
  indistinguishable.
* **Ports are reported whether or not they are wired.** The registry lists every
  backend it knows about, including the ones nobody installed, so "we did not
  ask" is visible and never reads as "we asked and found nothing".
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from mission_machine.planning.milp import MilpModel

#: Problem classes a provider may declare support for.
DISPATCH_MILP = "dispatch-milp"
DISPATCH_LP = "dispatch-lp"


class SolverStatus(str, Enum):
    """How a solve ended. Every value is distinguishable from every other."""

    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"                  # a solution, not proven optimal
    INFEASIBLE = "INFEASIBLE"
    UNBOUNDED = "UNBOUNDED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"    # ran out of time before proving anything
    UNAVAILABLE = "UNAVAILABLE"            # no backend could take the problem
    ERROR = "ERROR"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    @property
    def is_answer(self) -> bool:
        return self in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)


@dataclass(frozen=True)
class ProviderDescriptor:
    """What a backend is, and whether it is actually here."""

    name: str
    kind: str
    available: bool
    version: str = ""
    problem_classes: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "available": self.available,
            "version": self.version,
            "problem_classes": list(self.problem_classes),
            "detail": self.detail,
        }


@dataclass
class SolverOutcome:
    """A solve, with its provenance attached."""

    status: SolverStatus
    backend: str
    objective: float | None = None
    assignment: dict[str, float] = field(default_factory=dict)
    wall_time_s: float = 0.0
    backend_version: str = ""
    gap: float | None = None
    detail: str = ""
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def is_answer(self) -> bool:
        return self.status.is_answer

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "backend": self.backend,
            "backend_version": self.backend_version,
            "objective": round(self.objective, 4) if self.objective is not None else None,
            "wall_time_s": round(self.wall_time_s, 3),
            "gap": round(self.gap, 6) if self.gap is not None else None,
            "detail": self.detail,
            "variables_assigned": len(self.assignment),
            "data_labels": list(self.data_labels),
        }


@runtime_checkable
class OptimisationProvider(Protocol):
    """A solver backend. Read-only: it answers questions, it commands nothing."""

    name: str

    def describe(self) -> ProviderDescriptor: ...

    def supports(self, problem_class: str) -> bool: ...

    def solve(self, model: MilpModel, *, time_budget_s: float) -> SolverOutcome: ...


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------


class PulpCbcProvider:
    """CBC through PuLP, when it is installed.

    PuLP is an optional extra (``pip install 'mission-machine[milp]'``) and is
    imported inside the methods, so that importing this module never requires
    it. ``tests/test_standalone.py`` enforces that.
    """

    name = "pulp-cbc"
    kind = "milp"

    def _pulp(self):
        import pulp  # noqa: PLC0415 - optional backend, imported on use

        return pulp

    def describe(self) -> ProviderDescriptor:
        try:
            pulp = self._pulp()
        except ImportError:
            return ProviderDescriptor(
                name=self.name,
                kind=self.kind,
                available=False,
                problem_classes=(DISPATCH_MILP, DISPATCH_LP),
                detail=(
                    "Not installed. pip install 'mission-machine[milp]' to enable a real "
                    "branch-and-cut solve."
                ),
            )
        return ProviderDescriptor(
            name=self.name,
            kind=self.kind,
            available=True,
            version=getattr(pulp, "__version__", "unknown"),
            problem_classes=(DISPATCH_MILP, DISPATCH_LP),
            detail="CBC via PuLP.",
        )

    def supports(self, problem_class: str) -> bool:
        return problem_class in (DISPATCH_MILP, DISPATCH_LP) and self.describe().available

    def solve(self, model: MilpModel, *, time_budget_s: float = 120.0) -> SolverOutcome:
        started = time.monotonic()
        try:
            pulp = self._pulp()
        except ImportError as exc:
            return SolverOutcome(
                status=SolverStatus.UNAVAILABLE,
                backend=self.name,
                detail=str(exc),
                wall_time_s=time.monotonic() - started,
            )

        problem = pulp.LpProblem(model.name.replace(" ", "_"), pulp.LpMinimize)
        variables = {
            name: pulp.LpVariable(
                name,
                lowBound=variable.lower,
                upBound=None if variable.upper == float("inf") else variable.upper,
                cat="Binary" if variable.kind == "binary" else "Continuous",
            )
            for name, variable in model.variables.items()
        }
        objective = model.objective
        problem += (
            pulp.lpSum(
                coefficient * variables[name] for name, coefficient in objective.terms.items()
            )
            if objective is not None
            else 0
        )
        for constraint in model.constraints:
            expression = pulp.lpSum(
                coefficient * variables[name] for name, coefficient in constraint.terms.items()
            )
            if constraint.sense == "<=":
                problem += expression <= constraint.rhs, constraint.name
            elif constraint.sense == ">=":
                problem += expression >= constraint.rhs, constraint.name
            else:
                problem += expression == constraint.rhs, constraint.name

        try:
            solver = pulp.PULP_CBC_CMD(msg=False, timeLimit=max(1, int(time_budget_s)))
            code = problem.solve(solver)
        except Exception as exc:  # pragma: no cover - solver plumbing
            return SolverOutcome(
                status=SolverStatus.ERROR,
                backend=self.name,
                detail=f"{type(exc).__name__}: {exc}",
                wall_time_s=time.monotonic() - started,
            )

        elapsed = time.monotonic() - started
        label = pulp.LpStatus.get(code, "Unknown")
        status = {
            "Optimal": SolverStatus.OPTIMAL,
            "Infeasible": SolverStatus.INFEASIBLE,
            "Unbounded": SolverStatus.UNBOUNDED,
            "Not Solved": SolverStatus.BUDGET_EXCEEDED,
            "Undefined": SolverStatus.BUDGET_EXCEEDED,
        }.get(label, SolverStatus.ERROR)

        assignment = {
            name: (variable.value() or 0.0) for name, variable in variables.items()
        }
        # CBC reports "Optimal" when it stops at a time limit with an incumbent,
        # so a solve that used its whole budget is reported as FEASIBLE rather
        # than claimed as proven optimal.
        if status is SolverStatus.OPTIMAL and elapsed >= time_budget_s:
            status = SolverStatus.FEASIBLE
        return SolverOutcome(
            status=status,
            backend=self.name,
            backend_version=getattr(pulp, "__version__", "unknown"),
            objective=pulp.value(problem.objective) if status.is_answer else None,
            assignment=assignment if status.is_answer else {},
            wall_time_s=elapsed,
            detail=f"CBC reported {label} after {elapsed:.1f}s.",
        )


class OrToolsCpSatProvider:
    """Declared, never wired in Pack 1.

    CP-SAT needs an integer-coefficient reformulation of a model whose
    coefficients are litres per kilowatt-hour, and doing that scaling badly
    would produce answers that are wrong in a way nobody would notice. The port
    is declared so the registry can report it as unasked rather than absent.
    """

    name = "ortools-cpsat"
    kind = "cp"

    def describe(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            name=self.name,
            kind=self.kind,
            available=False,
            problem_classes=(DISPATCH_MILP,),
            detail=(
                "Declared but not implemented. CP-SAT requires an integer reformulation of the "
                "fuel coefficients; see docs/research/questions.md RQ-009."
            ),
        )

    def supports(self, problem_class: str) -> bool:
        return False

    def solve(self, model: MilpModel, *, time_budget_s: float = 120.0) -> SolverOutcome:
        return SolverOutcome(
            status=SolverStatus.UNAVAILABLE,
            backend=self.name,
            detail=self.describe().detail,
        )


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


class ProviderRegistry:
    """Every backend the demonstrator knows about, wired or not."""

    def __init__(self, providers: list[OptimisationProvider] | None = None) -> None:
        self._providers: list[OptimisationProvider] = (
            list(providers) if providers is not None else [PulpCbcProvider(), OrToolsCpSatProvider()]
        )

    def register(self, provider: OptimisationProvider) -> None:
        self._providers.append(provider)

    def status_report(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(provider.describe() for provider in self._providers)

    def available(self, problem_class: str = DISPATCH_MILP) -> list[OptimisationProvider]:
        return [provider for provider in self._providers if provider.supports(problem_class)]

    def solve(
        self,
        model: MilpModel,
        *,
        problem_class: str = DISPATCH_MILP,
        time_budget_s: float = 120.0,
    ) -> SolverOutcome:
        """Solve with the first backend that can take it, or say plainly that none can."""

        for provider in self.available(problem_class):
            return provider.solve(model, time_budget_s=time_budget_s)
        declared = ", ".join(descriptor.name for descriptor in self.status_report())
        return SolverOutcome(
            status=SolverStatus.UNAVAILABLE,
            backend="none",
            detail=(
                f"No backend is installed for {problem_class}. Declared backends: {declared}. "
                "Export the model with MilpModel.to_lp_string() and solve it elsewhere, or "
                "install one: pip install 'mission-machine[milp]'."
            ),
        )


#: The default registry. Import and pass a different one to test in isolation.
DEFAULT_REGISTRY = ProviderRegistry()
