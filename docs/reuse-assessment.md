# Reuse assessment: RODOT and Solid Soup

The Pack 1 brief asks whether existing capabilities from RODOT and Solid Soup
can be reused through adapters, and requires that all reuse be documented.

**Summary.** No code was copied. Mission Machine has no dependency on either
system and is independently deployable. RODOT was inspected directly and has
several capabilities worth adapting - one of them, its Decide module, is close
enough to Mission Machine's planning contract that re-implementing it here would
be a duplication of design effort. Solid Soup could not be located in the
repositories reachable from this environment, so that half of the assessment is
**incomplete and marked as such**, with the questions that would close it listed
at the end.

---

## What was inspected

| System | Access | What was examined |
| --- | --- | --- |
| RODOT | `PederFalkman/Rodot`, commit `ec92c25`, read-only shallow clone | `packages/domain`, `packages/ports`, `packages/contracts`, `packages/scheduler-cpsat`, `README.md`, `docs/ARCHITECTURE.md`, `docs/DOMAIN_MODEL.md` |
| Solid Soup | **Not found.** No repository matching "solid soup" / "solidsoup" is visible in the account reachable from this session, and the string does not occur anywhere in RODOT | Nothing |

RODOT is a TypeScript pnpm monorepo (10 packages) under a proprietary licence
(Copyright Mica AB, all rights reserved). Mission Machine is Python. That
language boundary is a fact of the assessment, not an obstacle to be wished
away: it rules out source-level sharing and points every option towards a
service or contract boundary.

---

## RODOT capabilities relevant to Mission Machine

| RODOT capability | Where | Relevance to Mission Machine | Verdict |
| --- | --- | --- | --- |
| Course-of-action generation, one per **named objective**, with explicit refusal to blend objectives into a score | `packages/domain/src/decide.ts` | This is the same contract as `PlanningEngine.generate_options`: `MAX_CONTINUITY`, `MIN_LOSS`, `MIN_RESOURCE`, `MIN_TIME` against Mission Machine's `MAX_ENDURANCE`, `MIN_FUEL`, `MIN_LOGISTICS` | **Adopt the design; adapt later** |
| Recommendation-only enforcement (`assertRecommendationOnly`) | `packages/domain/src/action.ts` | Mission Machine enforces the same rule via `Recommendation.operator_decision_required` and the `OperatorDecision` log | **Design already adopted** |
| Graded evidence levels `E0`-`E6` with `isAtLeast` floors, and decision-grade gating | `packages/domain/src/evidence-level.ts`, `evidence.ts` | Strictly stronger than Mission Machine's four flat labels. A graded scale would let the planner refuse to plan on data below a floor | **Adopt in Pack 2** |
| Dependency graph and disruption propagation carrying **capacity as well as state**, so a shortfall is distinguishable from an outage | `packages/domain/src/graph.ts`, `propagation.ts` | Mission Machine currently has no dependency graph: a failure is an asset going unavailable, and consequences are found by re-simulation. Propagation would let it model "the charger array is short of what it needs" rather than only "the generator stopped" | **Adapt - highest value** |
| CP-SAT restoration scheduling out of process over a JSON contract, with a transparent list-scheduler fallback that names which produced the answer | `packages/scheduler-cpsat/src/index.ts` | Mission Machine's `planning/milp.py` has the same shape - declared model, optional backend, honest failure when no solver is present. The *sidecar pattern* is directly reusable | **Adopt the pattern** |
| `OptimisationProvider` port: `supports(problemClass)` plus `solve(request)` with an explicit time budget | `packages/ports/src/providers.ts` | The right interface for Mission Machine's Pack 2 MILP/CP-SAT backend | **Adopt the interface shape** |
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

## Proposed adapter boundaries

If reuse proceeds, it should cross exactly three seams. Each is an interface
Mission Machine already has or would gain, so that the RODOT implementation is
substitutable and absence is survivable.

### 1. `OptimisationProvider` - solver backend

```python
class OptimisationProvider(Protocol):
    def supports(self, problem_class: str) -> bool: ...
    def solve(self, model: MilpModel, time_budget_ms: int) -> SolverOutcome: ...
```

`planning/milp.py:solve()` is already this shape. A RODOT-backed provider would
run the CP-SAT sidecar over the same JSON contract
`packages/scheduler-cpsat` uses. **Absence must remain survivable**: Mission
Machine's default stays the in-process deterministic baseline, and the result
says which backend produced it.

### 2. `PropagationProvider` - dependency and consequence propagation

```python
class PropagationProvider(Protocol):
    def propagate(self, graph: DependencyGraph, states: Mapping[str, OperatingState]) -> PropagationResult: ...
```

Mission Machine would need a `DependencyGraph` it does not yet have. The natural
Pack 2 step is to build a minimal one (asset -> function dependencies, with
capacity) behind this interface, and only then consider whether RODOT's
propagation implementation should serve it over a service boundary.

### 3. `EvidenceGrading` - evidence levels

Replace `evidence/labels.py`'s four flat labels with a graded scale compatible
with RODOT's `E0`-`E6`, keeping the existing labels as a presentation layer over
it. This is a value-object mapping, not an integration; it can be done with no
dependency at all, and should be, because the two systems then speak the same
language about how much a number is worth.

### What must not become an adapter

The MissionSpec, the asset model and the dispatch simulator. They are Mission
Machine's subject matter. Reusing someone else's model of them would mean the
demonstrator could not answer RQ-001 about its own schema.

---

## Independence check

Mission Machine as delivered:

* has zero runtime dependencies (Python standard library only);
* contains no RODOT source, no RODOT identifiers, and no `@rodot/*` package
  references in code or configuration;
* runs, tests and demonstrates with no network access and no sidecar;
* keeps every seam above optional, with an in-process default.

Verify with:

```
python3 -m unittest discover -s tests   # passes offline, no dependencies
grep -ri "rodot" mission_machine/ data/ # expected: no matches
```

---

## Solid Soup - incomplete

This half of the assessment could not be completed. To close it, three things
are needed:

1. **The repository or its location.** No repository named Solid Soup (or an
   obvious variant) is reachable from this session. The nearest candidates by
   subject matter in the same account are `capacity-machine`,
   `ato-energy-platform` and `energy`; none was inspected, because guessing at
   which system is meant would make the assessment unreliable.
2. **Its licence and ownership**, on the same footing as RODOT's, before any
   reuse is designed.
3. **A named capability list** - the brief mentions BESS models, energy-flow
   logic, capacity constraints and scenario evaluation. Those are precisely the
   areas where Mission Machine built its own in Pack 1
   (`assets/energy.py`, `simulation/simulator.py`, `planning/milp.py`), so the
   question for Solid Soup is not *can we reuse it* but *is its model better
   grounded than ours, and should ours be replaced*. That is worth asking
   directly, and it should be asked before Pack 2 hardens the energy model.

Until those are answered, nothing in Mission Machine assumes Solid Soup exists.
