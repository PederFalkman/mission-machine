# Research register

Mission Machine is a research demonstrator. This register states the questions
Pack 1 exists to test, what the demonstrator can currently say about each one,
and what would be needed to answer it properly.

Every finding below comes from running the bundled MM-DEMO-001 scenario. All
input data is **SYNTHETIC**, all results are **SIMULATED**, and nothing here is
**VALIDATED**. A finding is a statement about the model, not about the world.
Reproduce them with:

```
python3 -m mission_machine demo
python3 -m mission_machine verify
```

Status values: `OPEN` (not yet addressed), `PARTIAL` (evidence from the model
only), `ADDRESSED IN MODEL` (the demonstrator answers it within its own
assumptions, which is the most Pack 1 can claim).

---

## RQ-001 - Can mission requirements be converted into an optimisation problem without excessive manual engineering?

**Why it matters.** If every new mission needs an engineer to hand-build a
model, the system is a consultancy service, not a product.

**How Pack 1 addresses it.** A mission is one JSON document
(`data/missions/mm-demo-001.json`, 82 lines) referencing an asset inventory.
`MissionSpec.validate()` checks it and returns problems as text for the operator
rather than raising. `planning/milp.py:build_model` turns that document
mechanically into a mixed-integer linear program - 1368 variables, 144 of them
binary, 937 constraints for a 72-hour mission - with no per-mission code.

**What Pack 1 found.** The conversion is mechanical for everything that fits the
supported vocabulary: five load-profile types, affine generator fuel curves, one
storage model, availability windows. Nothing in MM-DEMO-001 needed custom code.
Two things did *not* convert cleanly. One has since been closed, and the way it
was closed is the more interesting result:

* **Operator priorities - now converted.** The ranked priority statements were
  originally shown to the operator and never read by the optimiser. They are now
  carried as a closed vocabulary of intents - NEVER_INTERRUPT, MAINTAIN,
  DEGRADE_ACCEPTABLE, SERVE_IF_AFFORDABLE, DISCRETIONARY, MINIMISE - alongside
  the operator's own words, which are never parsed. See RQ-008 for what that
  took and what it changed.
* **Reserve semantics.** "Minimum reserve" had to be given an explicit type
  (`hours_of_critical_load`) before it could be modelled at all. A bare number
  would have been unusable. The same turned out to be true of every priority:
  the conversion is only mechanical once the *meaning* is stated.

**What would be needed to answer it.** Three to five more missions of genuinely
different shape (different asset classes, different failure modes, a mission
with mobility as a hard requirement), written by somebody who did not build the
schema, and a count of how much new code each one needed.

**Status: PARTIAL** - the schema converts mechanically; whether it generalises
beyond one mission is still untested.

---

## RQ-002 - Does joint configuration of generation, storage and loads materially improve mission endurance versus simple rule-based dispatch?

**Why it matters.** If a merit-order rule of thumb does as well, the planning
engine is not worth its complexity.

**How Pack 1 addresses it.** A rule-based baseline - one generator run
continuously, no storage, no PV, discretionary load shed - is simulated against
the configurations the engine generates from the same inventory.

**What Pack 1 found (SIMULATED).**

| Approach | Fuel used | Endurance | Minimum reserve | Completes the 72 h mission |
| --- | --- | --- | --- | --- |
| Rule-based: single generator, run continuously | 520 L (all of it) | 62 h | 0.0 h | **No** |
| Best jointly configured option (OPTION B) | 406 L | 72 h | 15.7 h | Yes |
| Recommended option (OPTION A, with redundancy) | 431 L | 72 h | 14.0 h | Yes |

The reserve column counts only energy each approach can actually reach. The
rule-based baseline never connects the battery, so its reserve is zero at its
worst hour - the 96 kWh sitting in the BESS is reported separately as withheld,
with the question that would release it.

Joint configuration saved 22 % of the fuel and turned a mission that fails at
H+62 into one that completes with 114 L in the tanks - while also keeping UAS
charging running, which the rule-based baseline sheds. The saving comes from
three effects the rule of thumb cannot capture: taking host-nation supply when
it is available, cycling the generator against the battery so it runs at high
loading instead of idling, and shedding discretionary load *before* it depletes
the reserve rather than after.

**Caveat.** The comparison is internal to one model and one synthetic scenario.
It shows the mechanism is real inside the model; it does not size the benefit in
the field.

**What would be needed to answer it.** The same comparison against a dispatch
rule that a real support unit actually uses, on measured load and fuel data.

**Status: ADDRESSED IN MODEL.**

---

## RQ-003 - Can the system explain why one configuration is preferable?

**Why it matters.** An unexplained recommendation cannot be authorised, argued
with, or overruled on informed grounds.

**How Pack 1 addresses it.** `explainability/explain.py` builds every
recommendation from the computed metrics: the reasons in WHY are derived from
comparisons the reader can check on the COMPARE table, and each trade-off names
the option it is measured against and by how much. There is deliberately **no
composite score** - the dimensions are shown separately, because a weighted
total would hide exactly the judgement the operator is there to make.

**What Pack 1 found.** Explanations of the form "this option, on this metric,
against that option, by this much" are generated mechanically and stay correct
when the scenario changes, because they are computed rather than written. Two
limits showed up immediately:

