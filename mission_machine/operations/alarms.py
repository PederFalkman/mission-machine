"""What a false alarm costs, and what a true one is worth (RQ-017).

RQ-016 built a panel that tells the operator the world has left the plan's
premise, and found it worth more than a better optimiser in the case that
actually threatens the mission. It also raised the obvious objection, which is
the reason this module exists: *an operator told twice that the premise has
changed, and twice wrong, will stop reading the panel.* The detectors are
threshold rules. Nothing so far says how often they fire on a difference that
did not matter.

Half of that question is measurable here and half is not, and the split is the
important part:

* **Measurable in the model.** How often the detectors fire when there was
  nothing to gain, how much acting on those alarms costs in fuel and in service
  the node did not have to give up, and how that trades against noticing late.
* **Not measurable here.** What a false alarm costs an operator's *trust* -
  whether the second wrong alarm makes them close the panel and miss the third,
  true one. That is a question about people and needs people. This module
  measures the rate and the material cost; it deliberately does not pretend to
  price the trust.

The method. For each world the node might really be living in, the mission is
planned on the premise the MissionSpec states and then lived in, hour by hour,
through an ordinary operations session. The first hour at which the *panel
speaks* is recorded - not the first hour a detector fires, because a
contradiction the session judges immaterial never reaches the operator and so
never costs them anything. From that hour three continuations are compared,
differing *only* in the premise they plan against:

    IGNORE   plan on the premise as stated - the operator dismissed the panel
    ACCEPT   plan on the revision the machine offered
    TRUTH    plan on the world that is actually there - nobody has this

Every arm replans at the same hour and is then carried out in the same world,
so the only variable is the premise. That is what makes the comparison a
statement about the alarm rather than about the planner.

From the three: what perfect knowledge was worth (TRUTH against IGNORE) and
what the alarm captured of it (ACCEPT against IGNORE). An alarm that fires
where perfect knowledge would have gained nothing interrupted the operator for
nothing, whatever the threshold says was breached.

TRUTH is the right premise, not the best play. It replans with the same
enumerate-and-rank engine as the others, so it is what an operator who somehow
knew the future would get from this planner - not an optimum. It can be beaten:
against supply four hours late, the deliberately pessimistic revision came out
ahead of planning on the truth, because it committed differently. So a verdict
of "perfect knowledge would have gained nothing" means what it says and no
more, and it is a bound on the premise's worth rather than on the planner's.

Note what is *not* used anywhere below: a hand-written label saying whether the
premise "really" changed. Such a label would be the author marking their own
homework. Whether there was anything worth telling the operator is computed,
by asking what knowing the premise would have been worth.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Sequence

from mission_machine.assets.inventory import AssetInventory
from mission_machine.assets.loads import Load, LoadProfile, scale_profile
from mission_machine.environment.model import Environment
from mission_machine.evidence.labels import DEMONSTRATOR_DISCLAIMER
from mission_machine.explainability.explain import build_recommendation
from mission_machine.mission.spec import MissionSpec
from mission_machine.operations.premises import (
    DEFAULT_THRESHOLDS,
    PremiseBreach,
    Thresholds,
    check_premises,
)
from mission_machine.operations.session import OperationsSession
from mission_machine.planning.configuration import Configuration
from mission_machine.planning.engine import PlanningEngine
from mission_machine.simulation.simulator import Simulator
from mission_machine.simulation.state import NodeState, StepRecord

# --------------------------------------------------------------------------
# what counts as better
# --------------------------------------------------------------------------

#: Outcomes are compared on the dimensions the mission itself ranks, in the
#: order it ranks them - critical service first, then the discretionary service
#: the operator asked to keep where affordable, then fuel. This is a
#: lexicographic comparison of three reported quantities, not a composite
#: score: every dimension stays visible and none is weighted against another.
#:
#: Each carries a deadband, because a comparison without one calls a rounding
#: difference a result. The deadbands are chosen, and registered as AS-023.
CRITICAL_DEADBAND_KWH = 1.0
DISCRETIONARY_DEADBAND_KWH = 5.0
FUEL_DEADBAND_L = 5.0

BETTER, SAME, WORSE = "BETTER", "SAME", "WORSE"

#: What the harness concluded about one alarm - or about its absence.
WORTH_RAISING = "WORTH_RAISING"      # fired, and acting on it left the node better off
WEAK_REVISION = "WEAK_REVISION"      # fired on something real; the revision captured none of it
NUISANCE = "NUISANCE"                # fired where perfect knowledge would have gained nothing
HARMFUL = "HARMFUL"                  # fired, and acting on it left the node worse off
MISSED = "MISSED"                    # silent, while there was something to gain
CORRECT_SILENCE = "CORRECT_SILENCE"  # silent, and there was nothing to say


@dataclass(frozen=True)
class World:
    """A world the node might really be living in, as against the stated one."""

    key: str
    description: str
    environment: Environment
    inventory: AssetInventory | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "description": self.description}


@dataclass
class Outcome:
    """How the mission ended up, under one premise, in one world."""

    arm: str                      # IGNORE | ACCEPT | TRUTH
    premise: str
    configuration_id: str = ""
    completed: bool = False
    critical_shortfall_kwh: float = 0.0
    first_shortfall_hour: float | None = None
    discretionary_served_kwh: float = 0.0
    fuel_used_l: float = 0.0
    planning_failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "premise": self.premise,
            "configuration_id": self.configuration_id,
            "completed": self.completed,
            "critical_shortfall_kwh": round(self.critical_shortfall_kwh, 2),
            "first_shortfall_hour": self.first_shortfall_hour,
            "discretionary_served_kwh": round(self.discretionary_served_kwh, 1),
            "fuel_used_l": round(self.fuel_used_l, 1),
            "planning_failed": self.planning_failed,
        }


def compare_outcomes(candidate: Outcome, reference: Outcome) -> str:
    """Is ``candidate`` better than ``reference``, on the mission's own order?

    Lexicographic over three separately reported quantities with explicit
    deadbands (AS-023). Deliberately not a weighted total: the point of the
    comparison is to say *which* dimension moved, and a scalar would hide it.
    """

    delta_critical = reference.critical_shortfall_kwh - candidate.critical_shortfall_kwh
    if delta_critical > CRITICAL_DEADBAND_KWH:
        return BETTER
    if -delta_critical > CRITICAL_DEADBAND_KWH:
        return WORSE

    delta_service = candidate.discretionary_served_kwh - reference.discretionary_served_kwh
    if delta_service > DISCRETIONARY_DEADBAND_KWH:
        return BETTER
    if -delta_service > DISCRETIONARY_DEADBAND_KWH:
        return WORSE

    delta_fuel = reference.fuel_used_l - candidate.fuel_used_l
    if delta_fuel > FUEL_DEADBAND_L:
        return BETTER
    if -delta_fuel > FUEL_DEADBAND_L:
        return WORSE
    return SAME


def differences(candidate: Outcome, reference: Outcome) -> list[str]:
    """The dimensions on which two outcomes differ, in words, with numbers."""

    lines: list[str] = []
    crit = candidate.critical_shortfall_kwh - reference.critical_shortfall_kwh
    if abs(crit) > CRITICAL_DEADBAND_KWH:
        lines.append(f"critical shortfall {crit:+.0f} kWh")
    service = candidate.discretionary_served_kwh - reference.discretionary_served_kwh
    if abs(service) > DISCRETIONARY_DEADBAND_KWH:
        lines.append(f"discretionary service {service:+.0f} kWh")
    fuel = candidate.fuel_used_l - reference.fuel_used_l
    if abs(fuel) > FUEL_DEADBAND_L:
        lines.append(f"fuel {fuel:+.0f} L")
    if candidate.completed != reference.completed:
        lines.append("completes the mission" if candidate.completed else "fails the mission")
    return lines


@dataclass
class AlarmCase:
    """One world, one set of thresholds: did the panel speak, and was it worth it?"""

    world: World
    thresholds: Thresholds
    fired: bool = False
    detected_at_hour: float | None = None
    premise_key: str = ""
    revision_statement: str = ""
    outcomes: dict[str, Outcome] = field(default_factory=dict)
    headroom: str = SAME          # TRUTH against IGNORE - what perfect knowledge was worth
    captured: str = SAME          # ACCEPT against IGNORE - what the alarm was worth
    verdict: str = CORRECT_SILENCE
    cost_of_acting: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "world": self.world.to_dict(),
            "thresholds": self.thresholds.to_dict(),
            "fired": self.fired,
            "detected_at_hour": self.detected_at_hour,
            "premise_key": self.premise_key,
            "revision_statement": self.revision_statement,
            "outcomes": {arm: outcome.to_dict() for arm, outcome in self.outcomes.items()},
            "headroom": self.headroom,
            "captured": self.captured,
            "verdict": self.verdict,
            "cost_of_acting": list(self.cost_of_acting),
        }


@dataclass
class AlarmLedger:
    """Every case, and what the sweep of thresholds says about them."""

    mission_id: str
    cases: list[AlarmCase] = field(default_factory=list)
    disclaimer: str = DEMONSTRATOR_DISCLAIMER
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    def for_threshold(self, grid_hours: float) -> list[AlarmCase]:
        return [case for case in self.cases if case.thresholds.grid_hours == grid_hours]

    def counts(self, cases: Sequence[AlarmCase] | None = None) -> dict[str, int]:
        selected = self.cases if cases is None else cases
        tally: dict[str, int] = {}
        for case in selected:
            tally[case.verdict] = tally.get(case.verdict, 0) + 1
        return tally

    def to_dict(self) -> dict[str, Any]:
        return {
            "mission_id": self.mission_id,
            "cases": [case.to_dict() for case in self.cases],
            "counts": self.counts(),
            "disclaimer": self.disclaimer,
            "data_labels": list(self.data_labels),
        }


# --------------------------------------------------------------------------
# the worlds
# --------------------------------------------------------------------------


def _with_heavier_critical_load(mission: MissionSpec, factor: float) -> AssetInventory:
    """An inventory whose critical functions draw more than the mission says."""

    inventory = mission.inventory.copy()
    for load_id in mission.critical_loads:
        asset = inventory.find(load_id)
        if isinstance(asset, Load):
            asset.profile = scale_profile(asset.profile, factor)
    return inventory


def _with_noisy_critical_load(
    mission: MissionSpec, environment: Environment, swing: float, seed: int = 7
) -> AssetInventory:
    """An inventory that draws the stated energy, hour by wobbly hour.

    The mission-long mean is the stated one exactly. Only the hour-to-hour
    shape differs, which is the ordinary condition of any real load and
    precisely the case a threshold on a short observation can mistake for a
    changed premise.
    """

    hours = int(mission.mission_duration_h)
    rng = random.Random(seed)
    factors = [1.0 + rng.uniform(-swing, swing) for _ in range(hours)]

    # Normalise on energy, not on the factors themselves. A flat mean of 1.0
    # still moves the mission's total whenever the wobble happens to land on
    # the heavy hours, and a world that quietly draws more than stated would
    # make the detector look better than it is.
    weights = [
        mission.critical_demand_kw(float(hour), environment.temperature_c(float(hour)))
        for hour in range(hours)
    ]
    planned = sum(weights)
    drawn = sum(weight * factor for weight, factor in zip(weights, factors))
    if drawn > 0:
        factors = [factor * planned / drawn for factor in factors]

    inventory = mission.inventory.copy()
    for load_id in mission.critical_loads:
        asset = inventory.find(load_id)
        if not isinstance(asset, Load):
            continue
        values = [
            asset.profile.demand_kw(float(hour), environment.temperature_c(float(hour)))
            * factors[hour]
            for hour in range(hours)
        ]
        asset.profile = LoadProfile(
            type="hourly", values=values, max_kw=asset.profile.max_kw
        )
    return inventory


def demonstrator_worlds(
    mission: MissionSpec, engine: PlanningEngine | None = None
) -> list[World]:
    """Worlds spanning the cases the detectors are supposed to tell apart.

    Three kinds, deliberately mixed: worlds where the premise is badly wrong,
    worlds where it is wrong in a way that may not matter, and worlds where it
    is right or very nearly so. A harness containing only the first kind would
    report the detectors as flawless.
    """

    engine = engine or PlanningEngine(mission)
    stated = engine.environment
    windows = [list(window) for window in stated.grid.available_windows]
    worlds: list[World] = [
        World("as-forecast", "The world the mission describes.", stated),
    ]

    if len(windows) >= 2:
        first, second = windows[0], windows[1]
        # Every world below perturbs the *second* supply window. Missions with
        # more than two carry the rest through unchanged - MM-DEMO-003 has four,
        # and building its worlds from the first two alone silently deleted the
        # other two, so a world called "supply returns 2 h late" was really
        # "2 h late and then never again" (RQ-001).
        rest = [list(window) for window in windows[2:]]
        for late in (2.0, 4.0, 8.0):
            worlds.append(
                World(
                    f"grid-{late:.0f}h-late",
                    f"Host-nation supply returns {late:.0f} h later than stated.",
                    stated.with_grid_windows(
                        [first, [second[0] + late, second[1]], *rest],
                        name=f"grid_late_{late:.0f}",
                        note=f"Supply returned {late:.0f} h late.",
                    ),
                )
            )
        worlds.append(
            World(
                "grid-never",
                "Host-nation supply never returns.",
                stated.with_grid_windows(
                    [first], name="grid_never", note="Supply never returned."
                ),
            )
        )
        # A transient: supply is there exactly as promised, apart from a
        # two-hour dropout early in the first window that then recovers. The
        # premise is right about the mission and wrong about two hours of it.
        hole = min(first[0] + 8.0, max(first[0], first[1] - 3.0))
        worlds.append(
            World(
                "grid-blink",
                f"Supply drops out for 2 h at H+{hole:.0f} and comes back, as stated.",
                stated.with_grid_windows(
                    [[first[0], hole], [hole + 2.0, first[1]], second, *rest],
                    name="grid_blink",
                    note=f"Two-hour dropout at H+{hole:.0f}; otherwise as stated.",
                ),
            )
        )
        # Two separate single-hour dropouts. No run of missing supply is longer
        # than an hour, and the mission's premise is otherwise exactly right.
        flick = min(first[0] + 6.0, max(first[0], first[1] - 5.0))
        worlds.append(
            World(
                "grid-flicker",
                f"Supply drops for 1 h at H+{flick:.0f} and again at H+{flick + 4:.0f}.",
                stated.with_grid_windows(
                    [
                        [first[0], flick],
                        [flick + 1.0, flick + 4.0],
                        [flick + 5.0, first[1]],
                        second,
                        *rest,
                    ],
                    name="grid_flicker",
                    note="Two isolated one-hour dropouts; otherwise as stated.",
                ),
            )
        )
        # Two separate outages in one mission. RQ-018's shape: the panel has to
        # speak twice, and the second time has to be news rather than the first
        # alarm still running.
        worlds.append(
            World(
                "grid-twice",
                "Supply fails, returns for 4 h, and fails again.",
                stated.with_grid_windows(
                    [first, [second[0] + 6.0, second[0] + 10.0], *rest],
                    name="grid_twice",
                    note="Two separate outages inside the promised window.",
                ),
            )
        )

    worlds.append(
        World(
            "load-6pc-heavier",
            "Critical functions draw 6 % more than the stated profiles.",
            stated,
            _with_heavier_critical_load(mission, 1.06),
        )
    )
    worlds.append(
        World(
            "load-15pc-heavier",
            "Critical functions draw 15 % more than the stated profiles.",
            stated,
            _with_heavier_critical_load(mission, 1.15),
        )
    )
    worlds.append(
        World(
            "load-noisy",
            "Critical demand wobbles hour to hour; over the mission it is as stated.",
            stated,
            _with_noisy_critical_load(mission, stated, 0.18),
        )
    )
    worlds.append(
        World(
            "overcast",
            "Heavy cloud: the array yields about half the forecast.",
            stated.variant(name="overcast", cloud_scale=0.35),
        )
    )
    return worlds


# --------------------------------------------------------------------------
# running one case
# --------------------------------------------------------------------------


def _outcome(
    arm: str,
    premise: str,
    steps: Sequence[StepRecord],
    configuration_id: str,
    *,
    planning_failed: bool = False,
) -> Outcome:
    shortfall = sum(step.critical_shortfall_kwh for step in steps)
    short_hours = [step.hour for step in steps if step.critical_shortfall_kwh > 1e-6]
    return Outcome(
        arm=arm,
        premise=premise,
        configuration_id=configuration_id,
        completed=shortfall <= CRITICAL_DEADBAND_KWH,
        critical_shortfall_kwh=shortfall,
        first_shortfall_hour=min(short_hours) if short_hours else None,
        discretionary_served_kwh=sum(
            step.secondary_served_kw * step.duration_h for step in steps
        ),
        fuel_used_l=sum(step.fuel_used_l for step in steps),
        planning_failed=planning_failed,
    )


def _arm(
    mission: MissionSpec,
    world: World,
    arm: str,
    premise: str,
    premise_environment: Environment,
    premise_inventory: AssetInventory | None,
    prefix: Sequence[StepRecord],
    state: NodeState,
    from_hour: float,
) -> Outcome:
    """Replan at ``from_hour`` on one premise, then live in the real world."""

    engine = PlanningEngine(mission, premise_environment, premise_inventory)
    plan = engine.generate_options(
        start_hour=from_hour,
        initial_state=state,
        id_prefix=f"CFG-{arm[:3]}-H{int(from_hour)}",
    )
    if not plan.options:
        return _outcome(arm, premise, prefix, "", planning_failed=True)
    recommendation = build_recommendation(plan, engine, include_sensitivity=False)
    configuration = plan.option(recommendation.recommended_configuration_id).configuration

    simulator = Simulator(mission, world.environment, world.inventory or mission.inventory)
    tail = simulator.run(
        configuration,
        start_hour=from_hour,
        end_hour=mission.mission_duration_h,
        initial_state=state,
    )
    return _outcome(
        arm, premise, list(prefix) + list(tail.steps), configuration.configuration_id
    )


def _session_for(
    mission: MissionSpec,
    world: World,
    stated_environment: Environment,
) -> tuple[OperationsSession, Configuration]:
    """An operator's session: planning on the premise, living in ``world``."""

    session = OperationsSession(
        mission,
        PlanningEngine(mission, stated_environment),
        realised_environment=world.environment,
        realised_inventory=world.inventory,
    )
    plan = session.generate_options(with_resilience=False)
    recommendation = build_recommendation(plan, session.engine, include_sensitivity=False)
    option = session.select(recommendation.recommended_configuration_id)
    return session, option.configuration


