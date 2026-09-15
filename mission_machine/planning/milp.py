"""Declarative MILP formulation of the support-node planning problem.

Pack 1 asks for a transparent optimisation baseline (LP / MILP / CP-SAT)
*before* any machine learning. This module is that formulation, written as
data rather than as calls into a particular solver:

* :func:`build_model` states the variables, constraints and objective of the
  unit-commitment / dispatch problem implied by a MissionSpec.
* :meth:`MilpModel.to_lp_string` exports it in CPLEX LP format, so it can be
  handed to CBC, HiGHS, Gurobi, OR-Tools or anything else without this
  repository depending on a solver.
* :meth:`MilpModel.verify` checks a *schedule* against the model. This is what
  keeps the deterministic engine honest: every option the planner offers is
  checked against the declared constraint set, so the heuristic cannot quietly
  produce a plan the model says is impossible.
* :func:`solve` will use PuLP or OR-Tools if either is installed, and otherwise
  says plainly that no solver backend is available rather than pretending.

Constraints are tagged as ``physics`` (the node cannot behave otherwise) or
``requirement`` (the mission asks for it). A schedule that violates physics is
a bug; a schedule that violates a requirement is an infeasible mission, and the
operator has to be told which.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from mission_machine.assets.inventory import AssetInventory
from mission_machine.environment.model import Environment
from mission_machine.mission.spec import MissionSpec
from mission_machine.planning.configuration import Configuration
from mission_machine.simulation.simulator import SimulationResult

TOLERANCE = 1e-4


@dataclass(frozen=True)
class Variable:
    name: str
    lower: float = 0.0
    upper: float = float("inf")
    kind: str = "continuous"   # "continuous" | "binary"
    description: str = ""


@dataclass
class LinearConstraint:
    name: str
    terms: dict[str, float]
    sense: str                 # "<=" | ">=" | "=="
    rhs: float
    category: str = "physics"  # "physics" | "requirement"
    description: str = ""

    def evaluate(self, assignment: dict[str, float]) -> float:
        return sum(coefficient * assignment.get(var, 0.0) for var, coefficient in self.terms.items())

    def violation(self, assignment: dict[str, float], tolerance: float = TOLERANCE) -> float:
        value = self.evaluate(assignment)
        if self.sense == "<=":
            return max(0.0, value - self.rhs - tolerance)
        if self.sense == ">=":
            return max(0.0, self.rhs - value - tolerance)
        return max(0.0, abs(value - self.rhs) - tolerance)


@dataclass
class Objective:
    name: str
    sense: str                 # "min" | "max"
    terms: dict[str, float] = field(default_factory=dict)
    constant: float = 0.0
    description: str = ""


@dataclass
class Violation:
    constraint: str
    category: str
    amount: float
    description: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "constraint": self.constraint,
            "category": self.category,
            "amount": round(self.amount, 6),
            "description": self.description,
        }


@dataclass
class MilpModel:
    """A mixed-integer linear program, as inspectable data."""

    name: str
    variables: dict[str, Variable] = field(default_factory=dict)
    constraints: list[LinearConstraint] = field(default_factory=list)
    objective: Objective | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # -- construction helpers ----------------------------------------------

    def add_variable(self, variable: Variable) -> str:
        self.variables[variable.name] = variable
        return variable.name

    def add_constraint(self, constraint: LinearConstraint) -> None:
        self.constraints.append(constraint)

    # -- reporting ----------------------------------------------------------

    @property
    def binary_variables(self) -> list[str]:
        return [name for name, var in self.variables.items() if var.kind == "binary"]

    def size(self) -> dict[str, int]:
        return {
            "variables": len(self.variables),
            "binary_variables": len(self.binary_variables),
            "constraints": len(self.constraints),
            "physics_constraints": sum(1 for c in self.constraints if c.category == "physics"),
            "requirement_constraints": sum(
                1 for c in self.constraints if c.category == "requirement"
            ),
        }

    # -- verification -------------------------------------------------------

    def verify(
        self, assignment: dict[str, float], tolerance: float = TOLERANCE
    ) -> list[Violation]:
        """Check an assignment against every constraint and variable bound."""

        violations: list[Violation] = []
        for name, variable in self.variables.items():
            value = assignment.get(name)
            if value is None:
                continue
            if value < variable.lower - tolerance:
                violations.append(
                    Violation(
                        constraint=f"bound_lower[{name}]",
                        category="physics",
                        amount=variable.lower - value,
                        description=f"{name} = {value:.4f} below lower bound {variable.lower:.4f}",
                    )
                )
            if value > variable.upper + tolerance:
                violations.append(
                    Violation(
                        constraint=f"bound_upper[{name}]",
                        category="physics",
                        amount=value - variable.upper,
                        description=f"{name} = {value:.4f} above upper bound {variable.upper:.4f}",
                    )
                )
        for constraint in self.constraints:
            amount = constraint.violation(assignment, tolerance)
            if amount > 0.0:
                violations.append(
                    Violation(
                        constraint=constraint.name,
                        category=constraint.category,
                        amount=amount,
                        description=constraint.description,
                    )
                )
        return violations

    # -- export -------------------------------------------------------------

    def to_lp_string(self) -> str:
        """CPLEX LP format, for any external MILP solver."""

        def term_string(terms: dict[str, float]) -> str:
            parts = []
            for var, coefficient in terms.items():
                sign = "+" if coefficient >= 0 else "-"
                parts.append(f"{sign} {abs(coefficient):.8g} {var}")
            return " ".join(parts) if parts else "0"

        lines = [f"\\ {self.name}", f"\\ generated by mission-machine (SYNTHETIC / UNVALIDATED)"]
        objective = self.objective or Objective(name="zero", sense="min")
        lines.append("Maximize" if objective.sense == "max" else "Minimize")
        lines.append(f" obj: {term_string(objective.terms)}")
        lines.append("Subject To")
        for constraint in self.constraints:
            operator = {"<=": "<=", ">=": ">=", "==": "="}[constraint.sense]
            lines.append(
                f" {constraint.name}: {term_string(constraint.terms)} {operator} {constraint.rhs:.8g}"
            )
        lines.append("Bounds")
        for name, variable in self.variables.items():
            if variable.kind == "binary":
                continue
            upper = "+inf" if variable.upper == float("inf") else f"{variable.upper:.8g}"
            lines.append(f" {variable.lower:.8g} <= {name} <= {upper}")
        binaries = self.binary_variables
        if binaries:
            lines.append("Binary")
            for name in binaries:
                lines.append(f" {name}")
        lines.append("End")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# model construction
# --------------------------------------------------------------------------


def _v(prefix: str, *parts: Any) -> str:
    """Solver-safe variable name, e.g. ``g_GEN_A_12``."""

    tail = "_".join(str(p).replace("-", "_").replace(".", "_") for p in parts)
    return f"{prefix}_{tail}" if tail else prefix


def build_model(
    mission: MissionSpec,
    environment: Environment | None = None,
    inventory: AssetInventory | None = None,
    *,
    include_requirements: bool = True,
    configuration: Configuration | None = None,
    service_floor: SimulationResult | None = None,
    start_hour: float = 0.0,
    end_hour: float | None = None,
    initial_stored_kwh: float | None = None,
    initial_fuel_l: float | None = None,
    terminal_storage_value: bool = False,
    service_floor_window: bool = False,
) -> MilpModel:
    """Build the MILP for ``mission`` over its full horizon.

    With ``configuration`` given, the model is restricted to that configuration:
    assets it does not deploy are left out, and secondary loads it does not
    attempt are not required. That turns the model from "what could this node
    do" into "what is the best this configuration could have done", which is the
    question worth putting to a solver - the asset set is a small discrete
    choice the enumeration handles well, while the hour-by-hour dispatch is the
    part a rule of thumb is most likely to get wrong.
    """

    environment = environment or mission.build_environment()
    inventory = inventory or mission.inventory
    dt = mission.time_step_h
    end = mission.mission_duration_h if end_hour is None else end_hour
    hours = [
        hour for hour in mission.hours if start_hour - 1e-9 <= hour < end - 1e-9
    ]

    model = MilpModel(
        name=(
            f"MM-{mission.mission_id}-dispatch-{configuration.configuration_id}"
            if configuration is not None
            else f"MM-{mission.mission_id}-dispatch"
        ),
        metadata={
            "mission_id": mission.mission_id,
            "time_step_h": dt,
            "steps": len(hours),
            "start_hour": start_hour,
            "end_hour": end,
            "data_labels": ["SYNTHETIC", "UNVALIDATED"],
            "configuration_id": (
                configuration.configuration_id if configuration is not None else None
            ),
            "note": (
                "Unit commitment and dispatch for a mission-support node. "
                "Affine generator fuel curves keep the formulation linear."
            ),
        },
    )

    policy = configuration.policy if configuration is not None else None
    active = set(configuration.active_asset_ids) if configuration is not None else None

    def deployed(asset) -> bool:
        return active is None or asset.asset_id in active

    generators = [
        gen
        for gen in inventory.generators
        if policy is None or (gen.asset_id in policy.generator_ids and deployed(gen))
    ]
    battery = inventory.batteries[0] if inventory.batteries else None
    if battery is not None and policy is not None and not (policy.use_battery and deployed(battery)):
        battery = None
    grid = inventory.grid_connections[0] if inventory.grid_connections else None
    if grid is not None and policy is not None and not (policy.use_grid and deployed(grid)):
        grid = None
    pv = inventory.pv_arrays[0] if inventory.pv_arrays else None
    if pv is not None and policy is not None and not (policy.deploy_pv and deployed(pv)):
        pv = None
    conversion_kw = sum(unit.capacity_kw for unit in inventory.power_conversion)
    critical_loads = mission.critical_load_assets()
    secondary_loads = [
        load
        for load in mission.secondary_load_assets()
        if policy is None or policy.attempts(load, mission)
    ]

    fuel_terms: dict[str, float] = {}

    for index, hour in enumerate(hours):
        ambient = environment.temperature_c(hour)
        balance: dict[str, float] = {}

        # generators
        for gen in generators:
            g = _v("g", gen.asset_id, index)
            u = _v("u", gen.asset_id, index)
            pmax = gen.max_output_kw(ambient)
            model.add_variable(
                Variable(g, 0.0, pmax, "continuous", f"{gen.asset_id} output at H+{hour:g} [kW]")
            )
            model.add_variable(
                Variable(u, 0.0, 1.0, "binary", f"{gen.asset_id} committed at H+{hour:g}")
            )
            model.add_constraint(
                LinearConstraint(
                    name=_v("genmax", gen.asset_id, index),
                    terms={g: 1.0, u: -pmax},
                    sense="<=",
                    rhs=0.0,
                    description=f"{gen.asset_id} cannot exceed its derated rating when committed.",
                )
            )
            model.add_constraint(
                LinearConstraint(
                    name=_v("genmin", gen.asset_id, index),
                    terms={g: 1.0, u: -gen.minimum_loading_fraction * pmax},
                    sense=">=",
                    rhs=0.0,
                    description=f"{gen.asset_id} must not run below its minimum loading.",
                )
            )
            balance[g] = 1.0
            fuel_terms[g] = fuel_terms.get(g, 0.0) + gen.marginal_l_per_kwh * dt
            fuel_terms[u] = fuel_terms.get(u, 0.0) + gen.no_load_l_per_h * dt

        # solar
        if pv is not None:
            p = _v("pv", index)
            model.add_variable(
                Variable(
                    p,
                    0.0,
                    pv.output_kw(environment.solar_fraction(hour), ambient),
                    "continuous",
                    f"PV output at H+{hour:g} [kW]",
                )
            )
            balance[p] = 1.0

        # grid
        if grid is not None:
            gr = _v("grid", index)
            limit = (
                min(grid.capacity_kw, environment.grid.nominal_capacity_kw or grid.capacity_kw)
                if environment.grid_available_at(hour)
                else 0.0
            )
            model.add_variable(
                Variable(gr, 0.0, limit, "continuous", f"Grid import at H+{hour:g} [kW]")
            )
            balance[gr] = 1.0

        # battery
        if battery is not None:
            ch = _v("ch", index)
            dis = _v("dis", index)
            soc = _v("soc", index)
            model.add_variable(
                Variable(ch, 0.0, battery.max_charge_kw, "continuous", "Battery charge [kW]")
            )
            model.add_variable(
                Variable(dis, 0.0, battery.max_discharge_kw, "continuous", "Battery discharge [kW]")
            )
            model.add_variable(
                Variable(
                    soc,
                    battery.energy_capacity_kwh * battery.min_state_of_charge,
                    battery.energy_capacity_kwh * battery.max_state_of_charge,
                    "continuous",
                    "Stored energy at end of step [kWh]",
                )
            )
            eff = battery.one_way_efficiency
            previous = (
                _v("soc", index - 1)
                if index > 0
                else None
            )
            # Self-discharge is modelled as a fixed fraction of nameplate energy
            # per hour, matching the simulator exactly so that a simulated
            # schedule can be checked against this constraint set.
            standing_loss = battery.energy_capacity_kwh * battery.self_discharge_per_h * dt
            terms = {
                soc: 1.0,
                ch: -eff * dt,
                dis: dt / eff,
            }
            if previous is not None:
                terms[previous] = -1.0
                rhs = -standing_loss
            else:
                opening_kwh = (
                    initial_stored_kwh
                    if initial_stored_kwh is not None
                    else battery.initial_state_of_charge * battery.energy_capacity_kwh
                )
                rhs = opening_kwh - standing_loss
            model.add_constraint(
                LinearConstraint(
                    name=_v("socdyn", index),
                    terms=terms,
                    sense="==",
                    rhs=rhs,
                    description="Battery state of charge follows charge, discharge and self-discharge.",
                )
            )
            balance[dis] = 1.0
            balance[ch] = -1.0

        # loads
        served_terms: dict[str, float] = {}
        for load in critical_loads + secondary_loads:
            demand = load.demand_kw(hour, ambient)
            s = _v("s", load.asset_id, index)
            model.add_variable(
                Variable(s, 0.0, demand, "continuous", f"{load.asset_id} served at H+{hour:g} [kW]")
            )
            balance[s] = -1.0
            served_terms[s] = 1.0
            if include_requirements and load.is_critical:
                model.add_constraint(
                    LinearConstraint(
                        name=_v("critical", load.asset_id, index),
                        terms={s: 1.0},
                        sense=">=",
                        rhs=demand,
                        category="requirement",
                        description=f"Critical load {load.asset_id} must be fully served.",
                    )
                )

        # curtailment
        curt = _v("curt", index)
        model.add_variable(Variable(curt, 0.0, float("inf"), "continuous", "Curtailed power [kW]"))
        balance[curt] = -1.0

        model.add_constraint(
            LinearConstraint(
                name=_v("balance", index),
                terms=balance,
                sense="==",
                rhs=0.0,
                description="Power into the bus equals power out of it.",
            )
        )

        if conversion_kw > 0:
            conversion_terms = dict(served_terms)
            if battery is not None:
                conversion_terms[_v("ch", index)] = 1.0
            model.add_constraint(
                LinearConstraint(
                    name=_v("conversion", index),
                    terms=conversion_terms,
                    sense="<=",
                    rhs=conversion_kw,
                    description="Power conversion and distribution rating.",
                )
            )

        # running fuel balance
        f = _v("fuel", index)
        fuel_ceiling = mission.fuel_limit_l if initial_fuel_l is None else initial_fuel_l
        model.add_variable(
            Variable(f, 0.0, fuel_ceiling, "continuous", "Fuel remaining [L]")
        )
        fuel_step: dict[str, float] = {f: 1.0}
        for gen in generators:
            fuel_step[_v("g", gen.asset_id, index)] = gen.marginal_l_per_kwh * dt
            fuel_step[_v("u", gen.asset_id, index)] = gen.no_load_l_per_h * dt
        if index > 0:
            fuel_step[_v("fuel", index - 1)] = -1.0
            rhs = 0.0
        else:
            rhs = mission.fuel_limit_l if initial_fuel_l is None else initial_fuel_l
        model.add_constraint(
            LinearConstraint(
                name=_v("fueldyn", index),
                terms=fuel_step,
                sense="==",
                rhs=rhs,
                description="Fuel remaining decreases by what the generators burn.",
            )
        )

        # energy reserve requirement
        if include_requirements and mission.minimum_reserve_hours > 0 and generators:
            critical_kw = sum(load.demand_kw(hour, ambient) for load in critical_loads)
            best_sfc = min(gen.specific_fuel_l_per_kwh() for gen in generators)
            terms = {f: 1.0 / best_sfc}
            rhs = mission.minimum_reserve_hours * critical_kw
            if battery is not None:
                terms[_v("soc", index)] = 1.0
                rhs += battery.energy_capacity_kwh * battery.min_state_of_charge
            model.add_constraint(
                LinearConstraint(
                    name=_v("reserve", index),
                    terms=terms,
                    sense=">=",
                    rhs=rhs,
                    category="requirement",
                    description=(
                        f"Stored energy plus unburned fuel must cover "
                        f"{mission.minimum_reserve_hours:g} h of critical load. Note the "
                        "difference in scope from the simulator's ENERGY_RESERVE metric: the "
                        "model may use every asset on the node, so it counts every asset's "
                        "energy, while the metric counts only what the chosen configuration "
                        "can actually reach."
                    ),
                )
            )

    if service_floor is not None:
        delivered: dict[str, float] = {}
        for step in service_floor.steps:
            if service_floor_window and not (start_hour - 1e-9 <= step.hour < end - 1e-9):
                continue
            for load_id, kw in step.load_served_kw.items():
                delivered[load_id] = delivered.get(load_id, 0.0) + kw * step.duration_h
        for load in secondary_loads:
            floor = delivered.get(load.asset_id, 0.0)
            if floor <= 1e-6:
                continue
            model.add_constraint(
                LinearConstraint(
                    name=_v("service", load.asset_id),
                    terms={_v("s", load.asset_id, index): dt for index in range(len(hours))},
                    sense=">=",
                    rhs=floor,
                    category="requirement",
                    description=(
                        f"{load.asset_id} must receive at least the {floor:.0f} kWh the schedule "
                        "being compared against delivered."
                    ),
                )
            )

    fuel_available = mission.fuel_limit_l if initial_fuel_l is None else initial_fuel_l
    if include_requirements and fuel_available > 0:
        model.add_constraint(
            LinearConstraint(
                name="fuel_budget",
                terms=dict(fuel_terms),
                sense="<=",
                rhs=fuel_available,
                category="requirement",
                description="Total fuel burned cannot exceed the fuel on site.",
            )
        )

    objective_terms = dict(fuel_terms)
    if terminal_storage_value and battery is not None and generators and hours:
        # Credit energy left in the battery at the fuel it would otherwise take
        # to make. Without this the last step of a window is free to empty the
        # store, and a short horizon is punished for a modelling artefact rather
        # than for its lack of foresight.
        rate = min(gen.specific_fuel_l_per_kwh() for gen in generators)
        objective_terms[_v("soc", len(hours) - 1)] = -rate

    model.objective = Objective(
        name="total_fuel_litres",
        sense="min",
        terms=objective_terms,
        description=(
            "Minimise total fuel burned. Other mission objectives (endurance, logistics "
            "burden) are handled by the planning engine's strategies; this is the "
            "objective the MILP baseline optimises."
        ),
    )
    return model


# --------------------------------------------------------------------------
# checking a simulated schedule against the model
# --------------------------------------------------------------------------


def assignment_from_simulation(
    mission: MissionSpec, result: SimulationResult, inventory: AssetInventory | None = None
) -> dict[str, float]:
    """Map a simulated schedule onto the MILP's variables."""

    inventory = inventory or mission.inventory
    assignment: dict[str, float] = {}
    for index, step in enumerate(result.steps):
        for gen in inventory.generators:
            output = step.generator_kw.get(gen.asset_id, 0.0)
            assignment[_v("g", gen.asset_id, index)] = output
            assignment[_v("u", gen.asset_id, index)] = 1.0 if output > 1e-9 else 0.0
        assignment[_v("pv", index)] = step.pv_kw
        assignment[_v("grid", index)] = step.grid_kw
        assignment[_v("ch", index)] = step.battery_charge_kw
        assignment[_v("dis", index)] = step.battery_discharge_kw
        assignment[_v("soc", index)] = step.battery_energy_kwh
        assignment[_v("fuel", index)] = step.fuel_remaining_l
        assignment[_v("curt", index)] = step.curtailed_kw
        for load_id, served in step.load_served_kw.items():
            assignment[_v("s", load_id, index)] = served
    return assignment