* Explanations describe *what* differs, not *why the physics* made it differ.
  "Uses 20 L more fuel than OPTION B" is checkable; "because GEN-A idles through
  the grid window" is not yet generated.
* When two options are near-identical the generated reasons read as padding. The
  system does not currently say "these two options are materially the same".

**What would be needed to answer it.** Put the explanations in front of people
who plan support for a living and measure whether they can predict what the
system will do next, and whether they catch its mistakes.

**Status: PARTIAL.**

---

## RQ-004 - How sensitive are recommendations to uncertain load and weather assumptions?

**Why it matters.** A recommendation that only holds if the weather behaves is a
liability presented as an answer.

**How Pack 1 addresses it.** `explainability/explain.py:sensitivity` re-runs the
recommended configuration under four one-factor perturbations - every load 20 %
heavier, ambient +6 degC, solar yield cut to 25 %, and host-nation supply never
available - and reports a confidence level derived from how many of them the
critical functions survive. The perturbations and the result are shown on the
same screen as the recommendation.

**What Pack 1 found (SIMULATED).** For MM-DEMO-001 the recommended option holds
critical assurance in 3 of the 4 perturbations - 20 % heavier load, +6 degC and
heavy overcast all leave the 72 hours intact. It fails only when host-nation
supply never appears, where assured support ends at H+55. That correctly
identifies the grid assumption (AS-008), not the weather, as the load-bearing
one. Confidence is reported as MEDIUM on that basis.

**Limits.** Four one-factor perturbations are not an uncertainty analysis. There
is no joint variation, no probability attached to any variant, and the
perturbation magnitudes are themselves assumptions (AS-008, AS-011).

**What would be needed to answer it.** Distributions rather than point variants,
combined perturbations, and a decision rule that says how much robustness is
worth how much fuel.

**Status: PARTIAL.**

---

## RQ-005 - Can a degraded-state reconfiguration preserve critical functions better than static contingency plans?

**Why it matters.** This is the core claim of the product: that replanning
against the situation beats a plan written before it.

**How Pack 1 addresses it.** Two disruption scenarios are run against a
configuration selected at H+0. `SC-DEGRADED-001` removes Generator B at H+30;
`SC-DEGRADED-002` additionally removes the host-nation supply the plan expected
to return. In each case the system re-assesses the current configuration,
regenerates options from the surviving assets, and lists recovery actions with
their simulated effect.

**What Pack 1 found (SIMULATED).**

* **Single generator loss.** Critical functions are held for the full mission on
  the existing configuration. What is lost is margin: tolerance to losing the
  next generator falls from 42.8 h to 3.0 h, and GEN-A becomes a single point of
  failure. The system reports this as a change of *assurance*, not of
  capability - it does not raise a false alarm, and it does not stay silent
  either.
* **Compound loss.** Critical functions can no longer be held to the end of the
  mission. **No reconfiguration of supply recovers it**: the fuel on site is
  simply insufficient. What does recover it is giving up part of a function -
  and because the operator pre-authorised exactly that in the MissionSpec
  (priority 3, DEGRADE_ACCEPTABLE on the shelter cooling), the planner now
  returns feasible options that use it, each labelled with the authorisation it
  relies on and an instruction to confirm it still stands. The same action is
  also offered against the current configuration, quantified (+3 h of assured
  support, ready in 10 minutes).

**This is the most useful result in Pack 1.** The value of reconfiguration was
not that the machine found a clever new supply arrangement; it was that it
established quickly that no such arrangement exists, and named the specific
sacrifice that would work. A static contingency plan written at H+0 would have
had to anticipate this exact combination to offer the same.

**What would be needed to answer it.** A real contingency plan for a comparable
node, and a blind comparison of what it prescribes against what the system
proposes, judged by people who would have to carry it out.

**Status: ADDRESSED IN MODEL.**

---

## RQ-006 - Which parts of the workflow require human judgement and must remain outside automation?

**Why it matters.** Getting this boundary wrong in either direction is fatal:
automate a judgement and the system becomes untrustworthy; automate nothing and
it becomes useless.

**How Pack 1 addresses it.** The boundary is enforced in code, not in policy
text. `Recommendation.operator_decision_required` is always true; the
`OperationsSession` will not advance past a disruption without a recorded
`OperatorDecision`; and every decision is logged with whether it followed the
recommendation.

**What Pack 1 found.** Running the scenarios, four judgements turned out to be
ones the machine should not make:

1. **Accepting a degraded function.** In the compound scenario this is the only
   action that saves the mission. It means letting equipment run warmer than its
   nominal setpoint. The machine can price it; it cannot decide it. What it
   *can* do is act on a decision the operator already made: priority 3
   pre-authorises the degradation, and the planner uses it only when nothing
   else is feasible, says which authorisation it is relying on, and asks for
   confirmation. The judgement was made when the mission was written, not by the
   planner.
2. **Whether to depend on host-nation supply.** Grid dependence is 37-39 % in
   every generated option. Whether that dependence is acceptable is a question
   about the situation, not about energy.
3. **Whether to accept a single point of failure.** OPTION C is cheaper in
   assets and burns more fuel and has no ride-through at all. That is a command
   trade, not an optimum.