def _run_until_alarm(
    session: OperationsSession,
    thresholds: Thresholds,
) -> tuple[float, PremiseBreach] | None:
    """Advance hour by hour until the panel speaks, exactly as the operator does.

    Two stages, in the order an operator meets them: the detectors run on the
    observations available at that hour and nothing later, and where they fire
    the session decides whether the contradiction changes the picture enough to
    raise. What is being measured is what an operator is actually interrupted
    by, not what a threshold technically caught.
    """

    duration = session.mission.mission_duration_h
    hour = 1.0
    while hour <= duration:
        session.run_to(hour)
        breaches = check_premises(
            session.mission,
            session.planned_environment,
            session.observed,
            hour,
            thresholds,
        )
        if breaches:
            report = session.check_premises(thresholds)
            if not report.clear:
                return hour, report.raised[0].breach
        hour += 1.0
    return None


def evaluate_world(
    mission: MissionSpec,
    world: World,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    engine: PlanningEngine | None = None,
    cache: dict[tuple[str, float], dict[str, Outcome]] | None = None,
) -> AlarmCase:
    """Run one world, see whether the panel spoke, and price what it said."""

    engine = engine or PlanningEngine(mission)
    stated_environment = engine.environment
    case = AlarmCase(world=world, thresholds=thresholds)

    session, configuration = _session_for(mission, world, stated_environment)
    found = _run_until_alarm(session, thresholds)

    if found is None:
        # The panel stayed silent. Whether it should have is not a matter of
        # opinion: ask what planning on the truth from the start would have won.
        simulator = Simulator(mission, world.environment, world.inventory or mission.inventory)
        truth = _arm(
            mission, world, "TRUTH", "the world as it really is",
            world.environment, world.inventory, [], simulator.initial_state(), 0.0,
        )
        ignore = _outcome(
            "IGNORE", "the premise as stated", session.observed,
            configuration.configuration_id,
        )
        case.outcomes = {"IGNORE": ignore, "TRUTH": truth}
        case.headroom = compare_outcomes(truth, ignore)
        case.verdict = MISSED if case.headroom == BETTER else CORRECT_SILENCE
        if case.headroom == BETTER:
            case.cost_of_acting = differences(truth, ignore)
        return case

    at_hour, breach = found
    case.fired = True
    case.detected_at_hour = at_hour
    case.premise_key = breach.premise.key
    case.revision_statement = breach.revision_statement

    key = (world.key, at_hour)
    if cache is not None and key in cache:
        case.outcomes = dict(cache[key])
    else:
        prefix = list(session.observed)
        state = session.state.copy()
        ignore = _arm(
            mission, world, "IGNORE", "the premise as stated",
            stated_environment, None, prefix, state, at_hour,
        )
        accept = _arm(
            mission, world, "ACCEPT", breach.revision_statement,
            breach.revised_environment or stated_environment,
            breach.revised_inventory,
            prefix, state, at_hour,
        )
        truth = _arm(
            mission, world, "TRUTH", "the world as it really is",
            world.environment, world.inventory, prefix, state, at_hour,
        )
        case.outcomes = {"IGNORE": ignore, "ACCEPT": accept, "TRUTH": truth}
        if cache is not None:
            cache[key] = dict(case.outcomes)

    ignore = case.outcomes["IGNORE"]
    case.headroom = compare_outcomes(case.outcomes["TRUTH"], ignore)
    case.captured = compare_outcomes(case.outcomes["ACCEPT"], ignore)
    case.cost_of_acting = differences(case.outcomes["ACCEPT"], ignore)

    if case.captured == WORSE:
        case.verdict = HARMFUL
    elif case.captured == BETTER:
        case.verdict = WORTH_RAISING
    elif case.headroom == BETTER:
        case.verdict = WEAK_REVISION
    else:
        case.verdict = NUISANCE
    return case


