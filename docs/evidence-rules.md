# Evidence rules

Mission Machine is a research demonstrator built on invented data. The rules
below exist so that nobody - including its authors - can mistake a simulation
result for a statement about the world.

## The four labels

| Label | Means | Example |
| --- | --- | --- |
| `SYNTHETIC` | Invented input data. Not measured, not supplied by any organisation. | Every asset rating and load profile in `data/` |
| `SIMULATED` | Produced by this model from synthetic inputs. | Endurance, fuel use, reserve, coverage |
| `ASSUMED` | A modelling choice made by the developers. | Affine fuel curves, one-hour time step |
| `UNVALIDATED` | Not checked against field data or an external reference. | Everything, without exception |

A derived value inherits the weakest label of its inputs. In code this is
`mission_machine/evidence/labels.py:Provenance.weakest()`.

## What must never be claimed

Unless evidence exists - and in Pack 1 it does not - the system and its
documentation must never state or imply:

* military validation of any kind;
* operational readiness, or readiness for trial;
* that any requirement, figure or scenario originates from the Swedish Armed
  Forces or any other armed forces organisation;
* field performance, or performance of any real equipment;
* that a configuration is safe, certified, or compliant with any standard.

`tests/test_evidence_and_cli.py::test_no_document_claims_validation` scans every
document in the repository for these claims and fails the build if one appears.

## What may be claimed

* That the model produces a given result from given synthetic inputs, stated as
  such, and reproducible by a named command.
* That a schedule satisfies the declared MILP constraint set - because that is
  checked, by `mission-machine verify`, and the check can be re-run.
* That a design decision was taken for a stated reason.

## Where the labels appear

* On every screen of the UI, as a permanent banner and on individual panels.
* At the top of every CLI report.
* In every JSON payload, as `data_labels`.
* On every asset and mission in `data/`, as `data_labels` and `source`.
* Next to the results, as the assumption register - not only in a document.

## The mission data

`MM-DEMO-001` is an invented scenario. The location is generic, the weather is a
sine wave, the grid outage schedule was chosen to make the trade-offs visible,
and the fuel limit was chosen so the mission is achievable but not comfortable.
It does not reflect any real deployment, doctrine, or requirement.

## Scope limits that are also evidence limits

Pack 1 models disruption **only** as asset unavailability. It contains no
adversary model, no targeting, no offensive capability, and no signature or
detectability model. Resilience results therefore describe equipment failure.
They say nothing about survivability in a contested environment, and must not be
presented as if they did.
