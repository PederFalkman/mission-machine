# Mission Machine

**Pack 1 - research demonstrator.**
Mission requirements, available assets and uncertain conditions in; feasible
mission-support infrastructure configurations, their trade-offs, and a
reconfiguration path when something breaks, out. The operator decides.

> **SYNTHETIC DATA - SIMULATED RESULTS - UNVALIDATED.**
> Mission Machine is a research demonstrator. It has no military validation, no
> operational readiness status, and no requirement basis from any armed forces
> organisation. All bundled data is invented. See
> [`docs/evidence-rules.md`](docs/evidence-rules.md).

---

## What it does

Mission Machine is not a container product and not a dispatch controller. It
answers one question: *can a mission be translated automatically into robust
infrastructure configurations, while the human stays the decision authority?*

Give it a machine-readable mission - what must keep running, for how long, with
what on site, under what limits - and it will:

* generate several **feasible configurations**, one per stated objective;
* report each on **ten separate dimensions**, with no composite score;
* say **why** one leads, what it **costs** against the others, and how much of
  that rests on **assumptions**;
* re-assess the mission when an asset fails, showing **what changed, why it
  matters, what the options are and what they trade**;
* and never decide. Every recommendation carries
  `operator_decision_required = True`, and every selection is logged with
  whether it followed the recommendation.

## Run it

Python 3.10 or later. No dependencies, no build step, no network.

```bash
python3 -m mission_machine demo      # the whole demonstration flow, ~3 seconds
python3 -m mission_machine serve     # the UI on http://127.0.0.1:8000
```

Other commands:

```bash
python3 -m mission_machine mission             # the mission definition and assets
python3 -m mission_machine configure           # generate and compare configurations
python3 -m mission_machine operate --at 30 --scenario SC-DEGRADED-001
python3 -m mission_machine verify              # check the plans against the MILP model
python3 -m mission_machine export-lp --out mm.lp   # the formulation, for any solver
python3 -m mission_machine assumptions         # what the results rest on
python3 -m unittest discover -s tests          # 82 tests, ~21 s
```

Add `--json` to any command for machine-readable output.

## The first scenario - MM-DEMO-001

A dispersed support node has to hold four critical functions - communications,
command and IT, medical, and shelter cooling - for 72 hours. Four more functions
are discretionary. There is 520 L of fuel and no resupply. Host-nation power is
available for 39 % of the mission and disappears twice. Two generators, a
120 kWh battery, an optional mobile PV array, one power conversion unit.

From 312 candidate configurations the engine puts forward three:

| | OPTION A - max endurance | OPTION B - min fuel | OPTION C - min logistics |
| --- | --- | --- | --- |
| Endurance | 72 h | 72 h | 72 h |
| Fuel used | 418 L | **398 L** | 439 L |
| Minimum reserve | 15.5 h | **16.6 h** | 13.0 h |
| Ride-through after losing the largest generator | **42.8 h** | 3.0 h | 0 h |
| Active assets | 10 | 9 | **7** |

*SIMULATED from SYNTHETIC data. Reproduce with `python3 -m mission_machine demo`.*

All three complete the mission and none serves any discretionary load. The
machine reports separately that the discretionary functions *could* be supported
for 24 more litres and 6.7 fewer hours of reserve - and leaves that trade to the
operator.

Then Generator B fails at H+30. Critical functions are still supported, but
tolerance to losing the next generator falls from 42.8 h to 3.0 h and GEN-A
becomes a single point of failure. The system reports a change in *assurance*
rather than raising a false alarm or staying quiet.

In the compound scenario - generator lost *and* host-nation power not restored -
no reconfiguration of supply saves the mission. The system establishes that, and
names the one action that does work: accepting a degraded cooling setpoint, worth
an extra hour of assured support and 2.3 h of reserve, ready in ten minutes. It
does not take it.

## How it is built

```
mission/       what must keep running       planning/        candidate configurations, metrics, MILP
assets/        what things can do           simulation/      deterministic hour-by-hour dispatch
environment/   weather, grid, uncertainty   resilience/      failures, SPOF, recovery options
evidence/      labels and provenance        explainability/  why, trade-offs, confidence, assumptions
ui/            four screens                 operations/      the operating picture and the decision log
```

The planner is a **deterministic baseline**: enumerate an explicit space of
configurations, simulate each one hour by hour, rank with a stated key per
objective. The same problem is also **declared as a mixed-integer linear
program** (`planning/milp.py`) - 1 368 variables, 937 constraints for a 72-hour
mission - which exports to LP format for any solver and is used to *verify* every
schedule the planner produces. If the simulator ever drifts from the declared
physics, `mission-machine verify` fails.

No machine learning is used in the planning path, deliberately. See
[`docs/architecture.md`](docs/architecture.md).

## Documentation

| Document | What is in it |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Module boundaries, interfaces, and the design decisions worth arguing about |
| [`docs/research/questions.md`](docs/research/questions.md) | RQ-001 to RQ-006 with what Pack 1 actually found, and four new questions |
| [`docs/assumptions.md`](docs/assumptions.md) | Every assumption that moves a number, generated from the register in code |
| [`docs/reuse-assessment.md`](docs/reuse-assessment.md) | RODOT (inspected from source) and Solid Soup (could not be located) |
| [`docs/evidence-rules.md`](docs/evidence-rules.md) | The labels, what may never be claimed, and how that is enforced in tests |
| [`docs/demo-script.md`](docs/demo-script.md) | The five-minute demonstration, with what to say |
| [`docs/pack1-deliverables.md`](docs/pack1-deliverables.md) | Deliverables, every file added, and the recommendation for Pack 2 |

## Scope limits

Pack 1 models disruption **only** as asset unavailability. There is no adversary
model, no targeting, no offensive capability, and no signature or detectability
modelling. Resilience results describe equipment failure; they say nothing about
survivability in a contested environment.
