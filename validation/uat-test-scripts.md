# User acceptance test (UAT) scripts

Manual, analyst-style acceptance tests for the CytoBridge LIS Interface
Simulator. Each script is written so a reviewer can execute it by hand and record
the outcome — they map to the same behavior covered by the automated `pytest`
suite (see the [traceability matrix](traceability-matrix.md)) and to the four
scenarios in `python -m src.demo_run`.

> **Synthetic learning project — no PHI.** These are demonstration acceptance
> tests, not clinical/regulatory validation. Accession numbers, MRNs, and
> results are synthetic. This is **Beaker-adjacent learning, not Epic build
> experience.**

## How to run these

Two ways to produce evidence:

- **Guided demo** — `python -m src.demo_run` runs UAT-001, -002, -003, -004,
  -005, -006, -008, -009, and -010 end-to-end and prints the evidence. Capture
  its console output.
- **Interactive** — start `python` from the repo root and drive the workflow
  directly (each script gives the calls). A fresh in-memory database is created
  with:

  ```python
  from src.db import create_database
  from src import workflow
  from src.interfaces import inbound_hl7, outbound_hl7, outbound_fhir
  conn = create_database(":memory:")
  ```

**Tester:** ____________________  **Date:** ____________  **Build/commit:** ____________

---

## UAT-001 — Happy-path order finalization

**Requirements:** R-001, R-002, R-006, R-007

**Precondition:** Fresh database (`create_database(":memory:")`); AML/MDS panel
and its 9 required probes seeded (they load from `schema.sql`).

**Steps:**
1. Create a synthetic patient (`workflow.create_patient(...)`).
2. Create an AML/MDS FISH order (`workflow.create_order(...)`); note the order is
   `ORDERED`.
3. Receive and accession a bone-marrow specimen.
4. Enter a result for **all 9** required probes.
5. Finalize the order (`workflow.finalize_order(conn, order_id, "analyst01")`).

**Expected result:** `finalize` returns `finalized=True` with a `report_id`; the
order status is `FINALIZED`; a `report` row exists; validation produced no
blocking errors.

**Evidence to capture:** `FinalizeResult` value (`finalized=True`, `report_id`);
the printed report summary; the order status query.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-002 — Missing required probe blocks finalization

**Requirements:** R-003, R-004, R-005, R-006

**Precondition:** A patient + accessioned order (as UAT-001 steps 1–3).

**Steps:**
1. Enter results for **8 of 9** required probes (omit `TP53_17p`).
2. Attempt to finalize the order.
3. Inspect the returned validation findings.
4. Query `validation_error` for the order.

**Expected result:** `finalize` returns `finalized=False`; findings include a
`MISSING_PROBE` **ERROR** for `TP53_17p`; the order is **not** `FINALIZED`; the
findings are persisted in `validation_error`.

**Evidence to capture:** `finalized=False`; the `[ERROR] MISSING_PROBE …` finding
line; the `validation_error` rows.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-003 — Outbound HL7 export from a finalized order

**Requirements:** R-008

**Precondition:** A **finalized** order (complete UAT-001).

**Steps:**
1. Generate the HL7 message: `outbound_hl7.generate_oru(conn, order_id)`.
2. Confirm the segment order begins `MSH`, `PID`, `OBR`, `SPM`, then `OBX`.
3. Confirm `MSH-9 = ORU^R01`, `MSH-11 = T` (training), version `2.5.1`.
4. Confirm one `OBX` per probe plus a summary + impression OBX.
5. Store it: `outbound_hl7.store_oru(...)` and confirm an `interface_message` row
   (`direction = OUTBOUND`, `format = HL7`).
6. **Negative:** on a *non-finalized* order, confirm `generate_oru` raises
   `OutboundError`.

**Expected result:** A well-formed ORU^R01-style message with the accession in
`OBR-3` and the synthetic MRN in `PID-3`; export refused for a non-finalized
order.

