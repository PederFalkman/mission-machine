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
| `resilience/` | `FailureEvent`, `Scenario`, single-point-of-failure analysis, recovery options, and the dependency graph that says by what mechanism a failure reaches a function | Choose a response, or simulate the consequence of an unmet dependency - the node's physics stay in `simulation/` |
| `operations/` | The operating picture: `MissionAssessment`, `ReconfigurationReport`, `OperatorDecision`, the premise checks behind them, and what one watch hands the next | Hide a change from the operator, revise a mission premise on its own, or tell the incoming watch what to do about an inherited one |
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
| `Premise`, `PremiseBreach` | `operations/premises.py` | What the mission asserts about the world, and what the node has seen instead |
| `StandingAlarm`, `HandoverBrief` | `operations/session.py` | What the operator has already been told, and what the next watch inherits |
| `Recommendation` | `explainability/explain.py` | Always carries `operator_decision_required = True` |
| `ElectricalFeasibilityProvider` | `planning/electrical.py` | Declared port for network physics; no backend wired in Pack 1 |

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

A solver has since been plugged in, and the result was not the one expected.
`planning/providers.py` is the `OptimisationProvider` seam: CBC answers through
it when PuLP is installed, every declared backend is reported whether or not it
is wired, and each answer carries the backend that produced it. What the solver
turned out to be good for is **measuring** the rules rather than replacing them -
for the same asset set and the same delivered service, the rule-based dispatch
gives up 10-22 % of the fuel against a perfect-foresight optimum, and gives up
most where there are most machines to choose between. None of it changes which
option the planner puts forward.

So the enumeration stays the default. It is what makes the demonstrator
explainable, it picks the configuration, and the solver improves the dispatch
within it. The case for replacing it in a later pack is about scale - the
candidate space doubles per dispatchable asset and breaks around five or six -
and about fuel, not about correcting the advice. See RQ-009.

`planning/rolling.py` then asked how much of that fuel gap was the solver's
perfect foresight rather than its skill, by replanning over a finite lookahead
window and carrying the node state forward. The answer is that twelve hours of
lookahead recovers the whole gap, in about two seconds of solving against the
sixty a full-horizon solve takes - so the saving is real and a rolling
controller is the tractable way to get it. Two pieces of machinery make that
measurement trustworthy rather than an artefact: a window that does not reach
the end of the mission credits its closing stored energy at the best generator's
fuel rate, so a short horizon is not punished for emptying a battery it cannot
see a use for; and the discretionary service floor is applied per window, so a
rolling plan cannot win by deferring service past the comparison. The harness is
checked against the case where the answer is known - a window as long as the
mission must reproduce the single solve exactly. See RQ-012.

`run_closed_loop` then lets the controller plan against one world and live in
another, which is the only way to ask what a wrong forecast costs. The execution
model is the one real energy management uses: the plan fixes the generator
commitment, because a machine cannot be synchronised retrospectively, and
everything else re-balances against what actually happened. With the binaries
pinned that re-balance is an LP. Where even it is infeasible the node reverts to
its dispatch rules and the window is counted, because "no plan existed" is an
answer and not a reason to stop reporting.

That design was arrived at the hard way. The first version handed the rules only
the solver's commitment and let them balance, which with a perfectly correct
forecast was *worse than no plan at all* - a plan commits a machine because it
means to run it hard and bank the surplus, and a dispatcher given only the
commitment pays the no-load fuel without banking anything. The machinery for
that hybrid is still there and tested, because the negative result is a finding
and the design is one somebody will propose again. See RQ-014.

### Operator intent is data the planner reads, not text on a screen

The MissionSpec carries the commander's priorities twice over: `statement` keeps
their own words and is never parsed, and `intent` is a closed vocabulary the
planner acts on. Keeping both side by side means a reader can check the
machine-readable form against what was actually meant, which is not possible if
the system either parses the sentence or ignores it.

Each intent reaches a specific place:

| Intent | What the planner does with it |
| --- | --- |
| `NEVER_INTERRUPT` | Served first, first claim on stored energy, and configurations that hold it through the loss of the largest generator - for at least the mission's own deployment time limit - are preferred over cheaper ones |
| `MAINTAIN` | Served ahead of everything discretionary |
| `DEGRADE_ACCEPTABLE` | A pre-authorisation. When nothing else is feasible, the planner may propose the degraded mode - for that function only, labelled, with an instruction to confirm the authorisation still stands |
| `SERVE_IF_AFFORDABLE` | A feasibility tier, not a tie-break: the strategies rank over candidates that serve it, and drop to ones that do not only when no feasible candidate can |
| `DISCRETIONARY` | Shed first - ahead of functions the mission never mentions, because silence is not consent to shed something |
| `MINIMISE` | Orders the recommendation, in the operator's own rank order, over a closed set of quantities that each resolve to a real metric |
| `ADVISORY` | Displayed and not acted on. The honest default for anything the planner cannot read |

Two consequences worth stating plainly. The *shed order* now comes from the
mission rather than from a `shed_priority` number on the equipment, because how
readily a function is given up is a command judgement and not a property of the
hardware. And the recommendation can now quote the line that decided it, which is
checkable against the mission in a way that a synthesised phrase never was.

A mission whose priorities are all advisory still plans: the policy falls back to
the equipment's shed priorities, and says on screen that it is doing so.

### A solver is a backend, never a dependency

Three rules hold the seam, taken from capacity-machine:

* **Absence is survivable.** The deterministic baseline is always available and
  always the default. With nothing installed, `ProviderRegistry.solve` returns
  `UNAVAILABLE` with the LP export as the way forward - it does not fall back to
  something that is not a solve and call it one.
* **The answer names its source.** Every `SolverOutcome` carries the backend and
  version. A number from CBC and a number from the dispatch rules are different
  claims.
* **Unwired ports are reported, not hidden.** `OrToolsCpSatProvider` is declared
  and unimplemented, and says why: CP-SAT needs an integer reformulation of the
  fuel coefficients, and doing that scaling badly would produce answers wrong in
  a way nobody would notice.

Two further disciplines are specific to trusting a solver. Its answer is checked
against the declared constraint set before it is used, by the same verifier that
checks the simulator's schedules - a solver that returns an infeasible
assignment, or a model mapped wrongly onto it, fails loudly instead of producing
a confident wrong number. And a solve that spends its entire time budget is
reported as `FEASIBLE`, never as `OPTIMAL`, whatever the solver's own status
string says.

### A physics this demonstrator does not own is a declared port, not a guess

The MILP verifier checks that a schedule's energy sums are internally
consistent. It cannot say whether the currents those numbers imply keep a
real network's buses inside their voltage band - that is a different
physics, and its owner is ato-energy-platform's `PowerSystemSolver` (frozen
at `power_system_solver/1.0`), not this repository. Cross-repo governance
says so explicitly: Mission Machine may consume it through a versioned
provider/service contract and must not implement its own "good enough"
power-flow solver.

`planning/electrical.py` declares `ElectricalFeasibilityProvider` in the same
shape as the solver seam and the propagation seam - a descriptor, a registry
that reports every backend whether or not it is wired, and an `assess()` that
answers `NOT_ASSESSED` rather than raising or guessing when nothing is
available. Unlike `GraphPropagationProvider`, there is no in-process default
that computes a real answer: dependency propagation is this demonstrator's
own subject matter, network physics is not, and a same-repository stand-in
would be exactly the second, uncoordinated model this design keeps refusing
to build (see the dependency-graph section below).

Its one backend, `PowerSystemSolverProvider`, reports itself unwired for two
reasons that do not reduce to each other: no service in ato-energy-platform
exposes `PowerSystemSolver` over a network boundary yet, so there is nothing
to call without a source import across repositories this demonstrator is not
permitted to take; and even if there were, nothing in the current asset model
could fill in the request - every asset carries one `location` string, not a
bus, a line or an impedance. RQ-024 names both and neither is built around.

### The dependency graph reports; it does not act

