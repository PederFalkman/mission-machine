# Physics AI / Defense interoperability plan

**Status:** PLANNED  
**Decision date:** 2026-10-03  
**Boundary:** Mission Machine stays human-in-the-loop. Physics AI may improve physical-state evidence and scenario evaluation; it does not decide command priorities or actuate infrastructure.

## REPO / REPOS

**Primary:** `mission-machine`  
**Secondary:** `mica-data-room` (canonical Physics-AI plan), `Rodot` (future recovery-state exchange), `PINNeAPPle-Defense` fork (research/reference only)

## Planned seam: AssetOperatingEnvelope

Mission Machine may consume a versioned physical-state envelope containing:

- power/energy availability;
- thermal derating;
- battery/generator usable state;
- electrical-feasibility result where available;
- environment/weather effects;
- communications/cooling support state where modelled;
- confidence and uncertainty;
- applicability-domain result;
- provenance;
- model/physics version;
- validity horizon.

This is evidence used by the existing planner/simulator/provider seams. It is not a new command layer.

## Defense / critical-infrastructure opportunities

Prioritise only cases that improve mission adequacy or resilience:

1. sparse-telemetry state estimation for remote/disconnected infrastructure;
2. thermal/environmental derating of power assets;
3. battery/generator state relevant to endurance;
4. validated sensor fusion for infrastructure state;
5. bounded surrogate acceleration for repeated what-if analysis;
6. physics validation of synthetic training/scenario data;
7. optional research in electromagnetics, acoustics, thermal/structural physics where a concrete mission-infrastructure use case exists.

The `PINNeAPPle-Defense` fork is a research map, not an implementation dependency. Any method harvested
from it must pass a use-case test, licence review and independent validation before entering Mission Machine.

## Hard boundaries

Physics AI must never:

- choose or rewrite commander/operator priorities;
- silently revise a `MissionSpec` premise;
- convert confidence into a composite mission score;
- create a control/actuation path;
- treat research-only/non-commercial outputs as operational truth;
- make PINNeAPPle, PhysicsNeMo, FEniCS, OpenFOAM or another framework mandatory at runtime.

The existing doctrine remains: **the optimiser proposes; the operator decides**.

## Mission Machine ↔ RODOT future option

Where a real resilience/defense use case requires it:

- Mission Machine may emit a `MissionRequirementEnvelope`;
- RODOT may return a `RecoveryStateSnapshot`.

This creates a clean interaction between mission needs and restoration capability without merging the
products or creating source-level dependencies.

## Acceptance criteria for first interoperability experiment

1. a synthetic `AssetOperatingEnvelope` enters through a provider contract;
2. provenance, confidence, uncertainty and validity horizon are visible in the resulting evidence;
3. removal of the provider preserves the current deterministic baseline;
4. the envelope cannot alter command intent or operator-decision semantics;
5. stale/OOD/rejected evidence is visibly downgraded/refused;
6. the runtime has no mandatory external Physics-AI framework dependency;
7. tests prove no actuation path is introduced.

Until then this is a planned interoperability seam, not a delivered defense capability.