4. **Which discretionary functions matter.** The engine sheds UAS charging
   because it costs energy. Whether the sortie rate it supports matters more
   than 7 L of fuel is not in the model, and should not be.

Conversely, three things clearly belong to the machine: enumerating feasible
configurations, computing consequences of a failure, and checking that a
proposed schedule is physically possible.

**What would be needed to answer it.** Observation of operators using the tool
under time pressure, and specifically of what they do when they disagree with it.

**Status: PARTIAL.**

---

## RQ-008 - What is the right way to express operator priorities so that they reach the optimiser?

**Why it matters.** Pack 1's largest gap. The MissionSpec carried the
commander's priorities as ranked sentences; nothing downstream could read them,
so the planner shed a function the operator had explicitly asked for and the
system needed a footnote to explain why.

**How it was addressed.** A closed vocabulary of seven intents, carried
*alongside* the operator's own words rather than parsed out of them:
NEVER_INTERRUPT, MAINTAIN, DEGRADE_ACCEPTABLE, SERVE_IF_AFFORDABLE,
DISCRETIONARY, MINIMISE (naming a quantity), and ADVISORY. Each one reaches a
specific place in the planner: serving and shedding order, which secondary
functions a configuration attempts, which candidates the strategies are allowed
to rank over, and which option is put forward first.

**What Pack 1 found (SIMULATED).**

1. **A closed vocabulary beats parsing.** Six of the seven statements in
   MM-DEMO-001 mapped onto an intent without strain. The seventh - "keep the
   node's physical footprint and emissions signature as small as the mission
   allows" - maps onto nothing, because Pack 1 does not model signature
   (AS-010). Marking it ADVISORY and saying so on screen is a better answer than
   any guess: the operator can see exactly which of their instructions the
   machine is and is not acting on.

2. **"If it does not threaten critical functions" is a tier, not a tie-break.**
   Implemented as a preference, it changed nothing - every objective still shed
   UAS charging, because serving it always costs fuel. It only works as a
   feasibility tier: the strategies rank over candidates that serve the
   prioritised functions, and drop to ones that do not only when no feasible
   candidate can. All three options now keep UAS charging running; the footnote
   is gone.

3. **"Never" means through a failure.** The sharpest finding. Reading "minimise
   fuel resupply exposure" (priority 5) as a straightforward objective led the
   planner to recommend the option with 3 h of ride-through over the one with
   42.8 h - and the mission's own mandated failure then broke it. The operator
   had never ranked redundancy, so nothing outranked fuel. The fix was not to
   overrule them: it was to read priority 1 properly. "Communications must never
   be interrupted" is a stronger statement than "communications are served in
   the plan we drew up", so a NEVER_INTERRUPT function now puts configurations
   that survive the loss of the largest generator ahead of cheaper ones. The
   threshold is the mission's own deployment time limit - hold it for at least
   as long as standing this node up takes, or there is no time to do anything
   about the failure.

4. **Pre-authorisation is how a human decision reaches the planner in advance.**
   "A degraded setpoint is acceptable if it buys endurance" is an authorisation,
   given before the event. When the compound failure leaves nothing feasible,
   the planner now applies exactly that degradation, to exactly the function it
   was authorised for, and labels every resulting option with which
   authorisation it is leaning on and an instruction to confirm it still stands.
   Before, the same failure produced three infeasible options and a recovery
   action somebody had to notice.

5. **The explanation got better for free.** Because the priorities are ranked
   data, the recommendation can quote the line that decided it. "Serves
   UAS-CHG-01 without threatening the critical functions, as operator priority 4
   asks" is checkable against the mission in a way that "best balance of
   endurance and output" never was.

**What it cost.** Honouring priority 4 costs 25 L of fuel across the three
options, and putting priority 1 above priority 5 costs another 25 L. Both are
reported as trade-offs rather than absorbed.

**What would be needed to answer it properly.** Missions written by somebody who
did not design the vocabulary. The open question is not whether these seven
intents work for MM-DEMO-001 - they do - but what the eighth one is, and whether
an operator would recognise their own intent in the form the machine ends up
with.

**Status: ADDRESSED IN MODEL.**

---

## RQ-009 - Where does the enumerate-and-simulate baseline stop being good enough?

**Why it matters.** Pack 1 chose a configuration by enumerating candidates and
simulating each one under transparent dispatch rules. That is what makes the
demonstrator explainable, and it was never checked against anything: there was
no optimum to compare with, only the rules and their output. Two separate
questions hide in there - does the *search* scale, and is the *dispatch* any
good?

**How Pack 1 addresses it.** Both are now measured.

The search is measured directly, by adding synthetic generators to the inventory
and counting candidates (`mission-machine scaling`). The dispatch is measured by
putting the same configuration to a real branch-and-cut solver - CBC, through
the `OptimisationProvider` seam - with the asset set fixed, the critical loads
required in full, and a floor requiring at least as much energy delivered to
each discretionary function as the simulated schedule delivered
(`mission-machine optimise`). Every solver answer is checked against the same
declared constraint set that checks the simulator's schedules before it is
believed.

**What Pack 1 found (SIMULATED).**

*The search is exponential, and the boundary is close.*

