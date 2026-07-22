# CLAUDE.md - CytoBridge autonomous-builder rules

Read this file on every run. It is short on purpose.

## Project nature

- CytoBridge is a synthetic, analyst-first, educational LIS/interface portfolio
  project. **All data is synthetic; never introduce PHI or real patient,
  laboratory, instrument, or employer-confidential material.**
- Interfaces are educational HL7/FHIR-style only. This is **not** a production
  clinical application, certified interface engine, validated medical device, or
  Epic/Beaker implementation. Do not overstate standards conformance, clinical
  validity, or production readiness.

## Before any v1.1 task

Read all three frozen files first, in this order:

1. `validation/v1.1-design-record.md` (Austin's approved, frozen source of truth)
2. `validation/v1.1-requirements.md`
3. `validation/v1.1-test-intent.md`

Implement only the approved shapes in those files. **Never invent product
semantics.** Any failure that does not map to the approved taxonomy, or any
expected behavior the design record does not dictate, is a **blocker**: stop and
return it to Austin rather than resolving it yourself. Newly discovered
hardening semantics also return to Austin.

## Frozen files - do not edit from autonomous task branches

- `CLAUDE.md`
- `validation/v1.1-design-record.md`
- `validation/v1.1-requirements.md`
- `validation/v1.1-test-intent.md`
- `.github/CODEOWNERS`
- The frozen-file guard workflow (`.github/workflows/frozen-file-guard.yml`)

Substantive changes to these require Austin's explicit approval. A `claude/*`
pull request that modifies a frozen file fails the frozen-file guard; the guard
has no bypass (the supervised setup PR that introduced these files was the
one-time bootstrap, completed before the guard existed on `main`).
`AUTONOMOUS_STATUS.md` is the only control document autonomous builders may
routinely update.

## Branch, PR, and pace rules

- One approved task per branch and one draft pull request.
- Branch naming: `claude/v1.1-<task-id>-<short-name>`.
- At most two completed-but-unreviewed v1.1 task branches at a time; reaching
  the cap produces a status-only run.
- Schema/data-model work is its own separately reviewed task.
- Never merge, deploy, release, enable auto-merge, or push to `main`.
- A no-change run is acceptable. Do not invent work merely to use capacity.

## Validation commands (run and report; do not weaken checks to pass)

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python -m src.demo_run
```

Also confirm: relative documentation links resolve, new Markdown stays
plain-ASCII (repository convention), `git diff --check` is clean, and no
application code, SQL schema, sample message, or executable test changed unless
the task explicitly authorizes it.