def sweep_thresholds(
    mission: MissionSpec,
    grid_hours: Sequence[float] = (1.0, 2.0, 3.0, 4.0, 6.0),
    *,
    worlds: Sequence[World] | None = None,
    engine: PlanningEngine | None = None,
) -> AlarmLedger:
    """Move the line between a difference and a contradiction, and price it.

    The same worlds are run at each threshold. Raising it buys quiet and costs
    time - the question RQ-017 asks is what the exchange rate is.
    """

    engine = engine or PlanningEngine(mission)
    worlds = list(worlds if worlds is not None else demonstrator_worlds(mission, engine))
    ledger = AlarmLedger(mission_id=mission.mission_id)
    cache: dict[tuple[str, float], dict[str, Outcome]] = {}
    for hours in grid_hours:
        thresholds = Thresholds(grid_hours=hours)
        for world in worlds:
            ledger.cases.append(
                evaluate_world(mission, world, thresholds, engine=engine, cache=cache)
            )
    return ledger


# --------------------------------------------------------------------------
# alarm load over a whole mission (RQ-018)
# --------------------------------------------------------------------------


@dataclass
class AlarmLoad:
    """How often the panel speaks over a whole mission, not just the first time.

    RQ-017 asked whether the first alarm of a world was worth raising. It never
    counted how many times the panel spoke afterwards, and the answer turned out
    to be the whole question: the same true contradiction was raised once an
    hour for as long as it lasted. ``interruptions`` is what an operator lives
    through; ``contradiction_hours`` is what the panel would have said without
    the standing rule, and both come from the same run.
    """

    world_key: str
    interruptions: int = 0
    contradiction_hours: int = 0
    standing_hours: int = 0
    resolutions: int = 0
    raised_at: list[float] = field(default_factory=list)
    resolved_at: list[float] = field(default_factory=list)
    premises: list[str] = field(default_factory=list)

    @property
    def repeats_avoided(self) -> int:
        return self.contradiction_hours - self.interruptions

    def to_dict(self) -> dict[str, Any]:
        return {
            "world_key": self.world_key,
            "interruptions": self.interruptions,
            "contradiction_hours": self.contradiction_hours,
            "standing_hours": self.standing_hours,
            "resolutions": self.resolutions,
            "repeats_avoided": self.repeats_avoided,
            "raised_at": list(self.raised_at),
            "resolved_at": list(self.resolved_at),
            "premises": list(self.premises),
        }