| Dispatchable generators | Candidate configurations | Time to evaluate all |
| --- | --- | --- |
| 2 (as shipped) | 312 | 2.5 s |
| 3 | 696 | 6.0 s |
| 4 | 1 464 | 12.7 s |
| 5 | 3 000 | ~26 s (extrapolated) |

The candidate space roughly doubles per dispatchable asset at about 8.7 ms per
candidate. Interactive use breaks somewhere around five or six: an operator will
not wait a minute for options, and the growth does not flatten.

*The dispatch rules give up real fuel.* For each option the planner put forward,
against the optimum for the same asset set and the same delivered service:

| Configuration | Rule-based dispatch | Optimum for the same configuration | Left on the table |
| --- | --- | --- | --- |
| OPTION A | 431.0 L | 334.6 L | **22.4 %** |
| OPTION B | 405.9 L | 357.3 L | 12.0 % |
| OPTION C | 420.3 L | 375.6 L | 10.6 % |

All three solver answers were verified against the declared constraint set.
Note where the gap is largest: OPTION A, the configuration with two generators.
The merit-order commitment rule is weakest exactly where there is most choice
about which machine runs when, which is the behaviour one would predict and had
never been measured.

**What this does not say.** Two caveats travel with every one of those numbers,
in the code as well as here:

* **Perfect foresight.** The solver knows every hour of load, weather and grid
  availability in advance. The dispatch rules do not, so the gap is an upper
  bound on what a causal controller could recover. RQ-012 went and measured how
  tight a bound: twelve hours of lookahead recovers 96-100 % of it, so this
  caveat narrows the finding far less than it appears to.
* **Not proven optimal.** Each solve stopped at a 60-second budget with a
  solution CBC had not proved optimal, so the true optimum is no higher than the
  figures above and the gap is a *lower* bound on what the rules give up. The
  two caveats push in opposite directions and neither is negligible.

**What it changes.** Less than one might expect, and that is itself the finding.
The enumeration picks the *configuration*; the solver improves the *dispatch
within* it. A 10-22 % fuel saving is worth having, but none of it changes which
option the planner puts forward or whether the mission is feasible - so the case
for a solver in Pack 2 is about scale and about fuel, not about correcting the
demonstrator's advice.

**What would be needed to answer it properly.** A rolling-horizon comparison, in
which the solver is given only the forecast a real node would have, would
separate "the rules are crude" from "the rules cannot see the future". That is
the honest way to size the recoverable saving, and it is the obvious next
experiment.

**Status: ADDRESSED IN MODEL.**

---

## RQ-012 - How much of the solver's advantage is foresight rather than skill?

**Why it matters.** RQ-009 measured the dispatch rules against a solver that
knew every hour of the mission in advance, and attached a caveat: the gap is an
upper bound on what any causal rule could recover. That caveat was doing a lot
of work. If most of the 10-22 % was clairvoyance, the finding was close to
useless - no controller can have it. If most of it was not, the saving is real
and somebody should go and get it.

**How Pack 1 addresses it.** The mission is replanned on a fixed cadence over a
finite lookahead window (`planning/rolling.py`): solve the next `window_h`
hours, commit the first `commit_h`, carry the node state forward, solve again.
Sweeping the window measures what foresight was worth. Two details decide
whether the numbers mean anything, and both are in the code:

* Every window that does not reach the end of the mission credits its closing
  stored energy at the best generator's fuel rate. Without that, a finite window
  empties the battery on its last step - energy it cannot see a use for is free
  to spend - and a short horizon gets punished for a modelling artefact instead
  of for its lack of foresight (AS-020).
* The discretionary service floor is applied per window, so a rolling plan
  cannot win by deferring service past the end of the comparison.

The harness is checked against the case where the answer is known: a window as
long as the mission reproduces the single full-horizon solve exactly.

**What Pack 1 found (SIMULATED).** Fuel over the 72-hour mission, by how far
ahead the controller can see. Replan cadence 6 h, 10-second budget per solve.

Each cell is the fuel used over the whole mission, and the share of the
perfect-foresight gap that much lookahead recovers.

| Lookahead | OPTION A | | OPTION B | | OPTION C | |
| --- | --- | --- | --- | --- | --- | --- |
| none (the dispatch rules) | 431.0 L | - | 405.9 L | - | 420.3 L | - |
| 6 h | 360.9 L | 73 % | 390.8 L | 31 % | 410.5 L | 22 % |
| **12 h** | **334.6 L** | **100 %** | **357.4 L** | **100 %** | **377.5 L** | **96 %** |
| 24 h | 336.5 L | 98 % | 357.3 L | 100 % | 375.6 L | 100 % |
| 48 h | 336.8 L | 98 % | 357.3 L | 100 % | 375.6 L | 100 % |
| whole mission | 334.6 L | 100 % | 357.3 L | 100 % | 375.6 L | 100 % |

**Almost none of it was foresight.** Twelve hours of lookahead recovers between
96 % and 100 % of the gap on all three configurations, and six hours recovers
between a fifth and three quarters of it. The caveat attached to RQ-009 was too
kind to the rules: what they give up is not clairvoyance, it is commitment logic
- which machine runs when - and a controller that can see half a day ahead
captures essentially all of it.

Three things follow, and the second was a surprise:

1. **Twelve hours is the threshold because the mission's grid windows are about
   fourteen.** Host-nation supply is available H+0 to H+14 and H+30 to H+44. A
   six-hour window cannot see across a grid transition and so cannot prepare for
   one; a twelve-hour window mostly can. The number is a property of this
   scenario, not a universal constant, and the way to predict it elsewhere is to
   look at the timescale of the disturbances rather than to reuse "12".

2. **A shorter horizon beat a longer one.** On OPTION A, 24 h and 48 h came out
   slightly *worse* than 12 h (336.5 L and 336.8 L against 334.6 L), because
   those windows carry two and four times the binaries and hit their time budget
   without proving optimality, while a 12-hour window solves out comfortably.
   Under a fixed compute budget more lookahead is not monotonically better - a
   real and slightly counter-intuitive operating point, and one that only shows
   up on the configuration with two generators to choose between.

3. **Rolling is cheaper than the single solve it matches.** Twelve 12-hour
   solves took 1-2 seconds in total across all three configurations. The single
   full-horizon solve each of them equals took 60-90 seconds and never proved
   optimality. Going to 48-hour windows costs 66-86 seconds to end up no better.
   The tractable way to use a solver here is also the realistic one.

**What this still does not say.** Within its window the controller has a perfect
forecast. This measures the value of *lookahead length*, not of *forecast
accuracy*: a node whose twelve-hour forecast is wrong - the grid does not come
back at H+30 as the plan assumed - will do worse than any figure above. That is
RQ-014, and it is the experiment that would finally retire the caveat rather
than narrow it.

**Status: ADDRESSED IN MODEL.**

---

## RQ-014 - What does a wrong forecast cost?

