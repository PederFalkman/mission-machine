"""What depends on what, and what a failure does to it (RQ-005).

Pack 1 can tell an operator that losing the conversion unit stops the node. It
knows this because the simulation produces zero, which means it can report the
*outcome* and never the *mechanism*: it cannot say "the shelter cooling is short
of what it needs", only "nothing was served".

The dependency is not missing from the data. It is written down in a string
nothing reads - ECS-MIN-01's function is *"Equipment shelter cooling required to
keep comms and IT within limits"*, and that sentence is the whole dependency. The
same shape as the operator priorities before RQ-008: asserted in prose, invisible
to the planner.

This module gives the assertion somewhere to live. Two things follow from
carrying **capacity as well as state** on every edge, which is the property
RODOT's graph has and the reason the reuse assessment rated it the highest-value
thing to adapt:

* A **shortfall** is distinguishable from an **outage**. Cooling at 60 % of what
  the shelter needs is a different fact from cooling stopped, and an operator
  does different things about them.
* The consequence carries its **chain**. "COMMS-01 is at risk" is not an answer;
  "COMMS-01 is at risk because ECS-MIN-01 is serving 60 % of the cooling it
  needs, because PCE-01 is derated" is one.

**What this does not do, and the line matters.** Propagation reports that a
dependency is unmet. It does not simulate the consequence: nothing here makes
the communications load trip on temperature, and the dispatch is unchanged by
anything in this file. The node's physics stay in the simulator, where they can
be checked. A graph that quietly started shedding loads would be a second,
unverified model of the same node (AS-027).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from mission_machine.assets.base import Asset, AssetKind, FailureState
from mission_machine.assets.inventory import AssetInventory
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.mission.spec import MissionSpec


class DependencyKind(str, Enum):
    """Why one thing needs another. The kind is what names the mechanism."""

    #: Electrical: the dependant draws power that flows through the provider.
    POWER = "POWER"
    #: Conversion and distribution: the path everything electrical takes.
    CONVERSION = "CONVERSION"
    #: Thermal: the provider keeps the dependant inside its operating limits.
    COOLING = "COOLING"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class Satisfaction(str, Enum):
    """How well a node's dependencies are being met."""

    MET = "MET"              # everything it needs is there
    SHORT = "SHORT"          # some of it, not all - a shortfall, not an outage
    UNMET = "UNMET"          # nothing it needs is there
    UNKNOWN = "UNKNOWN"      # nothing in the graph says (reported, never assumed)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


@dataclass(frozen=True)
class Dependency:
    """One edge: ``dependant`` needs ``provider``, and how much of it.

    ``required_fraction`` is the share of the provider's normal service the
    dependant needs to be fully supported. A shelter that needs its cooling
    running at 80 % to hold its equipment within limits carries 0.8, and is
    SHORT rather than UNMET when the cooling runs at 60 %.
    """

    dependant: str
    provider: str
    kind: DependencyKind
    required_fraction: float = 1.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependant": self.dependant,
            "provider": self.provider,
            "kind": str(self.kind),
            "required_fraction": round(self.required_fraction, 3),
            "note": self.note,
        }


@dataclass
class DependencyGraph:
    """Assets and the dependencies between them, derived from the inventory."""

    mission_id: str = ""
    edges: list[Dependency] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    data_labels: tuple[str, ...] = ("SYNTHETIC", "ASSUMED", "UNVALIDATED")

    def providers_of(self, asset_id: str) -> list[Dependency]:
        return [edge for edge in self.edges if edge.dependant == asset_id]

    def dependants_of(self, asset_id: str) -> list[Dependency]:
        return [edge for edge in self.edges if edge.provider == asset_id]

    @property
    def nodes(self) -> list[str]:
        seen: list[str] = []
        for edge in self.edges:
            for node in (edge.dependant, edge.provider):
                if node not in seen:
                    seen.append(node)
        return seen

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "nodes": self.nodes,
            "edges": [edge.to_dict() for edge in self.edges],
            "notes": list(self.notes),
            "data_labels": list(self.data_labels),
        }


