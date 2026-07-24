# User acceptance test (UAT) scripts

Manual, analyst-style acceptance tests for the CytoBridge LIS Interface
Simulator. Each script is written so a reviewer can execute it by hand and record
the outcome - they map to the same behavior covered by the automated `pytest`
suite (see the [traceability matrix](traceability-matrix.md)) and to the five
scenarios in `python -m src.demo_run`. UAT-001 through UAT-010 cover v1;
UAT-011 through UAT-018 cover the v1.1 controlled error-queue recovery service
(frozen requirements R-020 through R-041).

> **Synthetic learning project - no PHI.** These are demonstration acceptance
> tests, not clinical/regulatory validation. Accession numbers, MRNs, and
> results are synthetic. This is **Beaker-adjacent learning, not Epic build
> experience.**

> **These procedures are DEFINED, not executed.** Writing an executable step and
> an expected result is not the same as running it. The Pass/Fail blanks below
> are intentionally empty; do not read the existence of a script as evidence that
> a tester ran it. Only a filled-in blank with recorded evidence is a manual pass.
> The automated `pytest` suite is what currently proves this behavior runs and
> passes.

## How to run these

Two ways to produce evidence:

- **Guided demo** - `python -m src.demo_run` runs UAT-001, -002, -003, -004,
  -005, -006, -008, -009, and -010 end-to-end and prints their evidence, and
  its scenario 5 shows representative recovery cases that illustrate UAT-012,
  -013, -015, -016, and -017. Capture its console output.
- **Interactive** - start `python` from the repo root and drive the workflow and
  recovery service directly (each script gives the calls). A fresh in-memory
  database is created with:

  ```python
  from src.db import create_database
  from src import workflow, recovery
  from src.interfaces import inbound_hl7, outbound_hl7, outbound_fhir
  conn = create_database(":memory:")
  ```

The recovery scripts (UAT-011 - UAT-018) also read the approved synthetic corpus
fixtures under `sample_messages/recovery/original/` and
`sample_messages/recovery/corrected/`:

```python
from pathlib import Path
REC = Path("sample_messages/recovery")
def original(name): return (REC / "original" / name).read_text(encoding="utf-8")
def corrected(name): return (REC / "corrected" / name).read_text(encoding="utf-8")

def open_order(mrn, accession):
    """An accessioned, non-terminal AML/MDS FISH bone-marrow order."""
    pid = workflow.create_patient(conn, mrn, "Synthetic", "Recovery", "1970-01-01", "M")
    oid = workflow.create_order(conn, pid, accession, "Dr. Synthetic")
    sid = workflow.receive_specimen(conn, oid)
    workflow.accession_specimen(conn, sid)
    return oid
```

**All recovery actions go through the public service** (`recovery.retry_queue_item`,
`recovery.redrive_queue_item`, `recovery.get_recovery_history`). **Do not resolve
or terminalize a queue item with a manual `UPDATE`.** Raw SQL is used below only
read-only, to inspect evidence.

**Tester:** ____________________  **Date:** ____________  **Build/commit:** ____________

---

## UAT-001 - Happy-path order finalization

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

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-002 - Missing required probe blocks finalization

**Requirements:** R-003, R-004, R-005, R-006

**Precondition:** A patient + accessioned order (as UAT-001 steps 1-3).

**Steps:**
1. Enter results for **8 of 9** required probes (omit `TP53_17p`).
2. Attempt to finalize the order.
3. Inspect the returned validation findings.
4. Query `validation_error` for the order.

**Expected result:** `finalize` returns `finalized=False`; findings include a
`MISSING_PROBE` **ERROR** for `TP53_17p`; the order is **not** `FINALIZED`; the
findings are persisted in `validation_error`.

**Evidence to capture:** `finalized=False`; the `[ERROR] MISSING_PROBE ...` finding
line; the `validation_error` rows.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-003 - Outbound HL7 export from a finalized order

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

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-004 - Outbound FHIR DiagnosticReport export from a finalized order

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

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-005 - Inbound valid ORU files results

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

**Evidence to capture:** `filed=True ... probes=9`; the `fish_result` count; the
`interface_message` row (`INBOUND / ORU / FILED`).

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-006 - Unmatched accession goes to the error queue

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

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-007 - Missing OBX goes to the error queue

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

