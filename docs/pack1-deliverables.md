# Pack 1 - deliverables, files and recommendation for Pack 2

## Deliverables against the brief

| # | Deliverable | Where | State |
| --- | --- | --- | --- |
| 1 | Repository structure | `mission_machine/` with the module boundaries in `docs/architecture.md` | Complete |
| 2 | MissionSpec schema | `mission_machine/mission/spec.py`, instance in `data/missions/mm-demo-001.json` | Complete |
| 3 | Asset model | `mission_machine/assets/` - generic `Asset` plus nine concrete types | Complete |
| 4 | Deterministic planning-engine baseline | `mission_machine/planning/engine.py`, evaluated by `mission_machine/simulation/simulator.py`, declared as a MILP in `mission_machine/planning/milp.py` | Complete |
| 5 | MM-DEMO-001 scenario | `data/missions/mm-demo-001.json`, `data/assets/synthetic-asset-set-001.json`; two further missions of a different shape were added afterwards for RQ-001 | Complete |
| 6 | Three generated configuration types | `Strategy.MAX_ENDURANCE`, `MIN_FUEL`, `MIN_LOGISTICS`; a fourth, `MAX_SUPPORTED_FUNCTIONS`, is available on request | Complete |
| 7 | Generator-failure reconfiguration | `resilience/failures.py:GENERATOR_B_UNAVAILABLE`, handled by `operations/session.py:OperationsSession.inject` | Complete |
| 8 | Comparison UI | COMPARE screen; `mission-machine configure` on the command line | Complete |
| 9 | Operations / degraded-mode UI | OPERATE screen; `mission-machine operate --scenario` | Complete |
| 10 | Architecture note | `docs/architecture.md` | Complete |
| 11 | Research-question register | `docs/research/questions.md` - the six original questions answered as far as the model allows, plus the thirteen raised by building it, and `docs/research/shift-study-protocol.md` for the one that needs people | Complete |
| 12 | Assumption register | `docs/assumptions.md`, generated from `explainability/assumptions.py` and shown in the UI | Complete |
| 13 | Reuse assessment for RODOT / Solid Soup | `docs/reuse-assessment.md` | Complete - both assessed from source (Solid Soup is `capacity-machine`); the upstream `interop-capacity-service` behind it remains out of reach and is noted as such |
| 14 | Exact files added or changed | Below | Complete |
| 15 | Recommendation for Pack 2 | Below | Complete |

### Success criterion

> Pack 1 succeeds when a user can describe a synthetic 72-hour support mission,
> generate multiple feasible infrastructure configurations, understand their
> trade-offs, simulate a resource failure, and receive new options that preserve
> critical mission functions.

Met, with one honest qualification: in the **compound** failure case
(`SC-DEGRADED-002`) no reconfiguration preserves the critical functions, because
the fuel on site is insufficient. The system establishes that, and identifies the
one action that does work - accepting a degraded environmental-control setpoint -
without taking it. That is the intended behaviour, not a shortfall.

---

## Exact files added

The repository contained only `README.md` before this pack. Everything else is
new; `README.md` was replaced.

### Application code - `mission_machine/` (43 files, ~12 900 lines)

