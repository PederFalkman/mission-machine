# Assumption register

Generated from `mission_machine/explainability/assumptions.py` by
`tools/render_assumptions.py`. Do not edit by hand - change the register and
re-run the script.

Every assumption below materially affects a number the demonstrator puts on
screen. The UI shows this register on the MISSION screen so that a reader can
see what the results rest on without leaving the tool.

Labels: **SYNTHETIC** invented input data; **ASSUMED** a modelling choice;
**SIMULATED** produced by the model; **UNVALIDATED** not checked against field
data or an external reference.

| Id | Label | Category | Assumption | If it is wrong | Where it is used |
| --- | --- | --- | --- | --- | --- |
| AS-001 | SYNTHETIC | data | All asset ratings, fuel curves, load profiles and weather data are invented for this demonstrator. None of it comes from the Swedish Armed Forces or any other organisation. | Every quantitative result changes. No result should be read as a performance claim. | `data/assets, data/missions` |
| AS-002 | ASSUMED | model | Generator fuel consumption is affine in output: litres/h = no-load + marginal x kW. | Fuel figures drift at very low and very high loading; the merit order between similar sets could change. | `assets/energy.py, planning/milp.py` |
| AS-003 | ASSUMED | model | A one-hour time step is fine enough to plan a 72-hour mission. | Ramp rates, transfer breaks and short peaks are invisible at this resolution. The UPS behaviour that protects communications during a source change is not modelled. | `simulation/simulator.py` |
| AS-004 | ASSUMED | model | Round-trip battery losses are split evenly between charging and discharging, and self-discharge is a fixed fraction of nameplate energy per hour. | State of charge drifts from reality over long missions; reserve estimates shift. | `assets/energy.py, simulation/simulator.py` |
| AS-005 | ASSUMED | metric | Unburned fuel is converted to an energy reserve using the best available generator's specific fuel consumption at 75 % loading. | Reserve in hours is optimistic when the remaining set ends up lightly loaded. | `simulation/simulator.py` |
| AS-006 | ASSUMED | model | All generators draw from one shared bulk fuel supply; individual tank capacities are recorded but not enforced as a constraint. | A configuration may be reported as feasible when it would in practice require refuelling runs between tanks. | `simulation/simulator.py` |
| AS-007 | ASSUMED | metric | Deployment crews work in parallel, one crew per asset, so setup time is the longest single setup rather than the sum. | Deployment complexity is understated when personnel are the binding constraint. | `planning/metrics.py` |
| AS-008 | ASSUMED | scenario | Grid availability follows a fixed, known schedule of windows. | Real supply fails without warning. Options that lean on the grid would be riskier than the comparison suggests. | `environment/model.py, data/missions` |
| AS-009 | ASSUMED | model | Thermal derating of generators is linear at 1 %/degC above 25 degC, floored at 80 %. | Capacity margins in hot conditions are approximate. | `assets/base.py` |
| AS-010 | ASSUMED | scope | Disruption is modelled only as asset unavailability. No adversary behaviour, no targeting, no cascading physical damage, no signature or detectability effects. | Resilience results describe equipment failures, not contested-environment survivability. | `resilience/` |
| AS-011 | ASSUMED | model | Cooling demand is a linear function of ambient temperature above a reference point; shelter thermal mass is ignored. | Short-term cooling peaks and the ride-through that thermal mass provides are not represented. | `assets/loads.py` |
| AS-012 | ASSUMED | model | A generator started within a time step delivers only for the remainder of that step; ramp rates are otherwise non-binding at one-hour resolution. | Stop/start cycling looks cheaper than it is; maintenance impact of cycling is not costed. | `simulation/simulator.py` |
| AS-013 | ASSUMED | scenario | No fuel resupply arrives during the mission unless the environment explicitly allows it. | Endurance limits are conservative where resupply is in fact available. | `data/missions, resilience/analysis.py` |
| AS-014 | ASSUMED | metric | Projected endurance beyond the mission horizon extrapolates the final step's critical demand at constant load. | Projected endurance is indicative only; it is not a mission-planning figure. | `planning/metrics.py` |
| AS-015 | ASSUMED | metric | Energy counts towards the reserve only when the configuration being assessed can actually deliver it: the battery must be deployed, and a generator must be committed to burn the fuel. Energy the node holds but cannot reach is reported as a withheld quantity with an open question, never as part of the reserve and never as zero. | A configuration that could reach the energy quickly - by starting a generator that is already on site - looks worse than one that has it connected, which is the intended bias but is a judgement, not a fact. | `simulation/simulator.py, planning/metrics.py` |

## What is deliberately not modelled in Pack 1

* Adversary behaviour, targeting, offensive action, or contested-environment
  survivability. Disruption appears only as asset unavailability.
* Signature, emissions and detectability of any configuration.
* Personnel availability as a scheduling constraint (only setup crew size is
  recorded).
* Cyber dependencies, control-system availability and communications bearer
  behaviour beyond the electrical load they draw.
* Maintenance intervals, spares and consumables other than fuel.
* Financial cost of any kind.

These are exclusions, not oversights. Each one is a candidate for a later pack
and is listed in `docs/pack1-deliverables.md` under the Pack 2 recommendation.