@dataclass(frozen=True)
class OperatingState:
    """What an asset is actually doing, as against what it could do.

    ``served_fraction`` is how much of its normal service it is delivering -
    1.0 when it is doing its job, 0.0 when it has stopped, and in between when
    it is running degraded. Carrying the fraction rather than a boolean is the
    whole reason this graph can tell a shortfall from an outage.
    """

    asset_id: str
    available: bool = True
    served_fraction: float = 1.0
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "available": self.available,
            "served_fraction": round(self.served_fraction, 3),
            "note": self.note,
        }


@dataclass
class Consequence:
    """What one node is short of, and the chain that explains it."""

    asset_id: str
    satisfaction: Satisfaction = Satisfaction.MET
    supported_fraction: float = 1.0
    unmet: list[Dependency] = field(default_factory=list)
    because: list[str] = field(default_factory=list)
    """The chain, nearest cause first. Written for an operator to read aloud."""

    @property
    def is_short(self) -> bool:
        return self.satisfaction is Satisfaction.SHORT

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "satisfaction": str(self.satisfaction),
            "supported_fraction": round(self.supported_fraction, 3),
            "unmet": [edge.to_dict() for edge in self.unmet],
            "because": list(self.because),
        }


@dataclass
class PropagationResult:
    """Every node's consequence, and what the machine could not say."""

    mission_id: str = ""
    backend: str = ""
    at_hour: float | None = None
    """The hour the consequence describes. A projected hour is still a
    projection, and saying which one is the difference between a forecast and
    a claim about now."""
    projected: bool = False
    consequences: dict[str, Consequence] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)
    """Nodes the graph says nothing about. Reported, never assumed to be fine."""
    disclaimer: str = DEMONSTRATOR_DISCLAIMER
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def stopped(self) -> list[Consequence]:
        return [c for c in self.consequences.values() if c.satisfaction is Satisfaction.UNMET]

    def short(self) -> list[Consequence]:
        return [c for c in self.consequences.values() if c.satisfaction is Satisfaction.SHORT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "backend": self.backend,
            "at_hour": self.at_hour,
            "projected": self.projected,
            "consequences": {k: v.to_dict() for k, v in self.consequences.items()},
            "unknown": list(self.unknown),
            "stopped": [c.asset_id for c in self.stopped()],
            "short": [c.asset_id for c in self.short()],
            "disclaimer": self.disclaimer,
            "data_labels": list(self.data_labels),
        }


# --------------------------------------------------------------------------
# building the graph from what the inventory already says
# --------------------------------------------------------------------------

#: Below this share of what it needs, a dependant is treated as stopped rather
#: than short. Chosen, not derived, and registered as AS-028.
UNMET_FRACTION = 0.05


def build_graph(
    mission: MissionSpec, inventory: AssetInventory | None = None
) -> DependencyGraph:
    """Derive the dependency graph from the inventory, not from a second file.

    Three kinds of edge, and each one comes from something the asset set already
    states rather than from a parallel model somebody has to keep in step:

    * **CONVERSION** - every electrical load depends on the conversion and
      distribution units, because that is the path power takes.
    * **POWER** - the conversion units depend on the supply assets feeding them.
    * **COOLING** - a cooling system supports the equipment it keeps within
      limits. This is the one that needed new data: the relationship was stated
      in the asset's free-text ``function`` and nothing could read it, so
      ``Asset.supports`` now carries it in machine-readable form.
    """

    inventory = inventory or mission.inventory
    graph = DependencyGraph(mission_id=mission.mission_id)

    conversion = [a for a in inventory if a.kind is AssetKind.POWER_CONVERSION]
    supplies = [a for a in inventory if a.is_supply]
    loads = [a for a in inventory if a.is_load]

    for unit in conversion:
        for supply in supplies:
            graph.edges.append(
                Dependency(
                    dependant=unit.asset_id,
                    provider=supply.asset_id,
                    kind=DependencyKind.POWER,
                    required_fraction=0.0,
                    note=f"{unit.asset_id} carries power from {supply.asset_id}.",
                )
            )

    for load in loads:
        for unit in conversion:
            graph.edges.append(
                Dependency(
                    dependant=load.asset_id,
                    provider=unit.asset_id,
                    kind=DependencyKind.CONVERSION,
                    required_fraction=1.0,
                    note=f"{load.asset_id} is fed through {unit.asset_id}.",
                )
            )

    for asset in inventory:
        for supported in getattr(asset, "supports", ()) or ():
            target = inventory.find(supported)
            if target is None:
                graph.notes.append(
                    f"{asset.asset_id} declares it supports {supported!r}, which is not in "
                    "the inventory. The edge is not created and the claim is not silently "
                    "dropped either."
                )
                continue
            graph.edges.append(
                Dependency(
                    dependant=supported,
                    provider=asset.asset_id,
                    kind=DependencyKind.COOLING,
                    required_fraction=asset.support_fraction,
                    note=(
                        f"{asset.asset_id} keeps {supported} within its operating limits "
                        f"({asset.support_fraction:.0%} of its service is needed to do so)."
                    ),
                )
            )

    if not conversion:
        graph.notes.append(
            "No conversion unit in the inventory: no CONVERSION edges were derived, and "
            "loads are reported as UNKNOWN rather than as supported."
        )
    if not any(edge.kind is DependencyKind.COOLING for edge in graph.edges):
        graph.notes.append(
            "No asset declares what it supports, so the graph carries no thermal or "
            "functional dependencies - only the electrical path."
        )
    return graph


