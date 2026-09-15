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

* read the operator's **ranked priorities** as machine-readable intent, and
  serve the functions they asked for whenever a configuration can;
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
python3 -m mission_machine optimise            # solve the same configurations exactly and compare
python3 -m mission_machine foresight           # how much of the solver's edge is lookahead
python3 -m mission_machine forecast            # what a wrong forecast costs the controller
python3 -m mission_machine scaling             # where the candidate search stops being tractable
python3 -m mission_machine export-lp --out mm.lp   # the formulation, for any solver
python3 -m mission_machine assumptions         # what the results rest on
python3 -m unittest discover -s tests          # 154 tests, ~81 s
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
| Fuel used | 431 L | **406 L** | 420 L |
| Minimum reachable reserve | 14.0 h | **15.7 h** | 12.9 h |
| Ride-through after losing the largest generator | **42.8 h** | 3.0 h | 3.0 h |
| Active assets | 11 | 10 | **9** |

*SIMULATED from SYNTHETIC data. Reproduce with `python3 -m mission_machine demo`.*

OPTION C does not deploy the battery, so 96 kWh of stored energy it cannot reach
is **withheld** from its reserve rather than counted or silently dropped - the
system reports the quantity and the question it raises.

All three complete the mission, and all three keep UAS charging running -
because operator priority 4 says *"keep UAS charging available if it does not
threaten critical functions"*, and the planner reads that. Vehicle charging and
welfare HVAC stay off: priority 6 calls them discretionary. The machine reports
separately that those *could* be supported for 11 more litres and 5.2 fewer
hours of reserve, and leaves that trade to the operator.

OPTION A leads, and the reason is quoted from the mission rather than invented:
priority 1 says communications must **never** be interrupted, so the planner puts
configurations that survive the loss of their largest generator ahead of the
cheaper ones - 42.8 h of ride-through against the 4 h this node takes to deploy.
That costs 25 L more fuel than priority 5 would like, and the trade-off says so.

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

A solver is not a dependency, but it is a supported backend. `OptimisationProvider`
is the seam; CBC plugs into it when PuLP is installed, the registry reports every
backend it knows about whether or not anybody wired it, and the answer always
names what produced it. The demonstrator uses a solver to **measure** the
dispatch rules rather than to replace them:

| | Rule-based dispatch | Optimum, same configuration | Left on the table |
| --- | --- | --- | --- |
| OPTION A | 431.0 L | 334.6 L | **22.4 %** |
| OPTION B | 405.9 L | 357.3 L | 12.0 % |
| OPTION C | 420.3 L | 375.6 L | 10.6 % |

*Every solver answer was checked against the declared constraint set before being
quoted. Each solve stopped at a 60-second budget without proving optimality, so
the true optimum is no higher. Reproduce with `python3 -m mission_machine
optimise`.*

That solver had perfect foresight, so the obvious question is how much of the
gap is clairvoyance. Almost none of it:

| Lookahead | OPTION A | Gap recovered |
| --- | --- | --- |
| none (the dispatch rules) | 431.0 L | - |
| 6 h | 360.9 L | 73 % |
| **12 h** | **334.6 L** | **100 %** |
| whole mission | 334.6 L | 100 % |

A controller that can see half a day ahead captures the entire saving, and does
it in about two seconds of solving against the sixty the full-horizon solve
takes. All three options show the same threshold - 96-100 % recovered at 12 h -
and going further ahead buys nothing. What the rules give up is commitment logic
- which machine runs when - not foresight. Reproduce with
`python3 -m mission_machine foresight`.

And when the forecast is *wrong* - the plan expects host-nation supply back at
H+30 and it returns hours late, or never - between 65 % and 100 % of the saving
survives, with critical-load coverage untouched, because replanning every six
hours corrects the error before it compounds. The exception is the severe case,
and it points somewhere unexpected: when the grid never returns, the controller
*told the truth* completes the mission while the dispatch rules fail at H+69 -
but the same controller told the nominal forecast fails at H+70, buying one hour
over doing nothing. Where the disturbance is that large, knowing about it is
worth more than optimising against it. Reproduce with
`python3 -m mission_machine forecast`.

No machine learning is used in the planning path, deliberately. See
[`docs/architecture.md`](docs/architecture.md).

Three claims that would otherwise be prose are enforced by tests: the
demonstrator imports nothing but the standard library and boots in a clean
interpreter (`tests/test_standalone.py`), no public callable anywhere can command
an asset (`tests/test_no_control_path.py`), and synthetic labelling survives
every stage of the pipeline to the API (`tests/test_synthetic_labelling.py`).

## Documentation

| Document | What is in it |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Module boundaries, interfaces, and the design decisions worth arguing about |
| [`docs/research/questions.md`](docs/research/questions.md) | RQ-001 to RQ-006 with what Pack 1 actually found, and four new questions |
| [`docs/assumptions.md`](docs/assumptions.md) | Every assumption that moves a number, generated from the register in code |
| [`docs/reuse-assessment.md`](docs/reuse-assessment.md) | RODOT and Solid Soup / capacity-machine, both inspected from source: what to adopt, what to reject, and two defects it found in this repository |
| [`docs/evidence-rules.md`](docs/evidence-rules.md) | The labels, what may never be claimed, and how that is enforced in tests |
| [`docs/demo-script.md`](docs/demo-script.md) | The five-minute demonstration, with what to say |
| [`docs/pack1-deliverables.md`](docs/pack1-deliverables.md) | Deliverables, every file added, and the recommendation for Pack 2 |

## Scope limits

Pack 1 models disruption **only** as asset unavailability. There is no adversary
model, no targeting, no offensive capability, and no signature or detectability
modelling. Resilience results describe equipment failure; they say nothing about
survivability in a contested environment.