```
mission_machine/__init__.py                     package, version, scope statement
mission_machine/__main__.py                     python -m mission_machine
mission_machine/cli.py                          demo / mission / configure / operate / premise / alarms / handover / rules / verify / export-lp / assumptions / serve

mission_machine/evidence/__init__.py
mission_machine/evidence/labels.py              EvidenceLabel, Provenance, is_operational_truth, the disclaimer
mission_machine/evidence/questions.py           OpenQuestion - a withheld quantity with the reason attached
mission_machine/evidence/control.py             the no-control-path rule, stated where a test can check it
mission_machine/mission/spec.py                 (also) PriorityIntent - the vocabulary the planner reads

mission_machine/assets/__init__.py
mission_machine/assets/base.py                  Asset, AssetKind, Mobility, FailureState, OperatingConstraints
mission_machine/assets/energy.py                Generator, Battery, GridConnection, SolarPV, PowerConversion
mission_machine/assets/loads.py                 Load, LoadProfile, CommunicationsLoad, CoolingSystem, ChargingSystem
mission_machine/assets/inventory.py             AssetInventory, deserialisation, failure injection

mission_machine/environment/__init__.py
mission_machine/environment/model.py            Environment, WeatherProfile, GridAvailability, perturbation variants

mission_machine/mission/__init__.py
mission_machine/mission/spec.py                 MissionSpec, ReserveRequirement, MobilityRequirement, OperatorPriority, validation
mission_machine/mission/library.py              bundled mission lookup

mission_machine/planning/__init__.py
mission_machine/planning/configuration.py       Configuration, DispatchPolicy, SecondaryPolicy, GeneratorMode
mission_machine/planning/engine.py              PlanningEngine, Strategy, PlannedOption, PlanningResult
mission_machine/planning/metrics.py             ConfigurationMetrics, DeploymentComplexity, the ten output metrics
mission_machine/planning/milp.py                MILP formulation, LP export, schedule verification
mission_machine/planning/providers.py           OptimisationProvider seam, CBC backend, registry
mission_machine/planning/optimal.py             the dispatch rules measured against the optimum
mission_machine/planning/rolling.py             lookahead, and planning against a world that turns out wrong

mission_machine/simulation/__init__.py
mission_machine/simulation/state.py             NodeState, StepRecord
mission_machine/simulation/simulator.py         the deterministic dispatch simulator

mission_machine/resilience/__init__.py
mission_machine/resilience/failures.py          FailureEvent, Scenario, SC-DEGRADED-001, SC-DEGRADED-002
mission_machine/resilience/analysis.py          single points of failure, recovery options

mission_machine/explainability/__init__.py
mission_machine/explainability/assumptions.py   the assumption register, in code
mission_machine/explainability/explain.py       comparison, trade-offs, sensitivity, Recommendation

mission_machine/operations/__init__.py
mission_machine/operations/session.py           OperationsSession, MissionAssessment, ReconfigurationReport, OperatorDecision, StandingAlarm, HandoverBrief
mission_machine/operations/premises.py          what the mission asserts, checked against what the node has seen
mission_machine/operations/alarms.py            what a false alarm costs, what a true one is worth, how often the panel speaks

mission_machine/ui/__init__.py
mission_machine/ui/server.py                    standard-library HTTP server and JSON API
mission_machine/ui/static/index.html            MISSION / CONFIGURE / COMPARE / OPERATE
mission_machine/ui/static/app.js                UI logic and SVG charts
mission_machine/ui/static/style.css             styling
```

### Synthetic data - `data/` (three missions, three asset sets)

```
data/assets/synthetic-asset-set-001.json        14 assets: grid, 2 generators, BESS, PV, conversion, 8 loads
data/assets/synthetic-asset-set-002.json        12 assets: a light detachment, plus one generator it cannot take
data/assets/synthetic-asset-set-003.json        13 assets: a field hospital, cooling-dominated
data/missions/mm-demo-001.json                  MM-DEMO-001, Resilient 72-hour Support Node
data/missions/mm-demo-002.json                  MM-DEMO-002, Displacing Signals Detachment (mobility binds)
data/missions/mm-demo-003.json                  MM-DEMO-003, Role 2 Field Hospital in Heat (heat binds)
```

### Documentation - `docs/`

```
docs/architecture.md                            module boundaries, interfaces, design decisions, performance
docs/research/questions.md                      RQ-001 to RQ-006 with findings, plus the questions building it raised
docs/research/shift-study-protocol.md           the study with people Pack 1 cannot run, specified in advance
docs/assumptions.md                             generated from the register in code
docs/reuse-assessment.md                        RODOT and Solid Soup / capacity-machine, adapter boundaries, licensing
docs/evidence-rules.md                          labels, what may never be claimed, how it is enforced
docs/demo-script.md                             the five-minute demonstration
docs/pack1-deliverables.md                      this document
```