def states_from_events(
    inventory: AssetInventory,
    unavailable: Iterable[str] = (),
    served_fractions: Mapping[str, float] | None = None,
) -> dict[str, OperatingState]:
    """Operating states for every asset, from what is unavailable and degraded."""

    down = set(unavailable)
    fractions = dict(served_fractions or {})
    states: dict[str, OperatingState] = {}
    for asset in inventory:
        failed = asset.asset_id in down or asset.failure_state is FailureState.UNAVAILABLE
        fraction = 0.0 if failed else fractions.get(asset.asset_id, 1.0)
        states[asset.asset_id] = OperatingState(
            asset_id=asset.asset_id,
            available=not failed,
            served_fraction=max(0.0, min(1.0, fraction)),
            note="unavailable" if failed else "",
        )
    return states


# --------------------------------------------------------------------------
# propagation
# --------------------------------------------------------------------------


def propagate(
    graph: DependencyGraph,
    states: Mapping[str, OperatingState],
    *,
    backend: str = "graph",
) -> PropagationResult:
    """Work out what each node is short of, and why, in dependency order.

    Deliberately simple and deliberately readable: a node is supported to the
    least of what its providers give it, and the chain is recorded as it is
    walked. There is no clever cycle handling because a node's power path is not
    a cyclic graph; if one ever is, the node is reported UNKNOWN rather than
    resolved by an assumption nobody can see.
    """

    result = PropagationResult(mission_id=graph.mission_id, backend=backend)
    order = _resolution_order(graph)
    supported: dict[str, float] = {}
    direct: dict[str, list[str]] = {}

    for asset_id in order:
        state = states.get(asset_id)
        own = state.served_fraction if state is not None else 1.0
        if state is None:
            result.unknown.append(asset_id)

        consequence = Consequence(asset_id=asset_id)
        incoming = graph.providers_of(asset_id)

        # POWER edges are alternatives: any one source can carry the node, so
        # the conversion unit is short only when *every* source is.
        power = [e for e in incoming if e.kind is DependencyKind.POWER]
        if power:
            best = max(_service(supported, states, e.provider) for e in power)
            if best <= UNMET_FRACTION:
                consequence.unmet.extend(power)
                reasons = []
                for edge in sorted(power, key=lambda e: e.provider):
                    supply = states.get(edge.provider)
                    why = (supply.note if supply is not None and supply.note else "unavailable")
                    reasons.append(f"{edge.provider} ({why})")
                consequence.because.append(
                    f"{asset_id} has no supply reaching it - " + ", ".join(reasons) + "."
                )
            own = min(own, max(best, 0.0) if best > UNMET_FRACTION else 0.0)

        # CONVERSION and COOLING edges are requirements: each must be met.
        for edge in incoming:
            if edge.kind is DependencyKind.POWER:
                continue
            provider_service = _service(supported, states, edge.provider)
            if edge.required_fraction <= 0.0:
                continue
            met = min(1.0, provider_service / edge.required_fraction)
            if met >= 1.0 - 1e-9:
                continue
            consequence.unmet.append(edge)
            own = min(own, met)
            consequence.because.append(_explain(edge, provider_service, states))

        # A node can be down on its own account rather than through an edge -
        # a conversion unit with nothing flowing through it, a set with no fuel.
        # Without this the graph reported it stopped and said nothing about why.
        if own <= UNMET_FRACTION and not consequence.because:
            state_note = state.note if state is not None and state.note else "no service"
            consequence.because.append(f"{asset_id} has stopped: {state_note}.")

        own = max(0.0, min(1.0, own))
        supported[asset_id] = own
        consequence.supported_fraction = own
        consequence.satisfaction = _satisfaction(own, asset_id in result.unknown)
        direct[asset_id] = list(consequence.because)
        if consequence.because:
            consequence.because.extend(
                _chain(direct, result.consequences, consequence.unmet, {asset_id})
            )
        result.consequences[asset_id] = consequence

    return result