**Evidence to capture:** The `reason` ("No OBX result segments..."); the empty
`fish_result` count for the order.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-008 - Invalid numeric value goes to the error queue

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

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-009 - Audit trail review

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

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-010 - Analyst query review

**Requirements:** R-019

**Precondition:** A database with a mix of orders (finalized, pending, and an
open error-queue item) - the state at the end of `python -m src.demo_run`.

**Steps:**
1. Run `pending_review.sql` - orders awaiting review, STAT first.
2. Run `stat_pending.sql` - un-finalized STAT orders, aging.
3. Run `turnaround_time.sql` - TAT for finalized orders.
4. Run `validation_error_rate.sql` - share of orders with blocking errors.
5. Run `interface_error_queue.sql` - open inbound interface errors.
6. Run `audit_lookup.sql` for one order.

**Expected result:** Each query returns a sensible, human-readable worklist
against the synthetic data (e.g. the error-queue query lists the OPEN inbound
failures with their reasons).

**Evidence to capture:** Output of each query (the demo prints `pending_review`,
`validation_error_rate`, and `interface_error_queue`; run the remaining ones
interactively).

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-011 - Structured classification and initial queue state

**Requirements:** R-020, R-021

**Precondition:** Fresh database. Uses the approved recovery corpus.

**Steps:**
1. Create an open order matching the specimen-incompatible case:
   `open_order("SYN-8901", "ACC-REC-0901")`.
2. Ingest the original failed message:
   `r = inbound_hl7.ingest_message(conn, original("09_specimen_incompatible.hl7"))`.
3. Read the queue item's classification (read-only):

   ```sql
   SELECT failure_code, failure_category, recovery_policy, status,
          resolved_at, terminal_at, reason
   FROM interface_error_queue WHERE queue_id = :queue_id;   -- r.queue_id
   ```
4. Repeat step 1-3 for a terminal case: finalize an order on `ACC-REC-0601`
   (`SYN-8601`), then ingest `original("06_order_finalized.hl7")`.

**Expected result:** The specimen case is `SPECIMEN_INCOMPATIBLE` / `SPECIMEN` /
`REDRIVE_ONLY`, `status = OPEN`, both timestamps null, `reason` retained. The
finalized case is `ORDER_FINALIZED` / `ORDER_STATE` / `TERMINAL`, `status =
TERMINAL`, `terminal_at` set, `resolved_at` null. No code, category, or policy
outside the approved sets appears.

**Evidence to capture:** Both classification rows; the OPEN-vs-TERMINAL status
and timestamp values.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-012 - Corrected re-drive, lineage, and original-message immutability

**Requirements:** R-022, R-023, R-024, R-027, R-028

**Precondition:** The OPEN `SPECIMEN_INCOMPATIBLE` queue item from UAT-011
(`ACC-REC-0901`), still OPEN.

**Steps:**
1. Snapshot the original failed message (read-only):

   ```sql
   SELECT message_id, payload, control_id, status, created_at
   FROM interface_message WHERE message_id = :original_message_id;  -- r.message_id
   SELECT raw_payload FROM interface_error_queue WHERE queue_id = :queue_id;
   ```
2. Re-drive with the corrected payload through the service:
   `a = recovery.redrive_queue_item(conn, r.queue_id, corrected("09_specimen_incompatible.hl7"), request_id="UAT12-A", actor="analyst01")`.
3. Re-read the original message and queue `raw_payload`.
4. Inspect the attempt `a` and the new resulting message:

   ```sql
   SELECT message_id, payload, status FROM interface_message
   WHERE message_id = :resulting_message_id;   -- a.resulting_message_id
   ```

**Expected result:** `a.outcome = SUCCEEDED`, `a.action = REDRIVE_CORRECTED`,
`a.resulting_message_id` is a **new, distinct** message holding the corrected
payload with `status = FILED`; `a.queue_id` links the attempt to the queue item.
The original message (`message_id`, `payload`, `control_id`, `status = ERRORED`,
`created_at`) and the queue `raw_payload` are byte-for-byte unchanged. Only the
new message is `FILED`.

