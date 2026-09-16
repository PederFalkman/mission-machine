"""Deterministic dispatch simulator.

Given a mission, an environment and a :class:`Configuration`, step through the
mission hour by hour and record exactly what every asset did. The simulator is
the *evaluation* half of the planner: the planning engine proposes
configurations, the simulator says what each one would actually deliver.

Design rules for Pack 1:

* **Deterministic.** Same inputs, same outputs. No sampling, no learning.
* **Transparent.** The dispatch rules are stated below in the order they are
  applied, and every step is recorded in full.
* **Auditable.** The resulting schedule can be checked against the declared
  MILP constraint set (``planning/milp.py``), so the heuristic cannot quietly
  produce a plan that violates the model.

Dispatch rules, in order, for each time step:

1. PV output and (if the policy allows and it is available) grid import serve
   the critical load first, then attempted secondary loads in shed-priority
   order.
2. If critical demand is still unmet:
   a. generators already running take the load;
   b. otherwise, if the battery can cover the whole remaining critical demand
      for this step while staying above the policy reserve floor, it does, so
      that a generator start is avoided;
   c. otherwise generators are started in merit order (lowest specific fuel
      consumption first).
3. Committed generators are loaded to cover the remaining critical and
   attempted secondary demand, plus a battery recharge request when the policy
   cycles generators. No generator is loaded below its minimum loading.
4. Any remaining shortfall is taken from the battery: critical demand down to
   the battery's hard floor, discretionary demand only down to the policy
   reserve floor.
5. Anything still unmet sheds secondary loads, highest shed priority first.
   Only if every secondary load is shed and demand is still unmet is critical
   demand recorded as unserved - that is a mission-assurance failure.
6. Surplus generation charges the battery; what cannot be stored is curtailed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from mission_machine.assets.base import Asset, FailureState
from mission_machine.assets.energy import Battery, Generator, GridConnection, PowerConversion, SolarPV
from mission_machine.assets.inventory import AssetInventory
from mission_machine.assets.loads import Load
from mission_machine.evidence.questions import OpenQuestion
from mission_machine.environment.model import Environment
from mission_machine.mission.spec import MissionSpec
from mission_machine.planning.configuration import Configuration, DispatchPolicy, GeneratorMode
from mission_machine.resilience.failures import FailureEvent
from mission_machine.simulation.state import (
    UNSERVED_TOLERANCE_KWH,
    NodeState,
    ReserveBreakdown,
    StepRecord,
)

EPS = 1e-9


@dataclass
class SimulationResult:
    """The full record of one configuration run over one stretch of mission time."""

    mission_id: str
    configuration_id: str
    start_hour: float
    end_hour: float
    time_step_h: float
    steps: list[StepRecord] = field(default_factory=list)
    initial_state: NodeState = field(default_factory=NodeState)
    final_state: NodeState = field(default_factory=NodeState)
    events: list[FailureEvent] = field(default_factory=list)
    endurance_hours: float = 0.0
    endurance_limited_by: str = "NOT_LIMITED_WITHIN_MISSION"
    first_critical_shortfall_hour: float | None = None
    data_labels: tuple[str, ...] = ("SYNTHETIC", "SIMULATED", "UNVALIDATED")

    @property
    def horizon_h(self) -> float:
        return self.end_hour - self.start_hour

    @property
    def completed_mission(self) -> bool:
        return self.first_critical_shortfall_hour is None

    def series(self, attribute: str) -> list[float]:
        return [getattr(step, attribute) for step in self.steps]

    def to_dict(self, include_steps: bool = True) -> dict[str, Any]:
        out = {
            "mission_id": self.mission_id,
            "configuration_id": self.configuration_id,
            "start_hour": self.start_hour,
            "end_hour": self.end_hour,
            "time_step_h": self.time_step_h,
            "endurance_hours": round(self.endurance_hours, 2),
            "endurance_limited_by": self.endurance_limited_by,
            "first_critical_shortfall_hour": self.first_critical_shortfall_hour,
            "completed_mission": self.completed_mission,
            "initial_state": self.initial_state.to_dict(),
            "final_state": self.final_state.to_dict(),
            "events": [e.to_dict() for e in self.events],
            "data_labels": list(self.data_labels),
        }
        if include_steps:
            out["steps"] = [s.to_dict() for s in self.steps]
        return out


# --------------------------------------------------------------------------
# asset state helpers
# --------------------------------------------------------------------------


def effective_state(asset: Asset, hour: float, events: Sequence[FailureEvent]) -> FailureState:
    """Asset state at ``hour``, taking scheduled failure events into account."""

    state = asset.failure_state
    for event in sorted(events, key=lambda e: e.hour):
        if event.asset_id != asset.asset_id:
            continue
        if event.active_at(hour):
            state = event.state
        elif event.restored_hour is not None and hour >= event.restored_hour:
            state = FailureState.NOMINAL
    return state


def _usable(asset: Asset | None, hour: float, events: Sequence[FailureEvent]) -> bool:
    return asset is not None and effective_state(asset, hour, events) is not FailureState.UNAVAILABLE


# --------------------------------------------------------------------------
# the simulator
# --------------------------------------------------------------------------


class Simulator:
    """Runs a configuration over mission time."""

    def __init__(
        self,
        mission: MissionSpec,
        environment: Environment | None = None,
        inventory: AssetInventory | None = None,
    ) -> None:
        self.mission = mission
        self.environment = environment or mission.build_environment()
        self.inventory = inventory or mission.inventory

    # -- public API ---------------------------------------------------------

    def run(
        self,
        configuration: Configuration,
        *,
        start_hour: float | None = None,
        end_hour: float | None = None,
        initial_state: NodeState | None = None,
        events: Iterable[FailureEvent] = (),
        commitment_schedule: Mapping[float, set[str]] | None = None,
        storage_schedule: Mapping[float, float] | None = None,
    ) -> SimulationResult:
        """Run ``configuration`` over mission time.

        ``commitment_schedule`` prescribes, per hour, which generators are
        committed - synchronised and running at or above minimum loading. It is
        how a plan made earlier, possibly from a forecast that turned out to be
        wrong, is carried out. A generator the plan did not commit is started
        only if the hour actually needs it, and that override is recorded: it is
        the visible cost of having planned against the wrong world.
        """

        mission = self.mission
        dt = mission.time_step_h
        start = configuration.start_hour if start_hour is None else start_hour
        end = mission.mission_duration_h if end_hour is None else end_hour
        event_list = list(events)

        battery = self._battery()
        state = initial_state.copy() if initial_state else self._initial_state(battery)
        state.hour = start

        result = SimulationResult(
            mission_id=mission.mission_id,
            configuration_id=configuration.configuration_id,
            start_hour=start,
            end_hour=end,
            time_step_h=dt,
            initial_state=state.copy(),
            events=event_list,
        )

        hour = start
        while hour < end - EPS:
            step = self._step(
                configuration,
                state,
                hour,
                min(dt, end - hour),
                event_list,
                commitment_schedule,
                storage_schedule,
            )
            result.steps.append(step)
            hour += dt

        result.final_state = state.copy()
        self._finalise(result)
        return result

    # -- setup helpers ------------------------------------------------------

    def _battery(self) -> Battery | None:
        batteries = self.inventory.batteries
        return batteries[0] if batteries else None

    def _initial_state(self, battery: Battery | None) -> NodeState:
        return NodeState(
            hour=0.0,
            battery_soc=battery.initial_state_of_charge if battery else 0.0,
            fuel_remaining_l=self.mission.fuel_limit_l,
            generator_running={g.asset_id: False for g in self.inventory.generators},
            generator_output_kw={g.asset_id: 0.0 for g in self.inventory.generators},
            generator_run_hours={g.asset_id: 0.0 for g in self.inventory.generators},
            generator_current_run_h={g.asset_id: 0.0 for g in self.inventory.generators},
            generator_starts={g.asset_id: 0 for g in self.inventory.generators},
        )

    def initial_state(self) -> NodeState:
        """A fresh node state at H+0 (full tanks, battery at its initial SoC)."""

        return self._initial_state(self._battery())

    def _loads(self, load_ids: list[str]) -> list[Load]:
        """Resolve mission load ids against *this simulator's* inventory.

        The inventory can differ from the mission's own copy - that is how a
        what-if such as "accept a degraded cooling setpoint" is evaluated.
        """

        loads = []
        for load_id in load_ids:
            asset = self.inventory.find(load_id)
            if isinstance(asset, Load):
                loads.append(asset)
        return loads

    # -- one time step ------------------------------------------------------

    def _step(
        self,
        configuration: Configuration,
        state: NodeState,
        hour: float,
        dt: float,
        events: Sequence[FailureEvent],
        commitment_schedule: Mapping[float, set[str]] | None = None,
        storage_schedule: Mapping[float, float] | None = None,
    ) -> StepRecord:
        mission = self.mission
        policy = configuration.policy
        env = self.environment
        ambient = env.temperature_c(hour)
        solar = env.solar_fraction(hour)
        active = set(configuration.active_asset_ids)

        battery = self._battery()
        battery_active = (
            battery is not None
            and policy.use_battery
            and battery.asset_id in active
            and _usable(battery, hour, events)
        )
        capacity_kwh = battery.energy_capacity_kwh if battery else 0.0
        energy_kwh = state.battery_soc * capacity_kwh

        step = StepRecord(
            hour=hour,
            duration_h=dt,
            ambient_c=ambient,
            solar_fraction=solar,
            grid_available=env.grid_available_at(hour),
        )

        # ---- 1. demand ---------------------------------------------------
        critical_loads = [
            load for load in self._loads(mission.critical_loads) if _usable(load, hour, events)
        ]
        # Serving and shedding order comes from the operator's ranked intent,
        # falling back to the shed priority recorded against the equipment.
        secondary_loads = sorted(
            [load for load in self._loads(mission.secondary_loads) if _usable(load, hour, events)],
            key=mission.shed_order_key,
        )
        critical_demand = {load.asset_id: load.demand_kw(hour, ambient) for load in critical_loads}
        secondary_demand = {
            load.asset_id: load.demand_kw(hour, ambient) for load in secondary_loads
        }
        attempted = {
            load.asset_id: kw
            for load, kw in ((l, secondary_demand[l.asset_id]) for l in secondary_loads)
            if policy.attempts(load, mission)
        }
        step.load_demand_kw = {**critical_demand, **secondary_demand}
        step.critical_demand_kw = sum(critical_demand.values())
        step.secondary_demand_kw = sum(secondary_demand.values())
        step.secondary_attempted_kw = sum(attempted.values())

        # ---- 2. free supply (PV, then grid) -------------------------------
        pv_kw = 0.0
        pv = self.inventory.pv_arrays[0] if self.inventory.pv_arrays else None
        if pv is not None and policy.deploy_pv and pv.asset_id in active and _usable(pv, hour, events):
            pv_kw = pv.output_kw(solar, ambient)

        grid_kw_limit = 0.0
        grid = self.inventory.grid_connections[0] if self.inventory.grid_connections else None
        if (
            grid is not None
            and policy.use_grid
            and grid.asset_id in active
            and _usable(grid, hour, events)
            and env.grid_available_at(hour)
        ):
            grid_kw_limit = min(grid.capacity_kw, env.grid.nominal_capacity_kw or grid.capacity_kw)

        conversion_limit = self._conversion_limit(active, hour, events)
        if conversion_limit <= EPS:
            # With no serviceable conversion and distribution unit, no source can
            # reach any load, whatever else is running.
            pv_kw = 0.0
            grid_kw_limit = 0.0
            battery_active = False
            step.notes.append(
                "No serviceable power conversion / distribution unit: the node cannot "
                "transfer power."
            )

        # ---- 3. how much do the generators have to make? ------------------
        free_supply = pv_kw + grid_kw_limit
        critical_total = step.critical_demand_kw
        attempted_total = step.secondary_attempted_kw

        critical_deficit = max(0.0, critical_total - free_supply)
        attempted_deficit = max(0.0, critical_total + attempted_total - free_supply)

        generators = [
            g
            for g in self.inventory.generators
            if g.asset_id in policy.generator_ids
            and g.asset_id in active
            and _usable(g, hour, events)
        ]
        forced_ids: frozenset[str] = frozenset()
        if commitment_schedule is not None:
            forced_ids = frozenset(
                commitment_schedule.get(round(hour, 6), frozenset())
            ) & {g.asset_id for g in generators}
        any_running = any(state.generator_running.get(g.asset_id, False) for g in generators)

        battery_reserve_floor_kwh = capacity_kwh * max(
            battery.min_state_of_charge if battery else 0.0, policy.battery_reserve_soc
        )
        battery_hard_floor_kwh = capacity_kwh * (battery.min_state_of_charge if battery else 0.0)
        # Standing loss happens whatever the dispatch does, so it has to be left
        # in the cells: discharging to exactly the floor and then letting
        # self-discharge eat into it breaks the floor the dispatch is there to
        # protect. Caught by the MILP verifier, which declares the floor a bound.
        standing_loss_kwh = (
            capacity_kwh * battery.self_discharge_per_h * dt if battery is not None else 0.0
        )

        discretionary_battery_kw = 0.0
        if battery_active and battery is not None:
            discretionary_battery_kw = min(
                battery.max_discharge_kw,
                max(0.0, energy_kwh - standing_loss_kwh - battery_reserve_floor_kwh)
                * battery.one_way_efficiency
                / dt,
            )

        effective_mode = (
            GeneratorMode.CYCLED if commitment_schedule is not None else policy.generator_mode
        )
        # Rule 2b: a generator start can be avoided if the battery can carry the
        # whole critical deficit this step without eating into its reserve.
        # A prescribed commitment decides what runs, so the policy's own
        # start/stop rule does not also get a vote.
        #
        # COAST is the same test without `not any_running`: a set that is
        # already turning is *stopped* when the battery can carry the node, not
        # merely not started. RQ-015 is the measurement of what that second
        # sentence is worth; the first version of CYCLED described it in its own
        # docstring ("so it can be stopped again") and never did it, so the
        # battery charged to its target once and then sat there while the
        # generator tracked the load at part loading for the rest of the night.
        battery_can_carry = (
            commitment_schedule is None
            and battery_active
            and critical_deficit > 0.0
            and discretionary_battery_kw >= attempted_deficit - EPS
        )
        avoid_start = (
            battery_can_carry
            and effective_mode is GeneratorMode.CYCLED
            and not any_running
        ) or (
            battery_can_carry
            and effective_mode is GeneratorMode.COAST
            and self._may_stop(generators, state, policy)
        )

        gen_target = 0.0
        # What the *load* needs, as against what the load plus a battery top-up
        # needs. The difference decides how many sets are committed: a recharge
        # is worth loading a running machine harder, and is not worth starting a
        # second one for. RQ-015 measured that distinction as the whole of the
        # difference between the stop rule saving fuel and costing it.
        service_target = 0.0
        if generators and not avoid_start:
            if effective_mode is GeneratorMode.CONTINUOUS:
                gen_target = attempted_deficit
                service_target = attempted_deficit
            elif attempted_deficit > 0.0 or forced_ids or (any_running and critical_deficit > 0.0):
                gen_target = attempted_deficit
                service_target = attempted_deficit
                if battery_active and battery is not None:
                    # A plan's storage trajectory, where one was handed over,
                    # otherwise the policy's own recharge target.
                    target_kwh = (
                        storage_schedule.get(round(hour, 6))
                        if storage_schedule is not None
                        else None
                    )
                    if target_kwh is None:
                        target_kwh = capacity_kwh * policy.battery_charge_target_soc
                    charge_request = min(
                        battery.max_charge_kw,
                        max(0.0, target_kwh - energy_kwh)
                        / (battery.one_way_efficiency * dt),
                    )
                    gen_target += charge_request

        gen_target = min(gen_target, max(0.0, conversion_limit))

        gen_output, fuel_used, gen_notes = self._dispatch_generators(
            generators,
            gen_target,
            state,
            ambient,
            dt,
            state.fuel_remaining_l,
            effective_mode,
            forced_ids=forced_ids,
            commit_target_kw=(
                min(service_target, max(0.0, conversion_limit))
                if effective_mode is GeneratorMode.COAST
                else None
            ),
        )
        step.generator_kw = gen_output
        step.notes.extend(gen_notes)
        generation_kw = sum(gen_output.values())

        # ---- 4. allocate supply to loads ---------------------------------
        available = min(pv_kw + grid_kw_limit + generation_kw, max(0.0, conversion_limit))

        served: dict[str, float] = {}
        remaining = available

        critical_shortfall = 0.0
        for load in sorted(critical_loads, key=mission.shed_order_key):
            want = critical_demand[load.asset_id]
            give = min(want, remaining)
            served[load.asset_id] = give
            remaining -= give
            critical_shortfall += want - give

        # battery covers critical shortfall down to its hard floor
        battery_discharge_kw = 0.0
        if critical_shortfall > EPS and battery_active and battery is not None:
            capability = min(
                battery.max_discharge_kw,
                max(0.0, energy_kwh - standing_loss_kwh - battery_hard_floor_kwh)
                * battery.one_way_efficiency
                / dt,
            )
            take = min(critical_shortfall, capability)
            if take > EPS:
                battery_discharge_kw += take
                critical_shortfall -= take
                for load in sorted(critical_loads, key=mission.shed_order_key):
                    gap = critical_demand[load.asset_id] - served[load.asset_id]
                    if gap <= EPS:
                        continue
                    give = min(gap, take)
                    served[load.asset_id] += give
                    take -= give
                    if take <= EPS:
                        break
                step.notes.append("Battery discharged to hold critical load.")

        # discretionary loads take what is left, best priority first
        shed: list[str] = []
        battery_headroom_for_secondary = 0.0
        if battery_active and battery is not None:
            drawn_kwh = battery_discharge_kw * dt / battery.one_way_efficiency
            battery_headroom_for_secondary = min(
                max(0.0, battery.max_discharge_kw - battery_discharge_kw),
                max(0.0, energy_kwh - drawn_kwh - standing_loss_kwh - battery_reserve_floor_kwh)
                * battery.one_way_efficiency
                / dt,
            )

        for load in secondary_loads:
            want = secondary_demand[load.asset_id]
            if want <= EPS:
                served[load.asset_id] = 0.0
                continue
            if load.asset_id not in attempted:
                served[load.asset_id] = 0.0
                shed.append(load.asset_id)
                continue
            give = min(want, remaining)
            remaining -= give
            gap = want - give
            extra = 0.0
            if gap > EPS and battery_headroom_for_secondary > EPS:
                extra = min(gap, battery_headroom_for_secondary)
                battery_headroom_for_secondary -= extra
                battery_discharge_kw += extra
                give += extra
                gap -= extra
            if gap > EPS and give < want * max(load.min_service_fraction, EPS):
                # Partial service below the useful threshold is not worth the
                # energy: hand it all back, including anything drawn from the
                # battery for this load.
                remaining += give - extra
                battery_headroom_for_secondary += extra
                battery_discharge_kw -= extra
                served[load.asset_id] = 0.0
                shed.append(load.asset_id)
                continue
            served[load.asset_id] = give
            if gap > EPS:
                shed.append(load.asset_id)

        # ---- 5. battery charging from surplus ----------------------------
        battery_charge_kw = 0.0
        if remaining > EPS and battery_active and battery is not None:
            drawn_kwh = battery_discharge_kw * dt / battery.one_way_efficiency
            room_kwh = max(
                0.0,
                capacity_kwh * battery.max_state_of_charge - (energy_kwh - drawn_kwh),
            )
            battery_charge_kw = min(
                remaining,
                battery.max_charge_kw,
                room_kwh / (battery.one_way_efficiency * dt),
            )
            remaining -= battery_charge_kw

        # ---- 6. state update ---------------------------------------------
        if battery is not None:
            energy_kwh -= battery_discharge_kw * dt / battery.one_way_efficiency
            energy_kwh += battery_charge_kw * battery.one_way_efficiency * dt
            energy_kwh -= capacity_kwh * battery.self_discharge_per_h * dt
            energy_kwh = max(0.0, min(capacity_kwh, energy_kwh))
            state.battery_soc = energy_kwh / capacity_kwh if capacity_kwh else 0.0

        state.fuel_remaining_l = max(0.0, state.fuel_remaining_l - fuel_used)
        for gen in self.inventory.generators:
            out = gen_output.get(gen.asset_id, 0.0)
            was_running = state.generator_running.get(gen.asset_id, False)
            running = out > EPS
            if running and not was_running:
                state.generator_starts[gen.asset_id] = state.generator_starts.get(gen.asset_id, 0) + 1
            if running:
                state.generator_run_hours[gen.asset_id] = (
                    state.generator_run_hours.get(gen.asset_id, 0.0) + dt
                )
                state.generator_current_run_h[gen.asset_id] = (
                    state.generator_current_run_h.get(gen.asset_id, 0.0) + dt
                )
            else:
                state.generator_current_run_h[gen.asset_id] = 0.0
            state.generator_running[gen.asset_id] = running
            state.generator_output_kw[gen.asset_id] = out
        state.hour = hour + dt

        # ---- 7. record ----------------------------------------------------
        step.pv_kw = pv_kw
        # Grid import and curtailment are settled from the energy balance rather
        # than from leftover headroom: unused grid capacity is not curtailment.
        bus_demand_kw = sum(served.values()) + battery_charge_kw - battery_discharge_kw
        must_take_kw = pv_kw + generation_kw
        if bus_demand_kw >= must_take_kw:
            step.grid_kw = min(grid_kw_limit, bus_demand_kw - must_take_kw)
            step.curtailed_kw = 0.0
        else:
            step.grid_kw = 0.0
            step.curtailed_kw = must_take_kw - bus_demand_kw
        step.battery_discharge_kw = battery_discharge_kw
        step.battery_charge_kw = battery_charge_kw
        step.battery_soc = state.battery_soc
        step.battery_energy_kwh = energy_kwh
        step.fuel_used_l = fuel_used
        step.fuel_remaining_l = state.fuel_remaining_l
        step.load_served_kw = served
        step.critical_served_kw = sum(served[l.asset_id] for l in critical_loads)
        step.secondary_served_kw = sum(served.get(l.asset_id, 0.0) for l in secondary_loads)
        step.unserved_critical_kw = max(0.0, step.critical_demand_kw - step.critical_served_kw)
        step.shed_load_ids = shed
        # Generators the operator could still commit: every serviceable set on the
        # node, not only those this configuration already deploys. That is the
        # whole point of the question - the fuel is reachable if somebody starts
        # one.
        startable = [
            gen for gen in self.inventory.generators if _usable(gen, hour, events)
        ]
        breakdown = self._reserve(
            battery,
            battery_active,
            energy_kwh,
            state.fuel_remaining_l,
            generators,
            startable,
            conversion_limit > EPS,
            step.critical_demand_kw,
        )
        step.reserve_kwh = breakdown.reserve_kwh
        step.reserve_hours = breakdown.reserve_hours
        step.reserve_withheld_kwh = breakdown.withheld_kwh
        step.reserve_questions = list(breakdown.questions)
        if step.unserved_critical_kw > EPS:
            step.notes.append(
                f"CRITICAL LOAD NOT FULLY SERVED: shortfall {step.unserved_critical_kw:.1f} kW."
            )
        return step

    # -- generator commitment and loading -----------------------------------

    def _may_stop(
        self, generators: list[Generator], state: NodeState, policy: DispatchPolicy
    ) -> bool:
        """Has every running set been on long enough to be stopped again?

        The second clause of the COAST rule. Without it the stop rule cycles a
        set whenever the battery is briefly able to carry the node, which on
        MM-DEMO-001 turns two starts into seventeen. Start wear is a real cost
        that this demonstrator does not model, so the rule is stated with the
        clause and RQ-015 measures what the clause costs in fuel.
        """

        if policy.min_run_hours <= 0.0:
            return True
        for gen in generators:
            if not state.generator_running.get(gen.asset_id, False):
                continue
            if state.generator_current_run_h.get(gen.asset_id, 0.0) < policy.min_run_hours - EPS:
                return False
        return True

    def _dispatch_generators(
        self,
        generators: list[Generator],
        target_kw: float,
        state: NodeState,
        ambient: float,
        dt: float,
        fuel_available_l: float,
        mode: GeneratorMode,
        forced_ids: frozenset[str] = frozenset(),
        commit_target_kw: float | None = None,
    ) -> tuple[dict[str, float], float, list[str]]:
        outputs: dict[str, float] = {}
        notes: list[str] = []
        if not generators:
            return outputs, 0.0, notes

        merit = sorted(generators, key=lambda g: g.specific_fuel_l_per_kwh())

        if mode is GeneratorMode.CONTINUOUS:
            committed = list(merit)
        else:
            # Anything the plan committed is synchronised and runs. Merit order
            # then tops up from what is left, only as far as the hour needs.
            committed = [gen for gen in merit if gen.asset_id in forced_ids]
            capacity = sum(self._max_output(gen, state, ambient, dt) for gen in committed)
            # Sized on what the load needs. Under COAST the target also carries a
            # battery top-up, and letting that decide the commitment starts a
            # second set whose no-load fuel costs more than the top-up saves.
            needed = target_kw if commit_target_kw is None else commit_target_kw
            for gen in merit:
                if gen.asset_id in forced_ids:
                    continue
                if capacity >= needed - EPS:
                    break
                committed.append(gen)
                capacity += self._max_output(gen, state, ambient, dt)
                notes.append(
                    f"{gen.asset_id} started although the plan did not commit it: the hour "
                    "needed more than the committed set could make."
                )
            if target_kw <= EPS and not forced_ids:
                committed = []

        remaining_target = target_kw
        fuel_left = fuel_available_l
        fuel_used = 0.0
        for gen in committed:
            max_out = self._max_output(gen, state, ambient, dt)
            if max_out <= EPS:
                continue
            min_out = min(gen.min_loading_kw(ambient), max_out)
            want = max(0.0, min(remaining_target, max_out))
            if want < min_out:
                want = min_out
            affordable = (fuel_left / dt - gen.no_load_l_per_h) / gen.marginal_l_per_kwh
            if affordable <= EPS:
                notes.append(f"{gen.asset_id} could not run: fuel exhausted.")
                continue
            out = min(want, affordable)
            if out < min_out - EPS:
                notes.append(
                    f"{gen.asset_id} run below minimum loading ({out:.1f} kW) because fuel is "
                    "nearly exhausted."
                )
            burn = gen.fuel_for(out, dt)
            fuel_left -= burn
            fuel_used += burn
            outputs[gen.asset_id] = out
            remaining_target -= out
            if out > want + EPS:
                notes.append(
                    f"{gen.asset_id} held at minimum loading; surplus available for the battery."
                )
        if remaining_target > EPS and committed:
            notes.append(
                f"Committed generation short of target by {remaining_target:.1f} kW."
            )
        return outputs, fuel_used, notes

    def _max_output(self, gen: Generator, state: NodeState, ambient: float, dt: float) -> float:
        """Derated capacity, reduced on the first step after a start.

        ASSUMED: a set that needs ``startup_time_min`` to come on line can only
        deliver for the remainder of the step in which it is started. At a
        one-hour resolution this is a small correction; it is kept because it
        makes the cost of stop/start cycling visible.
        """

        max_out = gen.max_output_kw(ambient)
        if not state.generator_running.get(gen.asset_id, False) and gen.startup_time_min > 0:
            usable_fraction = max(0.0, 1.0 - gen.startup_time_min / (dt * 60.0))
            max_out *= usable_fraction
        return max_out

    def _conversion_limit(
        self, active: set[str], hour: float, events: Sequence[FailureEvent]
    ) -> float:
        units = [
            unit
            for unit in self.inventory.power_conversion
            if unit.asset_id in active and _usable(unit, hour, events)
        ]
        if not units:
            # No conversion/distribution unit in the configuration: the node
            # cannot transfer power at all.
            return 0.0
        return sum(unit.capacity_kw for unit in units)

    # -- reserve ------------------------------------------------------------

    def _reserve(
        self,
        battery: Battery | None,
        battery_active: bool,
        energy_kwh: float,
        fuel_l: float,
        committed_generators: list[Generator],
        startable_generators: list[Generator],
        conversion_available: bool,
        critical_kw: float,
    ) -> ReserveBreakdown:
        """Energy reserve, split into what this configuration can reach and what it cannot.

        The rule: **only energy the configuration can actually deliver counts
        towards the reserve.** Stored energy in a battery the configuration does
        not connect is not reserve, and fuel with no generator committed to burn
        it is not reserve either.

        Energy that exists but cannot be reached is not silently dropped and not
        counted as zero: it is returned as a withheld quantity with an
        :class:`OpenQuestion` attached, so the operator can see both the number
        and the reason it does not count.

        ASSUMED (AS-005): unburned fuel is converted to kWh using the best
        reachable generator's specific fuel consumption at 75 % loading. This
        overstates the reserve slightly if the set ends up lightly loaded.
        """

        questions: list[OpenQuestion] = []
        stored = withheld_stored = 0.0
        fuel_kwh = withheld_fuel = 0.0

        usable_stored = 0.0
        if battery is not None:
            usable_stored = max(
                0.0, energy_kwh - battery.energy_capacity_kwh * battery.min_state_of_charge
            )

        if not conversion_available:
            # Nothing can leave the bus, so nothing on the node is reserve.
            withheld_stored = usable_stored
            withheld_fuel = self._fuel_energy_kwh(fuel_l, startable_generators)
            if withheld_stored + withheld_fuel > EPS:
                questions.append(
                    OpenQuestion(
                        key="RESERVE_NO_POWER_CONVERSION",
                        topic="reserve",
                        question=(
                            "No serviceable power conversion and distribution unit is in this "
                            "configuration. Can one be restored or replaced?"
                        ),
                        impact=(
                            "Until it is, no stored energy and no fuel on site can reach any "
                            "load, whatever else is running."
                        ),
                        withheld_kwh=withheld_stored + withheld_fuel,
                    )
                )
        else:
            if battery_active:
                stored = usable_stored
            elif usable_stored > EPS:
                withheld_stored = usable_stored
                questions.append(
                    OpenQuestion(
                        key="RESERVE_BATTERY_NOT_DEPLOYED",
                        topic="reserve",
                        question=(
                            f"{battery.asset_id if battery else 'The battery'} holds "
                            f"{usable_stored:.0f} kWh above its floor but is not deployed in this "
                            "configuration. Should it be?"
                        ),
                        impact=(
                            "Deploying it would add that energy to the reserve and give the node "
                            "ride-through if a generator stops."
                        ),
                        withheld_kwh=usable_stored,
                    )
                )

            if committed_generators and fuel_l > 0:
                fuel_kwh = self._fuel_energy_kwh(fuel_l, committed_generators)
            elif fuel_l > 0:
                withheld_fuel = self._fuel_energy_kwh(fuel_l, startable_generators)
                if startable_generators:
                    questions.append(
                        OpenQuestion(
                            key="RESERVE_NO_GENERATOR_COMMITTED",
                            topic="reserve",
                            question=(
                                f"{fuel_l:.0f} L of fuel is on site but no generator is committed "
                                "to burn it. Should one be committed?"
                            ),
                            impact=(
                                f"Committing "
                                f"{', '.join(g.asset_id for g in startable_generators)} would make "
                                f"about {withheld_fuel:.0f} kWh available as reserve."
                            ),
                            withheld_kwh=withheld_fuel,
                        )
                    )
                else:
                    questions.append(
                        OpenQuestion(
                            key="RESERVE_NO_SERVICEABLE_GENERATOR",
                            topic="reserve",
                            question=(
                                f"{fuel_l:.0f} L of fuel is on site and no generator is "
                                "serviceable. Can one be repaired or brought forward?"
                            ),
                            impact="Without one, the fuel on site is not energy the node can use.",
                            withheld_kwh=0.0,
                        )
                    )

        reserve = stored + fuel_kwh
        hours = reserve / critical_kw if critical_kw > EPS else float("inf")
        return ReserveBreakdown(
            reserve_kwh=reserve,
            reserve_hours=hours,
            stored_kwh=stored,
            fuel_kwh=fuel_kwh,
            withheld_kwh=withheld_stored + withheld_fuel,
            questions=tuple(questions),
        )

    @staticmethod
    def _fuel_energy_kwh(fuel_l: float, generators: list[Generator]) -> float:
        """Fuel converted to kWh through the most efficient of ``generators``."""

        if fuel_l <= 0.0 or not generators:
            return 0.0
        best = min(generators, key=lambda g: g.specific_fuel_l_per_kwh())
        return fuel_l / best.specific_fuel_l_per_kwh()

    # -- post-processing ----------------------------------------------------

    def _finalise(self, result: SimulationResult) -> None:
        for step in result.steps:
            if step.critical_shortfall_kwh > UNSERVED_TOLERANCE_KWH:
                result.first_critical_shortfall_hour = step.hour
                result.endurance_hours = max(0.0, step.hour - result.start_hour)
                result.endurance_limited_by = self._limiting_factor(result, step)
                return
        result.endurance_hours = result.horizon_h
        result.endurance_limited_by = "NOT_LIMITED_WITHIN_MISSION"

    def _limiting_factor(self, result: SimulationResult, step: StepRecord) -> str:
        if step.fuel_remaining_l <= 1.0:
            return "FUEL_EXHAUSTED"
        if step.battery_energy_kwh <= 0.5 and step.generator_total_kw <= EPS:
            return "NO_GENERATION_AVAILABLE"
        if step.generator_total_kw > EPS:
            return "GENERATION_CAPACITY"
        return "SUPPLY_UNAVAILABLE"


def simulate(
    mission: MissionSpec,
    configuration: Configuration,
    *,
    environment: Environment | None = None,
    inventory: AssetInventory | None = None,
    start_hour: float | None = None,
    end_hour: float | None = None,
    initial_state: NodeState | None = None,
    events: Iterable[FailureEvent] = (),
) -> SimulationResult:
    """Convenience wrapper around :class:`Simulator`."""

    return Simulator(mission, environment, inventory).run(
        configuration,
        start_hour=start_hour,
        end_hour=end_hour,
        initial_state=initial_state,
        events=events,
    )
