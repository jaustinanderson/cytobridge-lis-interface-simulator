# Interface troubleshooting and controlled recovery runbook

An analyst runbook for inbound HL7 ORU-style messages: how to read a failed
message's structured classification, and how to recover it safely through the
**controlled recovery service** (CytoBridge v1.1). Recovery is headless: an
analyst calls the Python service functions; there is no UI, background worker,
or asynchronous queue.

> **Educational simulator - synthetic data only, no PHI.** The messages and
> accession numbers below are made up for demonstration. This is an HL7-*style*
> parser, not a certified HL7 engine, and the recovery service is a synchronous,
> single-process learning implementation.

> **Recovery is done through the service, not by hand.** Earlier revisions of
> this runbook told an analyst to close a queue item with a manual
> `UPDATE ... SET status = 'RESOLVED'`. **Do not do that.** Queue resolution and
> terminalization happen only inside the recovery service, atomically, and only
> after exactly one successful recovery (or a proven-prohibited action). Raw SQL
> below is **read-only**, for investigation and evidence.

## The recovery service boundary

Three public functions in [`src/recovery.py`](../src/recovery.py):

```python
from src import recovery

recovery.retry_queue_item(conn, queue_id, *, request_id, actor)
recovery.redrive_queue_item(conn, queue_id, corrected_payload, *, request_id, actor)
recovery.get_recovery_history(conn, queue_id)
```

- `retry_queue_item` re-processes the **unchanged original payload**.
- `redrive_queue_item` processes a **corrected payload** you supply.
- `get_recovery_history` returns every recovery attempt for a queue item, in
  order.

Both action functions return a `RecoveryAttempt` (its `outcome` is `SUCCEEDED`,
`FAILED`, or `REJECTED`). A reused-but-mismatched `request_id` raises
`recovery.RequestIdConflictError`; a contradictory queue state raises
`recovery.RecoveryError`.

## Where to look

| Question | Where |
|---|---|
| Did the message arrive? | `interface_message` (`direction = 'INBOUND'`) |
| Did it file or error? | `interface_message.status` (`FILED` / `ERRORED`) |
| Why did it not file? | `interface_error_queue.reason` (free-text detail) |
| What kind of failure is it? | `interface_error_queue.failure_code`, `failure_category`, `recovery_policy` |
| Is it still open? | `interface_error_queue.status` (`OPEN` / `RESOLVED` / `TERMINAL`) |
| What recovery has been tried? | `recovery.get_recovery_history(conn, queue_id)` |
| What did a recovery change? | `fish_result` + `audit_event` (`INBOUND_RESULT_FILED`) |
| Was a request_id misused? | `audit_event` (`REQUEST_ID_CONFLICT`) |

The analyst worklist of open failures is
[`queries/interface_error_queue.sql`](../queries/interface_error_queue.sql). To
read the structured classification, query the queue directly (read-only):

```sql
SELECT queue_id, failure_code, failure_category, recovery_policy,
       status, resolved_at, terminal_at, reason, message_id
FROM interface_error_queue
WHERE queue_id = :queue_id;
```

Everything below can be reproduced with `python -m src.demo_run` (scenario 5) or
by driving the corpus under
[`sample_messages/recovery/`](../sample_messages/recovery/) through the recovery
service, exactly as `tests/test_recovery_service.py` does.

## Step 1 - Classify the failure

Every failed inbound message carries a structured triple, populated by the
inbound path from one authoritative mapping. The five categories and three
policies are fixed:

| `failure_code` | `failure_category` | `recovery_policy` | What recovery is allowed |
|---|---|---|---|
| `EMPTY_MESSAGE` | `MESSAGE_STRUCTURE` | `REDRIVE_ONLY` | Corrected re-drive only |
| `MISSING_REQUIRED_SEGMENT` | `MESSAGE_STRUCTURE` | `REDRIVE_ONLY` | Corrected re-drive only |
| `NO_OBX` | `MESSAGE_STRUCTURE` | `REDRIVE_ONLY` | Corrected re-drive only |
| `MISSING_ACCESSION` | `ORDER_MATCHING` | `REDRIVE_ONLY` | Corrected re-drive only |
| `ORDER_NOT_FOUND` | `ORDER_MATCHING` | `RETRY_OR_REDRIVE` | Unchanged retry **or** corrected re-drive |
| `ORDER_FINALIZED` | `ORDER_STATE` | `TERMINAL` | None (prohibited) |
| `ORDER_CANCELLED` | `ORDER_STATE` | `TERMINAL` | None (prohibited) |
| `SPECIMEN_UNRECOGNIZED` | `SPECIMEN` | `REDRIVE_ONLY` | Corrected re-drive only |
| `SPECIMEN_INCOMPATIBLE` | `SPECIMEN` | `REDRIVE_ONLY` | Corrected re-drive only |
| `MISSING_PROBE_CODE` | `FISH_RESULT_CONTENT` | `REDRIVE_ONLY` | Corrected re-drive only |
| `UNKNOWN_PROBE_CODE` | `FISH_RESULT_CONTENT` | `REDRIVE_ONLY` | Corrected re-drive only |
| `INVALID_CELL_COUNT` | `FISH_RESULT_CONTENT` | `REDRIVE_ONLY` | Corrected re-drive only |
| `ABNORMAL_EXCEEDS_SCORED` | `FISH_RESULT_CONTENT` | `REDRIVE_ONLY` | Corrected re-drive only |
| `INVALID_INTERPRETATION` | `FISH_RESULT_CONTENT` | `REDRIVE_ONLY` | Corrected re-drive only |

The `reason` is the human-readable detail (e.g. which probe or field). The
`recovery_policy` tells you which action, if any, the service will permit.

## Step 2 - Choose the allowed action

### When RETRY_ORIGINAL is allowed

Only for an **OPEN** queue item whose failure is `ORDER_NOT_FOUND`
(`RETRY_OR_REDRIVE`). The message itself was valid; it simply arrived before its
order existed. Once the matching order has been created (accessioned, not
finalized/cancelled), retry the **unchanged** original:

```python
attempt = recovery.retry_queue_item(
    conn, queue_id, request_id="RETRY-0001", actor="analyst01"
)
```

The retry reuses the exact original payload read from the linked original
`interface_message` (never a caller-supplied replacement, never the queue's
`raw_payload` copy), files it to the now-available order on a **new** message,
and moves the queue `OPEN -> RESOLVED`. `RETRY_ORIGINAL` against any other
failure/policy is `REJECTED` with no processing message.

### When REDRIVE_CORRECTED is allowed

For any **OPEN** item whose policy is `REDRIVE_ONLY` or `RETRY_OR_REDRIVE`. Fix
the single offending field at the source, then submit the corrected payload:

```python
attempt = recovery.redrive_queue_item(
    conn, queue_id, corrected_payload, request_id="REDRIVE-0001", actor="analyst01"
)
```

The corrected payload is processed and stored on a **new, distinct** message;
the original payload and the queue's `raw_payload` are never overwritten. On
success the queue moves `OPEN -> RESOLVED`.

### Why TERMINAL or closed items are rejected

- A `TERMINAL` item (`ORDER_FINALIZED`, `ORDER_CANCELLED`) is permanently
  prohibited: recovery never reopens, unfinalizes, or uncancels an order. Any
  request against it is `REJECTED`.
- A `RESOLVED` item already had its one successful recovery. A new request
  (new `request_id`) against it is `REJECTED` - no message, no FISH change, no
  second filing event.
- An item can also become terminal **during** a permitted retry: if processing
  discovers the now-matched order is `FINALIZED` or `CANCELLED`, the request is
  `REJECTED` and the queue moves `OPEN -> TERMINAL` (nothing is filed, the order
  is not changed).

## Step 3 - request_id and actor requirements

Both action functions require:

- `request_id` - a globally unique idempotency key you choose per logical
  recovery request.
- `actor` - a free-text actor consistent with the project's educational identity
  model (e.g. `analyst01`); it is recorded on the attempt.

`request_id` resolution runs **before** any eligibility check:

- **Matching replay:** re-submitting the same `request_id` with the same
  `queue_id`, action, payload fingerprint, and actor returns the **existing**
  recorded attempt and writes nothing - safe to retry a call whose result you
  did not see.
