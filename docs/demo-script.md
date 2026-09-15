# MM-DEMO-001 - five-minute demonstration

Two ways to run it: the command line tells the whole story in about three
seconds, the UI lets somebody drive it.

```
python3 -m mission_machine demo      # scripted, end to end
python3 -m mission_machine serve     # UI on http://127.0.0.1:8000
```

Open with one sentence: *"This is a research demonstrator. All the data is
invented. The point is not the numbers - it is whether a mission can be turned
into an infrastructure plan, and re-planned when something breaks, with the
operator still in charge."*

---

## 1. Open the mission (40 s) - MISSION screen

The top of the screen is the whole product in four boxes: **MISSION → AVAILABLE
RESOURCES → MISSION MACHINE → OPTIONS**.

Say: *"Four critical functions - communications, command and IT, medical, and
shelter cooling - must run for 72 hours. Four more are discretionary. There is
520 litres of fuel and no resupply. Host-nation power is there for 39 % of the
mission and the plan has to survive losing it."*

Point at the operator priorities. These are the commander's words, ranked - and
next to each one is the machine-readable intent the planner actually reads:
NEVER_INTERRUPT, MAINTAIN, DEGRADE_ACCEPTABLE, SERVE_IF_AFFORDABLE, MINIMISE,
DISCRETIONARY. Priority 7, about footprint and signature, is marked ADVISORY in
amber: the planner has no way to act on it and says so rather than pretending.

## 2. Show the assets (30 s) - same screen

Two generators, a 120 kWh battery, an optional mobile PV array, one power
conversion unit and a grid connection. Every row is labelled SYNTHETIC.

Point at the conditions chart: red is critical demand, the shaded blocks are the
hours when host-nation power exists, and it disappears at H+14 and again at
H+44.

## 3. Generate configurations (30 s) - CONFIGURE

Press **GENERATE CONFIGURATIONS**. 312 candidate configurations are evaluated in
about two seconds and three are put forward, one per stated objective:

* **OPTION A - maximum endurance**: both generators, PV, battery held high.
* **OPTION B - minimum fuel**: the smaller generator only. 25 L cheaper, and
  3 h of ride-through instead of 42.8 h.
* **OPTION C - minimum logistics burden**: nine assets instead of eleven.

Say: *"All three complete the mission, and all three keep UAS charging running -
because priority 4 asks for it and the planner can afford it. Vehicle charging
and welfare HVAC stay off: priority 6 calls them discretionary. The operator's
words decided that, not a threshold in the code."*

## 4. Compare them (60 s) - COMPARE

The side-by-side table. Each dimension is marked for whichever option wins it,
and there is no total.

Say: *"There is deliberately no overall score. Endurance against fuel against
redundancy against how much kit you have to move is the command judgement. If we
weighted it into one number we would be making that judgement for them, invisibly."*

The row that does the work is **ride-through after the largest generator is
lost**: 42.8 h for A, 3.0 h for B, zero for C. That is the difference the fuel
column does not show.

Then the discretionary line underneath: *"UAS charging, auxiliary and welfare
loads could be supported at 69 % coverage for 24 more litres and 6.7 fewer hours
of reserve. The machine will not decide whether that is worth it."*

## 5. Select one (20 s) - CONFIGURE

Read the RECOMMENDED panel - WHY, TRADE-OFFS, CONFIDENCE.

The WHY quotes the mission back: *"Holds COMMS-01 for 42.8 h after losing its
largest generator, against the 4 h it takes to deploy this node - so operator
priority 1 (Communications must never be interrupted) survives a single failure,
not only the plan as drawn."*

Say: *"That is the machine reading 'never'. The operator did not rank redundancy
anywhere; they said communications must never be interrupted, and the planner
takes that to mean through a failure, not just on paper. It costs 25 litres
against what priority 5 asks for, and the trade-off line says exactly that."*

Confidence is MEDIUM: the plan holds critical functions under heavier load,
hotter weather and heavy cloud, and fails only in the variant where host-nation
power never appears.

Press **SELECT THIS CONFIGURATION** on OPTION A and type a rationale. It is
recorded, with whether it followed the recommendation.

## 6. Run it (30 s) - OPERATE

