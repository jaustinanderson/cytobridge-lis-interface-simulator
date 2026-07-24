# Change-control log

A change history for the CytoBridge LIS Interface Simulator, organized by
development session. Each session was developed on its own branch and merged to
`main` via a pull request - this log is the human-readable summary of that trail.

> **Synthetic learning project - no PHI.** This is a lightweight change log for a
> portfolio project, not a regulated change-control record. Commit SHAs are from
> the project's git history and may be abbreviated.

## Summary

| Session | Theme | Key additions | Reference |
|---|---|---|---|
| S1 | Workflow foundation | Schema, order/specimen/result/finalize workflow, validation rules, audit trail, analyst queries | `d71ad7f` (Merge Session 1) |
| S2 | Outbound interfaces | HL7 ORU^R01-style + FHIR DiagnosticReport-style generation, finalized-only gating | `5a0fd69` (Merge Session 2) |
| S3 | Inbound + error queue | Inbound ORU-style ingestion, accession matching, error-queue routing | `19bf8da` (Merge Session 3) |
| S4 | Validation package | Requirements, traceability, UAT, risk, demo/diagram/portfolio documentation | `92b06ad` (Merge Session 4) |
| S5 | Hiring-manager review | Portfolio scorecard, interview framing, and documentation-accuracy fixes | `bf7da52` (Merge Session 5) |
| S6 | Repository maintenance | CI on Python 3.11/3.12, licensing, security/contribution guidance, Dependabot, PR checklist | `f15cef1` |
| S7 | Analyst-query verification | Result-level tests for every analyst SQL view; R-019 moved from PARTIAL to PASS | 2026-07-11 validation maintenance |
| S8 | v1.1 Phase 1 controls and acceptance | Frozen design, approved/frozen requirements and test intent, builder rules, status control, CODEOWNERS, frozen-file guard | `cc3d395` plus 2026-07-22 Phase 1 closeout |
| P2-001 | v1.1 synthetic recovery corpus | 14 original + 12 corrected AML/MDS FISH failure fixtures, manifest, guide (review artifact only) | PR #13 merged |
| P3-001 | v1.1 recovery schema | `interface_error_queue` classification columns + expanded states; `interface_recovery_attempt` table and constraints | PR #15 merged |
| P3-002 | v1.1 structured failure classification | Fourteen-code classification populated for every inbound failure; OPEN/TERMINAL queue initialization | PR #17 merged |
| P3-003 | v1.1 controlled recovery service | `src/recovery.py` headless retry/re-drive/history with immutability, idempotency, rollback, terminal rejection | PR #19 merged; status closeout PR #20 merged |
| P3-004 | v1.1 validation/UAT/portfolio closeout | Recovery demo, R-020-R-041 traceability, UAT-011-UAT-018, troubleshooting/diagram/demo/README/portfolio updates | Draft PR (awaiting Austin's review) |

---

## Session 1 - AML/MDS FISH workflow foundation

**Requirements introduced:** R-001, R-002, R-003, R-004, R-005, R-006, R-007,
R-019 (analyst queries).

**What changed:**
- `schema.sql` - full v1 data model: `panel`/`probe` reference data, `patient`,
  `lab_order`, `specimen`, `fish_result`, `validation_error`, `report`,
  `audit_event`, and the interface tables (`interface_message`,
  `interface_error_queue`) provisioned for later sessions. Real PK/FK/CHECK
  constraints (incl. `cells_abnormal <= cells_scored`).
- `src/workflow.py` - patient/order/specimen/result lifecycle + finalize, each
  step writing an `audit_event`.
- `src/validation.py` - typed findings for `SPEC_ACCESSIONED`, `MISSING_PROBE`,
  `ABN_EXCEEDS_SCORED`, `INTERP_CONSISTENCY` (cutoff-aware), `CELL_COUNT_LOW`.
- `src/reports.py` - report summary + ISCN parser seam.
- `src/db.py` - `sqlite3` helpers + query loader.
- `queries/` - analyst SQL (`pending_review`, `stat_pending`, `turnaround_time`,
  `validation_error_rate`, `audit_lookup`, `interface_error_queue`).
- `tests/` - `test_workflow.py`, `test_validation.py`.
- `src/demo_run.py` - happy path + blocked-finalize scenarios.

**Representative commits:** `e12f479` (scaffold), `74ee9f4` (docs/column
cleanup), `39f53e0` (README wording), `d71ad7f` (merge).

---

## Session 2 - Outbound HL7 + FHIR generation

**Requirements introduced:** R-008, R-009.

**What changed:**
- `src/interfaces/__init__.py` - `collect_report_data` (single finalized-order
  snapshot), `store_message`, shared `ProbeResult`/`OutboundReportData` types,
  and `OutboundError` gating (finalized-only, fail-loud on missing data).
- `src/interfaces/outbound_hl7.py` - `generate_oru` / `store_oru`
  (MSH/PID/OBR/SPM/OBX, `MSH-11 = T`).
- `src/interfaces/outbound_fhir.py` - `build_diagnostic_report` /
  `generate_diagnostic_report_json` / `store_diagnostic_report` (Bundle:
  Patient, Specimen, per-probe Observations, DiagnosticReport).
- `sample_messages/outbound/` - sample HL7 + FHIR messages.
- `docs/interface-mapping.md` - outbound field-by-field mapping.
- `tests/test_outbound_interfaces.py` - generation + finalized-only gating.
- `src/demo_run.py` - outbound export scenario.

**Representative commits:** `ceaa8c8` (add outbound), `06d8d41` (FHIR performer +
HL7 CR doc note), `a2b3127` (validation-table rendering), `5a0fd69` (merge).

---

## Session 3 - Inbound ingestion + interface error queue

**Requirements introduced:** R-010, R-011, R-012, R-013, R-014, R-015, R-016,
R-017, R-018.

**What changed:**
- `src/interfaces/inbound_hl7.py` - `ingest_message` / `ingest_file` /
  `parse_message`; accession matching to a non-finalized order; all-or-nothing
  OBX validation (`_validate_obx`, `_parse_count`); filing via
  `workflow.enter_fish_result`; `INBOUND_RESULT_FILED` audit event;
  `_route_to_error_queue` for every routing reason (missing/unmatched accession,
  finalized order, missing segment, no OBX, unknown probe, abnormal > scored,
  malformed numeric, incompatible specimen).
- `src/interfaces/__init__.py` - re-export the inbound API.
- `sample_messages/inbound/` - valid, unmatched accession, missing OBX, and
  malformed-value samples.
- `docs/interface-mapping.md` - inbound mapping + routing-reason table.
- `docs/interface-troubleshooting.md` - analyst runbook.
- `tests/test_inbound_interfaces.py` - success + failure paths.
- `src/demo_run.py` - inbound ingestion scenario (scenario 4).

**Representative commits:** `5ca16d6` (add inbound), `19bf8da` (merge).

**No schema change** - reused the S1-provisioned interface tables.

---

## Session 4 - Validation, UAT, and portfolio documentation

**Requirements introduced:** none (documentation only). Consolidates S1-S3 into a
validation package.

**What changed (docs only):**
- `validation/` - `requirements.md`, `traceability-matrix.md`,
  `uat-test-scripts.md`, `validation-summary.md`, `known-issues.md`,
  `change-control-log.md` (this file), `risk-assessment.md`.
- `docs/` - `demo-script.md`, `workflow-diagram.md`, `portfolio-review.md`.
- `README.md` - links to the new validation + demo documentation.

**Code changes:** none intended (documentation-only session). Any code touch
would be limited to fixing a broken documentation reference and called out
explicitly in the PR.

---

## Session 5 - Hiring-manager review and portfolio polish

**Requirements introduced:** none (documentation only).

**What changed:**
- Added `docs/hiring-manager-review.md` with a scorecard, strengths, limitations,
  interview walkthrough, resume bullet, and LinkedIn framing.
- Corrected stale comments and documentation that still described implemented
  interface work as deferred.
- Linked the review from the README and repository layout.

**Representative merge:** `bf7da52`.

---

## Session 6 - CI and repository maintenance baseline

**Requirements introduced:** none; application and validated behavior unchanged.

**What changed:**
- Added GitHub Actions CI on Python 3.11 and 3.12 for the full pytest suite and
  four-scenario demo.
- Added the MIT license, security policy, contribution guide, Dependabot
  configuration, and pull-request checklist.
- Added the live CI badge and maintenance links to the README.

**Representative commit:** `f15cef1`.

---

## Session 7 - Analyst-query verification closure

**Requirements introduced:** none; R-019 was strengthened from view existence
to expected worklist and metric behavior.

**What changed:**
- Added `tests/test_queries.py` with deterministic result assertions for pending
  review, STAT aging, turnaround time, validation error rate, and audit lookup.
- Retained the existing direct assertion for `interface_error_queue.sql` in the
  inbound interface suite, giving all six analyst views result-level coverage.
- Updated traceability, risk, known-issue, demo, portfolio, and validation
  summaries; KI-01 is resolved and R-019 is PASS.
- Increased the current suite from 56 to 61 passing tests without changing
  application behavior or schema.

## Session 8 - CytoBridge v1.1 Phase 1 control-plane setup and acceptance

**Requirements introduced:** none implemented. This is a supervised
planning/control milestone, not an implementation session. **No v1.1 recovery
behavior, schema change, application code, sample data, or executable test was
added.**

Clearly distinguished:

- **Approved design.** `validation/v1.1-design-record.md` reproduces Austin's
  approved and frozen Phase 1 product architecture (plain-ASCII typography only;
  no meaning changed). Substantive edits require Austin's explicit approval.
- **Approved and frozen requirements/test intent.**
  `validation/v1.1-requirements.md` (`R-020`-`R-041`) and
  `validation/v1.1-test-intent.md` were reviewed and approved by Austin after
  setup PR #11 merged. They describe behavior that does **not yet exist**;
  substantive changes require Austin's explicit approval. The existing
  `requirements.md` numbering (`R-001`-`R-019`) is unchanged.
- **No implementation yet.** Recovery remains unbuilt.

**What changed (control-plane only):**
- `validation/v1.1-design-record.md` - frozen approved design record.
- `validation/v1.1-requirements.md` - approved, frozen v1.1 requirements
  (`R-020`-`R-041`), amended per Austin-approved corrections:
  handled-failure rollback semantics
  (preserve the `ERRORED` resulting message and `FAILED` attempt while rolling
  back FISH results, filing events, and queue resolution) and match-checked
  `request_id` replay with `REQUEST_ID_CONFLICT` rejection.
- `validation/v1.1-test-intent.md` - approved, frozen pre-implementation test
  intent, including the human-approved invariants I-01 and I-02.
- `CLAUDE.md` - stable autonomous-builder rules (synthetic-data/no-PHI,
  read-frozen-files-first, blockers, branch/PR/pace limits, no push to `main`).
- `AUTONOMOUS_STATUS.md` - the single writable status document; after Phase 1
  acceptance, phase `PHASE_2_READY_FOR_TASK_APPROVAL`, no approved
  implementation task, zero unreviewed tasks, and the Routine disabled.
- `.github/CODEOWNERS` - Austin owns the frozen files (review ownership/
  visibility, not edit prevention); protected by the guard along with the guard
  itself.
- `.github/workflows/frozen-file-guard.yml` - fails `claude/*` PRs that modify a
  frozen file. Hardened per Austin-approved correction: runs the trusted base-
  branch workflow via `pull_request_target`, inspects changed filenames only via
  the GitHub API, never checks out or executes PR code, and has no bootstrap
  bypass (the setup PR itself was the one-time supervised bootstrap, before the
  guard existed on `main`). Existing CI is unchanged.

**Phase 1 acceptance (2026-07-22):** Austin approved and froze the v1.1
requirements and test-intent specification after setup PR #11 merged. The
accepted baseline is `cc3d395`. Phase 2 is ready for Austin to approve its first
task; no implementation task is approved and the autonomous Routine remains
disabled.

**Validation:** existing pytest suite and demo run unchanged and green; workflow
YAML parses; new Markdown is plain-ASCII; relative links reviewed;
`git diff --check` clean; scope check confirms no application code, SQL schema,
sample message, or executable test changed.

---

## v1.1 implementation history (Phase 2-3)

Each v1.1 task was developed on its own `claude/*` branch under one draft pull
request, reviewed, and accepted by Austin before the next depended on it. The
prior entries above remain historical facts and are not rewritten.

### P2-001 - Synthetic recovery corpus (PR #13)

**What changed:** added `sample_messages/recovery/` - fourteen original synthetic
AML/MDS FISH failure fixtures, twelve corrected fixtures for the recoverable
cases, a machine-readable `recovery_corpus.json` manifest, and a human-readable
`README.md`. This was a **review artifact only**: no schema change, no recovery
implementation, no executable test. Every expected value was transcribed from the
frozen design record, not back-filled from a parser run. Accepted baseline
`681b8295`.

### P3-001 - Recovery data model and schema (PR #15)

**What changed:** `schema.sql` gained the v1.1 recovery shape - the
`interface_error_queue` classification columns (`failure_code`,
`failure_category`, `recovery_policy`), the expanded `OPEN`/`RESOLVED`/`TERMINAL`
states with `terminal_at`, and the state/timestamp `CHECK`s; plus the new
`interface_recovery_attempt` table with the exact approved logical fields,
foreign keys, `request_id` uniqueness, the single-`SUCCEEDED`-per-queue partial
unique index, and valid action/outcome/resulting-message rules. Added
`tests/test_recovery_schema.py`. Classification columns were left nullable for
schema-task sequencing. Accepted baseline `dafba1ae`.

### P3-002 - Structured failure classification (PR #17)

**What changed:** `src/interfaces/inbound_hl7.py` populated the fourteen approved
failure codes through one authoritative in-code mapping, initialized the twelve
recoverable failures as `OPEN` and the two order-state failures as `TERMINAL`
with `terminal_at`. Added `tests/test_failure_classification.py`. Preserved
reason text, original messages, raw payloads, filing behavior, and order state;
added no recovery service. Austin explicitly authorized two non-frozen
existing-test updates before acceptance. Accepted baseline `e6fa627b`.

### P3-003 - Controlled recovery service core (PR #19; status closeout PR #20)

**What changed:** added `src/recovery.py` - the headless service boundary
`retry_queue_item` / `redrive_queue_item` / `get_recovery_history` - implementing
the full frozen safety boundary: unchanged retry, corrected re-drive, attempt
history, eligibility and rejection rules, original-message immutability, queue
resolution and terminalization, `request_id` replay and `REQUEST_ID_CONFLICT`
handling, single-success protection, handled-failure rollback, transaction-safe
persistence, and recovery audit evidence. Threaded a keyword-only
`commit` control through `src/db.py`, `src/workflow.py`,
`src/interfaces/__init__.py`, and a private behavior-preserving refactor of
`src/interfaces/inbound_hl7.py` (extracted `_store_inbound_message` /
`_validate_inbound` seam). Added `tests/test_recovery_service.py` (54 tests).
Independent review found three blockers - complete transaction rollback, exact
stored-classification validation, and RETRY sourcing its payload from the linked
original `interface_message` - all fixed before acceptance. Accepted baseline
`672143ca`. PR #20 closed the P3-003 status in `AUTONOMOUS_STATUS.md`; the
resulting `main` merge commit is `8272bd18`.

### P3-004 - Recovery validation, UAT, and portfolio closeout (this task; draft PR)

**What changed (documentation closeout plus one authorized executable change):**

- `src/demo_run.py` - the **only** executable file changed. Added scenario 5, a
  deterministic synthetic recovery demonstration through the public recovery
  service (corrected re-drive, unchanged ORDER_NOT_FOUND retry, handled failure
  then later success, and duplicate/replay/`REQUEST_ID_CONFLICT` protection).
  Scenario-count references updated from four to five.
- `validation/traceability-matrix.md` - added R-020 through R-041 with exact
  implementing file/function or schema constraint, executable test coverage, and
  an applicable manual UAT; separated automated `PASS` from manual `DEFINED`;
  totals updated to 41 requirements.
- `validation/uat-test-scripts.md` - added UAT-011 through UAT-018 (recovery)
  through the public service, preserved UAT-001-UAT-010, updated the summary.
- `docs/interface-troubleshooting.md` - rewritten from the old manual-resend /
  manual-SQL model to the controlled recovery workflow (raw SQL now read-only).
- `docs/workflow-diagram.md` - added a compact recovery view.
- `docs/demo-script.md` - updated to the five-scenario demo with a recovery
  segment.
- `validation/validation-summary.md`, `validation/known-issues.md` (KI-03 moved
  to resolved), `validation/risk-assessment.md` (recovery risks RA-17-RA-22),
  and this log - updated for v1.1.
- `README.md`, `docs/portfolio-review.md`, `docs/hiring-manager-review.md` -
  v1.1 framing, corrected figures, provenance statement, roadmap update.
- `AUTONOMOUS_STATUS.md` - status control update.

No application module other than `src/demo_run.py`, no schema, query, sample
message, corpus, frozen file, CI workflow, or existing/new test was changed.

**Status:** **completed but awaiting Austin's review** on the new draft pull
request. It is **not** merged or accepted; this entry records the work as
submitted for review, not as accepted.

---

## Change-control principles used

- **One session/task = one branch = one PR**, never committed straight to `main`.
- **Tests accompany behavior** - every code session and v1.1 task added matching
  coverage; the current suite has 164 passing tests across eight suites,
  including result assertions for all analyst SQL views and the full controlled
  recovery service.
- **Scope guardrails** honored every session and task (no UI/Docker/ORM/new
  panels/new DB/real HL7-FHIR deps/Epic content; synthetic data only). CI was
  added as a maintenance gate without changing application behavior.
- **Docs kept in step** - `README.md`, `docs/interface-mapping.md`, and (S4) the
  validation package track the code as it grows.