- **Conflict:** re-submitting the same `request_id` with any of those four
  different is a `REQUEST_ID_CONFLICT`. It raises
  `recovery.RequestIdConflictError`, fabricates no message/attempt/FISH result,
  never overwrites the original attempt, and records exactly one
  `REQUEST_ID_CONFLICT` **audit event** (audit-only evidence). Use a fresh
  `request_id` for a genuinely new request.

## Step 4 - Read the outcome and its evidence

### Exact-payload fingerprinting

Every attempt stores `payload_sha256`, the lowercase SHA-256 of the exact UTF-8
payload processed. It is audit evidence of *which* payload was attempted (a
corrected re-drive and the original have different fingerprints); it is **not**
the idempotency control by itself - `request_id` is.

### SUCCEEDED

- A new `interface_message` is `FILED` to the matched order; per-probe
  `fish_result` rows are written; an `INBOUND_RESULT_FILED` audit event names the
  source message; the attempt is `SUCCEEDED`; the queue is `RESOLVED`
  (`resolved_at` set, `terminal_at` null).
- **Inspect:** the resulting message row (`FILED`), the order's `fish_result`,
  the `INBOUND_RESULT_FILED` event, and the queue's `RESOLVED` status.

### FAILED (handled-failure rollback)

- The corrected/retried payload was still invalid in a handled way. The service
  rolls back **all** filing side effects - FISH results, filing audit events, and
  queue resolution - while preserving the attempted message as `ERRORED` and the
  attempt as `FAILED` with its explanation in `outcome_detail`. The queue stays
  `OPEN`.
- A genuinely `FAILED` attempt may be tried again later with a **new**
  `request_id` while the item is still `OPEN` and the action is allowed.
- **Inspect:** the resulting message is `ERRORED`; the order has **no** new
  `fish_result` and **no** `INBOUND_RESULT_FILED` event; the queue is still
  `OPEN`.

### REJECTED

- The action was prohibited (wrong policy, closed item, or a proven
  now-terminal order). A single `REJECTED` attempt is recorded with **no**
  resulting processing message; nothing is filed.
- **Inspect:** the attempt's `REJECTED` outcome and `outcome_detail`; confirm no
  new message or FISH result was created.

### Immutability and single-error-queue guarantees

- The **original failed message** (`message_id`, `payload`, `control_id`,
  `status = ERRORED`, `created_at`) and the queue **`raw_payload`** copy are
  never modified by any recovery action.
- The original message is **never** updated to `FILED`; only a new recovery
  message may reach `FILED`.
- Recovery **never creates a second error-queue item**. A failed recovery
  preserves the same single queue item as `OPEN`; there is no "error queue of the
  error queue".

## Step 5 - Review the recovery history

```python
for a in recovery.get_recovery_history(conn, queue_id):
    print(a.attempt_id, a.action, a.outcome, a.payload_sha256, a.outcome_detail)
```

History lists every `SUCCEEDED` / `FAILED` / `REJECTED` attempt in order.
`REQUEST_ID_CONFLICT` events are **not** history rows (a conflict creates only an
audit event); find them in `audit_event`:

```sql
SELECT entity_id, actor, detail
FROM audit_event
WHERE action = 'REQUEST_ID_CONFLICT'
ORDER BY event_id;
```

## Worked cases

### Case A - Successful inbound filing (no recovery needed)

**Message:** [`aml_mds_valid_oru.hl7`](../sample_messages/inbound/aml_mds_valid_oru.hl7)
(accession `ACC-INBOUND-0001`), addressed to an open, accessioned order. It files
nine probe results (`interface_message.status = FILED`, an `INBOUND_RESULT_FILED`
event, no error-queue row). Nothing to recover - this is the happy path.

### Case B - Unmatched accession (ORDER_NOT_FOUND) - retry after the order exists

**Corpus:** [`recovery/original/05_order_not_found.hl7`](../sample_messages/recovery/original/05_order_not_found.hl7)
(accession `ACC-REC-0500-NOMATCH`). The message is valid but matches no order at
ingest, so it lands `OPEN` as `ORDER_NOT_FOUND` / `RETRY_OR_REDRIVE`.