`resilience/dependencies.py` can say that COMMS-01 is outside the cooling it
needs, name ECS-MIN-01 as the provider that stopped and follow the chain to the
supply that ran out. It cannot make anything happen. Nothing in it sheds a load,
derates an asset or changes a dispatch decision, and a test asserts that running
it leaves the simulation identical (AS-027).

That is a deliberate line rather than an unfinished feature. A graph that started
shedding the communications load when its cooling went short would be a second,
unverified model of the same node standing beside the simulator, and the two
would disagree without anybody being told which was right - the same failure mode
as a composite score, arrived at from a different direction. The physics stay
where the MILP verifier can check them.

Three consequences follow, and all three are visible rather than papered over.
The graph and the simulation can contradict each other in front of the operator
(RQ-023). Every edge carries a capacity that somebody chose (AS-028, RQ-022).
And the propagation backend is a declared port - `PropagationProvider`, the same
shape as the solver seam - so RODOT's mature implementation reports itself as
declared and unwired rather than being quietly absent.

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

### The session plans on the premise and lives in the world

An operations session holds two environments. The *planned* environment is the
mission's stated premise - host-nation supply at the hours the MissionSpec names,
the weather profile it assumes, the load profiles it declares. The *realised*
environment is whatever the node is actually being given. Advancing time uses the
realised world; projecting forward and assessing the mission use the planned one.

That split looks like a bug until you ask whose future is being shown. Projecting
on the realised world hands the operator a forecast they do not have - the
machine would be quietly using knowledge of how the disturbance ends. Projecting
on the premise shows the operator exactly the future they are currently working
from, *including its error*, which is the thing that has to be made visible
before it can be corrected.

`operations/premises.py` is what makes the error visible. Three detectors compare
observations already recorded by the simulator against what the MissionSpec
asserts: host-nation availability (AS-008), the declared critical load profiles
(AS-001), and solar yield against the weather profile (AS-008). Each breach names
the assumption it contradicts, quantifies what it costs against the projection
the plan is working from, and offers a revised premise.

Two properties are load-bearing, and `tests/test_premises.py` asserts both:

* **Detection uses only what has already happened.** No detector looks past the
  current hour, because the operator cannot either. The question is never what
  the world will do; it is whether what the node has already seen contradicts
  what the plan is still assuming.
* **The machine notices; it does not decide.** `check_premises()` never changes
  what the planner plans against. Revising a mission assumption is an operator's
  decision, so it happens in `accept_premise_revision()`, which is only ever
  reached from an operator action and is recorded in the decision log beside
  every other decision they made. The detector thresholds themselves are chosen,
  not derived, and are registered as AS-022 - then measured, moved and measured
  again under RQ-017.

Which contradictions are worth interrupting an operator about is a separate
question from whether one has occurred, and it is answered in the next section.

The reason this is in the architecture note rather than in a feature list: it is
a different *kind* of capability from everything else here. The rest of the
demonstrator makes the machine plan better. This checks whether it is planning
against the right world - and RQ-014 found a case where that is worth more (the
mission completes) than a better optimiser against the wrong premise (one extra
hour). See RQ-016.

### An alarm is raised only when it crosses a line the mission states

The premise panel from RQ-016 has one failure mode that matters more than being
wrong: being ignored. An operator told twice that the world has changed, and
twice wrong, stops reading it - and then misses the third one. So detection is
two stages, not one.

The detectors answer *is this premise contradicted by what the node has seen*.
The session then answers *does planning on the revision cross a line the mission
states* - the mission status changes, a critical function is no longer safe,
assured support no longer covers what is left, or a stated requirement goes into
breach. Only the second kind is raised. The first kind is reported as a note, in
the same panel, in one quiet line.

The rule is deliberately referenced to the mission rather than to a delta.
Measuring a difference is easy and says nothing: RQ-017 found the weather-yield
detector firing on a sky genuinely half as bright as forecast, moving the
projected reserve from 14.0 h to 12.4 h against an 8 h requirement. Correct,
real, and not something any operator would act on.

Two limits, both measured, both at RQ-017:

* The rule trusts the revision the machine itself offered. A revision that
  understates the change can quieten an alarm that mattered.