**Evidence to capture:** First ~4 segments of the message; the stored
`interface_message` row; the `OutboundError` from the negative step.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-004 — Outbound FHIR DiagnosticReport export from a finalized order

**Requirements:** R-009

**Precondition:** A **finalized** order (complete UAT-001).

**Steps:**
1. Build the Bundle: `outbound_fhir.build_diagnostic_report(conn, order_id)`.
2. Confirm `resourceType = Bundle` and entries include `Patient`, `Specimen`,
   one `Observation` per probe, and a `DiagnosticReport`.
3. Confirm `DiagnosticReport.status = final` and the accession appears in the
   report identifier.
4. Serialize with `generate_diagnostic_report_json(...)` and confirm it is valid
   JSON; store with `store_diagnostic_report(...)`.
5. **Negative:** on a non-finalized order, confirm `build_diagnostic_report`
   raises `OutboundError`.

**Expected result:** A FHIR R4-style Bundle whose DiagnosticReport references
every probe Observation; export refused for a non-finalized order.

**Evidence to capture:** The list of resource types; `status = final`; the stored
FHIR `interface_message` row; the `OutboundError` from the negative step.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-005 — Inbound valid ORU files results

**Requirements:** R-010, R-011

**Precondition:** An **open** (accessioned, not finalized) order whose accession
is `ACC-INBOUND-0001` and MRN `SYN-7001` (matches the valid sample). The demo's
scenario 4 sets this up automatically.

**Steps:**
1. Ingest `sample_messages/inbound/aml_mds_valid_oru.hl7`
   (`inbound_hl7.ingest_file(conn, path)` or `ingest_message(conn, text)`).
2. Inspect the returned `IngestResult`.
3. Query `fish_result` for the order.
4. Query `interface_message` for the stored inbound message.

**Expected result:** `IngestResult.filed = True`, `order_id` set, 9 probe codes
filed; `fish_result` now holds 9 rows for the order; the `interface_message` row
is `direction = INBOUND`, `status = FILED`, linked to the order.

**Evidence to capture:** `filed=True … probes=9`; the `fish_result` count; the
`interface_message` row (`INBOUND / ORU / FILED`).

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-006 — Unmatched accession goes to the error queue

**Requirements:** R-012, R-015, R-018

**Precondition:** Fresh database. (Optionally also finalize an order to exercise
the R-015 finalized-order variant.)

**Steps:**
1. Ingest `sample_messages/inbound/aml_mds_unmatched_accession.hl7`
   (accession `ACC-NOMATCH-9999`, which matches no order).
2. Inspect the `IngestResult`.
3. Run the analyst worklist query `interface_error_queue.sql`
   (`run_query(conn, "interface_error_queue")`).
4. **Finalized-order variant (R-015):** finalize `ACC-INBOUND-0001`, then ingest
   the *valid* sample against it; confirm it is rejected with a "finalized"
   reason.

**Expected result:** `filed = False` with a `queue_id`; one **OPEN** error-queue
row whose reason names `ACC-NOMATCH-9999`; the finalized-order variant is
rejected with a reason containing "finalized". No `fish_result` rows are created.

**Evidence to capture:** The `reason` string; the error-queue query output
(`OPEN`, `INBOUND`); the finalized-order rejection reason.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-007 — Missing OBX goes to the error queue

**Requirements:** R-013, R-018

**Precondition:** An open order matching `ACC-INBOUND-0001` (so matching
succeeds and the *only* problem is the absent OBX).

**Steps:**
1. Ingest `sample_messages/inbound/aml_mds_missing_obx.hl7` (MSH/PID/OBR/SPM but
   no OBX).
2. Inspect the `IngestResult`.
3. Confirm the matched order received **no** results.
4. Read the error-queue reason.

**Expected result:** `filed = False`; one OPEN error-queue row whose reason
mentions the absent OBX result segments; `fish_result` for the order is unchanged
(empty).

**Evidence to capture:** The `reason` ("No OBX result segments…"); the empty
`fish_result` count for the order.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-008 — Invalid numeric value goes to the error queue