**Evidence to capture:** Before/after original-message rows (identical); the new
`FILED` message row; the attempt's `queue_id`, `resulting_message_id`, `action`,
`outcome`.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-013 - Unchanged ORDER_NOT_FOUND retry

**Requirements:** R-025, R-026

**Precondition:** Fresh database.

**Steps:**
1. Ingest the order-not-found original with no matching order present:
   `r = inbound_hl7.ingest_message(conn, original("05_order_not_found.hl7"))`.
   Confirm (read-only) the queue item is `ORDER_NOT_FOUND` / `RETRY_OR_REDRIVE`,
   `status = OPEN`.
2. Make the matching order available: `open_order("SYN-8500", "ACC-REC-0500-NOMATCH")`.
3. Retry the unchanged original through the service:
   `a = recovery.retry_queue_item(conn, r.queue_id, request_id="UAT13-A", actor="analyst01")`.
4. Compare the new message payload to the original payload (read-only).

**Expected result:** `a.outcome = SUCCEEDED`, `a.action = RETRY_ORIGINAL`; the
new resulting message holds an **exact copy** of the immutable original payload
(and `a.payload_sha256` is its SHA-256), on a **distinct** `message_id`, `status
= FILED`, filed to the now-available order; the queue transitions `OPEN ->
RESOLVED`. The original message stays `ERRORED` and unchanged.

**Evidence to capture:** The equal payloads with distinct message IDs; the
attempt outcome/action; the queue `RESOLVED` row.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-014 - Prohibited actions, closed-item rejection, and dynamic OPEN -> TERMINAL

**Requirements:** R-029, R-030 (with R-025 negative)

**Precondition:** Fresh database.

**Steps:**
1. **Terminal-item rejection:** finalize an order on `ACC-REC-0601` (`SYN-8601`),
   ingest `original("06_order_finalized.hl7")` (queue item `TERMINAL`), then
   attempt a re-drive:
   `recovery.redrive_queue_item(conn, q, corrected("09_specimen_incompatible.hl7"), request_id="UAT14-T", actor="analyst01")`.
2. **Wrong-action rejection:** on a fresh OPEN `REDRIVE_ONLY` item
   (`open_order("SYN-8801","ACC-REC-0801")` then ingest
   `original("08_specimen_unrecognized.hl7")`), call
   `recovery.retry_queue_item(...)` (RETRY not allowed for this policy).
3. **Dynamic OPEN -> TERMINAL:** ingest `original("05_order_not_found.hl7")`
   (OPEN `ORDER_NOT_FOUND`), then create the matching order **already finalized**
   (`open_order` + enter all results + `workflow.finalize_order` on
   `ACC-REC-0500-NOMATCH`), then `recovery.retry_queue_item(...)`.
4. Read each queue item and the target order status (read-only).

**Expected result:** Steps 1 and 2 return `outcome = REJECTED` with
`resulting_message_id = None`, no new processing message, and the TERMINAL item
stays TERMINAL / the OPEN item stays OPEN. Step 3 returns `REJECTED`, files
nothing, and moves the queue `OPEN -> TERMINAL` (`terminal_at` set, `resolved_at`
null); the target order is **not** reopened, unfinalized, or uncancelled.

**Evidence to capture:** The three `REJECTED` attempts; the TERMINAL queue row
from step 3 with its timestamps; the unchanged order status.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-015 - Handled FAILED attempt, rollback evidence, and later success

**Requirements:** R-032, R-037, R-038

**Precondition:** Fresh database.

**Steps:**
1. Create the OPEN unknown-probe item:
   `open_order("SYN-9101", "ACC-REC-1101")`, then
   `r = inbound_hl7.ingest_message(conn, original("11_unknown_probe_code.hl7"))`.
2. Re-drive with a **still-invalid** permitted payload (the original, unknown
   probe):
   `f = recovery.redrive_queue_item(conn, r.queue_id, original("11_unknown_probe_code.hl7"), request_id="UAT15-F", actor="analyst01")`.
3. Inspect rollback evidence (read-only): the resulting message status, the
   order's `fish_result` count, `INBOUND_RESULT_FILED` events for the order, and
   the queue status.
