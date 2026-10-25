# Requirements specification

Numbered, testable requirements for the CytoBridge LIS Interface Simulator
(Sessions 1-3, plus R-043 - R-052 for the analyst investigation and recovery
lab). Each requirement has a stable ID (`R-0xx`) used throughout the
validation package - the [traceability matrix](traceability-matrix.md) maps every
ID to the code that implements it, its automated (`pytest`) coverage, and a
manual [UAT script](uat-test-scripts.md).

> **Scope of these requirements.** This is a **synthetic, analyst-first learning
> project**, not a validated medical device or production LIS. "Requirement"
> here means *a behavior the simulator is expected to demonstrate*, not a
> regulatory or clinical requirement. All data is synthetic - **no PHI**. This
> is **Beaker-adjacent learning, not Epic build experience**; see
> [`docs/portfolio-review.md`](../docs/portfolio-review.md).

## How to read a requirement

| Field | Meaning |
|---|---|
| **ID** | Stable identifier (`R-001` ...) |
| **Statement** | The behavior expected of the simulator |
| **Source** | Which session introduced it |
| **Type** | `Workflow`, `Validation`, `Interface (outbound)`, `Interface (inbound)`, `Audit`, `Analyst query`, or `Analyst lab` |

## Workflow requirements

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-001** | The system can create a synthetic patient and an AML/MDS FISH order, and a new order starts in status `ORDERED`. | S1 | Workflow |
| **R-002** | A specimen can be received against an order and accessioned; receiving a specimen advances the order out of `ORDERED`. | S1 | Workflow |

## Validation requirements

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-003** | Validation raises a blocking `MISSING_PROBE` error when any required panel probe has no result. | S1 | Validation |
| **R-004** | The number of abnormal cells can never exceed the number of scored cells - enforced by a schema `CHECK` and re-checked by validation (`ABN_EXCEEDS_SCORED`). | S1 | Validation |
| **R-005** | Interpretation consistency is **cutoff-aware**: a percent-abnormal at/above a probe's cutoff called `NORMAL` is a blocking error; an `ABNORMAL` call below cutoff is an advisory warning (`INTERP_CONSISTENCY`). | S1 | Validation |
| **R-006** | Finalization is **blocked** whenever validation produces any `ERROR`-severity finding; the findings are persisted to `validation_error` and the order is not finalized. | S1 | Validation |

## Audit requirement

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-007** | Every important state change (patient/order/specimen/result/validation/finalize) writes an `audit_event` row with entity, action, actor, and detail. | S1 | Audit |

## Outbound interface requirements

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-008** | An HL7 ORU^R01-style message is generated **only for a `FINALIZED` order**; a non-finalized (or data-incomplete) order raises `OutboundError`. | S2 | Interface (outbound) |
| **R-009** | A FHIR `DiagnosticReport`-style Bundle is generated **only for a `FINALIZED` order**; a non-finalized (or data-incomplete) order raises `OutboundError`. | S2 | Interface (outbound) |

## Inbound interface requirements

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-010** | Every inbound message is stored in `interface_message` (`direction = 'INBOUND'`), whether it files successfully or is routed to the error queue. | S3 | Interface (inbound) |
| **R-011** | A valid, matched inbound ORU-style message files its per-probe results to the open order. | S3 | Interface (inbound) |
| **R-012** | An inbound message whose accession does not match any order is routed to `interface_error_queue` with a clear reason. | S3 | Interface (inbound) |
| **R-013** | An inbound message with no `OBX` result segments is routed to `interface_error_queue` with a clear reason. | S3 | Interface (inbound) |
| **R-014** | An inbound message with a malformed numeric result field (e.g. a non-integer cell count) is routed to `interface_error_queue`, and **nothing** is filed (all-or-nothing). | S3 | Interface (inbound) |
| **R-015** | An inbound message matched to an **already-finalized** order is rejected to `interface_error_queue` rather than filed. | S3 | Interface (inbound) |

