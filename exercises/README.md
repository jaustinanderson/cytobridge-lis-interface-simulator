# CytoBridge analyst lab exercises

> **Synthetic educational material.** Every patient, MRN, accession, specimen,
> instrument, and result in these exercises is synthetic. No PHI. CytoBridge is
> a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation. Probe cutoffs come
> from the synthetic seed data in [`schema.sql`](../schema.sql) and are not a
> clinical reference.

Seven executable incident exercises for the analyst command-line interface
(`python -m src.analyst`). Each one starts from a fresh database, sets up a
synthetic interface incident, and asks you to investigate it with the analyst
commands and then act on it through the existing CytoBridge services.

## Index

| Exercise | Title | Incident type (issue #43, deliverable 4) |
|---|---|---|
| [EX01-valid-result-export](EX01-valid-result-export/README.md) | Valid inbound result to final report and exports | 1. Valid inbound result -> validation/final report -> matching HL7/FHIR-style exports |
| [EX02-unknown-accession-retry](EX02-unknown-accession-retry/README.md) | Unknown accession and unchanged retry | 2. Unknown accession -> inspect evidence -> create matching order -> unchanged retry |
| [EX03-invalid-result-redrive](EX03-invalid-result-redrive/README.md) | Invalid result value and corrected re-drive | 3. Malformed/invalid result -> corrected re-drive while preserving the original |
| [EX04-finalized-order-terminal](EX04-finalized-order-terminal/README.md) | Late result for a finalized order | 4. Finalized order -> terminal recovery rejection |
| [EX05-replay-and-conflict](EX05-replay-and-conflict/README.md) | Request replay and request-id conflict | 5. Successful request replay -> no duplicate filing; conflicting request-ID reuse rejected |
| [EX06-finalize-blocked-correction](EX06-finalize-blocked-correction/README.md) | Blocked finalization and result correction | 6. Incomplete panel and interpretation mismatch -> finalization blocked -> correction -> completion |
| [EX07-failed-recovery-then-success](EX07-failed-recovery-then-success/README.md) | Failed recovery, then success | Handled recovery failure and rollback, then later success |

EX07 needs no fault injection: its failed attempt is an ordinary inbound
validation failure that the unchanged recovery service handles as a `FAILED`
attempt (design record sec 8 and sec 9).

## Layout

```
exercises/
  README.md                  this index (no answers)
  <EXID>/README.md           analyst instructions: scenario, goal, prompts, questions
  <EXID>/setup.json          the setup steps that create the incident
  <EXID>/inputs/*.hl7        synthetic HL7-style fixture messages (LF line endings)
  answer-keys/               instructor material: solution steps, expected outcomes
```

Analyst instructions and answer keys are deliberately separate. The
per-exercise READMEs contain no expected outcomes and no solution commands.
The [answer keys](answer-keys/README.md) hold the solution steps, the expected
persisted outcomes the verifier checks, and instructor answers to the
questions. Work the exercise before opening them.

## Working an exercise

Run every command from the repository root. Exercise databases belong in a
local scratch directory such as `lab-work/`; do not commit them.

```bash
mkdir -p lab-work
python -m src.analyst exercise list
python -m src.analyst exercise show EX03-invalid-result-redrive
python -m src.analyst exercise setup EX03-invalid-result-redrive --db lab-work/EX03-invalid-result-redrive.db
```

Then follow that exercise's README: investigate with the read-only commands
(`worklist`, `trace`, `show-message --raw`, `reconcile`, `audit`,
`recover history`, `recover preview`, `evidence`), carry out the task, and
check your work:

```bash
python -m src.analyst exercise verify EX03-invalid-result-redrive --db lab-work/EX03-invalid-result-redrive.db
```

`setup` always creates a new database and refuses an existing path, so to
start over choose a new `--db` path. Because setup starts from an empty
database, the first order and the first queue item normally get id 1, but
always read the ids from `worklist` or `trace` rather than assuming them.
Add `--json` to any command for machine-readable output.

## What verification does and does not show

`exercise verify` is read-only. It queries the persisted records (orders,
results, reports, messages, queue items, recovery attempts, audit events, and
row totals) and compares them with the answer key's expected outcome. Fixture
fingerprints are computed from the fixture file bytes and compared with the
payload text stored in the database. It does not trust a command's success
message.

`exercise run <EXID> --db <new path>` executes the instructor answer key on a
new database and then verifies it; `exercise run-all --workdir <empty dir>`
does that for every exercise in isolation. These reference runs reveal the
solutions.

Automated exercise execution, including `exercise run` and `run-all`, is
automated testing of the simulator. It is not manual UAT, and a passing run is
not evidence of Austin's (or any analyst's) understanding or demonstrated
mastery. Reconciliation rules used by the lab are educational invariants
derived from this repository's design record and requirements
([`validation/v1.1-design-record.md`](../validation/v1.1-design-record.md),
[`validation/v1.1-requirements.md`](../validation/v1.1-requirements.md)), not
clinical or regulatory requirements.