4. Re-drive with the **corrected** payload under a **new** `request_id`:
   `g = recovery.redrive_queue_item(conn, r.queue_id, corrected("11_unknown_probe_code.hl7"), request_id="UAT15-G", actor="analyst01")`.

**Expected result:** `f.outcome = FAILED` with a resulting message preserved as
`ERRORED`; **no** FISH results filed, **no** `INBOUND_RESULT_FILED` event for the
order, queue still `OPEN` (rollback after filing but before success bookkeeping).
`g.outcome = SUCCEEDED`, its message `FILED`, queue now `RESOLVED`; exactly one
`SUCCEEDED` attempt exists.

**Evidence to capture:** The `FAILED` attempt + `ERRORED` message; the zero
filed-result / zero filing-event evidence with the queue still OPEN; the later
`SUCCEEDED` attempt and `RESOLVED` queue.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-016 - Identical replay, rejection after resolution, one-success, and conflict

**Requirements:** R-034, R-035, R-036, R-041

**Precondition:** A RESOLVED queue item from a successful re-drive. Reuse the
UAT-012 item, or build a fresh one:
`open_order("SYN-8901","ACC-REC-0901")`, ingest
`original("09_specimen_incompatible.hl7")`, then a successful
`recovery.redrive_queue_item(..., request_id="BASE", actor="analyst01")`.

**Steps:**
1. **Identical replay:** call the same re-drive again with the **same**
   `request_id="BASE"`, action, corrected payload, and actor. Snapshot message,
   FISH-result, attempt, and filing-event counts before and after (read-only).
2. **Post-resolution rejection:** call the re-drive with a **new**
   `request_id="NEW"` against the RESOLVED item.
3. **Conflict:** call the re-drive reusing `request_id="BASE"` with a **different
   actor** (`actor="someone-else"`) - expect `recovery.RequestIdConflictError`.
4. Confirm (read-only) exactly one `SUCCEEDED` attempt and one filing outcome
   remain, and exactly one `REQUEST_ID_CONFLICT` audit event was added:

   ```sql
   SELECT COUNT(*) FROM interface_recovery_attempt
   WHERE queue_id = :queue_id AND outcome = 'SUCCEEDED';
   SELECT COUNT(*) FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT';
   ```

**Expected result:** The replay returns the **existing** attempt with **no** new
or changed records. The new-`request_id` request is `REJECTED` with no message,
no FISH change, no additional filing event. The conflicting reuse raises
`RequestIdConflictError`, creates no message/attempt/FISH result, never
overwrites the original attempt, and records exactly one `REQUEST_ID_CONFLICT`
audit event. Exactly one `SUCCEEDED` attempt exists for the item.

**Evidence to capture:** Before/after counts for the replay (identical); the
`REJECTED` outcome; the raised conflict; the single-success and single-conflict
counts.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-017 - Ordered history, fingerprint/detail, lineage, filing, and conflict audit

**Requirements:** R-033, R-040

**Precondition:** A queue item with more than one attempt - e.g. the UAT-015 item
(a `FAILED` then a `SUCCEEDED`), or the UAT-016 item plus its conflict.

**Steps:**
1. Retrieve history through the service:
   `hist = recovery.get_recovery_history(conn, queue_id)`.
2. For each attempt read `attempt_id`, `action`, `outcome`, `payload_sha256`,
   `outcome_detail`, and `resulting_message_id`.
3. Confirm the resulting-message lineage (read-only): each non-null
   `resulting_message_id` is an `interface_message` row (`FILED` for
   `SUCCEEDED`, `ERRORED` for `FAILED`).
4. Confirm the successful filing produced an `INBOUND_RESULT_FILED` audit event
   on the order, and that any `request_id` conflict is visible as a
   `REQUEST_ID_CONFLICT` audit event (read-only) but is **not** a history row.

**Expected result:** `hist` lists every SUCCEEDED/FAILED/REJECTED attempt in
`attempt_id` order (conflicts excluded). Each carries a `payload_sha256`
fingerprint and a human-readable `outcome_detail` explaining what payload was
attempted and why it succeeded/failed/was rejected. Message lineage resolves;
the filing audit event and any conflict event are visible in `audit_event`.