## Supporting requirements (inbound robustness + analyst tooling)

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-016** | Successful inbound filing records an `INBOUND_RESULT_FILED` audit event on the order naming the source `interface_message.message_id`. | S3 | Audit |
| **R-017** | An inbound `OBX` whose probe code is not part of the AML/MDS panel is routed to `interface_error_queue`. | S3 | Interface (inbound) |
| **R-018** | Each error-queue entry carries `message_id`, `direction`, a clear `reason`, `status = 'OPEN'`, and a created timestamp. | S3 | Interface (inbound) |
| **R-019** | Analyst SQL views return the expected worklists and metrics for pending review, STAT aging, turnaround, validation error rate, audit lookup, and open interface errors. | S1-S3 | Analyst query |

## Analyst investigation and recovery lab requirements (issue #43)

These describe the additive, headless analyst lab (`python -m src.analyst`).
They add no recovery semantics: every mutation goes through the existing
workflow, interface, and frozen v1.1 recovery services, so R-020 - R-041 are
unchanged. The source is GitHub issue #43, written by Austin.

| ID | Statement | Source | Type |
|---|---|---|---|
| **R-043** | `python -m src.analyst` provides help, documented exit codes (0 ok, 1 check failed, 2 usage, 3 not found, 4 refused or negative recorded outcome, 5 invalid input, 6 internal error), readable text output, and a `--json` mode that prints exactly one parseable envelope object. Mutations go only through the existing public workflow, inbound, outbound, and recovery service functions; recovery commands require `--request-id` and `--actor`. | #43 | Analyst lab |
| **R-044** | `init` refuses any existing path and leaves it unchanged. Read commands open the database read-only, never create a missing database, and leave an existing database byte-for-byte unchanged. Write commands never create a missing database. | #43 | Analyst lab |
| **R-045** | State written by one CLI invocation persists in the database file and is visible to later, separate invocations after the writing process has exited. | #43 | Analyst lab |
| **R-046** | File-based ingestion and corrected re-drive read message files as exact strict UTF-8 with no newline translation, so the stored payload's SHA-256 equals the SHA-256 of the file bytes. A file that is missing or not valid UTF-8 is refused before anything is written. | #43 | Analyst lab |
| **R-047** | `trace` returns a chronological case view of the order, patient, specimens, current per-probe results, stored validation findings, report, inbound and outbound messages, queue item, recovery attempts, resulting messages, and audit events, linked by real primary/foreign keys. Payload-derived and audit-text links are labeled as such, and the case states that results are upserted (no historical versions) and that result-to-message attribution is not recorded. | #43 | Analyst lab |
| **R-048** | `reconcile` returns deterministic findings for broken documented relationships, inconsistent message/queue/recovery states, missing expected audit evidence, and outstanding workflow prerequisites. Each finding names its rule, severity, documented source rule, concrete row references, observations, and any ambiguity. Findings are educational invariants, not clinical requirements. The command exits 1 when any ERROR finding exists. | #43 | Analyst lab |
| **R-049** | `recover preview` runs the unchanged recovery service on an isolated in-memory backup of a read-only source and reports the simulated outcome and row-level changes. The source bytes, rows, request ids, and audit trail are unchanged, and the outcome and normalized changes match a real apply from the same baseline. A preview is not an authorization: a later apply re-evaluates current state under the existing service rules. | #43 | Analyst lab |
| **R-050** | Seven executable synthetic incident exercises keep analyst instructions separate from answer keys. Each runs in a new database, and a verifier checks the persisted records and fixture-file hashes, not CLI messages. Reruns reproduce the same verified outcome without contaminating other exercises. | #43 | Analyst lab |
| **R-051** | `evidence` exports deterministic, synthetic-marked case evidence JSON and a short ASCII report. It records the source revision, scenario identity, explicit record relationships, original and corrected payload fingerprints with consistency checks, validations, observed outcomes, recovery history, reconciliation findings, and limitations. A content fingerprint stays stable across reruns of the same steps. | #43 | Analyst lab |
| **R-052** | A bounded 1,000-message scale check confirms row counts, completeness, deterministic ordering, and that reconciliation and trace SQL statement counts do not grow with message volume. Timing is recorded without a machine-specific pass threshold. | #43 | Analyst lab |

See the [traceability matrix](traceability-matrix.md) for the full
requirement -> code -> test -> UAT mapping and current status.