### Tests and tooling

```
tests/test_mission.py                           MissionSpec loading and validation
tests/test_assets.py                            fuel curves, storage, PV, all five load-profile types
tests/test_simulation.py                        energy balance, limits, endurance, determinism, resume-from-state
tests/test_planning.py                          option generation, ranking, metrics, MILP verification
tests/test_resilience.py                        SPOF, recovery options, both degraded scenarios, operator override
tests/test_evidence_and_cli.py                  evidence discipline, explainability, every CLI command
tests/test_standalone.py                        guardrail: clean-interpreter boot, no third-party or neighbouring imports
tests/test_no_control_path.py                   guardrail: nothing anywhere can command an asset
tests/test_synthetic_labelling.py               guardrail: synthetic labelling survives to the API
tests/test_operator_priorities.py               operator intent reaching the planner, and its limits
tests/test_optimisation.py                      the solver seam, with and without a backend installed
tests/test_premises.py                          premise detection, and that noticing never becomes deciding
tests/test_alarms.py                            the alarm harness, and the two detector defects it found
tests/test_shifts.py                            the alarm lifecycle, dismissal, and what one watch hands the next
tests/test_missions.py                          three missions of different shape, and the assumptions they broke
tests/test_rules.py                             the two dispatch clauses, and the baseline they leave alone
tools/render_assumptions.py                     regenerates docs/assumptions.md from the register
pyproject.toml                                  packaging; zero runtime dependencies
.gitignore
README.md                                        replaced
```

238 tests, about five minutes, no dependencies, no network. The solver-backed tests
skip themselves when no backend is installed.

---

## What Pack 1 does not do

Stated plainly so that no reader has to infer it:

* No adversary model, no targeting, no offensive capability, no signature or
  detectability modelling. Disruption is asset unavailability only.
* No real data of any kind. No connection to any data source.
* No MILP solve *in the planning path* - the baseline still ranks an
  enumeration. A solver is now a supported backend and is used to measure the
  dispatch rules (RQ-009), not to choose configurations.
* No dependency graph. Consequences of a failure are found by re-simulation, not
  by propagation, so the system cannot yet distinguish "short of what it needs"
  from "stopped".
* No time cost for reconfiguration. Recovery options carry a time-to-effect but
  the simulation applies them instantly.
* No authentication, no multi-user state, no persistence. The UI holds one
  session in memory.
* No composite mission-assurance score, deliberately. See `docs/architecture.md`.

---

## What was built after Pack 1

Two changes landed after the deliverables above, both prompted by the reuse
assessment of capacity-machine. They are recorded here rather than folded
silently into Pack 1.

**The reserve metric now counts only reachable energy.** `_reserve` counted
stored energy whenever a battery existed in the inventory, whether or not the
configuration deployed it, and counted fuel as worth nothing whenever no
generator was committed. The first overstated OPTION C's minimum reserve by 28 %
(13.0 h reported, 9.3 h reachable) and the RQ-002 rule-based baseline by all of
it; the second was latent. Both are fixed: energy counts when the configuration
can deliver it, and energy it cannot reach is reported as a withheld quantity
with an `OpenQuestion` attached rather than as zero. Registered as AS-015.
No ranking changed and no conclusion reversed.

**Three guardrail tests were ported**, turning three claims previously made in
prose into claims the build enforces: that the demonstrator stands alone
(`tests/test_standalone.py`), that nothing in it can command an asset
(`tests/test_no_control_path.py`), and that synthetic labelling survives to the
API (`tests/test_synthetic_labelling.py`). The last found a real gap while being
written - `Recommendation` and `ReconfigurationReport` carried the disclaimer but
no evidence labels. Test count 82 to 106, then 126 with the operator-priority work, then 140 with the solver seam, then 147 with the foresight harness, then 154 with the forecast-error work, then 170 with the premise checks, then 190 with the alarm harness, then 210 with the alarm lifecycle and handover, then 226 with two more missions, then 238 with the dispatch-rule work.

