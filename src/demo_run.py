"""Headless demo of the CytoBridge AML/MDS FISH workflow.

Runs several scenarios against a fresh in-memory database:

  1. Happy path — a complete order that passes validation and finalizes.
  2. Missing-probe failure — an order missing a required probe, whose
     finalization is blocked with recorded validation errors.
  3. Outbound interfaces — HL7 ORU + FHIR DiagnosticReport export.
  4. Inbound ingestion — a valid instrument message files probe results to an
     open order, while an unmatched message lands in the interface error queue.
  5. Controlled error-queue recovery (v1.1) - corrected re-drive, unchanged
     ORDER_NOT_FOUND retry, a handled failure and later success, and duplicate/
     replay/conflict protection, all through the public recovery service.

The recovery scenario shows representative cases only. The automated recovery
suite (``tests/test_recovery_service.py``) is what proves all twelve corrected
re-drive corpus cases and every approved invariant; this demo narrates a
readable subset for a walkthrough.

Run with:  python -m src.demo_run
All data is synthetic. No PHI.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import recovery, workflow
from .db import create_database, run_query
from .interfaces import inbound_hl7, outbound_fhir, outbound_hl7

_SAMPLES = Path(__file__).resolve().parent.parent / "sample_messages"
_INBOUND_SAMPLES = _SAMPLES / "inbound"
_RECOVERY_ORIGINAL = _SAMPLES / "recovery" / "original"
_RECOVERY_CORRECTED = _SAMPLES / "recovery" / "corrected"

# All nine required AML/MDS FISH probes with synthetic, plausible results.
# One abnormal probe (t(8;21)) to exercise the ABNORMAL path.
_FULL_RESULTS = [
    # probe_code,      scored, abnormal, signal_pattern,       interpretation
    ("RUNX1T1_RUNX1", 200, 38, "2F1R1G (fusion present)", "ABNORMAL"),
    ("CBFB",          200,  1, "2 orange/green",           "NORMAL"),
    ("PML_RARA",      200,  2, "2R2G",                     "NORMAL"),
    ("KMT2A",         200,  1, "2 fusion",                 "NORMAL"),
    ("EGR1_5q",       200,  3, "2 orange 2 green",         "NORMAL"),
    ("D7S486_7q",     200,  2, "2 signals",                "NORMAL"),
    ("CEP8",          200,  4, "2 aqua",                   "NORMAL"),
    ("D20S108_20q",   200,  1, "2 signals",                "NORMAL"),
    ("TP53_17p",      200,  2, "2R2G",                     "NORMAL"),
]


def _banner(title: str) -> None:
    print("\n" + "=" * 68)
    print(title)
    print("=" * 68)


def scenario_happy_path(conn: sqlite3.Connection) -> int:
    _banner("Scenario 1: Happy path — complete order finalizes")

    patient_id = workflow.create_patient(
        conn, mrn="SYN-1001", last_name="Doe", first_name="Alex",
        date_of_birth="1970-05-14", sex="F",
    )
    order_id = workflow.create_order(
        conn, patient_id, accession_number="ACC-2026-0001",
        ordering_provider="Dr. Synthetic", priority="ROUTINE",
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)

    for probe_code, scored, abn, pattern, interp in _FULL_RESULTS:
        workflow.enter_fish_result(
            conn, order_id, probe_code, scored, abn, pattern, interp,
            entered_by="tech01",
        )

    result = workflow.finalize_order(conn, order_id, finalized_by="analyst01")
    print(f"Finalized: {result.finalized}  (report_id={result.report_id})")

    report = conn.execute(
        "SELECT summary_text FROM report WHERE order_id = ?", (order_id,)
    ).fetchone()
    print("\n--- Report summary ---")
    print(report["summary_text"])

    print("\n--- Audit trail (audit_lookup.sql) ---")
    for row in run_query(conn, "audit_lookup", {"order_id": order_id}):
        print(f"  [{row['event_id']:>2}] {row['action']:<18} "
              f"{row['entity_type']}#{row['entity_id']}  {row['detail'] or ''}")

    return order_id


def scenario_missing_probe(conn: sqlite3.Connection) -> int:
    _banner("Scenario 2: Missing required probe — finalization blocked")

    patient_id = workflow.create_patient(
        conn, mrn="SYN-1002", last_name="Roe", first_name="Sam",
        date_of_birth="1985-11-02", sex="M",
    )
    order_id = workflow.create_order(
        conn, patient_id, accession_number="ACC-2026-0002",
        ordering_provider="Dr. Synthetic", priority="STAT",
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)

    # Enter every required probe EXCEPT the last one (TP53_17p).
    for probe_code, scored, abn, pattern, interp in _FULL_RESULTS[:-1]:
        workflow.enter_fish_result(
            conn, order_id, probe_code, scored, abn, pattern, interp,
            entered_by="tech01",
        )

    result = workflow.finalize_order(conn, order_id, finalized_by="analyst01")
    print(f"Finalized: {result.finalized}  (expected False)")

    print("\n--- Validation findings (blocking finalize) ---")
    for f in result.findings:
        print(f"  [{f.severity}] {f.rule_code}: {f.message}")

    return order_id


def scenario_outbound_interfaces(conn: sqlite3.Connection, order_id: int) -> None:
    _banner("Scenario 3: Outbound interfaces — HL7 ORU + FHIR DiagnosticReport")
    # Educational, ORU^R01-style / FHIR R4-style output for a FINALIZED order.
    # Only finalized orders can be exported; both messages are stored in the
    # interface_message table (direction = OUTBOUND).
    outbound_hl7.store_oru(conn, order_id)
    outbound_fhir.store_diagnostic_report(conn, order_id)

    hl7 = outbound_hl7.generate_oru(conn, order_id)
    print("HL7 ORU^R01-style message (first 4 segments):")
    for segment in hl7.split("\r")[:4]:
        print(f"  {segment}")

    print("\nFHIR DiagnosticReport-style Bundle (resource types):")
    bundle = outbound_fhir.build_diagnostic_report(conn, order_id)
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    print(f"  {', '.join(types)}")

    print("\nStored interface messages (interface_message):")
    for row in conn.execute(
        "SELECT message_id, direction, format, message_type, status "
        "FROM interface_message WHERE order_id = ? ORDER BY message_id",
        (order_id,),
    ).fetchall():
        print(f"  [{row['message_id']}] {row['direction']} {row['format']} "
              f"{row['message_type']} — {row['status']}")


def scenario_inbound_ingestion(conn: sqlite3.Connection) -> None:
    _banner("Scenario 4: Inbound ingestion — file results + error queue")
    # A synthetic instrument sends an ORU-style result message. A valid message
    # matched to an open order files its per-probe results; an unmatched or
    # malformed one is routed to interface_error_queue with a clear reason.

    # An open (accessioned, not finalized) order matching the valid sample.
    patient_id = workflow.create_patient(
        conn, mrn="SYN-7001", last_name="Synthetic", first_name="Ingest",
        date_of_birth="1966-03-12", sex="M",
    )
    order_id = workflow.create_order(
        conn, patient_id, accession_number="ACC-INBOUND-0001",
        ordering_provider="Dr. Synthetic",
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)

    valid = inbound_hl7.ingest_file(conn, _INBOUND_SAMPLES / "aml_mds_valid_oru.hl7")
    print(f"Valid message   -> filed={valid.filed}  "
          f"order_id={valid.order_id}  probes={len(valid.probe_codes_filed)}")

    unmatched = inbound_hl7.ingest_file(
        conn, _INBOUND_SAMPLES / "aml_mds_unmatched_accession.hl7"
    )
    print(f"Unmatched msg   -> filed={unmatched.filed}  "
          f"queued (reason): {unmatched.reason}")

    invalid = inbound_hl7.ingest_file(
        conn, _INBOUND_SAMPLES / "aml_mds_invalid_result_value.hl7"
    )
    print(f"Invalid value   -> filed={invalid.filed}  "
          f"queued (reason): {invalid.reason}")

    print("\n--- Inbound messages (interface_message) ---")
    for row in conn.execute(
        "SELECT message_id, direction, message_type, status "
        "FROM interface_message WHERE direction = 'INBOUND' ORDER BY message_id"
    ).fetchall():
        print(f"  [{row['message_id']}] {row['direction']} "
              f"{row['message_type']} — {row['status']}")

    print("\n--- Open interface error queue (interface_error_queue.sql) ---")
    for row in run_query(conn, "interface_error_queue"):
        print(f"  [{row['queue_id']}] {row['status']} — {row['reason']}")


# ---------------------------------------------------------------------------
# Scenario 5 helpers - controlled error-queue recovery (v1.1).
#
# Everything here goes through the public recovery service boundary
# (retry_queue_item / redrive_queue_item / get_recovery_history) and the
# existing public workflow/inbound functions. No private recovery or inbound
# helper is called, no recovery attempt is inserted by hand, and no queue item
# is resolved or terminalized through manual SQL.
# ---------------------------------------------------------------------------

def _recovery_original(fixture: str) -> str:
    return (_RECOVERY_ORIGINAL / fixture).read_text(encoding="utf-8")


def _recovery_corrected(fixture: str) -> str:
    return (_RECOVERY_CORRECTED / fixture).read_text(encoding="utf-8")


def _make_open_order(conn: sqlite3.Connection, mrn: str, accession: str) -> int:
    """An accessioned, non-terminal AML/MDS FISH bone-marrow order."""
    patient_id = workflow.create_patient(
        conn, mrn=mrn, last_name="Synthetic", first_name="Recovery",
        date_of_birth="1970-01-01", sex="M",
    )
    order_id = workflow.create_order(
        conn, patient_id, accession_number=accession,
        ordering_provider="Dr. Synthetic",
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def _message(conn: sqlite3.Connection, message_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT message_id, status, payload FROM interface_message "
        "WHERE message_id = ?",
        (message_id,),
    ).fetchone()


def _queue(conn: sqlite3.Connection, queue_id: int) -> sqlite3.Row:
    return conn.execute(
        "SELECT status, resolved_at, terminal_at FROM interface_error_queue "
        "WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()


def _count(conn: sqlite3.Connection, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _print_history(conn: sqlite3.Connection, queue_id: int) -> None:
    print(f"  recovery history for queue {queue_id} (get_recovery_history):")
    for h in recovery.get_recovery_history(conn, queue_id):
        print(f"    attempt {h.attempt_id}: {h.action:<17} -> {h.outcome:<9} "
              f"msg={h.resulting_message_id} sha={h.payload_sha256[:12]}...")
        print(f"      detail: {h.outcome_detail}")


def _corrected_redrive(conn: sqlite3.Connection) -> int:
    """Corrected re-drive: original stays ERRORED; a new message files; RESOLVED."""
    print("\n-- 5a. Corrected re-drive (SPECIMEN_INCOMPATIBLE, REDRIVE_ONLY) --")
    _make_open_order(conn, "SYN-8901", "ACC-REC-0901")
    ingest = inbound_hl7.ingest_message(conn, _recovery_original("09_specimen_incompatible.hl7"))
    queue_id = ingest.queue_id
    original_before = dict(_message(conn, ingest.message_id))
    print(f"  original message {ingest.message_id} status="
          f"{original_before['status']}; queue {queue_id} OPEN "
          f"(reason: {ingest.reason})")

    attempt = recovery.redrive_queue_item(
        conn, queue_id, _recovery_corrected("09_specimen_incompatible.hl7"),
        request_id="DEMO-REDRIVE-A", actor="analyst01",
    )
    after = dict(_message(conn, ingest.message_id))
    new_msg = _message(conn, attempt.resulting_message_id)
    print(f"  redrive attempt {attempt.attempt_id}: {attempt.outcome}; "
          f"new message {attempt.resulting_message_id} status={new_msg['status']}")
    print(f"  original message unchanged: {after == original_before} "
          f"(still {after['status']})")
    q = _queue(conn, queue_id)
    print(f"  queue {queue_id} -> {q['status']} "
          f"(resolved_at set: {q['resolved_at'] is not None}, "
          f"terminal_at null: {q['terminal_at'] is None})")
    print(f"  payload fingerprint: {attempt.payload_sha256}")
    _print_history(conn, queue_id)
    return queue_id


def _unchanged_retry(conn: sqlite3.Connection) -> None:
    """Unchanged ORDER_NOT_FOUND retry once the matching order is available."""
    print("\n-- 5b. Unchanged retry (ORDER_NOT_FOUND, RETRY_OR_REDRIVE) --")
    original = _recovery_original("05_order_not_found.hl7")
    ingest = inbound_hl7.ingest_message(conn, original)
    queue_id = ingest.queue_id
    print(f"  original message {ingest.message_id} ERRORED; queue {queue_id} "
          f"OPEN ORDER_NOT_FOUND (no matching order yet)")

    # The matching synthetic order becomes available.
    _make_open_order(conn, "SYN-8500", "ACC-REC-0500-NOMATCH")
    print("  matching order ACC-REC-0500-NOMATCH now exists")

    attempt = recovery.retry_queue_item(
        conn, queue_id, request_id="DEMO-RETRY-A", actor="analyst01",
    )
    new_msg = _message(conn, attempt.resulting_message_id)
    print(f"  retry attempt {attempt.attempt_id}: {attempt.outcome}; "
          f"new message {attempt.resulting_message_id} status={new_msg['status']}")
    print(f"  original payload reused byte-for-byte on a distinct message: "
          f"{new_msg['payload'] == original and attempt.resulting_message_id != ingest.message_id}")
    print(f"  queue {queue_id} -> {_queue(conn, queue_id)['status']}")


def _handled_failure_then_success(conn: sqlite3.Connection) -> None:
    """A still-invalid payload FAILS (queue stays OPEN); a later valid request wins."""
    print("\n-- 5c. Handled failure, then later recovery (UNKNOWN_PROBE_CODE) --")
    order_id = _make_open_order(conn, "SYN-9101", "ACC-REC-1101")
    ingest = inbound_hl7.ingest_message(conn, _recovery_original("11_unknown_probe_code.hl7"))
    queue_id = ingest.queue_id

    # A re-drive whose payload is still invalid: FAILED, no partial filing.
    failed = recovery.redrive_queue_item(
        conn, queue_id, _recovery_original("11_unknown_probe_code.hl7"),
        request_id="DEMO-FAIL-1", actor="analyst01",
    )
    failed_msg = _message(conn, failed.resulting_message_id)
    q = _queue(conn, queue_id)
    order_fish = _count(
        conn, "SELECT COUNT(*) FROM fish_result WHERE order_id = ?", (order_id,))
    print(f"  attempt {failed.attempt_id}: {failed.outcome}; resulting message "
          f"{failed.resulting_message_id} status={failed_msg['status']}")
    print(f"  queue {queue_id} still {q['status']}; results filed to order "
          f"{order_id}: {order_fish} (no partial filing)")

    # A later valid request with a new request_id succeeds.
    good = recovery.redrive_queue_item(
        conn, queue_id, _recovery_corrected("11_unknown_probe_code.hl7"),
        request_id="DEMO-FAIL-2", actor="analyst01",
    )
    print(f"  attempt {good.attempt_id}: {good.outcome}; new message "
          f"{good.resulting_message_id} status={_message(conn, good.resulting_message_id)['status']}")
    print(f"  queue {queue_id} -> {_queue(conn, queue_id)['status']}")
    _print_history(conn, queue_id)


def _duplicate_replay_conflict(conn: sqlite3.Connection, resolved_queue_id: int) -> None:
    """Replay is a no-op; a new request is REJECTED; reuse conflict is audit-only."""
    print("\n-- 5d. Duplicate, replay, and conflict protection --")
    attempts_before = _count(
        conn, "SELECT COUNT(*) FROM interface_recovery_attempt")
    messages_before = _count(conn, "SELECT COUNT(*) FROM interface_message")

    # Identical replay of the resolved item's original request: no new records.
    replay = recovery.redrive_queue_item(
        conn, resolved_queue_id,
        _recovery_corrected("09_specimen_incompatible.hl7"),
        request_id="DEMO-REDRIVE-A", actor="analyst01",
    )
    print(f"  identical replay of DEMO-REDRIVE-A returns attempt "
          f"{replay.attempt_id} ({replay.outcome}); new attempts: "
          f"{_count(conn, 'SELECT COUNT(*) FROM interface_recovery_attempt') - attempts_before}, "
          f"new messages: "
          f"{_count(conn, 'SELECT COUNT(*) FROM interface_message') - messages_before}")

    # A new request_id against the RESOLVED item is REJECTED.
    rejected = recovery.redrive_queue_item(
        conn, resolved_queue_id,
        _recovery_corrected("09_specimen_incompatible.hl7"),
        request_id="DEMO-REDRIVE-B", actor="analyst01",
    )
    print(f"  new request DEMO-REDRIVE-B on the RESOLVED item: "
          f"{rejected.outcome} (resulting message: {rejected.resulting_message_id})")

    # Reusing DEMO-REDRIVE-A with a different actor is a REQUEST_ID_CONFLICT.
    try:
        recovery.redrive_queue_item(
            conn, resolved_queue_id,
            _recovery_corrected("09_specimen_incompatible.hl7"),
            request_id="DEMO-REDRIVE-A", actor="someone-else",
        )
        print("  ERROR: expected a REQUEST_ID_CONFLICT but none was raised")
    except recovery.RequestIdConflictError as err:
        print(f"  reuse of DEMO-REDRIVE-A with a different actor -> "
              f"REQUEST_ID_CONFLICT ({err.request_id})")

    succeeded = _count(
        conn,
        "SELECT COUNT(*) FROM interface_recovery_attempt "
        "WHERE queue_id = ? AND outcome = 'SUCCEEDED'",
        (resolved_queue_id,),
    )
    filed = _count(
        conn,
        "SELECT COUNT(*) FROM interface_recovery_attempt a "
        "JOIN interface_message m ON m.message_id = a.resulting_message_id "
        "WHERE a.queue_id = ? AND m.status = 'FILED'",
        (resolved_queue_id,),
    )
    conflicts = _count(
        conn,
        "SELECT COUNT(*) FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'",
    )
    print(f"  invariant: queue {resolved_queue_id} has {succeeded} SUCCEEDED "
          f"attempt and {filed} FILED filing outcome; "
          f"REQUEST_ID_CONFLICT audit events: {conflicts}")


def scenario_recovery(conn: sqlite3.Connection) -> None:
    _banner("Scenario 5: Controlled error-queue recovery (v1.1)")
    # Representative controlled-recovery cases through the public service. The
    # automated suite proves all twelve corrected corpus cases and every
    # approved invariant; this shows a readable subset.
    resolved_queue_id = _corrected_redrive(conn)
    _unchanged_retry(conn)
    _handled_failure_then_success(conn)
    _duplicate_replay_conflict(conn, resolved_queue_id)


def main() -> None:
    conn = create_database(":memory:")
    finalized_order_id = scenario_happy_path(conn)
    scenario_missing_probe(conn)
    scenario_outbound_interfaces(conn, finalized_order_id)
    scenario_inbound_ingestion(conn)
    scenario_recovery(conn)

    _banner("Cross-order analyst views")
    print("Pending review (pending_review.sql):")
    for row in run_query(conn, "pending_review"):
        print(f"  {row['accession_number']}  {row['patient_name']}  "
              f"{row['results_entered']}/{row['required_probes']} probes")

    print("\nValidation error rate (validation_error_rate.sql):")
    row = run_query(conn, "validation_error_rate")[0]
    print(f"  {row['orders_with_errors']}/{row['total_orders']} orders "
          f"with errors = {row['error_rate_pct']}%")

    conn.close()


if __name__ == "__main__":
    main()
