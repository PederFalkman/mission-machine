# Architecture note

Mission Machine translates a mission statement, an asset inventory and an
uncertain environment into several feasible infrastructure configurations, and
re-translates them when something breaks. This note describes how it is put
together and, more importantly, why the boundaries fall where they do.

## Shape

```
         MissionSpec            AssetInventory          Environment
              |                       |                      |
              +-----------+-----------+----------------------+
                          |
                   PlanningEngine  ── enumerate candidate Configurations
                          |
                     Simulator     ── evaluate each one, hour by hour
                          |
                 ConfigurationMetrics
                          |
        +-----------------+------------------+-------------------+
        |                 |                  |                   |
  ResilienceAnalyst  Explainability     MilpModel           OperationsSession
  (SPOF, recovery)   (why, trade-offs,  (declared           (run, disrupt,
                      confidence)        constraints,        reassess, record
                                         verification)       the decision)
                          |
                        UI / CLI
```

## Modules and their boundaries

| Module | Owns | Must not |
| --- | --- | --- |
| `mission/` | What the mission requires: `MissionSpec`, validation, the bundled mission library | Know how power is produced |
| `assets/` | What things are and what they can do: `Asset` and subclasses, `AssetInventory` | Know what the mission wants |
| `environment/` | Conditions outside the node: weather, ambient temperature, grid availability, perturbation variants | Know about assets or missions |
| `planning/` | `Configuration`, `DispatchPolicy`, the `PlanningEngine`, output metrics, and the MILP formulation | Decide anything on the operator's behalf |
| `simulation/` | Deterministic hour-by-hour dispatch and the full `StepRecord` trace | Contain planning policy or scoring |
| `resilience/` | `FailureEvent`, `Scenario`, single-point-of-failure analysis, recovery options | Choose a response |
| `operations/` | The operating picture: `MissionAssessment`, `ReconfigurationReport`, `OperatorDecision` | Hide a change from the operator |
| `explainability/` | Why an option leads, what it costs, how confident to be, what it assumes | Invent a reason that is not computed |
| `evidence/` | Labels and provenance | Be optional |
| `ui/` | Four screens and a small JSON API | Contain domain logic |

The dependency direction is one-way: `evidence` <- `assets` <- `environment` <-
`mission` <- `planning` <- `simulation` <- `resilience` <- `explainability` <-
`operations` <- `ui`. Nothing imports upwards. The domain modules have no
dependency on the UI, the CLI, or each other's internals.

## Interfaces

The types named in the Pack 1 brief exist as concrete classes:

| Interface | Where | Note |
| --- | --- | --- |
| `MissionSpec` | `mission/spec.py` | JSON-backed, validates itself, reports problems rather than raising |
| `Asset` | `assets/base.py` | Generic base; `Generator`, `Battery`, `GridConnection`, `SolarPV`, `PowerConversion`, `Load`, `CoolingSystem`, `ChargingSystem`, `CommunicationsLoad` extend it |
| `Environment` | `environment/model.py` | Also produces perturbed variants for sensitivity analysis |
| `Configuration` | `planning/configuration.py` | Active assets plus an explicit, readable `DispatchPolicy` |
| `PlanningEngine` | `planning/engine.py` | Enumerate, simulate, rank - one option per named strategy |
| `Scenario`, `FailureEvent` | `resilience/failures.py` | Disruption as asset unavailability only |
| `MissionAssessment` | `operations/session.py` | The operating picture at a point in time |
| `Recommendation` | `explainability/explain.py` | Always carries `operator_decision_required = True` |

## Design decisions worth arguing about

### The optimiser proposes; it never decides

`Recommendation` cannot be constructed without `operator_decision_required`, and
`OperationsSession` records an `OperatorDecision` for every selection, including
whether it followed the recommendation. A system that quietly reconfigured the
node would be a different product with a different risk profile, and the code is
arranged so that building that by accident is hard.

### No composite mission-assurance score

The brief asks for the dimensions separately, and Pack 1 keeps them separate.
A single number would have to weight endurance against fuel against redundancy
against logistics burden, and those weights *are* the command judgement. The
COMPARE screen marks the best value on each dimension and says explicitly that
there is no total. `tests/test_planning.py` fails if a composite score appears in
the metrics payload.

A score could be added later only if its semantics are explicit, its weights are
configurable and visible, and the uncertainty behind each dimension is shown
alongside it. None of those three conditions is met today.

### Deterministic enumerate-and-simulate as the baseline, with a declared MILP

The brief asks for transparent optimisation - LP / MILP / CP-SAT - before any
machine learning. Pack 1 does two things rather than one:

1. **The baseline** enumerates a small, explicit space of configurations (312 for
   MM-DEMO-001), simulates each deterministically, and ranks them with a stated
   lexicographic key per strategy. It runs anywhere Python runs, needs no solver,
   and produces a schedule an operator can read line by line.
2. **The model is declared anyway.** `planning/milp.py` states the same problem
   as a mixed-integer linear program - variables, constraints, objective - as
   data. It exports to LP format for CBC, HiGHS, Gurobi or OR-Tools, and it can
   *verify* a schedule: every option the engine offers is checked against the
   declared constraint set before it is shown.

This keeps the heuristic honest. If the simulator ever drifts from the physics
the model declares, `mission-machine verify` fails. Constraints are tagged
`physics` (the node cannot behave otherwise) or `requirement` (the mission asks
for it), so an infeasible *mission* is reported differently from a broken
*model*.

The obvious next step is to solve the MILP rather than rank an enumeration. That
is deliberately Pack 2 work: the enumeration is the thing that made the
demonstrator explainable, and the MILP formulation now exists to replace it
without changing any interface.

### Three claims are enforced by tests rather than asserted

A demonstrator that says it is standalone, that it cannot command anything, and
that everything it shows is synthetic, is making three claims a reader cannot
check by reading. Each is therefore a test, adapted from capacity-machine (see
`docs/reuse-assessment.md`):

* `tests/test_standalone.py` boots the demonstrator in an interpreter with no
  `PYTHONPATH` and no user site directory, and scans every source file: nothing
  third-party may be imported at module level, an optional solver backend may be
  imported inside a function only if it is declared as an extra, nothing may
  import RODOT or capacity-machine, and nothing outside `ui/` may import a
  networking module.
* `tests/test_no_control_path.py` fails if any public callable anywhere in the
  package reads as an actuation - `dispatch`, `activate`, `set_output`,
  `close_breaker` and the rest - or if the API grows a route beyond the five that
  plan, assess and record a decision. `CONTROL_PATH_ENABLED` stays False.
  The policy lives in `evidence/control.py`, with its one exemption
  (`Simulator._dispatch_generators`, which models dispatch and is private) listed
  explicitly so that adding another is a visible act.
* `tests/test_synthetic_labelling.py` runs the whole flow - plan, select, run,
  disrupt, replan - and walks every resulting payload, asserting that no labelled
  value anywhere comes back as operational truth. `is_operational_truth` is
  computed from the labels rather than asserted, so the claim can fail.

The third of these found a real gap on the day it was written: `Recommendation`,
the most operator-facing object in the system, carried the disclaimer but no
evidence labels.

### Energy counts only when the configuration can reach it

The ENERGY_RESERVE metric counts stored energy only when the configuration
deploys the battery, and fuel only when a generator is committed to burn it.
Energy the node holds but cannot reach is reported as a *withheld* quantity with
an `OpenQuestion` attached (`evidence/questions.py`), never folded into the total
and never rendered as zero - because zero is a measurement and absence is not.

This distinction is why OPTION C reports 9.3 h of reserve and 96 kWh withheld
rather than 13.0 h. Note that the MILP's reserve constraint has a deliberately
different scope: the model is free to use every asset on the node, so it counts
every asset's energy. The metric describes one chosen configuration.

### Failure is modelled as unavailability, and nothing else

No adversary, no targeting, no cascade, no signature. This is a scope decision
rather than a modelling gap, and it is registered as AS-010. It means resilience
results in Pack 1 describe equipment failure, not survivability.

### The simulator records everything

Every time step keeps supply by source, per-load served power, state of charge,
fuel, reserve, shed loads and free-text notes. Nothing is aggregated away before
the operator sees it, because the aggregate is where a planning tool hides its
mistakes.

## Performance

For MM-DEMO-001 (72 one-hour steps, 6 supply assets, 8 loads):

| Operation | Simulations | Time |
| --- | --- | --- |
| Generate three options | 312 + 3 | ~2 s |
| Single-point-of-failure analysis per option | ~12 | ~0.1 s |
| Recovery options per configuration | 2-6 | ~0.05 s |
| Sensitivity sweep for one option | 4 | ~0.03 s |
| Full test suite (106 tests) | several thousand | ~33 s |

The candidate space grows exponentially in the number of dispatchable assets.
This is fine at demonstrator scale and is registered as RQ-009.

## Running it

```
python3 -m mission_machine demo         # the whole demonstration flow, ~3 s
python3 -m mission_machine serve        # the UI on http://127.0.0.1:8000
python3 -m mission_machine verify       # check the plans against the MILP model
python3 -m unittest discover -s tests   # the test suite
```

No dependencies beyond the Python standard library (3.10+). Optional MILP solver
backends are declared in `pyproject.toml` under the `milp` and `cpsat` extras and
are not required.