Set the hour to 30 and press **ADVANCE**. Status ASSURED, 366 L left, battery at
95 %, reserve 15.5 h against an 8 h requirement. The dispatch chart shows the
node living off host-nation power, then switching to generation.

## 7. Break something (20 s)

Choose `SC-DEGRADED-001 - Generator B unavailable` and press **INJECT
DISRUPTION**.

## 8-10. What it says (90 s)

**WHAT CHANGED** - Generator B is gone; tolerance to losing the next generator
fell from 42.8 h to 3.0 h.

**WHY IT MATTERS** - *"Critical functions are still supported; what has been lost
is margin, not capability."* And then the sentence that matters: *"If the largest
remaining generator also stops, critical load can be held for only 3.0 hours -
less than the time needed for most recovery actions."* GEN-A is now a single
point of failure.

Say: *"Note what it did not do. It did not raise an alarm about functions going
down, because none are. It also did not stay quiet. It reported a change in
assurance, which is the thing a static plan would have missed."*

**AVAILABLE ACTIONS** - each with its simulated cost: accepting a degraded
cooling setpoint saves 17 L and buys 3.5 h of reserve, ready in ten minutes, at
the cost of a warmer shelter.

New options have been generated from the surviving assets. They are on CONFIGURE.

## 11. Operator decides (20 s)

Select a response. It goes in the decision log with the rationale and whether it
followed the recommendation.

---

## If there is time: the harder case

Reset, select OPTION A again, advance to 30, and inject
`SC-DEGRADED-002 - Generator B unavailable and grid not restored`.

Now the status is DEGRADED and all four critical functions are threatened. No
configuration of the equipment as it stands is feasible - the fuel on site is
simply not enough.

So the planner reaches for the one thing the operator already authorised. Priority
3 says *"a degraded setpoint is acceptable if it buys endurance"*, so the options
it now returns run the shelter cooling in its degraded mode, each one labelled
**"relies on the degraded mode the operator pre-authorised for ECS-MIN-01 -
confirm that authorisation still stands"**.

Say: *"This is the difference the priorities make. Before the operator's intent
was machine-readable, the same failure produced three infeasible options and a
recovery action somebody had to notice. Now the planner uses the authorisation
it was given in advance, returns feasible options, and tells the operator which
authorisation it is leaning on and to confirm it. It still did not decide to
degrade anything - that decision was made when the mission was written."*

## If there is time: when the plan is right and the world is wrong

Reset, select OPTION A, and on the MISSION screen set the world to **grid never
returns** - the node is now living in a world the mission did not describe.
Advance to H+36 on OPERATE.

The mission picture says all four critical functions are supported. Above it, the
premise panel says something else:

> **PREMISE CONTRADICTED - GRID_AVAILABILITY.** The mission says supply is
> available H+0 to H+14 and H+30 to H+44 (AS-008). Observed: expected and absent
> for 6 h, H+30 to H+35. On what the node has actually seen, the mission reads
> DEGRADED rather than AT_RISK, assured support is 3 h shorter, the minimum
> reserve is 7.3 h lower, and COMMS-01, C2IT-01, MED-01 and ECS-MIN-01 are at
> risk rather than safe.

Say: *"Both of those are on the screen at once, and that is deliberate. The lower
half is what the plan believes. The upper half is what the node has seen. The
plan was not wrong about the equipment - it was wrong about the world, and every
number below inherited that error while still reading as survivable."*

Accept the revision. Replanning on the truth returns three *feasible* options on
the 301 L still in the tanks - where the configuration they were running was
heading for DEGRADED.

Say: *"The machine noticed and offered. It did not revise the mission - that
click was the operator's, and it is in the decision log next to every other
decision they made. We measured this: against a disturbance this large, being
told the premise had changed was worth more than a better optimiser working from
the old one."*

## Closing

*"Everything you have seen is synthetic and simulated, and none of it is
validated. What is being demonstrated is that a mission can be stated in machine
readable form, turned into feasible infrastructure configurations with visible
trade-offs, and re-planned against a failure - with the operator holding the
decision throughout. The research questions this raises, including the ones we
could not answer, are in docs/research/questions.md."*
