# Interface troubleshooting (inbound, Session 3)

A short analyst runbook for inbound HL7 ORU-style messages: what a successful
filing looks like, and how to investigate the two most common failures that land
in the **interface error queue**.

> **Educational simulator — synthetic data only, no PHI.** The messages and
> accession numbers below are made up for demonstration. This is an HL7-*style*
> parser, not a certified HL7 engine.

## Where to look

| Question | Where |
|---|---|
| Did the message arrive? | `interface_message` (`direction = 'INBOUND'`) |
| Did it file or error? | `interface_message.status` (`FILED` / `ERRORED`) |
| Why did it not file? | `interface_error_queue.reason` (worklist query below) |
| What did filing change? | `fish_result` + `audit_event` (`INBOUND_RESULT_FILED`) |

The analyst worklist of open failures is
[`queries/interface_error_queue.sql`](../queries/interface_error_queue.sql):

```sql
SELECT eq.queue_id, eq.created_at, eq.direction, eq.reason,
       eq.message_id, im.message_type, im.format, im.control_id, eq.status
FROM interface_error_queue AS eq
LEFT JOIN interface_message AS im ON im.message_id = eq.message_id
WHERE eq.status = 'OPEN'
ORDER BY eq.created_at ASC;
```

Everything below can be reproduced with `python -m src.demo_run` (scenario 4) or
by feeding the files in [`sample_messages/inbound/`](../sample_messages/inbound/)
to `interfaces.inbound_hl7.ingest_message`.

---

## Case 1 — Successful inbound filing

**Message:** [`aml_mds_valid_oru.hl7`](../sample_messages/inbound/aml_mds_valid_oru.hl7)
(accession `ACC-INBOUND-0001`, MRN `SYN-7001`), addressed to an open,
accessioned order.

```
MSH|^~\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||ORU^R01|INBND0001|T|2.5.1
PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M
OBR|1||ACC-INBOUND-0001|AML_MDS_FISH^AML/MDS FISH Panel^L|...
SPM|1|BM-7001||BMA^Bone Marrow^L|...
OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^36^2F1R1G^ABNORMAL||||||F
... (8 more probe OBX)
```

**Outcome:** `IngestResult(filed=True, order_id=…, probe_codes_filed=[9 probes])`.

**What the analyst sees:**

- `interface_message` row: `INBOUND / ORU / HL7`, `status = FILED`, linked to the
  order.
- `fish_result`: nine per-probe rows on the order (`RUNX1T1_RUNX1` abnormal,
  36/200; the rest normal).
- `audit_event`: one `RESULT_ENTERED` per probe **plus** an
  `INBOUND_RESULT_FILED` event on the order whose `detail` names the source
  `message_id` — the paper trail proving the result came from the interface.
- The error queue stays empty for this message.

**How to confirm:**

```sql
-- Results now on the order
SELECT pr.probe_code, fr.cells_scored, fr.cells_abnormal, fr.interpretation
FROM fish_result fr JOIN probe pr ON pr.probe_id = fr.probe_id
WHERE fr.order_id = :order_id ORDER BY pr.probe_id;

-- Proof it came from the interface
SELECT action, detail FROM audit_event
WHERE order_id = :order_id AND action = 'INBOUND_RESULT_FILED';
```

Nothing to resolve — this is the happy path.

---

## Case 2 — Unmatched accession

**Message:** [`aml_mds_unmatched_accession.hl7`](../sample_messages/inbound/aml_mds_unmatched_accession.hl7)
(accession `ACC-NOMATCH-9999`).

**Outcome:** `filed=False`; the message is stored (`status = ERRORED`) and an
error-queue row opens with:

```
No order matches accession number ACC-NOMATCH-9999.
```

**How the analyst investigates:**

1. Pull the open queue (query above); note the `reason` and `message_id`.
2. Read the raw message from `interface_message.payload` for that `message_id`
   and confirm the accession in `OBR-3`.
3. Look for the order:

   ```sql
   SELECT order_id, status, accession_number FROM lab_order
   WHERE accession_number = 'ACC-NOMATCH-9999';   -- returns nothing
   ```

**How to resolve:**

- **Typo / mismatched accession at the instrument** — the usual cause. Correct
  the accession at the source and have the instrument re-send; the corrected
  message matches and files normally.
- **Order not created yet** (message beat the order into the LIS) — once the
  order exists, re-send the message.
- Either way, once handled, close the queue item:

  ```sql
  UPDATE interface_error_queue
  SET status = 'RESOLVED', resolved_at = datetime('now')
  WHERE queue_id = :queue_id;
  ```

Nothing was filed to any order, so there is no partial result to unwind.

---

## Case 3 — Malformed result value

**Message:** [`aml_mds_invalid_result_value.hl7`](../sample_messages/inbound/aml_mds_invalid_result_value.hl7).
The accession matches an open order and the first OBX is fine, but the second
carries a non-numeric abnormal count:

```
OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^36^2F1R1G^ABNORMAL||||||F
OBX|2|ST|CBFB^CBFB break-apart^L||200^xx^2 orange/green^NORMAL||||||F
                                        ^^ not an integer
```

**Outcome:** `filed=False` with reason:

```
Probe CBFB: cells_abnormal 'xx' is not a valid integer.
```

Because filing is **all-or-nothing**, the valid `RUNX1T1_RUNX1` OBX in the same
message is **not** filed either — the order is left untouched so it can never end
up half-updated.

**How the analyst investigates:**

1. Read the `reason` — it names the probe (`CBFB`) and the offending field
   (`cells_abnormal = 'xx'`).
2. Open `interface_message.payload` and find that OBX; the packed `OBX-5` value
   is `scored^abnormal^signal^interpretation`, so the second component is the bad
   one.
3. Confirm the order itself is fine and simply awaiting a clean message:

   ```sql
   SELECT COUNT(*) FROM fish_result WHERE order_id = :order_id;  -- unchanged
   ```

**How to resolve:**

- Fix the value at the instrument/source (here, the true abnormal cell count for
  `CBFB`) and re-send the corrected message. On resend the whole message
  validates and all probes file together.
- Mark the queue item `RESOLVED` (same `UPDATE` as Case 2).

The same investigate-and-resend pattern applies to the other malformed cases the
queue can report — `cells_abnormal exceeds cells_scored`, an unknown probe code
for the panel, a missing required segment, or an incompatible specimen type. In
each the `reason` string names the exact problem, nothing is filed, and a
corrected resend is the fix.