**Operator priorities now reach the optimiser.** Pack 1's largest gap, and the
first item on the Pack 2 list. The MissionSpec carried ranked priority statements
that nothing downstream could read, so the planner shed a function the operator
had explicitly asked for. Priorities now carry a closed vocabulary of intents
alongside the operator's own words, and each intent reaches a specific place in
the planner - serving and shedding order, which functions a configuration
attempts, which candidates the strategies may rank over, and which option leads.

Three things came out of doing it. "Keep it available if it does not threaten
critical functions" only works as a feasibility tier, not a preference - as a
preference it changed nothing. "Never be interrupted" has to mean through a
failure, or the planner recommends a configuration the mission's own mandated
scenario then breaks. And a pre-authorised degradation is how a human decision
reaches the planner *in advance*: in the compound failure the planner now returns
feasible options using the degradation the operator authorised at priority 3,
each labelled with the authorisation it relies on. Registered as AS-016 to
AS-018, and answered in full at RQ-008.

**A real solver was plugged in, and it measures the rules rather than replacing
them.** `planning/providers.py` is the `OptimisationProvider` seam from the reuse
assessment: CBC answers through it when PuLP is installed, every declared backend
is reported whether or not anybody wired it, and each answer names its source. A
solve that spends its whole budget is reported as FEASIBLE, never as proven
OPTIMAL, and every solver answer is checked against the declared constraint set
before it is believed.

What it found: for the same asset set and the same delivered service, the
transparent dispatch rules give up 22.4 %, 12.0 % and 10.6 % of the fuel on the
three options - most on the configuration with two generators, where the
merit-order rule has the most choice to get wrong. The solver has perfect
foresight and the rules do not, so that is an upper bound; each solve also
stopped at a 60-second budget without proving optimality, so it is simultaneously
a lower bound on what the rules give up. Neither caveat is negligible and both
travel with the numbers in the code.

The candidate search was measured at the same time: 312 candidates at two
dispatchable generators, 1 464 at four, roughly doubling per asset at ~8.7 ms
each. Interactive use breaks around five or six. Both are RQ-009, now answered.

**And then the foresight caveat was tested, and did not survive.** The gap above
was measured against a solver that knew the whole mission in advance, which
raised the obvious objection: no controller has that, so how much of it is
recoverable? `planning/rolling.py` replans over a finite lookahead window and
carries the node state forward. Twelve hours of lookahead recovers 96-100 % of
the gap on all three configurations, in one to two seconds of solving against
the sixty a full-horizon solve takes. Six hours recovers between a fifth and
three quarters. Going to 24 or 48 hours buys nothing and, on the two-generator
configuration, is slightly worse because each window hits its time budget
without proving optimality.

So the saving is real and cheap to get, and what the rules give up is commitment
logic rather than clairvoyance. That changed the Pack 2 recommendation below
(RQ-012).

**And then the forecast was made wrong, which is the state a real node is always
in.** `run_closed_loop` plans against one world and lives in another: the plan
fixes the generator commitment, everything else re-balances against what
actually happened, and a window that cannot be carried out at all reverts to the
dispatch rules and is counted. Between 65 % and 100 % of the fuel saving
survives a four- to eight-hour error in when host-nation supply returns, with
critical-load coverage untouched - replanning every six hours corrects the error
before it compounds.

The severe case pointed somewhere unexpected. Where the grid never returns,
OPTION A *told the truth* completes the mission on its 520 L while the dispatch
rules fail at H+69; the same controller told the nominal forecast fails at H+70,
buying one hour over doing nothing. Past a certain size of disturbance, knowing
about it is worth more than optimising against it - which is a different product
from a better optimiser, and is now RQ-016.

Recorded as a negative result: the first execution model handed the rules only
the solver's commitment, and with a perfectly correct forecast that was worse
than no plan at all. A plan is a coherent whole. The hybrid machinery is kept
and tested because somebody will propose that design again. RQ-014.