* It cannot help with a contradiction that is real on everything observed so far
  and turns out transient. At the hour of the alarm those are indistinguishable,
  and one of the worlds in the harness is exactly that case. It is the price of
  noticing early, not a defect to be tuned away.

Nothing is hidden by this. A noted premise keeps its evidence, its revision and
its accept button; it is demoted, not suppressed, because the operator is still
the one who decides what to plan against.

### A rule is improved by reading what it does, not by adding a solver

RQ-009 measured the transparent dispatch rules giving up 10-22 % of the fuel
against a perfect-foresight solver. The instinct is to field the solver. What
RQ-015 did instead was print the two schedules side by side, and the defect was
legible in ten seconds: from H+20 the rules run GEN-A at 25.03 kW every hour
with the battery pinned at its charge target, while the optimum alternates
45 kW / nothing and lets the battery sawtooth.

`GeneratorMode.CYCLED` already claimed to do this - *"run it hard, and use the
surplus to recharge the battery so it can be stopped again"*. The stop test
required that nothing was already running, so it could decline to start a set
and could never stop one. The rule was half-implemented against its own
docstring, which is the second time in this pack that has been the finding
(RQ-017 found the same shape in the grid detector).

The replacement is two clauses, and both are things an operator can predict:

1. Stop the set as soon as the battery can carry the node without breaking its
   reserve - not merely decline to start one.
2. Never start a *second* set just to refill the battery.

The second clause is what makes the first one safe. With only the first, the
node ran both light sets at once on MM-DEMO-002 to refill the battery faster and
finished 5.6 % *worse*: node-hours fell, set-hours did not, and it paid two
no-load bills an hour. That is the whole mechanism, and it is why the saving
decomposes into exactly two readable parts - no-load fuel not burned, and energy
not generated to sit unused in a battery at the end of the mission.

**It is measured and not adopted**, and that distinction is the design decision
worth arguing about. The rule captures 50-85 % of the gap; it takes the
generator starts on a 72-hour mission from one or two to between four and
eleven. This model prices a start at the fuel burned in the step it happens and
at nothing else (AS-026). Making the saving the default would publish a benefit
whose cost the model cannot see - so the shipped rule is unchanged, every
published figure still reproduces, and the new one is one flag away with its
whole table printable by `mission-machine rules`.

### What may not be relied on is said, not filtered out

MM-DEMO-002 must be able to displace within the hour, and the 60 kW generator on
its pad is trailer-mounted. Three different parts of the system had an opinion
about that and only one of them was right: the MissionSpec validated it and
reported it, the planner ignored it entirely (192 of 336 candidate
configurations used the asset), and the resilience analyst offered *"Commit
GEN-HV-01"* as a recovery from a generator failure with nothing said about the
hour's notice to move.

The fix is not a filter, and the reason is a rule this design keeps returning
to: **whether to lift the relocation requirement for a better generator is a
command decision.** Withholding the option takes that decision away from the
operator by hiding it. So the option is still generated, still ranked, and now
carries what relying on it costs - `requires: the relocation requirement lifted:
GEN-HV-01 cannot displace inside 60 min`. It is the same shape as the
pre-authorised-degradation caveat: the machine states the price and the human
pays it or does not.

### Options that do not differ say so

A mission whose binding constraint is not the one the default strategies are
shaped around gets three options that are the same option under three names. On
MM-DEMO-003 - a field hospital where heat binds and fuel is ample - the three
defaults returned identical secondary coverage and differed only in fuel, of
which the mission had 1 400 L spare; `MAX_SUPPORTED_FUNCTIONS`, implemented but
not in the default set, returned an option serving every function.

The planner now reports when its options are materially the same on every
reported metric, and names the strategies it did not run. It deliberately does
*not* choose the strategy set from the mission, tempting as that is: a
demonstrator that silently widens its own search on a rule nobody can see has
started deciding what the operator should be shown. Saying "these three do not
differ, and here is what was not tried" leaves that where it belongs. Choosing
the set properly is a Pack 2 item.

### An alarm is a state, not an event