**Requirements:** R-014, R-017, R-018

**Precondition:** An open order matching `ACC-INBOUND-0001`.

**Steps:**
1. Ingest `sample_messages/inbound/aml_mds_invalid_result_value.hl7` (a valid OBX
   plus one with a non-integer abnormal count `xx`).
2. Inspect the `IngestResult`.
3. Confirm **all-or-nothing**: the *valid* OBX in the same message was **not**
   filed either.
4. Read the error-queue reason.
5. **Unknown-probe variant (R-017):** ingest a message with probe code
   `NOTAPROBE`; confirm it is queued with a reason naming the unknown probe.

**Expected result:** `filed = False`; reason names the probe and the malformed
field ("not a valid integer"); the order's `fish_result` is unchanged; the
unknown-probe variant queues with a reason naming `NOTAPROBE`.

**Evidence to capture:** The malformed-value `reason`; proof nothing was filed;
the unknown-probe `reason`.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-009 — Audit trail review

**Requirements:** R-007, R-016

**Precondition:** Completed UAT-001 (a finalized order) and UAT-005 (an inbound
filing).

**Steps:**
1. Run the audit lookup query for the finalized order:
   `run_query(conn, "audit_lookup", {"order_id": order_id})`.
2. Confirm the trail includes `ORDERED`, `RECEIVED`, `ACCESSIONED`,
   `RESULT_ENTERED` (per probe), `VALIDATION_RUN`, `REPORT_FINALIZED`,
   `FINALIZED`.
3. For the inbound-filed order, confirm an `INBOUND_RESULT_FILED` event whose
   `detail` names the source `message_id`.

**Expected result:** A complete, ordered audit trail for each order; the inbound
filing is attributable to its `interface_message`.

**Evidence to capture:** The audit-lookup rows; the `INBOUND_RESULT_FILED` detail
string.

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## UAT-010 — Analyst query review

**Requirements:** R-019

**Precondition:** A database with a mix of orders (finalized, pending, and an
open error-queue item) — the state at the end of `python -m src.demo_run`.

**Steps:**
1. Run `pending_review.sql` — orders awaiting review, STAT first.
2. Run `stat_pending.sql` — un-finalized STAT orders, aging.
3. Run `turnaround_time.sql` — TAT for finalized orders.
4. Run `validation_error_rate.sql` — share of orders with blocking errors.
5. Run `interface_error_queue.sql` — open inbound interface errors.
6. Run `audit_lookup.sql` for one order.

**Expected result:** Each query returns a sensible, human-readable worklist
against the synthetic data (e.g. the error-queue query lists the OPEN inbound
failures with their reasons).

**Evidence to capture:** Output of each query (the demo prints `pending_review`,
`validation_error_rate`, and `interface_error_queue`; run the remaining ones
interactively).

**Pass / Fail:** ☐ Pass ☐ Fail  Notes: ________________________________

---

## Result summary

| UAT | Title | Requirements | Result |
|---|---|---|---|
| UAT-001 | Happy-path finalization | R-001, R-002, R-006, R-007 | ☐ P ☐ F |
| UAT-002 | Missing probe blocks finalize | R-003, R-004, R-005, R-006 | ☐ P ☐ F |
| UAT-003 | Outbound HL7 export | R-008 | ☐ P ☐ F |
| UAT-004 | Outbound FHIR export | R-009 | ☐ P ☐ F |
| UAT-005 | Inbound valid ORU files results | R-010, R-011 | ☐ P ☐ F |
| UAT-006 | Unmatched accession → queue | R-012, R-015, R-018 | ☐ P ☐ F |
| UAT-007 | Missing OBX → queue | R-013, R-018 | ☐ P ☐ F |
| UAT-008 | Invalid numeric → queue | R-014, R-017, R-018 | ☐ P ☐ F |
| UAT-009 | Audit trail review | R-007, R-016 | ☐ P ☐ F |
| UAT-010 | Analyst query review | R-019 | ☐ P ☐ F |
