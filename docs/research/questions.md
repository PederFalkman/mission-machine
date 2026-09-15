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
Two things did *not* convert cleanly and remain open:

* **Operator priorities.** The ranked priority statements in the MissionSpec are
  shown to the operator but are not read by the optimiser. Translating "keep UAS
  charging available if it does not threaten critical functions" into a
  constraint or objective term is unsolved, and it is the single largest gap
  between what the operator writes and what the machine plans against.
* **Reserve semantics.** "Minimum reserve" had to be given an explicit type
  (`hours_of_critical_load`) before it could be modelled at all. A bare number
  would have been unusable.

**What would be needed to answer it.** Three to five more missions of genuinely
different shape (different asset classes, different failure modes, a mission
with mobility as a hard requirement), written by somebody who did not build the
schema, and a count of how much new code each one needed.

**Status: PARTIAL.**

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
| Rule-based: single generator, run continuously | 520 L (all of it) | 62 h | 2.9 h | **No** |
| Best jointly configured option (OPTION B) | 398 L | 72 h | 16.6 h | Yes |
| Recommended option (OPTION A, with redundancy) | 418 L | 72 h | 15.5 h | Yes |

> **Known defect in the reserve column.** The rule-based baseline runs without
> the battery, but the reserve metric counts the battery's stored energy anyway
> (`docs/reuse-assessment.md`, defect 2). Its 2.9 h of reserve is entirely
> unreachable; the reachable figure is 0.0 h, which makes the contrast sharper,
> not weaker. The two jointly configured rows deploy the battery and are
> unaffected. Fix scheduled as Pack 2 item 4.

Joint configuration saved 23 % of the fuel and turned a mission that fails at
H+62 into one that completes with 122 L in the tanks. The saving comes from
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
supply never appears, where assured support ends at H+57. That correctly
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
  mission. Notably, **no reconfiguration of supply recovers it**: all three
  regenerated options remain infeasible, because the fuel on site is simply
  insufficient. The one action that restores critical assurance is accepting a
  degraded environmental-control setpoint - that is, giving up part of a
  function. The system identifies it, quantifies it (+1 h of assured support,
  +2.3 h of reserve, ready in 10 minutes) and refuses to take it.

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
   nominal setpoint. The machine can price it; it cannot decide it.
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

## New questions raised by Pack 1

These were not in the original register. They came out of building it.

* **RQ-007** - Is a one-hour planning step sufficient, or does the behaviour
  that protects communications during a source change (transfer break, UPS
  ride-through) have to be modelled to make the plan trustworthy? (AS-003)
* **RQ-008** - What is the right way to express operator priorities so that they
  reach the optimiser rather than stopping at the screen? (RQ-001)
* **RQ-009** - Does the enumerate-and-simulate baseline stay tractable as the
  inventory grows, and where exactly does a MILP or CP-SAT solve become
  necessary rather than merely preferable? At 6 supply assets the candidate
  space is 312 configurations and evaluation takes about 2 seconds; the space
  grows exponentially in the number of dispatchable assets.
* **RQ-010** - Should the demonstrator model the *time* a reconfiguration takes,
  not just its steady-state effect? Every recovery option carries a
  time-to-effect, but the simulation applies changes instantly.
* **RQ-011** - Is a single power-times-duration scalar an adequate way to report
  an energy-limited resource? `n_minus_1_ride_through_h` says the battery holds
  the critical load for 3.0 h, but a battery that can give 60 kW for 1.7 h or
  15 kW for 6.8 h is two different answers to two different questions, and the
  metric reports one number. The same trap is documented from the other side in
  capacity-machine's Pack 8, which resolves it by excluding energy-limited
  resources from full-window totals and reporting a separate partial-window
  offer rather than averaging. Raised by `docs/reuse-assessment.md`.