**Why it matters.** RQ-012 showed that twelve hours of lookahead recovers the
whole fuel gap, but gave the controller a perfect forecast inside its window.
Real forecasts are wrong, and the mission's own assumption register says so
(AS-008: "grid availability follows a fixed, known schedule of windows... real
supply fails without warning"). A saving that evaporates when the forecast is
wrong is not a saving.

**How Pack 1 addresses it.** The controller plans against one world and lives in
another (`planning/rolling.py:run_closed_loop`). Each replan solves its window
against the *forecast*; the committed hours are then carried out in the world
that actually happened, with the generator commitment fixed to what the plan
decided - a machine cannot be synchronised retrospectively - and everything else
re-balancing. With the binaries pinned that re-balancing is an LP, which is
exactly what real-time economic dispatch is. Where even that is infeasible the
node reverts to its dispatch rules, and each such window is counted.

Three arms are run in each realised world: the dispatch rules alone; the
controller told the truth; and the controller told the nominal forecast. The
middle arm is what separates *the world got harder* from *the forecast was
wrong*.

The disturbance is the one the mission's own scenario is about: host-nation
supply is forecast to return at H+30, and actually returns later, or never.

**What Pack 1 found (SIMULATED).** Fuel over the mission, in the world that
happened. `x N` counts windows where the plan could not be carried out at all.

*OPTION A - two generators, PV, battery*

| Grid actually returns | Rules only | Correct forecast | Nominal forecast | Saving surviving |
| --- | --- | --- | --- | --- |
| as forecast (H+30) | 431.0 L | 336.4 L | 336.4 L | - |
| 4 h late | 470.9 L | 372.8 L | 406.9 L `x1` | 65 % |
| 8 h late | 510.3 L | 407.5 L | 425.9 L `x1` | 82 % |
| never | fails at H+69 | **completes, 520 L** | fails at H+70 | - |

*OPTION C - one generator, battery, no PV*

| Grid actually returns | Rules only | Correct forecast | Nominal forecast | Saving surviving |
| --- | --- | --- | --- | --- |
| as forecast (H+30) | 420.3 L | 377.2 L | 377.2 L | - |
| 4 h late | 463.5 L | 418.0 L | 420.4 L `x1` | 95 % |
| 8 h late | 506.7 L | 496.8 L | 496.8 L `x2` | 100 % |
| never | fails at H+67 | fails at H+67 | fails at H+67 | - |

**Most of the saving survives, and none of the assurance is lost.** At a four to
eight hour error, between 65 % and 100 % of the fuel saving remains, and
critical-load coverage stays at 100 % in every completed run. Rolling replanning
is doing what it is supposed to do: the forecast is wrong, but it is corrected
every six hours, and the node never rides a stale plan for longer than that.

Two things are sharper than the headline.

1. **What threatens the mission is the world being hard, not the forecast being
   wrong.** On OPTION C at eight hours late, the saving shrinks to 2 % whether
   the forecast is right or wrong, because the node is running flat out and
   there is no slack left to optimise. Optimisation is worth something exactly
   where there is choice, and a hard enough world removes the choice before it
   removes the forecast.

2. **When the disturbance is severe, knowing about it is worth more than
   optimising.** In the world where the grid never comes back, OPTION A told the
   truth *completes the mission* on its 520 L, while the dispatch rules fail at
   H+69. The same optimiser told the nominal forecast fails at H+70 - it buys one
   hour over doing nothing at all. The value there is in the information, not in
   the solver; a system that could tell the operator "the grid is not coming
   back" would be worth more than one that dispatches perfectly against the
   wrong premise.

**A negative result worth recording.** The first design handed the dispatch
rules only the solver's *commitment* - which machines are synchronised - and let
them balance. With a perfectly correct forecast that was **worse than no plan at
all**: 441.8 L against the rules' 420.3 L on OPTION C. A plan is a coherent
whole. It commits a generator in an hour because it means to run it hard and
bank the surplus, and a dispatcher handed only the commitment pays the no-load
fuel without banking anything. Transferring the storage trajectory as well
recovered part of it but not all. That is why the execution model is
fixed-commitment economic dispatch rather than a rule-based hybrid, and the
hybrid machinery is kept (`Simulator.run(commitment_schedule=, storage_schedule=)`)
because the negative result is a finding and somebody will propose that design
again.

**What this still does not say.** One disturbance, on one scenario, with the
error always in the same direction - the grid is late or absent, never early or
better than forecast. Load and weather forecasts are not perturbed at all. The
result says a rolling controller is robust to *this* kind of error on *this*
mission; it does not establish a general robustness margin.

**Status: ADDRESSED IN MODEL.**

---

## RQ-016 - Is it worth telling the operator that the premise has changed?

**Why it matters.** RQ-014 found that past a certain size of disturbance,
optimising against the wrong premise is nearly worthless: with host-nation
supply gone for good, a controller told the truth completed the mission, while
the same controller told the mission's stated premise failed one hour later than
doing nothing at all. Everything else in this demonstrator makes the machine
plan better. That result asks whether it should instead be *checking whether it
is planning against the right world.*

**How Pack 1 addresses it.** `operations/premises.py` compares what the node has
observed against what the MissionSpec asserts, and where the two have parted
company it says so, quantifies it, and offers a revised premise. Three premises
are checked, each tied to the assumption it comes from: host-nation availability
(AS-008), the critical load profiles (AS-001), and solar yield against the
weather profile (AS-008).

Two properties are load-bearing and are asserted in the tests rather than
described here:

* **Detection uses only what has already happened.** Nothing looks at the
  future, because the operator cannot either. The question is whether what the
  node has *already seen* contradicts what the plan is still assuming.
* **The machine notices; it does not decide.** Revising a mission assumption is
  an operator's decision, so `check_premises` never changes what the planner
  plans against. `accept_premise_revision` does, it is only ever called by an
  operator, and it is recorded in the decision log beside every other decision
  they made.

Making this possible needed a split the demonstrator did not have: the session
now plans and projects on the mission's premise - that is what the operator
believes - while advancing through whatever world it is actually given.
Projecting on the realised world would have handed the operator a future they do
not have.

**What Pack 1 found (SIMULATED).** Running MM-DEMO-001 in a world where
host-nation supply never returns, and checking at H+36:

```
PREMISE CONTRADICTED - GRID_AVAILABILITY
  The mission says: supply is available H+0 to H+14, H+30 to H+44.  (AS-008)
  OBSERVED: expected and absent for 6 h: H+30, H+31, H+32, H+33, H+34, H+35

  WHY IT MATTERS
    - The mission reads AT_RISK on the premise as stated and DEGRADED on what
      the node has actually seen.
    - Assured support from here is -3 h against the projection the plan is
      working from (33 h of 36 h remaining).
    - Minimum energy reserve is -7.3 h lower than projected.
    - Critical functions that the stated premise hides as safe:
      COMMS-01, C2IT-01, MED-01, ECS-MIN-01.

  REVISION OFFERED: plan on no host-nation supply from H+30 to the end.
```

The last line of WHY IT MATTERS is the answer to the question. On the premise as
stated the OPERATE screen reports all four critical functions supported and 36 of
36 hours assured; on what the node has actually seen, all four are at risk and
three of those hours are not there. The plan was not wrong about the node - it
was wrong about the world, and every downstream number inherited that error while
reading as survivable.

When the operator accepts the revision, replanning on the truth returns three
**feasible** options at 301 L, where the configuration they were running was
heading for DEGRADED. The value is not in dispatching better. It is in planning
against a world that exists.

**What this cost.** Very little: three detectors, about two hundred and sixty
lines, no solver, and nothing the simulator was not already recording. Detection
runs in under a millisecond; quantifying what a breach costs is two projections,
about a tenth of a second. Against the rest of Pack 2 that is the cheapest
capability measured here and, on RQ-014's evidence, the most valuable in the case
that actually threatens the mission.

**What this does not say.** The detectors are threshold rules tuned on one
scenario - two hours of missing supply, ten per cent of load, a thirty per cent
shortfall in yield, as first shipped - and the thresholds are chosen, not derived
(AS-022). A real deployment would have to answer what a false alarm costs, since
an operator who is told twice that the premise has changed and is twice wrong
will stop reading the panel.

*That paragraph is why RQ-017 exists, and RQ-017 did not leave it standing.*
Pricing those false alarms found two defects in the detectors above, moved the
supply threshold from two hours to three, and stopped a contradiction being
raised at all unless it crosses a line the mission states. The figures reported
in this section were all re-checked afterwards and are unchanged; the detector
they came from is not the one described here. Read RQ-017 with this section.

**Status: ADDRESSED IN MODEL.**

---

## RQ-017 - What does a false alarm cost?

**Why it matters.** RQ-016 found the premise panel worth more than a better
optimiser in the case that actually threatens the mission, and in the same
breath raised the objection that undermines it: an operator told twice that the
world has changed, and twice wrong, will stop reading the panel - and then miss
the third alarm, the true one. The detectors are threshold rules tuned on one
scenario. Nothing said how often they fire on a difference that did not matter.

**A correction to how this question was first written.** The register said
sizing this "needs people, not more simulation". That was half right and the
wrong half was load-bearing. Whether an operator's trust survives a false alarm
does need people. But *how often the panel speaks where there was nothing to
gain*, and *what acting on it costs*, are measurable here - and measuring them
found two defects and moved a shipped threshold. The question was not as
unanswerable as its author claimed.

**How Pack 1 addresses it.** `operations/alarms.py` runs eleven worlds the node
might really be living in. Each is planned on the premise the MissionSpec states
and then lived in, hour by hour, through an ordinary operations session. Where
the panel speaks, three continuations are compared from that hour, differing
only in the premise they plan against:

```
IGNORE   the premise as stated - the operator dismissed the panel
ACCEPT   the revision the machine offered
TRUTH    the world that is really there - nobody has this
```

Every arm replans at the same hour and is then carried out in the same world, so
the only variable is the premise. What knowing the premise was worth is TRUTH
against IGNORE; what the alarm captured of it is ACCEPT against IGNORE. An alarm
that fires where even the true premise would have gained nothing interrupted the
operator for nothing, whatever the threshold says was breached.

TRUTH is the right premise, not the best play: it replans with the same
enumerate-and-rank engine as the others, so it is a bound on what the premise
was worth, not on what the node could have achieved. It can be beaten, and was -
against supply four hours late the deliberately pessimistic revision came out
ahead of planning on the truth, because it committed differently. Which is worth
knowing on its own: being right about the world is not the same as playing it
well.

Two properties of the harness matter as much as its results:

* **No world carries a label saying whether the premise "really" changed.** Such
  a label would be the author marking their own homework. Whether there was
  anything worth saying is computed, from what planning on the truth would have
  been worth.
* **The world set can produce a bad answer.** Five of the eleven are worlds where
  the premise is right or nearly so, including supply that blinks and comes back
  and a load that wobbles hour to hour while drawing exactly the stated energy
  over the mission. A harness of catastrophes only reports any detector as
  flawless.

Outcomes are compared on critical shortfall, then discretionary service, then
fuel - the mission's own ranking - each with a deadband (AS-023). Lexicographic
over three separately reported quantities, not a composite score.

**What Pack 1 found (SIMULATED).** At the thresholds RQ-016 shipped, eight
alarms on eleven worlds: four worth raising, two nuisance, **two harmful**. The
two harmful ones are the finding.

In `grid-flicker`, supply drops for one hour at H+6 and again at H+10 and is
otherwise present exactly as promised. The panel declared the premise
contradicted and offered to plan on *no host-nation supply for the rest of the
mission*. Accepting it gave up 68 kWh of discretionary service - 83 % of it, most
of the mission's charging and auxiliary load - to save 33 L of fuel that did not
need saving. In `grid-blink`, a single two-hour dropout that then recovered did
exactly the same, to the litre.

The first of those was a defect, not a threshold: the detector counted *every*
missing hour ever observed, while the constant documenting it said
*consecutive*, and dated its revision from the first hour that ever went missing
rather than from the run that actually breached. Fixed to match its own
documentation - only a run still running counts - `grid-flicker` goes quiet and
nothing else moves. That fix is why the flicker figures above are historical;
`mission-machine alarms --grid-hours 2` still reproduces the identical case in
`grid-blink`, which the fix does not reach.

`grid-blink` survived that fix, because a two-hour dropout meets a two-hour
threshold exactly. So the threshold itself was swept, on all eleven worlds:

| Consecutive hours | Alarms | Worth raising | Nuisance | Harmful | Supply that never returns |
| --- | --- | --- | --- | --- | --- |
| 1 h | 7 | 5 | 0 | **2** | caught H+31 |
| 2 h *(as shipped)* | 6 | 4 | 1 | **1** | caught H+32 |
| **3 h** | **4** | **4** | **0** | **0** | caught H+33 |
| 4 h | 4 | 3 | 1 | 0 | caught H+34 |
| 6 h | 3 | 3 | 0 | 0 | caught H+36 |

Three hours is where this mission's curve turns. Every alarm it raises is worth
raising; the disturbance that actually threatens the mission is still caught one
hour later than before, which costs nothing measurable. Past four the detector
starts arriving after the answer has stopped being useful - at four hours the
alarm for supply returning four hours late lands at H+34, the hour supply comes
back, and its revision captures nothing. `GRID_BREACH_HOURS` is now 3.0 on that
evidence.

The third finding needed no threshold at all. The weather-yield detector fired
on a sky genuinely half as bright as forecast and moved the projected reserve
from 14.0 h to 12.4 h against an 8 h requirement: correct, real, and nothing any
operator would act on. Raising it spends the attention the next alarm needs. So
a contradiction is now *raised* only where planning on the revision crosses a
line the mission states - the status changes, a critical function is no longer
safe, assured support no longer covers what is left, or a stated requirement
goes into breach. Everything else is reported as a note in the same panel, one
quiet line, with its evidence and its revision still there. Demoted, not hidden:
the operator is still the one who decides what to plan against.

After all three changes, on the same eleven worlds: eight alarms became four,
all four worth raising, no nuisance, none harmful, and nothing missed.

```
grid-4h-late       H+33   fuel  -14 L against ignoring it       WORTH_RAISING
grid-8h-late       H+33   fuel  -32 L                           WORTH_RAISING
grid-never         H+33   critical shortfall -9 kWh             WORTH_RAISING
load-15pc-heavier  H+1    fuel  -11 L                           WORTH_RAISING
as-forecast, grid-2h-late, grid-blink, grid-flicker,
load-6pc-heavier, load-noisy, overcast                          silent
```

Reproduce with `python3 -m mission_machine alarms`, and the sweep with
`--sweep 1 2 3 4 6`.

**What this does not say, and it is most of the question.**

*The clean sheet is not a validation.* The threshold was chosen on these eleven
worlds and then scored on them. Fitting to the test set is exactly what that is,
and the honest reading of the final table is "no remaining failure of a kind
this harness contains", not "no remaining failures". A twelfth world written by
somebody else is worth more than another sweep.

*One class of false alarm is irreducible and should not be tuned away.* Supply
two hours late, at the hour of the alarm, is indistinguishable from supply that
never comes. At three hours that world happens to fall the right side of the
line - the table above shows it as a nuisance alarm at two hours and silence at
three - but nothing keeps it there on a mission whose windows are shaped
differently. No threshold both keeps the true alarms and drops that one, because
at the moment of the alarm the information genuinely supports it. It is the
price of noticing early, and the reason the answer to RQ-017 cannot be "tune
until the false alarms are gone".

*The materiality rule trusts the machine's own revision.* An alarm is quietened
when planning on the offered revision crosses no stated line - so a revision
that understates the change can quieten an alarm that mattered. No world here
produces that failure, which is not the same as it being impossible, and it is
the first thing to look for in a wider world set.

*And the trust question is untouched.* Everything above is material cost -
litres, kilowatt-hours, hours of assured support. What a false alarm costs an
operator's *attention* is not in any of these numbers. The experiment that would
answer it does not need a better simulator: it needs people who plan support for
a living, a run of shifts containing both true and false alarms in a realistic
mix, and a measurement of whether they still read the panel by the third day -
and whether they catch the one that matters when they do. That experiment is
RQ-018, and it is the first item on the Pack 2 list for this reason. Until
somebody runs it, the right claim for this capability is that its alarms can be
made materially worth raising, not that operators will keep reading them.

**Status: PARTLY ANSWERED IN MODEL** - the rate and the material cost are
measured; what a false alarm costs an operator's trust is not, and cannot be
measured here.

---

## New questions raised by Pack 1

These were not in the original register. They came out of building it.

* **RQ-007** - Is a one-hour planning step sufficient, or does the behaviour
  that protects communications during a source change (transfer break, UPS
  ride-through) have to be modelled to make the plan trustworthy? (AS-003)
* **RQ-008** - *Answered, in the model.* See below.
* **RQ-009** - *Answered, in the model.* See below.
* **RQ-010** - Should the demonstrator model the *time* a reconfiguration takes,
  not just its steady-state effect? Every recovery option carries a
  time-to-effect, but the simulation applies changes instantly.
* **RQ-012** - *Answered, in the model.* See below.
* **RQ-014** - *Answered, in the model.* See below.
* **RQ-016** - *Answered, in the model.* See below.
* **RQ-017** - *Partly answered, in the model.* The rate and the material cost
  are measured; what a false alarm costs an operator's trust is not, and cannot
  be. See below. (RQ-016, RQ-006)
* **RQ-018** - Does a premise alarm survive contact with a shift? The RQ-017
  harness prices one alarm at the hour it is raised. It says nothing about the
  second and third alarm of a 72-hour rotation, whether an operator who
  dismissed one reads the next, or what a handover does to a premise the
  outgoing shift decided to ignore. Needs people and a run of shifts, not a
  wider world set. (RQ-017, RQ-006)
* **RQ-015** - If twelve hours of lookahead recovers the whole gap, can the
  dispatch *rules* be improved to capture most of it without a solver at all?
  The deficiency is in which generator is committed when, not in seeing the
  future, and a better merit-order rule would keep the demonstrator's
  explainability. Cheaper than fielding a solver, and nobody has tried.
* **RQ-013** - Should the planner hand the operator a solver-produced schedule
  at all, given that it is a list of setpoints rather than a rule somebody can
  follow and check? The demonstrator currently uses the solver to *measure* the
  rules, not to replace them, and it is not obvious that replacing them would be
  an improvement in the field. (RQ-006)
* **RQ-011** - Is a single power-times-duration scalar an adequate way to report
  an energy-limited resource? `n_minus_1_ride_through_h` says the battery holds
  the critical load for 3.0 h, but a battery that can give 60 kW for 1.7 h or
  15 kW for 6.8 h is two different answers to two different questions, and the
  metric reports one number. The same trap is documented from the other side in
  capacity-machine's Pack 8, which resolves it by excluding energy-limited
  resources from full-window totals and reporting a separate partial-window
  offer rather than averaging. Raised by `docs/reuse-assessment.md`.