**Evidence to capture:** The ordered history list; a sample `outcome_detail` and
`payload_sha256`; the `INBOUND_RESULT_FILED` and `REQUEST_ID_CONFLICT` audit
rows.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## UAT-018 - File-backed durability, no dangling transaction, and FK integrity

**Requirements:** R-030, R-031, R-039

**Precondition:** A **file-backed** database (not `:memory:`), so durability can
be checked across a reopen:

```python
conn = create_database("uat_recovery.db")
```

**Steps:**
1. Create and recover an item successfully:
   `open_order("SYN-8901","ACC-REC-0901")`, ingest
   `original("09_specimen_incompatible.hl7")`, then a successful
   `recovery.redrive_queue_item(...)`.
2. Confirm no dangling transaction after the service call: `conn.in_transaction`
   is `False`.
3. Confirm foreign-key integrity (read-only):
   `conn.execute("PRAGMA foreign_key_check").fetchall()` returns `[]`.
4. Close the connection, reopen the same file (`create_database("uat_recovery.db")`),
   and re-read the queue item, attempt, and resulting message.

**Expected result:** After reopening, the queue item is durably `RESOLVED`
(`resolved_at` set, `terminal_at` null - consistent per state), exactly one
`SUCCEEDED` attempt persists, and its resulting message is `FILED`.
`conn.in_transaction` was `False` after the call and `PRAGMA foreign_key_check`
is empty. Recovery is reachable entirely through the headless Python service (no
UI/API/CLI).

**Evidence to capture:** The `in_transaction = False` result; the empty
`foreign_key_check`; the durable `RESOLVED` queue row, single `SUCCEEDED`
attempt, and `FILED` message after reopen.

> Delete the scratch `uat_recovery.db` file afterward; it is throwaway synthetic
> evidence.

**Pass / Fail:** [ ] Pass [ ] Fail  Notes: ________________________________

---

## Result summary

| UAT | Title | Requirements | Result |
|---|---|---|---|
| UAT-001 | Happy-path finalization | R-001, R-002, R-006, R-007 | [ ] P [ ] F |
| UAT-002 | Missing probe blocks finalize | R-003, R-004, R-005, R-006 | [ ] P [ ] F |
| UAT-003 | Outbound HL7 export | R-008 | [ ] P [ ] F |
| UAT-004 | Outbound FHIR export | R-009 | [ ] P [ ] F |
| UAT-005 | Inbound valid ORU files results | R-010, R-011 | [ ] P [ ] F |
| UAT-006 | Unmatched accession -> queue | R-012, R-015, R-018 | [ ] P [ ] F |
| UAT-007 | Missing OBX -> queue | R-013, R-018 | [ ] P [ ] F |
| UAT-008 | Invalid numeric -> queue | R-014, R-017, R-018 | [ ] P [ ] F |
| UAT-009 | Audit trail review | R-007, R-016 | [ ] P [ ] F |
| UAT-010 | Analyst query review | R-019 | [ ] P [ ] F |
| UAT-011 | Classification + initial queue state | R-020, R-021 | [ ] P [ ] F |
| UAT-012 | Corrected re-drive, lineage, immutability | R-022, R-023, R-024, R-027, R-028 | [ ] P [ ] F |
| UAT-013 | Unchanged ORDER_NOT_FOUND retry | R-025, R-026 | [ ] P [ ] F |
| UAT-014 | Prohibited/closed/dynamic-terminal rejection | R-029, R-030 | [ ] P [ ] F |
| UAT-015 | Handled FAILED, rollback, later success | R-032, R-037, R-038 | [ ] P [ ] F |
| UAT-016 | Replay, post-resolution reject, conflict | R-034, R-035, R-036, R-041 | [ ] P [ ] F |
| UAT-017 | Ordered history, fingerprint/detail, audit | R-033, R-040 | [ ] P [ ] F |
| UAT-018 | Durability, no dangling txn, FK integrity | R-030, R-031, R-039 | [ ] P [ ] F |

> The blanks above are unfilled by design: these v1 and v1.1 UAT scripts are
> **defined but not executed**. Record a manual pass only after actually running
> the steps and capturing the evidence.