def measure_alarm_load(
    mission: MissionSpec,
    world: World,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    *,
    engine: PlanningEngine | None = None,
) -> AlarmLoad:
    """Live the whole mission in ``world`` and count what the panel does.

    Nothing is accepted or dismissed along the way: this is the load on an
    operator who reads every alarm and acts on none of it, which is the upper
    bound on how often they are interrupted.
    """

    engine = engine or PlanningEngine(mission)
    session, _ = _session_for(mission, world, engine.environment)
    load = AlarmLoad(world_key=world.key)
    premises: list[str] = []

    hour = 1.0
    while hour <= mission.mission_duration_h:
        session.run_to(hour)
        report = session.check_premises(thresholds)
        load.interruptions += len(report.raised)
        load.standing_hours += len(report.standing)
        load.contradiction_hours += len(report.raised) + len(report.standing)
        for consequence in report.raised:
            load.raised_at.append(hour)
            if consequence.breach.premise.key not in premises:
                premises.append(consequence.breach.premise.key)
        for alarm in report.resolved:
            load.resolutions += 1
            load.resolved_at.append(hour)
        hour += 1.0

    load.premises = premises
    return load


def alarm_load_table(
    mission: MissionSpec,
    worlds: Sequence[World] | None = None,
    *,
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    engine: PlanningEngine | None = None,
) -> list[AlarmLoad]:
    """The alarm load of every world, for reading as one picture."""

    engine = engine or PlanningEngine(mission)
    worlds = list(worlds if worlds is not None else demonstrator_worlds(mission, engine))
    return [measure_alarm_load(mission, world, thresholds, engine=engine) for world in worlds]