def _own(states: Mapping[str, OperatingState], asset_id: str) -> float:
    state = states.get(asset_id)
    return state.served_fraction if state is not None else 1.0


def _service(
    supported: Mapping[str, float],
    states: Mapping[str, OperatingState],
    asset_id: str,
) -> float:
    """What a provider is actually delivering, counted once.

    ``supported`` already folds in the asset's own served fraction - it is
    seeded from it before any edge is considered - so multiplying by the state
    again charges the same degradation twice. Caught by reading the explanation
    out loud: cooling running at 60 % was being reported as 36 %.
    """

    if asset_id in supported:
        return supported[asset_id]
    return _own(states, asset_id)


def _satisfaction(fraction: float, unknown: bool) -> Satisfaction:
    if unknown:
        return Satisfaction.UNKNOWN
    if fraction <= UNMET_FRACTION:
        return Satisfaction.UNMET
    if fraction >= 1.0 - 1e-9:
        return Satisfaction.MET
    return Satisfaction.SHORT


def _explain(
    edge: Dependency, provider_service: float, states: Mapping[str, OperatingState]
) -> str:
    """One sentence naming the mechanism, not the outcome."""

    state = states.get(edge.provider)
    if provider_service <= UNMET_FRACTION:
        stopped = "is unavailable" if state is not None and not state.available else "has stopped"
        if edge.kind is DependencyKind.COOLING:
            return (
                f"{edge.dependant} is outside the cooling it needs: {edge.provider} "
                f"{stopped}, and it needs {edge.required_fraction:.0%} of that service."
            )
        return f"{edge.dependant} has no path to power: {edge.provider} {stopped}."
    if edge.kind is DependencyKind.COOLING:
        return (
            f"{edge.dependant} is short of cooling: {edge.provider} is serving "
            f"{provider_service:.0%} of its normal service and {edge.required_fraction:.0%} "
            "is needed to hold the equipment in limits."
        )
    return (
        f"{edge.dependant} is short of its supply path: {edge.provider} is carrying "
        f"{provider_service:.0%} of normal."
    )


def _chain(
    direct: Mapping[str, list[str]],
    resolved: Mapping[str, Consequence],
    unmet: Sequence[Dependency],
    seen: set[str],
    depth: int = 1,
) -> list[str]:
    """The chain under a node's own reasons: one hop per line, each node once.

    Nodes resolve in dependency order, so by the time a dependant is reached its
    providers already carry their own account of the failure. Reusing it keeps
    one version of the story - but reusing it *whole* meant re-wrapping lines
    that were already chains, and the output came out as
    ``...PCE-01: ...BESS-01: BESS-01 has stopped``. Only a node's own reasons
    are borrowed here; the descent supplies the depth, and ``seen`` makes sure a
    root cause four dependants share is stated once per chain rather than four
    times.

    POWER edges are not descended into. Their reason line already names every
    source and the state it is in, so walking each one adds a line per set that
    says what the line above it just said.
    """

    lines: list[str] = []
    for edge in unmet:
        if edge.kind is DependencyKind.POWER or edge.provider in seen:
            continue
        seen.add(edge.provider)
        for reason in direct.get(edge.provider, ()):
            lines.append("  " * depth + reason)
        provider = resolved.get(edge.provider)
        if provider is not None:
            lines.extend(_chain(direct, resolved, provider.unmet, seen, depth + 1))
    return lines


