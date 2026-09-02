# CytoBridge shop card

## What it is

CytoBridge is a synthetic, analyst-first educational simulator for a small
cytogenetics LIS workflow. It models order and specimen handling, FISH result
entry, validation, audit history, educational HL7- and FHIR-style exchange,
inbound error routing, and controlled recovery. The repository is
`jaustinanderson/cytobridge-lis-interface-simulator`.

## What this project proves

- A laboratory workflow can be translated into explicit data, validation, and
  audit requirements.
- Synthetic results can be traced through SQLite, Python services, interface
  messages, an error queue, and controlled recovery.
- The documented behavior is reproducible through an automated test suite,
  executable demonstration scenarios, traceability evidence, and selected
  manually executed synthetic UAT.
- Project boundaries, validation, rollback, ownership, and maintenance rules can
  be made readable by both people and automation.

## What it does not prove

CytoBridge is not a production LIS, medical device, certified interface engine,
or clinical decision system. Its HL7 and FHIR artifacts are educational style
examples, not conformance-validated implementations. It does not establish Epic
or Beaker build experience, patient-care fitness, production readiness, or
Austin's independent mastery of the implementation.

## Safety and privacy boundary

All examples and identifiers must remain synthetic. Never add PHI, real patient
or specimen identifiers, real laboratory or instrument identifiers,
employer-confidential material, credentials, private connection details, or
proprietary Epic or Beaker content. Nothing in this repository may connect to a
production clinical system.

## Ownership and authority

- Austin owns product decisions, merge decisions, releases, and consequential
  use.
- Codex may execute only a separately authorized, bounded task.
- A separate non-producer reviewer owns technical acceptance for an exact
  proposed revision.
- Direct pushes to `main`, autonomous merging, deployment, account changes,
  connector installation, and recurring autonomous work are prohibited.
- The recurring autonomous routine is disabled. New work starts only from a new
  bounded authorization.

## How to validate

Run these commands from the repository root:

```bash
python scripts/check_shop_contract.py
python -m pytest -q
python -m src.demo_run
python scripts/check_public_safety.py --commit-range BASE_SHA..HEAD_SHA
git diff --check BASE_SHA..HEAD_SHA
```

Replace `BASE_SHA` and `HEAD_SHA` with the exact proposed range. The first check
keeps this card aligned with `PROJECT_MANIFEST.json`; the existing tests and demo
exercise product behavior; the public-safety and diff checks protect the
publication boundary. A separate reviewer must examine the exact proposed
revision before Austin decides whether it may merge.

## Rollback

Before integration, leave the focused pull request unmerged and `main` remains
unchanged. After a separately authorized integration, use a separately
authorized non-destructive revert of the exact integration commit. Branch
deletion, force pushing, and history rewriting are not implied rollback steps.
This metadata-and-validation canary requires no data repair.

## Monitoring and maintenance

GitHub Actions provides repository-level evidence through CI, Public safety,
and the Frozen-file guard. There is no production service to monitor. Revisit
the manifest and card only when project purpose, ownership, validation routes,
data boundaries, dependencies, licenses, releases, or accepted capabilities
change. Each update remains one bounded task with proportional evidence.

## Austin mastery boundary

AI-generated files and passing checks do not demonstrate Austin's mastery. That
requires Austin to explain or modify the system and interpret the evidence
himself. Until then, mastery remains unestablished rather than inferred from
repository ownership.

## Next bounded learning bridge

Trace one synthetic result from order creation through validation and outbound
representation. Then explain how an invalid inbound message reaches the error
queue, how controlled recovery preserves the original message, and what must be
rolled back if recovery fails. This converts the project from portfolio exposure
into evidence Austin can personally demonstrate in an LIS or clinical-AI
interview.
