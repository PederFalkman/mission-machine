# Pack 1 - deliverables, files and recommendation for Pack 2

## Deliverables against the brief

| # | Deliverable | Where | State |
| --- | --- | --- | --- |
| 1 | Repository structure | `mission_machine/` with the module boundaries in `docs/architecture.md` | Complete |
| 2 | MissionSpec schema | `mission_machine/mission/spec.py`, instance in `data/missions/mm-demo-001.json` | Complete |
| 3 | Asset model | `mission_machine/assets/` - generic `Asset` plus nine concrete types | Complete |
| 4 | Deterministic planning-engine baseline | `mission_machine/planning/engine.py`, evaluated by `mission_machine/simulation/simulator.py`, declared as a MILP in `mission_machine/planning/milp.py` | Complete |
| 5 | MM-DEMO-001 scenario | `data/missions/mm-demo-001.json`, `data/assets/synthetic-asset-set-001.json` | Complete |
| 6 | Three generated configuration types | `Strategy.MAX_ENDURANCE`, `MIN_FUEL`, `MIN_LOGISTICS`; a fourth, `MAX_SUPPORTED_FUNCTIONS`, is available on request | Complete |
| 7 | Generator-failure reconfiguration | `resilience/failures.py:GENERATOR_B_UNAVAILABLE`, handled by `operations/session.py:OperationsSession.inject` | Complete |
| 8 | Comparison UI | COMPARE screen; `mission-machine configure` on the command line | Complete |
| 9 | Operations / degraded-mode UI | OPERATE screen; `mission-machine operate --scenario` | Complete |
| 10 | Architecture note | `docs/architecture.md` | Complete |
| 11 | Research-question register | `docs/research/questions.md` - six questions answered as far as the model allows, four new ones raised | Complete |
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

### Application code - `mission_machine/` (26 files, ~7 100 lines)

```
mission_machine/__init__.py                     package, version, scope statement
mission_machine/__main__.py                     python -m mission_machine
mission_machine/cli.py                          demo / mission / configure / operate / verify / export-lp / assumptions / serve

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
mission_machine/planning/milp.py                MILP formulation, LP export, schedule verification, optional solver backends

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
mission_machine/operations/session.py           OperationsSession, MissionAssessment, ReconfigurationReport, OperatorDecision

mission_machine/ui/__init__.py
mission_machine/ui/server.py                    standard-library HTTP server and JSON API
mission_machine/ui/static/index.html            MISSION / CONFIGURE / COMPARE / OPERATE
mission_machine/ui/static/app.js                UI logic and SVG charts
mission_machine/ui/static/style.css             styling
```

### Synthetic data - `data/`

```
data/assets/synthetic-asset-set-001.json        14 assets: grid, 2 generators, BESS, PV, conversion, 8 loads
data/missions/mm-demo-001.json                  MM-DEMO-001, Resilient 72-hour Support Node
```

### Documentation - `docs/`

```
docs/architecture.md                            module boundaries, interfaces, design decisions, performance
docs/research/questions.md                      RQ-001 to RQ-006 with findings, plus RQ-007 to RQ-010
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
tools/render_assumptions.py                     regenerates docs/assumptions.md from the register
pyproject.toml                                  packaging; zero runtime dependencies
.gitignore
README.md                                        replaced
```

126 tests, about 64 seconds, no dependencies, no network.

---

## What Pack 1 does not do

Stated plainly so that no reader has to infer it:

* No adversary model, no targeting, no offensive capability, no signature or
  detectability modelling. Disruption is asset unavailability only.
* No real data of any kind. No connection to any data source.
* No MILP *solve* - the model is declared, exported and used to verify, but the
  baseline ranks an enumeration rather than solving.
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
no evidence labels. Test count 82 to 106, and 126 with the operator-priority work.

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

Details in `docs/reuse-assessment.md` and `docs/research/questions.md`.

## Recommendation for Pack 2

Ordered by what would most improve the demonstrator's ability to answer its own
research questions, not by what is most interesting to build.

Three items from the first version of this list are done and are recorded under
"What was built after Pack 1" above rather than here: making the operator's
priorities reachable by the optimiser, fixing the two reserve-metric defects, and
porting capacity-machine's three guardrail tests.

### 1. Replace the ranked enumeration with a real MILP / CP-SAT solve (RQ-009)

The formulation already exists and every schedule is already checked against it.
What is missing is the solve. Do it behind the `OptimisationProvider` interface
proposed in `docs/reuse-assessment.md`, keep the deterministic baseline as the
default, and make the result say which produced it. The reason to do this is not
speed - it is that the enumeration cannot scale past a handful of dispatchable
assets, and the research question about where that boundary lies is worth
answering with the real thing.

### 2. Add a dependency graph and consequence propagation (RQ-005)

Pack 1 knows that losing the conversion unit stops the node, but only because
the simulation produces zero. It cannot say *"the cooling system is short of
what it needs"*. A minimal asset-to-function dependency graph carrying capacity,
behind a `PropagationProvider` interface, would let the degraded-mode picture
name the mechanism rather than only the outcome. RODOT has a mature
implementation of exactly this; see the reuse assessment.

### 3. Settle one evidence vocabulary across the three products

RODOT has `E0`-`E6`, capacity-machine has `EvidenceStatus` plus a structured
`Provenance` record, Mission Machine has four flat labels. Three attempts at the
same idea that do not interoperate. Settle one, port it into Mission Machine as
frozen dataclasses, and keep the current labels as presentation. No coupling, and
afterwards the three systems can quote each other's numbers.

`OpenQuestion` and a computed `is_operational_truth` are already ported from capacity-machine; the graded scale itself is what remains.

### 4. Model the time a reconfiguration takes (RQ-010)

Every recovery option already carries a time-to-effect. Applying it instantly
makes fast and slow responses look identical, which is precisely backwards when
ride-through is 3 hours and PV deployment takes 90 minutes.

### 5. Ask the upstream capacity-service question before hardening the energy model

The brief names BESS models, energy-flow logic and capacity constraints as
reusable. Pack 1 built its own. capacity-machine turns out not to hold them
either - it consumes them from `interop-capacity-service` through a read-only
contract and is forbidden from re-implementing them. That service is where the
question actually lands, and it was not reachable from this session. Ask it
before Pack 2 makes the energy model harder to change.

### 6. Two more missions of a different shape (RQ-001)

MM-DEMO-001 is one scenario, written by the people who wrote the schema. A
mission where mobility is a hard requirement, and one where the binding
constraint is personnel rather than fuel, would test whether the MissionSpec
generalises or merely fits.

### Explicitly not recommended for Pack 2

* **Machine learning anywhere in the planning path.** The baseline is what makes
  the demonstrator explainable, and it is not yet good enough to be worth
  replacing. ML has a clear later role in mission parsing, scenario generation
  and operator interaction - none of which is on the critical path now.
* **A mission-assurance score.** Not until its semantics, weights and uncertainty
  can all be shown. The conditions are in `docs/architecture.md`.
* **Any claim of operational relevance.** The next honest step towards that is
  exposure to people who plan support for a living, not more features.