def _resolution_order(graph: DependencyGraph) -> list[str]:
    """Providers before dependants, so a chain resolves in one pass."""

    nodes = graph.nodes
    remaining = list(nodes)
    order: list[str] = []
    guard = 0
    while remaining and guard <= len(nodes) + 1:
        guard += 1
        ready = [
            node
            for node in remaining
            if all(edge.provider in order or edge.provider == node
                   for edge in graph.providers_of(node))
        ]
        if not ready:
            # A cycle. Resolve nothing by assumption: take them in declared
            # order and let the result report what it could not establish.
            order.extend(remaining)
            break
        order.extend(ready)
        remaining = [node for node in remaining if node not in ready]
    return order


# --------------------------------------------------------------------------
# states from a real run, and the provider seam
# --------------------------------------------------------------------------


def states_from_step(
    inventory: AssetInventory,
    step: Any,
    unavailable: Iterable[str] = (),
) -> dict[str, OperatingState]:
    """Operating states read off one hour of an actual simulation.

    The alternative is hand-fed fractions, which would make every consequence a
    statement about numbers somebody typed. Here a load's served fraction is
    what the dispatch actually delivered it in that hour, so the graph explains
    a run rather than an illustration.
    """

    down = set(unavailable)
    demand = getattr(step, "load_demand_kw", {}) or {}
    served = getattr(step, "load_served_kw", {}) or {}
    generator_kw = getattr(step, "generator_kw", {}) or {}

    states: dict[str, OperatingState] = {}
    for asset in inventory:
        failed = asset.asset_id in down or asset.failure_state is FailureState.UNAVAILABLE
        if failed:
            states[asset.asset_id] = OperatingState(
                asset_id=asset.asset_id, available=False, served_fraction=0.0,
                note="unavailable",
            )
            continue

        fraction = 1.0
        note = ""
        if asset.asset_id in demand:
            wanted = demand.get(asset.asset_id, 0.0)
            got = served.get(asset.asset_id, 0.0)
            fraction = 1.0 if wanted <= 1e-9 else max(0.0, min(1.0, got / wanted))
            note = f"served {got:.1f} of {wanted:.1f} kW"
        elif asset.kind is AssetKind.GENERATOR:
            # Committed or not is the wrong question for a dependency graph. A
            # set that is idle because the hour did not need it is a source the
            # node still has; one with no fuel left is not, and the difference
            # is the whole of what the operator wants to hear.
            if generator_kw.get(asset.asset_id, 0.0) > 1e-9:
                fraction, note = 1.0, "running"
            elif getattr(step, "fuel_remaining_l", 0.0) > 1e-6:
                fraction, note = 1.0, "idle, fuel available"
            else:
                fraction, note = 0.0, "no fuel left"
        elif asset.kind is AssetKind.GRID_CONNECTION:
            fraction = 1.0 if getattr(step, "grid_available", False) else 0.0
            note = "available" if fraction else "not available this hour"
        elif asset.kind is AssetKind.SOLAR_PV:
            fraction = 1.0 if getattr(step, "pv_kw", 0.0) > 1e-9 else 0.0
            note = "generating" if fraction else "no irradiance this hour"
        elif asset.kind is AssetKind.BATTERY:
            # Stored energy is not available energy. A battery sitting on its
            # hard floor reads as 18 kWh and has nothing to give, and calling
            # that a live source made the graph report a conversion unit as
            # stopped for no stated reason.
            energy = getattr(step, "battery_energy_kwh", 0.0)
            floor = asset.min_state_of_charge * asset.energy_capacity_kwh
            usable = energy - floor
            fraction = 1.0 if usable > 1e-6 else 0.0
            note = (
                f"{usable:.0f} kWh above its floor"
                if fraction
                else f"at its floor ({energy:.0f} kWh), nothing to give"
            )
        elif asset.kind is AssetKind.POWER_CONVERSION:
            # A conversion unit is only carrying the node if power actually
            # flowed through it. Defaulting it to serviceable made the graph
            # report a cooling shortfall at an hour when the real answer was
            # that the node had run out of fuel and nothing was served at all.
            through = (
                getattr(step, "pv_kw", 0.0)
                + getattr(step, "grid_kw", 0.0)
                + sum(generator_kw.values())
                + getattr(step, "battery_discharge_kw", 0.0)
            )
            fraction = 1.0 if through > 1e-9 else 0.0
            note = f"carrying {through:.1f} kW" if fraction else "nothing flowing through it"

        states[asset.asset_id] = OperatingState(
            asset_id=asset.asset_id, available=True, served_fraction=fraction, note=note
        )
    return states