**And then the machine was taught to doubt its own premise.** That last result
said something the rest of Pack 1 does not: past a certain size of disturbance,
*noticing* beats optimising. `operations/premises.py` acts on it. Three detectors
compare what the node has observed against what the MissionSpec asserts - is
host-nation supply actually there in the window the mission promised, is the
command post drawing what it said, is the array yielding what the weather profile
forecast - and where observation and premise have parted company, the machine
names the assumption, quantifies what believing it costs, and offers a revision.

This required a split the demonstrator did not have. The session now plans and
projects on the mission's premise, which is what the operator believes, while
advancing through whatever world it is actually given. Projecting on the realised
world would have shown the operator a future they do not have.

Run in a world where host-nation supply never returns and checked at H+36, the
OPERATE screen reports all four critical functions supported and 36 of 36 hours
assured - and the premise panel above it reports that on what the node has
actually seen, all four are at risk, the assured-support figure is three hours
optimistic and the reserve is gone. The plan was not wrong about the node. It was
wrong about the world, and every number downstream inherited the error while still
reading as survivable. When the operator accepts the revision - an operator
action, logged like any other, never something the machine does for itself -
replanning on the truth returns three *feasible* options on the 301 L left.

Three detectors, about two hundred and sixty lines, no solver. Detection itself
costs under a millisecond on observations the simulator was already recording;
quantifying what a breach costs takes two projections, about a tenth of a second.
The thresholds are chosen rather than derived (AS-022), and what a false alarm
costs is the one thing this cannot answer without people: RQ-017. RQ-016.

**And then the panel was made to earn its place.** A panel that tells the
operator the premise has changed has one failure mode worse than being wrong,
which is being ignored. `operations/alarms.py` prices that: eleven worlds the
node might be living in, each planned on the stated premise and then lived in,
and where the panel speaks, three continuations from that hour differing only in
the premise they plan against - the premise as stated, the revision offered, and
the world as it really is. No world carries a label saying whether the premise
"really" changed; whether there was anything worth saying is computed from what
planning on the truth would have been worth.

At the thresholds RQ-016 shipped, eight alarms: four worth raising, two
nuisance, two **harmful**. In the worst, a one-hour supply dropout at H+6 and
another at H+10 - supply otherwise present exactly as promised - had the machine
offer to plan on no host-nation supply for the rest of the mission, giving up
68 kWh of discretionary service to save 33 L of fuel that did not need saving.

Three changes came out of it. The grid detector counted *every* missing hour ever
observed while the constant documenting it said *consecutive*, and dated its
revision from the first hour that ever went missing: fixed to match its own
documentation. Its threshold moved from two hours to three, on a measured curve
across eleven worlds at five settings - at three, every alarm raised is worth
raising and supply that never returns is still caught at H+33 instead of H+32.
And a contradiction is now raised only where planning on the revision crosses a
line the mission states, after the weather-yield detector was found firing on a
projected reserve moving from 14.0 h to 12.4 h against an 8 h requirement:
correct, real, and nothing an operator would act on. Those are demoted to a note
in the same panel, not hidden. Eight alarms became four, all four worth raising.

What that does not establish is what the question was really about. The threshold
was chosen on those eleven worlds and then scored on them, which is fitting to
the test set. One class of false alarm is irreducible rather than untuned -
supply two hours late is indistinguishable, at the hour of the alarm, from supply
that never comes, and at three hours that world happens to fall the right side of
the line where on differently shaped windows it would not. And nothing here says
what a false alarm costs an operator's attention. That needs people, a run of shifts and
a realistic mix of true and false alarms, and it is now RQ-018. Registered as
AS-023; RQ-017.

**And then it was counted over a whole mission, and the counting came out
badly.** RQ-017 asked whether the first alarm of a world was worth raising. An
operator does not live in a world, they live in a rotation, and over a whole
mission the panel spoke **95 times** across the twelve worlds where six of them
were news. One world - `load-15pc-heavier`, which RQ-017 correctly scored as
worth raising - raised the same true contradiction **71 times**, once an hour
from H+1 to H+71, each time carrying exactly the information of the first. A
second finding came out of the same measurement: the grid alarm stopped at H+44,
not because it was resolved and not because anybody acted, but because that is
where the mission stops promising supply. The panel simply went quiet, and an
operator cannot tell that from "it is fixed".