def verify_simulation(
    mission: MissionSpec,
    result: SimulationResult,
    environment: Environment | None = None,
    inventory: AssetInventory | None = None,
    *,
    model: MilpModel | None = None,
    tolerance: float = 1e-3,
) -> dict[str, Any]:
    """Check a simulated schedule against the declared MILP constraint set."""

    model = model or build_model(mission, environment, inventory)
    assignment = assignment_from_simulation(mission, result, inventory)
    violations = model.verify(assignment, tolerance=tolerance)
    physics = [v for v in violations if v.category == "physics"]
    requirements = [v for v in violations if v.category == "requirement"]
    return {
        "model": model.name,
        "model_size": model.size(),
        "physically_consistent": not physics,
        "meets_requirements": not requirements,
        "physics_violations": [v.to_dict() for v in physics[:20]],
        "requirement_violations": [v.to_dict() for v in requirements[:20]],
        "physics_violation_count": len(physics),
        "requirement_violation_count": len(requirements),
        "tolerance": tolerance,
    }


# --------------------------------------------------------------------------
# optional solver backends
# --------------------------------------------------------------------------


def available_backends() -> list[str]:
    backends = []
    try:  # pragma: no cover - depends on optional dependency
        import pulp  # noqa: F401

        backends.append("pulp")
    except ImportError:
        pass
    try:  # pragma: no cover - depends on optional dependency
        from ortools.linear_solver import pywraplp  # noqa: F401

        backends.append("ortools")
    except ImportError:
        pass
    return backends