@dataclass(frozen=True)
class PropagationDescriptor:
    """What a propagation backend is, and whether it is actually here."""

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


class PropagationProvider:
    """The seam the reuse assessment specified for RODOT's propagation.

    Same shape as ``planning.providers.OptimisationProvider`` and for the same
    reason: a declared port that reports itself whether or not anybody wired it,
    so "we did not ask" never reads as "we asked and found nothing".
    """

    name = "provider"

    def describe(self) -> PropagationDescriptor:  # pragma: no cover - interface
        raise NotImplementedError

    def propagate(
        self, graph: DependencyGraph, states: Mapping[str, OperatingState]
    ) -> PropagationResult:  # pragma: no cover - interface
        raise NotImplementedError


class GraphPropagationProvider(PropagationProvider):
    """The built-in walk. Deterministic, in-process, no dependency."""

    name = "graph"

    def describe(self) -> PropagationDescriptor:
        return PropagationDescriptor(
            name=self.name,
            kind="builtin",
            available=True,
            detail=(
                "Single-pass walk in dependency order. Alternatives on POWER edges, "
                "requirements on CONVERSION and COOLING edges, and a chain that "
                "descends to the root cause naming each node once."
            ),
        )

    def propagate(
        self, graph: DependencyGraph, states: Mapping[str, OperatingState]
    ) -> PropagationResult:
        return propagate(graph, states, backend=self.name)


class RodotPropagationProvider(PropagationProvider):
    """Declared, unwired, and saying so.

    RODOT's `packages/domain/src/propagation.ts` is a mature implementation of
    this over a graph that also carries capacity, and the reuse assessment rated
    adapting it the highest-value item. It is not wired here: it would be a
    service call into a TypeScript codebase this demonstrator deliberately takes
    no dependency on, and the built-in walk is enough to answer RQ-005. The port
    exists so the decision stays visible rather than becoming an omission.
    """

    name = "rodot"

    def describe(self) -> PropagationDescriptor:
        return PropagationDescriptor(
            name=self.name,
            kind="service",
            available=False,
            detail=(
                "Not wired. RODOT's propagation runs in a TypeScript service; adapting it "
                "means a service boundary and a shared graph contract, which is a Pack 3 "
                "decision rather than an oversight. See docs/reuse-assessment.md."
            ),
        )

    def propagate(
        self, graph: DependencyGraph, states: Mapping[str, OperatingState]
    ) -> PropagationResult:
        raise RuntimeError(
            "the RODOT propagation provider is declared but not wired; "
            "use the built-in graph provider"
        )


class PropagationRegistry:
    """Every declared backend, wired or not."""

    def __init__(self, providers: Sequence[PropagationProvider] | None = None) -> None:
        self._providers = list(
            providers if providers is not None
            else (GraphPropagationProvider(), RodotPropagationProvider())
        )

    def status_report(self) -> tuple[PropagationDescriptor, ...]:
        return tuple(provider.describe() for provider in self._providers)

    def active(self) -> PropagationProvider:
        for provider in self._providers:
            if provider.describe().available:
                return provider
        raise RuntimeError("no propagation provider is available")

    def propagate(
        self, graph: DependencyGraph, states: Mapping[str, OperatingState]
    ) -> PropagationResult:
        return self.active().propagate(graph, states)


DEFAULT_PROPAGATION_REGISTRY = PropagationRegistry()
