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
* tell the operator when **the world has left the plan's premise** - and what
  believing the premise is costing them, raising it only where it crosses a line
  the mission states, and only once until it crosses another;
* hand a standing premise, and the decision the last watch made about it, to
  **the next watch**;
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
python3 -m mission_machine --mission-id MM-DEMO-002 configure   # any bundled mission
python3 -m mission_machine configure           # generate and compare configurations
python3 -m mission_machine operate --at 30 --scenario SC-DEGRADED-001
python3 -m mission_machine verify              # check the plans against the MILP model
python3 -m mission_machine optimise            # solve the same configurations exactly and compare
python3 -m mission_machine foresight           # how much of the solver's edge is lookahead
python3 -m mission_machine forecast            # what a wrong forecast costs the controller
python3 -m mission_machine premise             # whether the world still matches the plan's premise
python3 -m mission_machine alarms              # what a false premise alarm costs, and a true one is worth
python3 -m mission_machine handover            # a premise alarm across a shift boundary
python3 -m mission_machine rules               # can the dispatch rules close the solver's gap?
python3 -m mission_machine configure --coast   # plan with the rule RQ-015 measured
python3 -m mission_machine scaling             # where the candidate search stops being tractable
python3 -m mission_machine export-lp --out mm.lp   # the formulation, for any solver
python3 -m mission_machine assumptions         # what the results rest on
python3 -m unittest discover -s tests          # 238 tests, ~5 min
```

Add `--json` to any command for machine-readable output.

## Three scenarios

| | MM-DEMO-001 | MM-DEMO-002 | MM-DEMO-003 |
| --- | --- | --- | --- |
| | Resilient support node | Displacing signals detachment | Role 2 hospital in heat |
| Duration | 72 h | 48 h | 96 h |
| Host-nation supply | two windows | none, ever | four windows |
| Binding constraint | fuel | mobility, then fuel | heat |
| Leading objective | minimise fuel | minimise fuel | minimise single points of failure |

The second and third exist to test whether the MissionSpec generalises past the
one it was written for (RQ-001). The schema converted both mechanically - no new
field, type or vocabulary - but writing them changed about 150 lines across six
modules, every one a place that had quietly assumed MM-DEMO-001's shape. The
planner never read the mobility limit, so **192 of 336 candidate configurations
used a generator the mission cannot take with it**, and after a failure the
machine offered it as a recovery without mentioning the requirement it broke.
On the hospital, the three default options came back identical on every metric
that mission cares about while a fourth strategy - implemented, not in the
default set - served every function for fuel the mission had spare.

Details, and the honest limit that these were written by the same hands that
wrote the schema, are at RQ-001.

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

So the last thing built was not a better optimiser. It was a check on whether the
machine is planning against the right world at all. Three detectors compare what
the node has observed against what the mission asserts - is host-nation supply
there in the window the mission promised, is the command post drawing what it
said, is the array yielding what the weather profile forecast - and where the two
have parted company, the machine names the assumption, quantifies what believing
it costs, and offers a revision.

In a world where supply never returns, checked at H+36, the OPERATE screen
reports all four critical functions supported and 36 of 36 hours assured; the
panel above it reports that on what the node has actually seen all four are at
risk, three of those hours are not there, and the reserve is gone. The plan was
not wrong about the node; it was wrong about the world. Accepting the revision -
an operator action, logged like every other - replans on the truth and returns
three *feasible* options. Reproduce with `python3 -m mission_machine premise`.

The machine notices. It does not decide: `check_premises()` never changes what
the planner plans against, and a test asserts it.

A panel like that has one failure mode worse than being wrong, which is being
ignored, so the next thing measured was what its false alarms cost. Eleven
worlds - including supply that blinks and comes back, and a load that wobbles
hour to hour while drawing exactly what the mission says over the whole mission
- were each planned on the stated premise, lived in, and where the panel spoke,
replanned three ways from that hour: on the premise as stated, on the revision
offered, and on the world as it really is.

At the thresholds first shipped, two of eight alarms were actively harmful. A
one-hour supply dropout at H+6 and another at H+10, with supply otherwise
present exactly as promised, had the machine offer to write host-nation supply
off for the rest of the mission - giving up 68 kWh of discretionary service to
save fuel that did not need saving. Three changes came out of that: the grid
detector now counts only a run of missing supply that is still running, its
threshold moved from two hours to three on the measured curve, and a
contradiction is raised only where planning on the revision crosses a line the
mission states - the weather-yield detector had been firing on a reserve moving
from 14.0 h to 12.4 h against an 8 h requirement. Eight alarms became four, all
four worth raising, none harmful.

What that does *not* establish is the thing the question was really about. The
threshold was chosen on that world set and then scored on it, and no
number here says what a false alarm costs an operator's attention - whether the
second wrong alarm makes them close the panel and miss the third, true one. That
needs people, a run of shifts and a realistic mix of true and false alarms.
Reproduce with `python3 -m mission_machine alarms`.

Then the same question was asked about the hours *between* alarms, and the
counting came out badly. An operator does not live in a world, they live in a
rotation - and over a whole mission the panel spoke 95 times where six of them
were news. One world raised the same true contradiction 71 times, once an hour
from H+1 to H+71. A contradiction now has a lifecycle: raised when it crosses a
line the mission states, raised again only when it crosses one it had not
crossed before, carried as **standing** in between, and resolved once - saying
which of the two reasons it stopped, because the grid alarm had been going quiet
at H+44 for the second reason with no word to anybody. Ninety-five interruptions
become six.

That made the rest of it possible. An operator who reads an alarm and decides to
keep the stated premise can record that, with a rationale, in the decision log -
declining had never been recorded, so an outgoing watch that looked and waited
left no trace the incoming watch could tell from nobody having looked. And
`handover` assembles what one watch hands the next: what is standing, what was
decided and why, what nobody has decided. Assembled from the record, with no
recommendation in it, because a machine that tells the incoming watch what to do
about an inherited premise has taken the decision this design refuses to take.
Reproduce with `python3 -m mission_machine handover`.

**None of that says an operator will read any of it**, and that is the open
question rather than a caveat on a finding. Six interruptions instead of 95 is a
property of a rule, not of anybody's attention. The experiment that would settle
it is specified in
[`docs/research/shift-study-protocol.md`](docs/research/shift-study-protocol.md)
- including the outcome that would say this whole direction is wrong.

The obvious response to a 10-22 % gap is to field the solver. Printing the two
schedules side by side instead showed the defect in ten seconds: from H+20 the
rules run a generator at 25.03 kW every hour while the battery sits full at its
charge target, where the optimum alternates 45 kW and nothing and lets the
battery cycle. `CYCLED`'s own description already said it would *"run it hard,
and use the surplus to recharge the battery so it can be stopped again"* - and
its stop test required that nothing was already running, so it could decline to
start a set and could never stop one.

Two clauses fix it, and both are things an operator can predict: **stop the set
as soon as the battery can carry the node**, and **never start a second set just
to refill the battery**. Together they capture **50-85 % of the measured gap**
with no solver in the planning path, and they are better or neutral on every
option of all three missions, with critical service and delivered energy
unchanged.

They are not the default. The starts on a 72-hour mission go from one or two to
between four and eleven, and this model prices a start at the fuel burned in the
step it happens and nothing else (AS-026) - so adopting it would publish a
saving while its price sat outside the model. It is one flag away - the whole
table prints from `python3 -m mission_machine rules`, and
`configure --coast` plans with it, where the three options come back at 365 L,
350 L and 366 L instead of 431 L, 406 L and 420 L. See RQ-015.

No machine learning is used in the planning path, deliberately. See
[`docs/architecture.md`](docs/architecture.md).

Three claims that would otherwise be prose are enforced by tests: the
demonstrator imports nothing but the standard library and boots in a clean
interpreter (`tests/test_standalone.py`), no public callable anywhere can command
an asset (`tests/test_no_control_path.py`), and synthetic labelling survives
every stage of the pipeline to the API (`tests/test_synthetic_labelling.py`).
The two premise-detector defects found by pricing its false alarms are held
fixed by `tests/test_alarms.py`.

## Documentation

| Document | What is in it |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | Module boundaries, interfaces, and the design decisions worth arguing about |
| [`docs/research/questions.md`](docs/research/questions.md) | RQ-001 to RQ-006 with what Pack 1 actually found, and the questions building it raised |
| [`docs/research/shift-study-protocol.md`](docs/research/shift-study-protocol.md) | The study with people that Pack 1 cannot run, specified in advance: conditions, measures, and what each outcome would mean |
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
