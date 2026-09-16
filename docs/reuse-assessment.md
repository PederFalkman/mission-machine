# Reuse assessment: RODOT and Solid Soup

The Pack 1 brief asks whether existing capabilities from RODOT and Solid Soup
can be reused through adapters, and requires that all reuse be documented.

**Summary.** No code was copied. Mission Machine has no dependency on either
system and is independently deployable.

**RODOT** was inspected directly and has several capabilities worth adapting -
one of them, its Decide module, is close enough to Mission Machine's planning
contract that re-implementing it here would be a duplication of design effort.
It is TypeScript, so every option points at a service or contract boundary
rather than at source-level sharing.

**Solid Soup** is `PederFalkman/capacity-machine`. It is Python 3.11, which
changes the assessment: source-level reuse is possible here in a way it is not
with RODOT. More usefully, capacity-machine has already solved the problem
Mission Machine is about to have - how to build a standalone product beside an
authoritative upstream system without coupling to it - and enforces the answer
with tests rather than with a policy document. Applying two of its rules to
Mission Machine's own code found two live defects in the energy-reserve metric.
**Both are now fixed, and three of its guardrail tests have been ported**, which
is the whole of the reuse so far: no code was copied, and no dependency taken.

---

## What was inspected

| System | Access | What was examined |
| --- | --- | --- |
| RODOT | `PederFalkman/Rodot`, commit `ec92c25`, read-only shallow clone | `packages/domain`, `packages/ports`, `packages/contracts`, `packages/scheduler-cpsat`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md` |
| Solid Soup (`capacity-machine`) | `PederFalkman/capacity-machine`, commit `11f7e08`, read-only shallow clone | `src/capacity_machine/domain`, `providers`, `services`, the guardrail tests in `tests/`, `README.md`, `docs/ARCHITECTURE.md`, `docs/UPSTREAM_CONTRACT.md`, `docs/PACK_08_ENERGY_LIMITS.md` |

RODOT is a TypeScript pnpm monorepo (10 packages) under a proprietary licence
(Copyright Mica AB, all rights reserved). Mission Machine is Python. That
language boundary is a fact of the assessment, not an obstacle to be wished
away: it rules out source-level sharing and points every option towards a
service or contract boundary.

capacity-machine is a Python 3.11 FastAPI/pydantic service, about 7 400 lines of
source and 227 tests. It carries **no licence file**, which has to be settled
before anything is copied from it - see the licensing note at the end.

### A naming point that matters

The user identified Solid Soup as `capacity-machine`, and that is the repository
assessed here. capacity-machine's own documents, however, use "Solid Soup /
MICA" for the system *upstream of it*: `docs/UPSTREAM_CONTRACT.md` names
`interop-capacity-service` as authoritative for available capacity, hosting
capacity, dynamic headroom, constraints, capacity timeline and asset ranking,
and capacity-machine's first rule is that it must never import that code, share
its database, or re-implement its calculations.

So there are two bodies of code behind the one name, and only one of them was
reachable from this session. Everything below concerns capacity-machine. If the
Pack 1 brief meant the upstream capacity service - which is where raw
energy-flow and capacity calculations would actually live - that remains
un-assessed, and the way to reach it is the contract in
`docs/UPSTREAM_CONTRACT.md`, not a clone.

---

## RODOT capabilities relevant to Mission Machine

| RODOT capability | Where | Relevance to Mission Machine | Verdict |
| --- | --- | --- | --- |
| Course-of-action generation, one per **named objective**, with explicit refusal to blend objectives into a score | `packages/domain/src/decide.ts` | This is the same contract as `PlanningEngine.generate_options`: `MAX_CONTINUITY`, `MIN_LOSS`, `MIN_RESOURCE`, `MIN_TIME` against Mission Machine's `MAX_ENDURANCE`, `MIN_FUEL`, `MIN_LOGISTICS` | **Adopt the design; adapt later** |
| Recommendation-only enforcement (`assertRecommendationOnly`) | `packages/domain/src/action.ts` | Mission Machine enforces the same rule via `Recommendation.operator_decision_required` and the `OperatorDecision` log | **Design already adopted** |
| Graded evidence levels `E0`-`E6` with `isAtLeast` floors, and decision-grade gating | `packages/domain/src/evidence-level.ts`, `evidence.ts` | Strictly stronger than Mission Machine's four flat labels. A graded scale would let the planner refuse to plan on data below a floor. capacity-machine has a third version of the same idea; see adapter boundary 3, where all three are settled at once | **Adopt in Pack 2** |
| Dependency graph and disruption propagation carrying **capacity as well as state**, so a shortfall is distinguishable from an outage | `packages/domain/src/graph.ts`, `propagation.ts` | Rated the highest-value item here, and now built: `resilience/dependencies.py` derives the graph from the asset set and carries a capacity on every edge, so the degraded picture says "the shelter cooling is short of what it needs" rather than only "nothing was served". RODOT's own implementation stays behind a declared, unwired `PropagationProvider` - adapting it means a service boundary into TypeScript, which is a Pack 3 decision rather than an oversight | **Design adopted**; RODOT's implementation still a service-boundary decision |
| CP-SAT restoration scheduling out of process over a JSON contract, with a transparent list-scheduler fallback that names which produced the answer | `packages/scheduler-cpsat/src/index.ts` | Mission Machine now has the same shape in `planning/providers.py`: declared model, optional backend, an answer that names its source, honest `UNAVAILABLE` when no solver is present | **Adopted** - the sidecar itself is still unbuilt |
| `OptimisationProvider` port: `supports(problemClass)` plus `solve(request)` with an explicit time budget | `packages/ports/src/providers.ts` | Adopted almost verbatim as `planning/providers.py`, including the explicit time budget - which turned out to matter, since every solve of this model hits it | **Adopted** |
| Survey of what the system still needs to be told, each question saying what answering it would unblock | `packages/domain/src/survey.ts` | Mission Machine has nothing equivalent. Its MissionSpec validation reports what is *wrong*, not what is *missing and would change the answer* | **Adapt in Pack 3** |
| Readiness assessment that returns what is unrecorded, stale or incompatible instead of a readiness score | `packages/domain/src/readiness.ts` | Same philosophy as Pack 1's refusal of a composite assurance score | **Design already adopted** |
| Replay fingerprint: reproduce an analysis later | `packages/domain/src/record.ts`, `audit.ts` | Mission Machine is deterministic but does not fingerprint a run. Needed before any result is quoted in a report | **Adapt in Pack 2** |

### What was explicitly rejected

* **Copying `@rodot/domain` wholesale.** It carries a large amount of RODOT's own
  problem - tenancy, capabilities and credentials, retention and erasure,
  disagreement resolution, licence gating. Mission Machine needs none of it in
  Pack 1, and importing it would make a demonstrator depend on a platform.
* **Sharing types across the language boundary.** Generating Python types from
  `@rodot/contracts` (zod schemas) was considered and rejected for Pack 1: it
  couples release cycles for no benefit while both systems are still moving.
* **RODOT's energy demo content** (`packages/demo`). It is RODOT's scenario
  material, not a model, and Mission Machine's synthetic set has to stand alone
  and be labelled as its own.

---

## Proposed adapter boundaries for RODOT

If reuse proceeds, it should cross exactly two seams. Each is an interface
Mission Machine already has or would gain, so that the RODOT implementation is
substitutable and absence is survivable. Evidence grading is the third candidate
and is dealt with once, for all three systems, in boundary 3 below.

### 1. `OptimisationProvider` - solver backend - built

Built as `planning/providers.py`, with the shape this document proposed:

```python
class OptimisationProvider(Protocol):
    name: str
    def describe(self) -> ProviderDescriptor: ...
    def supports(self, problem_class: str) -> bool: ...
    def solve(self, model: MilpModel, *, time_budget_s: float) -> SolverOutcome: ...