A contradiction now has a lifecycle. It is RAISED when it crosses a line the
mission states and raised again only when it crosses one it had not crossed
before (AS-024); in between it is STANDING - on the panel, in the handover, not
re-announced. It RESOLVES once, saying which of the two reasons it stopped
mattering. The rule is idempotent within an hour, so refreshing a screen is not
an event. Ninety-five interruptions became six, with 89 standing-hours still
visible.

That made the rest possible. Declining a revision had never been recorded - only
accepting one - so an outgoing watch that read an alarm and decided to wait left
no trace the next watch could tell from nobody having looked.
`dismiss_premise_revision` records it with a rationale, and is deliberately not a
mute: the alarm stays standing and is raised again if it crosses a new line. And
`handover()` assembles what one watch hands the next - what is standing, what was
decided and why, what is open, and the decision log - with no recommendation in
it, asserted by a test, because a machine that tells the incoming watch what to
do about an inherited premise has taken the decision the rest of this design
refuses to take.

**None of that answers RQ-018**, and the register says so as its first line. Six
interruptions instead of 95 is a property of a rule, not of anybody's attention;
it is entirely possible that six is still too many, or that a standing line reads
as "handled". The suppression has a known cost of its own (AS-024): a
contradiction can worsen without crossing a new line, and then it is not said
twice. What was built is the instrumentation a study of people would need, and
what was written is the study itself - `docs/research/shift-study-protocol.md`
specifies participants, conditions, measures and, fixed in advance, what each
outcome would mean, including the one that would say this whole direction is
wrong.

**And then two more missions were written, which is the only test the rest of
this could not do for itself.** Every number above comes from MM-DEMO-001, a
mission written by the people who wrote the schema. MM-DEMO-002 is a signals
detachment that must displace within the hour and has no host-nation supply at
all; MM-DEMO-003 is a Role 2 field hospital over four days where the binding
constraint is heat and fuel is ample.

**The schema converted both mechanically; the code that reads it did not.** No
new field, type or vocabulary was needed to state either mission - two JSON
documents and two asset sets, 25 assets, zero lines of Python to load or plan
them. Writing them changed about 150 lines across six modules, every one a place
that had quietly assumed the shape of the one mission it was written against:

* The planner never read the mobility limit. **192 of 336 candidate configurations
  used an asset MM-DEMO-002's own validation rejects**, and after a generator
  failure the machine offered *"Commit GEN-HV-01 - bring the heavy generator on
  line"* as a recovery, silent about the requirement that breaks. Options and
  recoveries now name it. Not filtered: lifting the requirement is a command
  decision, and hiding the option takes that decision away.
* On MM-DEMO-003 the three default options came back identical on every metric
  the mission cares about, while `MAX_SUPPORTED_FUNCTIONS` - implemented, not in
  the default set - served **every** function for 203 L out of 1 400 L spare.
  The planner now says when its options do not differ and names what it did not
  try.
* The alarm harness assumed two supply windows; MM-DEMO-003 has four, so a world
  called "supply returns 2 h late" was silently also deleting the third and
  fourth.
* A bad value in an asset file threw a traceback naming neither the asset nor
  the permitted values, in a system whose mission spec makes a point of
  reporting problems rather than raising.

**What transferred.** The premise machinery was applied unchanged to all three.
Both new missions are silent, and on both the harness confirms the silence is
correct - planning on the truth gains nothing. That bounds RQ-016's headline:
"noticing beats optimising" was measured on a node with 89 L of slack, and on a
mission with generous margins the panel correctly says nothing at all. The
capability is worth what the constraint is worth.

**The limit is the part RQ-001 actually asked for.** It wanted missions written
by somebody who did not build the schema. These were written by the same hands,
in the same session. A schema author writes missions the schema can express, so
the count of "how much new code" is a lower bound: two missions found four
faults in a day, and a stranger's mission would find different ones.

