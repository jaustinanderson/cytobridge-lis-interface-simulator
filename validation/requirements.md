# Requirements specification

Numbered, testable requirements for the CytoBridge LIS Interface Simulator
(Sessions 1-3). Each requirement has a stable ID (`R-0xx`) used throughout the
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
| **Type** | `Workflow`, `Validation`, `Interface (outbound)`, `Interface (inbound)`, `Audit`, or `Analyst query` |

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

See the [traceability matrix](traceability-matrix.md) for the full
requirement -> code -> test -> UAT mapping and current status.
