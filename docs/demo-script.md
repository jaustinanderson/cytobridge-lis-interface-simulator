# 5-minute demo script

A screen-share walkthrough of the CytoBridge LIS Interface Simulator for a
hiring manager or interviewer. Timed for ~5 minutes. Each step has a **show**
(what's on screen) and a **say** (the one or two sentences to narrate).

> **Framing to open with:** "This is a synthetic, analyst-first learning project
> that models one lab workflow - AML/MDS FISH - end to end, plus the HL7/FHIR
> interfaces around it. All data is synthetic, no PHI. It's *Beaker-adjacent*
> learning - it is **not** Epic build experience."

**Total: ~5:00.** Have a terminal open at the repo root and the repo browsable.

---

## 0:00-0:45 - README (orientation)

- **Show:** `README.md` top section and the "Scope" list.
- **Say:** "The project models a real lab lifecycle: create a synthetic patient
  and order, accession a specimen, enter per-probe FISH results, validate,
  finalize, and then move messages across interfaces. It's raw Python + SQLite +
  hand-written SQL - no ORM, no framework - so the data model and logic are on
  display, not hidden."
- **Point out:** the Epic/Beaker boundary and the synthetic-data notice near the
  top - "I'm deliberate about not overclaiming."

## 0:45-1:30 - schema.sql (data model)

- **Show:** `schema.sql` - the `lab_order`, `fish_result`, and
  `interface_error_queue` tables.
- **Say:** "The schema does real work. Note the constraints: enumerated statuses,
  foreign keys, `UNIQUE (order_id, probe_id)` so a probe result is entered once
  and updated in place, and a `CHECK` that abnormal cells can never exceed scored
  cells. Data integrity is enforced at the database, not just in code."
- **Point out:** `interface_message` and `interface_error_queue` - "the interface
  storage and error queue were provisioned in session 1 and implemented later."

## 1:30-2:00 - queries/ (analyst views)

- **Show:** the `queries/` folder; open `interface_error_queue.sql` and
  `pending_review.sql`.
- **Say:** "These are the analyst worklists - pending review with STAT first,
  turnaround time, validation error rate, an audit lookup for one order, and the
  open interface-error queue. This is the SQL an interface analyst actually lives
  in."

## 2:00-3:15 - python -m src.demo_run (the system running)

- **Show:** run `python -m src.demo_run` and scroll through the four scenarios.
- **Say, scenario by scenario:**
  1. "Scenario 1 - a complete order passes validation and finalizes, and you see
     the report summary and the full audit trail."
  2. "Scenario 2 - an order missing one required probe is **blocked** from
     finalizing, with the validation findings shown. The system refuses to
     finalize incomplete work."
  3. "Scenario 3 - that finalized order is exported outbound as both an HL7 ORU
     message and a FHIR DiagnosticReport, stored in `interface_message`."
  4. "Scenario 4 - inbound: a valid instrument message files results to an open
     order, while an unmatched and a malformed message land in the error queue
     with clear reasons."
- **Point out:** the printed error-queue lines at the end - "nothing is silently
  dropped."

## 3:15-3:45 - Sample outbound messages

- **Show:** `sample_messages/outbound/aml_mds_oru.hl7` and
  `aml_mds_diagnostic_report.json`.
- **Say:** "Here's the actual generated output - pipe-delimited HL7 with
  MSH/PID/OBR/SPM/OBX, and the FHIR Bundle. Both are generated from one snapshot
  so they always agree. `MSH-11` is `T` for training and the code systems are
  synthetic - these are educational, not certified."

## 3:45-4:15 - Sample inbound messages + error-queue behavior

- **Show:** `sample_messages/inbound/` - open `aml_mds_valid_oru.hl7` and
  `aml_mds_invalid_result_value.hl7`; then `docs/interface-troubleshooting.md`.
- **Say:** "Inbound messages carry the per-probe result packed into OBX-5. A
  valid one files to the matched open order. The invalid one has a non-numeric
  count - and because filing is **all-or-nothing**, the good result in that same
  message is *not* filed either; the whole message goes to the error queue. The
  troubleshooting doc is the analyst runbook: how to read the reason and resolve
  it."

## 4:15-4:45 - Traceability matrix

- **Show:** `validation/traceability-matrix.md`.
- **Say:** "Every requirement - R-001 through R-019 - maps to the exact file and
  function that implements it, the `pytest` test that proves it, and a manual UAT
  script. This is how I'd hand a system to QA or an auditor: nothing is claimed
  that isn't traced."

## 4:45-5:00 - Validation summary (close)

- **Show:** `validation/validation-summary.md` results table.
- **Say:** "The bottom line: 61 automated tests pass, all 19 requirements are
  fully traced and verified within the synthetic scope, and the demo runs clean.
  And I wrote down the limitations honestly in `known-issues.md` and
  `risk-assessment.md` - including that this is Beaker-adjacent learning, not
  Epic build experience."

---

## If you have 60 more seconds (optional Q&A hooks)

- **"Show me a test."** Open `tests/test_inbound_interfaces.py::`
  `test_invalid_numeric_result_goes_to_error_queue` - "all-or-nothing proven in
  one test."
- **"What would you build next?"** The error-queue *resolve/re-drive* workflow
  (KI-03), followed by the ISCN parser seam if it serves a specific learning goal.
- **"How is this different from Epic?"** Point to
  [`portfolio-review.md`](portfolio-review.md) - "I model the *category* of
  system Beaker is; I don't use Epic software or build content."