**And then the rules were improved instead of replaced.** RQ-009 measured the
transparent dispatch rules giving up 10-22 % of the fuel to a perfect-foresight
solver; RQ-012 showed twelve hours of lookahead recovers nearly all of it, which
meant the deficiency was commitment rather than clairvoyance. Printing the two
schedules side by side made it legible in ten seconds: from H+20 the rules run
GEN-A at 25.03 kW every hour with the battery pinned at its charge target, while
the optimum alternates 45 kW and nothing and lets the battery cycle.

`CYCLED` had promised exactly this in its own docstring - *"run it hard, and use
the surplus to recharge the battery so it can be stopped again"* - and its stop
test required that nothing was already running, so it could decline to start a
set and could never stop one. Two clauses fix it, both predictable by an
operator: **stop the set as soon as the battery can carry the node**, and
**never start a second set just to refill the battery**. The second is what
makes the first safe - with only the first, MM-DEMO-002 ran both light sets at
once to refill faster and finished 5.6 % *worse*.

Together they capture **81 %, 85 % and 50 %** of the measured gap on the three
MM-DEMO-001 options, and are better or neutral on every option of all three
missions - never worse, critical coverage 1.0000 throughout, the same service
energy delivered to the kilowatt-hour, worst-hour reserve improved. The saving
decomposes into exactly two parts, both asserted in tests: no-load fuel not
burned, and energy not generated to finish the mission sitting unused in a
battery.

**They are not adopted as the default**, and that is the decision worth arguing
about. The starts over 72 hours go from one or two to between four and eleven,
and this model prices a start at the fuel burned in the step it happens and at
nothing else (AS-026). Publishing the saving while its price sits outside the
model would be the wrong way round. The shipped rule is untouched, every figure
in this document still reproduces, and the new rule is one flag away with its
whole table printable by `mission-machine rules`. What it needs is somebody who
can price a start: RQ-021.

Details in `docs/reuse-assessment.md` and `docs/research/questions.md`.

## Recommendation for Pack 2

Ordered by what would most improve the demonstrator's ability to answer its own
research questions, not by what is most interesting to build.

Eleven items from earlier versions of this list are done and are recorded under
"What was built after Pack 1" above rather than here: making the operator's
priorities reachable by the optimiser, plugging a real solver in behind the
`OptimisationProvider` seam, measuring the dispatch gap under a realistic
lookahead, measuring what a wrong forecast costs, telling the operator when the
premise has changed, pricing what that panel's false alarms cost, giving a
contradiction a lifecycle and a handover, writing two more missions of a
different shape, improving the dispatch rules instead of replacing them, fixing
the two reserve-metric defects, and porting capacity-machine's three guardrail
tests.

The premise check is the one worth noticing: it was added to this list as item 7
and then built, in the same pack, because the evidence for it turned out to be
stronger than the evidence for anything above it - and measuring its false
alarms then found two defects in it and moved one of its thresholds. This list
is a reading of the results so far, and the results have already reordered it.

### 1. Run the shift study (RQ-018)

This is first because the evidence says so, not out of modesty, and it is now
the only item on this list that is fully specified before it starts:
`docs/research/shift-study-protocol.md` fixes the participants, the two
conditions, the measures and what each outcome means, so that it can be
criticised before it is run rather than interpreted after.

Pack 1's own measurements have run out of things to tell it. RQ-016 found the
premise panel worth more than a better optimiser against the disturbance that
actually threatens the mission. RQ-017 priced its false alarms, found two
harmful ones and moved a threshold. RQ-018 found it raising the same true
contradiction 71 times in one mission and gave contradictions a lifecycle.
Every one of those is a statement about the machine, and the next one cannot be:
whether an operator still reads the panel on the third day of a rotation is not
in any number this repository can produce.

It needs twelve to sixteen people who plan support for a living, paired so that
one participant's handover is another's inheritance. Everything else on this
list makes the machine better at something it is already adequate at. This is
the item that can show the whole direction to be wrong - the protocol names that
outcome explicitly - which is the reason to do it first.