```

CBC answers through it when PuLP is installed. Absence stayed survivable - the
deterministic baseline is the default and a missing backend returns
`UNAVAILABLE` pointing at the LP export - and every answer names the backend
that produced it, as both this document and RODOT's `scheduler-cpsat` insist.
`ProviderRegistry.status_report()` lists every declared backend wired or not,
which is capacity-machine's `registry.status_report()` pattern.

A RODOT-backed provider would now be a drop-in: run the CP-SAT sidecar over the
same JSON contract `packages/scheduler-cpsat` uses, behind this interface. That
remains unbuilt - see the CP-SAT note in `providers.py` for why the
reformulation is not something to do carelessly.

### 2. `PropagationProvider` - dependency and consequence propagation

```python
class PropagationProvider(Protocol):
    def propagate(self, graph: DependencyGraph, states: Mapping[str, OperatingState]) -> PropagationResult: ...
```

**Built.** `resilience/dependencies.py` carries the `DependencyGraph`,
`OperatingState` and `PropagationResult` named above, a `GraphPropagationProvider`
that answers with a single-pass walk, and a `RodotPropagationProvider` that is
declared and reports itself unwired for the reason this document gave: it would
be a service call into a TypeScript codebase the demonstrator deliberately takes
no dependency on, and the built-in walk answers RQ-005.

Two things came out of building it that the assessment did not anticipate. The
dependency the graph most needed was already in the asset data, as free text -
ECS-MIN-01's `function` asserts that the cooling keeps comms and IT within
limits - so the work was less modelling than giving an existing assertion
somewhere machine-readable to live. And capacity on the edge brings a number
with it: to say cooling at 60 % is a problem the graph has to hold the share
required, and nothing establishes that share (AS-028, RQ-022).

### What must not become an adapter

The MissionSpec, the asset model and the dispatch simulator. They are Mission
Machine's subject matter. Reusing someone else's model of them would mean the
demonstrator could not answer RQ-001 about its own schema.

---

## Solid Soup (capacity-machine) capabilities relevant to Mission Machine

capacity-machine is not an energy-flow library, and the capacity domain it
models - megawatts at a grid connection, firmness tiers, DSO interventions,
commercial offers - is not Mission Machine's subject matter. What it has that
Mission Machine needs is a **discipline for not lying about what you know**,
worked out in Python, in the same problem shape, and enforced by tests.

| capacity-machine capability | Where | Relevance to Mission Machine | Verdict |
| --- | --- | --- | --- |
| `Provenance`: provider, provider version, `source_ref` sufficient to re-fetch, `observed_at` / `retrieved_at`, `evidence_status`, stated confidence, uncertainty band, `synthetic` flag, and a validator that stops `synthetic` and `evidence_status` contradicting each other | `domain/provenance.py` | Strictly stronger than Mission Machine's four flat labels and its near-empty `Provenance`. In particular `is_operational_truth` is the property Mission Machine asserts in prose and does not compute | **Adopt the design - Pack 2** |
| `EvidenceStatus.UNAVAILABLE` held deliberately distinct from a value of zero: "a provider that cannot answer has told us nothing about capacity, and must never render that silence as 0 MW" | `domain/enums.py` | Mission Machine has this bug twice over. See "Two defects this assessment found" below | **Adopted** - see the defects section below |
| `CatalogueCoverage` / `SourceCoverage`: every capability source listed with `ANSWERED` / `FAILED` / `NOT_WIRED`, so a shortfall computed from a partial search is flagged `shortfall_is_upper_bound` | `domain/coverage.py` | Mission Machine reports "no feasible configuration supports more discretionary load" when what it can support is "none of the 312 candidates I enumerated did". Same claim, same gap between it and the evidence | **Adopt - Pack 2** |
| `OpenQuestion` with `withheld_mw`: prefer an explicit question to an invented number, and record the quantity withheld because of it | `domain/open_question.py` | Ported as `evidence/questions.py` with `withheld_kwh`, and used to carry the energy a configuration cannot reach. Converges with RODOT's `survey.ts` from the other direction | **Adopted** (reserve only; other metrics in Pack 2) |
| `CapacityStackLayer`: `claimed` vs `counted` vs `withheld_mw` with a reason, firmness downgrade recorded with the claim it replaced, and a refusal to add two layers relieving the same constraint unless independence is proven upstream | `domain/stack.py` | Mission Machine's metrics report totals with no attribution of what was withheld or why. The double-count rule has a direct analogue: PV output and grid import both relieve the same generator, and the reserve metric adds stored energy to fuel-equivalent energy without asking whether both are reachable | **Adapt - Pack 2** |
| **Energy-limited resources and partial windows** (Pack 8): a resource whose `sustainable_minutes` falls short of the requested duration is neither averaged into a smaller firm number, nor counted at full value, nor assumed to be sequenceable. Full-window totals exclude it, a separate partial-window offer includes it with `covered_minutes` and `covers_full_window: false`, and the sequencing question is *asked* | `domain/stack.py`, `domain/service_level.py`, `services/offer_builder.py`, `docs/PACK_08_ENERGY_LIMITS.md` | **The closest technical match in either system.** Mission Machine's `n_minus_1_ride_through_h` is exactly the MW-times-duration scalar that document warns about, and its `min_service_fraction` partial-service rule is the averaging trap in miniature | **Adopt the rule - Pack 2** |
| `ComparabilityIssue` with `BLOCKING` / `WARNING` severity: a scenario comparison carries its own caveats and declines to pick a winner when the two are not like for like | `domain/scenario.py` | Mission Machine compares a pre-failure and a post-failure assessment computed at different hours, from different node states, over different asset sets, and says nothing about whether they are comparable | **Adapt - Pack 2** |
| Provider ports as read-only `Protocol`s, a registry that reports status for every declared kind whether wired or not, and a composite that combines them | `providers/ports.py`, `registry.py`, `composite.py` | Adopted for the solver seam: `ProviderRegistry.status_report()` reports CBC and the unwired CP-SAT port alike, so "we did not ask" never reads as "we asked and found nothing". `PropagationRegistry` now does the same for the dependency graph, reporting the built-in walk and the unwired RODOT backend alike | **Adopted** for optimisation and for propagation |
| **Guardrail tests**: `test_standalone_boot.py` boots the app in a fresh interpreter with nothing on the path but the repository and scans the source for forbidden upstream imports; `test_no_write_methods.py` fails if any port ever grows an activation method; `test_synthetic_labelling.py` asserts synthetic evidence survives all the way to the response | `tests/` | Mission Machine had the weakest version of each | **Adopted** - all three ported |
| The isolation posture itself: a standalone product beside an authoritative upstream, consuming it through versioned read-only contracts, booting and testing with no upstream reachable | `README.md`, `docs/UPSTREAM_CONTRACT.md` | This is the pattern Mission Machine needs if it ever consumes RODOT or the upstream capacity service. It has been built once already; do not design it again | **Adopt the pattern** |

### What was explicitly rejected

* **The capacity domain.** `CapacityEnvelope`, `Firmness`, `CapacityStack`,
  `ServiceLevelOffer`, `CapacityEconomics` and the intervention catalogue model
  megawatts at a grid connection for a commercial customer. Mission Machine's
  node is behind the meter, its currency is kilowatt-hours and hours of
  endurance, and it has no commercial offer. Borrowing the vocabulary would
  make the demonstrator harder to read, not easier.
* **The dependency stack.** capacity-machine uses pydantic, FastAPI, httpx and
  uvicorn. Mission Machine runs on the standard library alone and can be
  demonstrated on a machine with no package manager and no network. That is
  worth more to a research demonstrator than pydantic's validators, so the
  models above should be **ported as dataclasses, not imported**.
* **Copying source.** With no licence file in the repository, nothing should be
  copied from it in any case. See the licensing note.

---

## Two defects this assessment found in Mission Machine - and how they were fixed

Applying capacity-machine's rule that *absence must never be rendered as zero*
to Mission Machine's own reserve metric found two real defects. Both were in
`simulation/simulator.py:_reserve`, and neither had been caught by 82 tests.

**1. Unburned fuel disappeared when no generator was committed.** A grid-only
configuration reported a 0.0 kWh reserve with 520 L untouched on site. 24 of the
312 candidate configurations commit no generator and every one of them
under-reported this way. Impact on any selection was nil - all 24 are infeasible
for an unrelated reason, since without a generator the node reaches only H+17 -
so this was a latent defect that would have become live the moment the inventory
changed.

**2. Battery energy was counted in configurations that do not deploy the
battery.** The mirror-image error, and the one with consequences: `_reserve`
checked whether a battery existed in the *inventory*, not whether the
configuration connected it. OPTION C's reported minimum reserve of 13.0 h was
really 9.3 h, a 28 % overstatement on an option that ships in the README, the
COMPARE screen and the demonstration script. The rule-based baseline used to
answer RQ-002 runs without the battery too, so its reported 2.9 h of reserve was
**entirely** unreachable energy.

### The fix

`_reserve` now counts only energy the configuration can actually deliver: the
battery must be deployed and serviceable, a generator must be committed to burn
the fuel, and a conversion unit must exist to move any of it. Energy that exists
but cannot be reached is neither counted nor dropped - it is returned as a
withheld quantity with an `OpenQuestion` attached, which is
capacity-machine's own answer transposed into kilowatt-hours:

```
CFG-A  reserve 15.5 h  withheld  0 kWh
CFG-B  reserve 16.6 h  withheld  0 kWh
CFG-C  reserve  9.3 h  withheld 96 kWh
       ? BESS-01 holds 96 kWh above its floor but is not deployed in this
         configuration. Should it be?
         Deploying it would add that energy to the reserve and give the node
         ride-through if a generator stops.
