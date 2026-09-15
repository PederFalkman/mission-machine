# RQ-018 - shift study protocol

**Status: SPECIFIED, NOT RUN.** Nothing in this document is a result. It is the
experiment that would answer RQ-018, written down so that it can be criticised
before it is run rather than after, and so that the claim "this needs people"
carries an obligation rather than serving as an excuse.

Everything Pack 1 can say about the premise panel it has now said. RQ-016 found
it worth more than a better optimiser against the disturbance that actually
threatens the mission. RQ-017 priced its false alarms, found two harmful ones,
fixed the detectors and moved a threshold. RQ-018 found the panel raising the
same true contradiction 71 times in one mission and fixed that too. Every one of
those is a statement about the machine. None of them is a statement about
whether an operator, on the third day of a rotation, still reads it.

---

## The question, stated so it can fail

**Does a premise alarm survive contact with a shift?**

Three sub-questions, in the order they matter:

1. **Attention.** After one or more alarms that turn out not to have needed
   action, does an operator still open and act on the one that does?
2. **Handover.** Does a standing premise alarm, and the outgoing watch's
   decision about it, survive a shift change without being said out loud?
3. **Direction.** Is the panel used at all, in preference to the numbers below
   it, when the two disagree?

Sub-question 3 is the one that can show the whole direction to be wrong. If
operators consistently trust the mission picture over the premise panel that
contradicts it, then RQ-016's central finding - that noticing beats optimising -
does not survive contact with the people it was for, and the Pack 2 ordering is
wrong.

---

## Participants

Twelve to sixteen people who plan or run support for deployed units: energy,
logistics or engineering roles with responsibility for keeping something
running. Not systems engineers, and not the people who built this.

Nobody who has seen the demonstrator before. No security clearance required, and
none should be needed: everything in the study is synthetic, and the protocol
must not become a route by which real deployment data enters the exercise.

Twelve is not a power calculation. It is the number at which a 20-percentage-
point difference in the primary measure would be visible against the noise a
study this size carries, and the size at which sub-question 3 - which is
qualitative - can be answered from what people say while they work. If the
primary measure comes out close to the decision boundary, the honest conclusion
is "not resolved at this sample size", not a p-value.

## Design

Within-subjects, two conditions, counterbalanced order, two mission variants so
nobody sees the same disturbance twice.

| | Condition SHIPPED | Condition NOISY |
| --- | --- | --- |
| Alarms raised over the rotation | the four the harness scores as worth raising | those four plus the four RQ-017 measured as nuisance or harmful |
| Detector settings | as shipped (AS-022) | `GRID_BREACH_HOURS = 1`, materiality rule off |
| Everything else | identical | identical |

Condition NOISY is not a straw man. It is exactly what this repository shipped
at RQ-016 and would have kept shipping had the false alarms not been priced. The
comparison is therefore between the demonstrator as it was and as it is, which
is the decision a team actually faces.

Both conditions are reproducible from the repository:
`mission-machine alarms --load` prints the alarm sequence for the shipped
settings, and `--grid-hours 1` prints the noisy one.

## The task

Each participant runs a compressed 72-hour rotation as two watches of about
twenty minutes, with a handover in the middle. They are told what MM-DEMO-001
requires, given the OPERATE screen, and asked to keep the critical functions
supported for the duration. They are not told the study is about alarms.

The handover is between **two participants**, not one participant with
themselves: participant A's second watch is participant B's first. This is the
only way sub-question 2 can be answered, and it means participants must be
scheduled in pairs.

The true alarm that matters - host-nation supply that never returns - is placed
in the **second** watch of each rotation, after the handover, so that its
detection depends on what the incoming watch was told.

## What is measured

**Primary.** On the true alarm, the proportion of participants who open it and
reach a decision (accept the revision, or record a rationale for keeping the
stated premise) within ten simulated hours of it being raised. Compared between
conditions.

**Secondary, in order of how much they would change the design:**

* **Handover fidelity.** Does the incoming watch know about the standing premise
  and the outgoing watch's decision? Scored from what they say and do before
  anybody prompts them, not from a questionnaire.
* **Attention decay.** Alarms opened per watch, over the rotation, in condition
  NOISY.
* **Disagreement behaviour.** When the premise panel and the mission picture
  below it disagree, which do they act on? This is sub-question 3 and is the
  least measurable - it comes from what people say while working, recorded and
  coded afterwards.
* **Dismissal quality.** The demonstrator records a free-text rationale when an
  operator keeps the stated premise. Are those rationales usable by the next
  watch, or are they empty?

**Instrumentation.** All of it is already in the session record: the alarm
lifecycle (`StandingAlarm`: first raised, times raised, dismissed with rationale
and hour, resolved with reason), the decision log including `HANDOVER` and
`DISMISS_PREMISE_REVISION`, and the handover brief itself. Nothing needs to be
built to run this study, which is the point of having built it.

## Decided in advance

Stating these before the study is what stops the result being read to suit
whatever comes out.

* **The false-alarm cost is real** if the primary measure is at least 20
  percentage points lower in NOISY than in SHIPPED. Consequence: detector
  thresholds are set conservatively by default, and the sweep in
  `mission-machine alarms --sweep` becomes a setting a deploying unit is
  expected to make deliberately rather than a research artefact.
* **The false-alarm cost is not established at this rate** if the difference is
  under 10 points. Consequence: the thresholds stop being the interesting
  question and effort moves to the rest of the Pack 2 list.
* **Between 10 and 20 points**: not resolved. Say so, and say what a larger
  study would need.
* **Handover is a real gap** if fewer than two thirds of incoming watches
  mention the standing premise unprompted. Consequence: the brief is not
  sufficient and the handover needs to become something the incoming watch must
  act on rather than read.
* **The direction is wrong** if, in either condition, most participants act on
  the mission picture in preference to the premise panel that contradicts it.
  Consequence: RQ-016's finding does not survive contact with operators, and
  Pack 2 should not build further on the panel until it is understood why.

That last row is the reason to run this before building anything else.

## What this study cannot settle

* **Whether any of it transfers.** Twenty-minute compressed watches with a
  synthetic mission are not a rotation. A participant who knows they are in a
  study attends differently from one on the third night of a real deployment,
  and the direction of that bias is towards *more* attention, so a null result
  on attention decay would be weak evidence and a positive one strong.
* **Anything about the energy model.** The mission, the assets and the numbers
  are synthetic throughout, and a participant's judgement that the plan is
  reasonable is not evidence that it is.
* **Whether the alarms are correct.** That was RQ-017, it was measured on twelve
  worlds the same threshold was then chosen on, and this study does not revisit
  it.

## Standing scope limits

Unchanged from the rest of Pack 1 and restated because a study with people is
where they are most likely to erode. No classified material, no real deployment
data, no operational planning performed as part of the exercise, and nothing
recorded about a participant's own unit or deployments. Participation is
voluntary and withdrawable, and the demonstrator's disclaimer is shown to every
participant before they start: synthetic, simulated, unvalidated, and the
operator is the decision authority.