### 2. Price a generator start, then decide about the dispatch rule (RQ-021)

The rule exists and is measured: two clauses, 50-85 % of the solver's gap, no
solver in the planning path, better or neutral on every option of three
missions. What stops it being the default is that its cost is a generator start
and this model prices one at nothing (AS-026).

So this item is not modelling work. It is finding out what a start actually
costs on the sets a support node carries - wear, maintenance interval,
failure-to-start rate - and then either adopting the rule, adopting it with a
minimum run time, or recording why the fuel is not worth the cycling. Any of the
three is a result, and `mission-machine rules` prints the table the decision
needs.

### 3. Add a dependency graph and consequence propagation (RQ-005)

Pack 1 knows that losing the conversion unit stops the node, but only because
the simulation produces zero. It cannot say *"the cooling system is short of
what it needs"*. A minimal asset-to-function dependency graph carrying capacity,
behind a `PropagationProvider` interface, would let the degraded-mode picture
name the mechanism rather than only the outcome. RODOT has a mature
implementation of exactly this; see the reuse assessment.

### 4. Settle one evidence vocabulary across the three products

RODOT has `E0`-`E6`, capacity-machine has `EvidenceStatus` plus a structured
`Provenance` record, Mission Machine has four flat labels. Three attempts at the
same idea that do not interoperate. Settle one, port it into Mission Machine as
frozen dataclasses, and keep the current labels as presentation. No coupling, and
afterwards the three systems can quote each other's numbers.

`OpenQuestion` and a computed `is_operational_truth` are already ported from capacity-machine; the graded scale itself is what remains.

### 5. Model the time a reconfiguration takes (RQ-010)

Every recovery option already carries a time-to-effect. Applying it instantly
makes fast and slow responses look identical, which is precisely backwards when
ride-through is 3 hours and PV deployment takes 90 minutes.

### 6. Ask the upstream capacity-service question before hardening the energy model

The brief names BESS models, energy-flow logic and capacity constraints as
reusable. Pack 1 built its own. capacity-machine turns out not to hold them
either - it consumes them from `interop-capacity-service` through a read-only
contract and is forbidden from re-implementing them. That service is where the
question actually lands, and it was not reachable from this session. Ask it
before Pack 2 makes the energy model harder to change.

### 7. A mission written by somebody who did not build the schema (RQ-001)

Two more missions are now bundled, and they found four faults, across six
modules, in code that had assumed the shape of the first one. What they could not do is the part RQ-001
actually asked for: they were written by the same hands that wrote the schema,
in the same session, and a schema author writes missions the schema can express.

This item is cheap and its value is entirely in who does it. Hand the MissionSpec
and `docs/architecture.md` to somebody who plans support, ask them to describe a
mission they have actually run, and count what breaks. It pairs naturally with
item 1 - the shift study needs two mission variants anyway, and a stranger's
mission would be a better second variant than another of ours.

### 8. Choose the strategy set from the mission (RQ-001, RQ-003)

MM-DEMO-003 showed the three default strategies returning one answer under three
names while a fourth, already implemented, returned the option worth seeing. The
planner now says when its options do not differ and names what it did not run,
which is the honest minimum. Choosing the set properly - so a mission whose
constraint is coverage gets a coverage strategy without anybody asking - needs a
rule that can be read and argued with, and it should be built after item 7, when
there is a mission nobody here wrote to test it against.

### Explicitly not recommended for Pack 2

* **Machine learning anywhere in the planning path.** The baseline is what makes
  the demonstrator explainable, and it is not yet good enough to be worth
  replacing. ML has a clear later role in mission parsing, scenario generation
  and operator interaction - none of which is on the critical path now.
* **A mission-assurance score.** Not until its semantics, weights and uncertainty
  can all be shown. The conditions are in `docs/architecture.md`.
* **Any claim of operational relevance.** The next honest step towards that is
  exposure to people who plan support for a living, not more features.