The panel from RQ-016 raises a contradiction when it crosses a line the mission
states. RQ-018 asked what it does on the *next* hour, and the answer was: says
the same thing again. In one mission the same true contradiction was raised 71
times, once an hour, each time carrying exactly the information of the first.
Ninety-five interruptions across twelve worlds, six of which were news.

So a contradiction now has a lifecycle, and the states are the distinctions an
operator actually needs:

| State | Meaning | On the panel |
| --- | --- | --- |
| RAISED | new, or crossing a line it had not crossed before | an alarm |
| STANDING | still true, already said, nothing further crossed | one quiet line, with any decision recorded against it |
| NOTED | contradicted, but crosses no line the mission states | one quiet line (RQ-017) |
| RESOLVED | said once, when it stops mattering, with which reason | one quiet line |

Three properties are load-bearing:

* **Idempotent within an hour.** Asking twice at the same hour gives the same
  answer. Refreshing a screen is not an event, and must not quietly turn an
  alarm into old news.
* **Getting worse still speaks.** Suppression is only defensible because a
  standing alarm that crosses a *new* stated line is raised again. Its cost is
  known and registered as AS-024: a contradiction can worsen without crossing a
  new line - a reserve falling from 2 h to 0.5 h crosses the requirement once -
  and in that case the operator is not told twice.
* **Stopping is not the same as being fixed.** RQ-018 found the grid alarm going
  quiet at H+44 because that is where the mission stops promising supply, not
  because anything was resolved. A resolution now says which of the two it was.

### A handover is a record, not a summary

`OperationsSession.handover()` assembles what one watch hands the next: what is
standing, what the outgoing watch decided and the rationale they wrote, what
nobody has decided, and the decision log. Every line of it is a record the
session already held - the brief is assembled, never authored, and it contains
no recommendation. A test asserts that, because a machine that tells the
incoming watch what to do about an inherited premise has quietly taken the
decision the rest of this design refuses to take.

The record it assembles from is what RQ-018 had to add. Accepting a revision was
always logged; *declining* one was not, so an outgoing watch that read an alarm
and decided to wait left no trace, and the incoming watch could not tell that
from nobody having looked. `dismiss_premise_revision` records it with a
rationale. It is deliberately not a mute button: the alarm stays standing, and
is raised again if it crosses a new line.

None of this says an operator will read any of it. That is RQ-018, it is not
answered, and the experiment that would answer it is specified in
`docs/research/shift-study-protocol.md`.

### The harness that measures the panel could report it failing

`operations/alarms.py` prices the panel, and the thing that makes it evidence
rather than decoration is that no world in it carries a hand-written label
saying whether the premise "really" changed. Such a label would be the author
marking their own homework.

Instead each world is run three times from the hour the panel speaks, differing
only in the premise planned against - the premise as stated, the revision
offered, and the world as it really is - and the verdict falls out of the
outcomes. An alarm where planning on the truth would have gained nothing
interrupted the operator for nothing, whatever the threshold says was breached.
Outcomes are compared on critical shortfall, then discretionary service, then
fuel, each with a deadband (AS-023): a lexicographic order over three separately
reported quantities, not a composite score.

The third arm is the right premise, not the best play - it replans with the same
enumerate-and-rank engine as the others - so "perfect knowledge would have gained
nothing" is a bound on what the premise was worth, not on what the node could
have achieved. It has been beaten by the deliberately pessimistic revision, which
is worth knowing on its own.

The world set is built to be able to produce a bad answer. Five of the twelve
worlds are ones where the premise is right or nearly so - including supply that
blinks and comes back, and a load that wobbles hour to hour while drawing
exactly the stated energy over the mission. A harness of catastrophes only would
have reported the detectors as flawless.

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
| Premise detection at any hour | 0 | ~0.4 ms |
| Building the dependency graph | 0 | ~0.06 ms |
| Propagating consequences over one hour | 0 | ~0.15 ms |
| Comparing both dispatch rules on one option | 2 | ~0.1 s |
| Assembling a handover brief | 12 | ~0.15 s |
| Quantifying what one breach costs | 2 | ~0.13 s |
| Pricing one alarm against three premises (RQ-017) | ~40 | ~8 s |
| Full test suite (269 tests) | several thousand | ~5 min |

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