```

The withheld quantity and its question now travel through `ConfigurationMetrics`
to the CLI, the COMPARE screen and the API, so the number an operator sees is
the reachable one and the difference is on the same screen.

**What changed in the published results.** Nothing reversed and no ranking moved.
OPTION C's minimum reserve is now reported as 9.3 h and still clears the 8 h
requirement; the rule-based baseline in RQ-002 now reads 0.0 h, which sharpens
that finding rather than weakening it. Both tables have been corrected and their
footnotes removed. The rule is registered as assumption AS-015 and held by
`tests/test_synthetic_labelling.py`.

---

## Three guardrail tests, ported

capacity-machine's strongest idea is not in its domain model: it is that the
product's central claims are tests. Three have been adapted:

| Ported to | From | What it now enforces |
| --- | --- | --- |
| `tests/test_standalone.py` | `test_standalone_boot.py` | The demonstrator boots in an interpreter with no `PYTHONPATH` and no user site directory; nothing third-party is imported at module level; an optional solver backend may be imported inside a function only if it is declared as an extra in `pyproject.toml`; nothing imports RODOT or capacity-machine; nothing outside `ui/` imports a networking module |
| `tests/test_no_control_path.py` | `test_no_write_methods.py` | No public callable anywhere in the package reads as an actuation, the API exposes only the five routes that plan, assess or record a decision, and `CONTROL_PATH_ENABLED` stays False. The policy and its one exemption live in `evidence/control.py` |
| `tests/test_synthetic_labelling.py` | `test_synthetic_labelling.py` | Synthetic labelling survives plan, select, run, disrupt and replan, and every payload the API serves; `is_operational_truth` is computed from the labels, so the claim can fail |

Two changes to the product came out of writing them. `Recommendation` - the most
operator-facing object in the system - carried the disclaimer but no evidence
labels, and neither did `ReconfigurationReport`; both now do. And the naive
substring match for actuation names flagged `Battery.round_trip_efficiency`,
which is why `evidence/control.py:reads_as_actuation` matches whole words: a rule
that cries wolf is a rule somebody switches off.

The test count went from 82 to 106.

---

## Proposed adapter boundaries for capacity-machine

Unlike RODOT, nothing here needs to cross a process boundary. The reuse is at
the level of models and rules, and all of it is one-way: Mission Machine adopts
the discipline, and takes on no dependency.

### 3. `Provenance` and `EvidenceStatus` - a shared evidence vocabulary

The single most valuable thing the three products could share. RODOT has a
seven-level scale (`E0`-`E6`), capacity-machine has a six-state
`EvidenceStatus` plus a structured `Provenance` record, and Mission Machine has
four flat strings. They are three attempts at the same idea, and they do not
interoperate.

Recommendation: settle **one** vocabulary across all three, port it into Mission
Machine as frozen dataclasses (no pydantic), and keep the existing labels as a
presentation layer over it. This is a value-object mapping, not an integration;
it creates no coupling, and afterwards the three systems can quote each other's
numbers without re-deriving what they are worth.

### 4. Coverage and withheld quantities - partly done

`OpenQuestion` is ported (`evidence/questions.py`) and carries the withheld
energy in the reserve metric. What remains is `SourceCoverage` /
`CatalogueCoverage`: in Mission Machine terms the "sources" are the candidate
space, the sensitivity variants and the failure probes, and the claim they
qualify is every "no configuration can..." statement the planner makes. That is
still Pack 2 work.

### 5. Guardrail tests - done

All three ported; see the section above.

---

## Independence check

Mission Machine as delivered:

* has zero runtime dependencies (Python standard library only);
* contains no RODOT and no capacity-machine source, no identifiers from either,
  and no references to `@rodot/*` or `capacity_machine` in code or
  configuration;
* runs, tests and demonstrates with no network access and no sidecar;
* keeps every seam proposed above optional, with an in-process default.

This is no longer checked by reading. `tests/test_standalone.py` boots the
demonstrator in an interpreter with no `PYTHONPATH` and no user site directory,
scans every source file for imports of RODOT or capacity-machine, asserts that
nothing third-party is imported at module level, and asserts that
`pyproject.toml` declares no runtime dependency. Verify with:

```
python3 -m unittest tests.test_standalone
```

---

## Licensing - unresolved for both

Neither system can be copied from until this is settled, and it should be
settled before Pack 2 rather than during it.

* **RODOT** carries an explicit proprietary licence: *Copyright (c) 2026 Mica
  AB. All rights reserved.* Any reuse needs written permission, whatever the
  common ownership.
* **capacity-machine** carries **no licence file at all**. Absent one, the
  default is all rights reserved, and a repository with no stated licence is
  harder to reuse than one with a restrictive licence, because there is nothing
  to point at.

Nothing recommended in this document requires copying source from either
repository - every item is "adopt the design" or "port as dataclasses", which
is why the recommendations stand as they are. But the moment somebody proposes
lifting a file, this has to be answered first.