1. Classify: `failure_code = ORDER_NOT_FOUND`, policy `RETRY_OR_REDRIVE`.
2. Confirm the order really is missing (read-only):

   ```sql
   SELECT order_id, status FROM lab_order
   WHERE accession_number = 'ACC-REC-0500-NOMATCH';   -- returns nothing yet
   ```
3. Once the order is created (message beat the order into the LIS), retry the
   unchanged original: `recovery.retry_queue_item(conn, queue_id,
   request_id="RETRY-0500", actor="analyst01")`. It files on a new message and the
   queue moves to `RESOLVED`. Nothing was filed before, so there is nothing to
   unwind.

### Case C - Corrected re-drive (REDRIVE_ONLY)

**Corpus:** [`recovery/original/09_specimen_incompatible.hl7`](../sample_messages/recovery/original/09_specimen_incompatible.hl7)
matches an OPEN order but carries peripheral blood (`PB`) against the bone-marrow
panel, so it lands `OPEN` as `SPECIMEN_INCOMPATIBLE` / `REDRIVE_ONLY`.

1. Classify: policy `REDRIVE_ONLY` - a corrected re-drive is the only allowed
   action.
2. Read `reason` to find the offending field (`SPM-4` specimen code).
3. Fix it at the source and re-drive the corrected payload
   ([`recovery/corrected/09_specimen_incompatible.hl7`](../sample_messages/recovery/corrected/09_specimen_incompatible.hl7)):
   `recovery.redrive_queue_item(conn, queue_id, corrected_payload,
   request_id="REDRIVE-0901", actor="analyst01")`. The corrected message files on
   a new `message_id`; the original stays `ERRORED`; the queue moves to
   `RESOLVED`.

The same investigate-and-redrive pattern applies to the other `REDRIVE_ONLY`
cases the queue can report (`MISSING_REQUIRED_SEGMENT`, `NO_OBX`,
`MISSING_ACCESSION`, `SPECIMEN_UNRECOGNIZED`, `MISSING_PROBE_CODE`,
`UNKNOWN_PROBE_CODE`, `INVALID_CELL_COUNT`, `ABNORMAL_EXCEEDS_SCORED`,
`INVALID_INTERPRETATION`, and the additive `EMPTY_MESSAGE`). In each the
`reason` names the exact problem, nothing was filed, and a single corrected
re-drive is the fix.

### Case D - Terminal order (ORDER_FINALIZED / ORDER_CANCELLED)

**Corpus:** [`recovery/original/06_order_finalized.hl7`](../sample_messages/recovery/original/06_order_finalized.hl7)
matches an order that is already `FINALIZED`, so it lands `TERMINAL`. There is no
corrected fixture and no permitted action: any recovery request is `REJECTED`,
and the order is never reopened or unfinalized. Confirm the classification
(`ORDER_FINALIZED` / `ORDER_STATE` / `TERMINAL`), record it, and route the
underlying clinical question (should this order have been finalized?) to the
appropriate workflow - not to a queue override.

## What evidence to inspect after each outcome

| Outcome | Inspect (read-only) |
|---|---|
| `SUCCEEDED` | New `FILED` message; order `fish_result` rows; `INBOUND_RESULT_FILED` audit event; queue `RESOLVED` (`resolved_at` set, `terminal_at` null); one `SUCCEEDED` attempt |
| `FAILED` | Attempted message `ERRORED`; **no** new `fish_result`; **no** `INBOUND_RESULT_FILED` event; queue still `OPEN`; `FAILED` attempt with `outcome_detail` |
| `REJECTED` (closed/prohibited) | No new message; no `fish_result`; queue unchanged; single `REJECTED` attempt with `outcome_detail` |
| `REJECTED` (dynamic terminal) | No new message; no `fish_result`; queue `OPEN -> TERMINAL` (`terminal_at` set); target order status unchanged |
| `REQUEST_ID_CONFLICT` | No new message/attempt/FISH result; original attempt unchanged; exactly one `REQUEST_ID_CONFLICT` audit event |

Always verify the **original** message row and the queue `raw_payload` are
unchanged after any action, and that at most one `SUCCEEDED` attempt exists per
queue item.
