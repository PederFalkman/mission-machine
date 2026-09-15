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

Point at the operator priorities. These are the commander's words, ranked, and
they are on the screen for the whole demonstration.

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
* **OPTION B - minimum fuel**: the smaller generator only. 20 L cheaper.
* **OPTION C - minimum logistics burden**: seven assets instead of ten, no
  battery, no PV - and 21 L more fuel than OPTION A. Because it does not deploy
  the battery, 96 kWh of stored energy is withheld from its reserve, with the
  question attached: *should it be deployed?*

Say: *"All three complete the mission. None of them serves any discretionary
load - the machine sheds all of it to protect the fuel, and says so."*

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

Read the RECOMMENDED panel - WHY, TRADE-OFFS, CONFIDENCE. Confidence is MEDIUM:
the plan holds critical functions under heavier load, hotter weather and heavy
cloud, and fails only in the variant where host-nation power never appears.

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

Now the status is DEGRADED and all four critical functions are threatened in the
last hour. Re-planning the supply does **not** fix it - every regenerated option
is infeasible, because the fuel on site is simply not enough. The only action
that restores critical assurance is accepting the degraded cooling setpoint.

Say: *"That is the most useful thing in the demonstrator. It established in two
seconds that no rearrangement of the equipment saves this mission, and named the
one sacrifice that does. It then refused to make that call."*

## Closing

*"Everything you have seen is synthetic and simulated, and none of it is
validated. What is being demonstrated is that a mission can be stated in machine
readable form, turned into feasible infrastructure configurations with visible
trade-offs, and re-planned against a failure - with the operator holding the
decision throughout. The research questions this raises, including the ones we
could not answer, are in docs/research/questions.md."*