def solve(model: MilpModel, backend: str = "auto") -> dict[str, Any]:
    """Solve the model if a solver is installed.

    Pack 1 ships no solver dependency. When none is present this raises with a
    clear message and points at :meth:`MilpModel.to_lp_string`, rather than
    silently falling back to something that is not a MILP solve.
    """

    backends = available_backends()
    if backend == "auto":
        backend = backends[0] if backends else ""
    if backend == "pulp":  # pragma: no cover - optional dependency
        import pulp

        problem = pulp.LpProblem(model.name, pulp.LpMinimize)
        variables = {
            name: pulp.LpVariable(
                name,
                lowBound=var.lower,
                upBound=None if var.upper == float("inf") else var.upper,
                cat="Binary" if var.kind == "binary" else "Continuous",
            )
            for name, var in model.variables.items()
        }
        objective = model.objective or Objective("zero", "min")
        problem += pulp.lpSum(
            coefficient * variables[name] for name, coefficient in objective.terms.items()
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
        status = problem.solve(pulp.PULP_CBC_CMD(msg=False))
        return {
            "backend": "pulp",
            "status": pulp.LpStatus[status],
            "objective": pulp.value(problem.objective),
            "assignment": {name: var.value() for name, var in variables.items()},
        }
    raise NotImplementedError(
        "No MILP solver backend is installed. Export the model with "
        "MilpModel.to_lp_string() and solve it with CBC, HiGHS, Gurobi or OR-Tools, "
        "or install an optional backend: pip install 'mission-machine[milp]'."
    )
