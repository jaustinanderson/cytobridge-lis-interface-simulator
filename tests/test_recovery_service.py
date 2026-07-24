"""Executable tests for the v1.1 controlled recovery service (task P3-003).

Every expectation here is transcribed by hand from the frozen design record
(``validation/v1.1-design-record.md``), requirements
(``validation/v1.1-requirements.md``), and test intent
(``validation/v1.1-test-intent.md``) -- never derived from the implementation or
back-filled from an implementation run. The approved synthetic corpus under
``sample_messages/recovery`` supplies inputs and setups; the corpus manifest is
used only to locate/cross-check fixtures, not to replace these independent
assertions.

The service under test (``src.recovery``) exposes:

    retry_queue_item(conn, queue_id, *, request_id, actor)
    redrive_queue_item(conn, queue_id, corrected_payload, *, request_id, actor)
    get_recovery_history(conn, queue_id)

All data is synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from src import recovery, workflow
from src.db import create_database
from src.interfaces import inbound_hl7
from tests.conftest import enter_all_results

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECOVERY_DIR = _REPO_ROOT / "sample_messages" / "recovery"
_ORIGINAL_DIR = _RECOVERY_DIR / "original"
_CORRECTED_DIR = _RECOVERY_DIR / "corrected"
_MANIFEST = _RECOVERY_DIR / "recovery_corpus.json"


# ---------------------------------------------------------------------------
# Frozen case table (design record sec 6-8; corpus README). Transcribed by hand.
#
# For each recoverable case: the original fixture that produces the failed queue
# item, the corrected fixture that re-drives it, and the synthetic accession the
# corrected re-drive targets (the order that must exist for it to file). For
# RC-08..RC-14 the original also matches this accession, so the order must exist
# before the original is ingested; for RC-01..RC-05 the original fails at or
# before matching and this accession only matters for the corrected re-drive.
# Every corrected fixture files exactly two probe results.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Recoverable:
    case_id: str
    fixture: str            # same base filename in original/ and corrected/
    accession: str          # accession the corrected re-drive targets
    mrn: str
    failure_code: str
    recovery_policy: str


RECOVERABLE = [
    Recoverable("RC-01", "01_empty_message.hl7", "ACC-REC-0101", "SYN-8101",
                "EMPTY_MESSAGE", "REDRIVE_ONLY"),
    Recoverable("RC-02", "02_missing_required_segment.hl7", "ACC-REC-0201",
                "SYN-8201", "MISSING_REQUIRED_SEGMENT", "REDRIVE_ONLY"),
    Recoverable("RC-03", "03_no_obx.hl7", "ACC-REC-0301", "SYN-8301",
                "NO_OBX", "REDRIVE_ONLY"),
    Recoverable("RC-04", "04_missing_accession.hl7", "ACC-REC-0401", "SYN-8401",
                "MISSING_ACCESSION", "REDRIVE_ONLY"),
    Recoverable("RC-05", "05_order_not_found.hl7", "ACC-REC-0501", "SYN-8501",
                "ORDER_NOT_FOUND", "RETRY_OR_REDRIVE"),
    Recoverable("RC-08", "08_specimen_unrecognized.hl7", "ACC-REC-0801",
                "SYN-8801", "SPECIMEN_UNRECOGNIZED", "REDRIVE_ONLY"),
    Recoverable("RC-09", "09_specimen_incompatible.hl7", "ACC-REC-0901",
                "SYN-8901", "SPECIMEN_INCOMPATIBLE", "REDRIVE_ONLY"),
    Recoverable("RC-10", "10_missing_probe_code.hl7", "ACC-REC-1001", "SYN-9001",
                "MISSING_PROBE_CODE", "REDRIVE_ONLY"),
    Recoverable("RC-11", "11_unknown_probe_code.hl7", "ACC-REC-1101", "SYN-9101",
                "UNKNOWN_PROBE_CODE", "REDRIVE_ONLY"),
    Recoverable("RC-12", "12_invalid_cell_count.hl7", "ACC-REC-1201", "SYN-9201",
                "INVALID_CELL_COUNT", "REDRIVE_ONLY"),
    Recoverable("RC-13", "13_abnormal_exceeds_scored.hl7", "ACC-REC-1301",
                "SYN-9301", "ABNORMAL_EXCEEDS_SCORED", "REDRIVE_ONLY"),
    Recoverable("RC-14", "14_invalid_interpretation.hl7", "ACC-REC-1401",
                "SYN-9401", "INVALID_INTERPRETATION", "REDRIVE_ONLY"),
]

# The eleven REDRIVE_ONLY codes for which RETRY_ORIGINAL must be rejected
# (design sec 7: unchanged retry is approved only for ORDER_NOT_FOUND).
REDRIVE_ONLY_CASES = [c for c in RECOVERABLE if c.recovery_policy == "REDRIVE_ONLY"]

_EXPECTED_PROBES_PER_REDRIVE = 2


# ---------------------------------------------------------------------------
# Fixture and setup helpers
# ---------------------------------------------------------------------------

def _original(fixture: str) -> str:
    return (_ORIGINAL_DIR / fixture).read_text(encoding="utf-8")


def _corrected(fixture: str) -> str:
    return (_CORRECTED_DIR / fixture).read_text(encoding="utf-8")


def _sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _make_open_order(conn, mrn: str, accession: str) -> int:
    """An accessioned, non-terminal AML/MDS FISH bone-marrow order."""
    patient_id = workflow.create_patient(
        conn, mrn, "Synthetic", "Recovery", "1970-01-01", "M"
    )
    order_id = workflow.create_order(conn, patient_id, accession, "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def _make_finalized_order(conn, mrn: str, accession: str) -> int:
    order_id = _make_open_order(conn, mrn, accession)
    enter_all_results(conn, order_id)
    assert workflow.finalize_order(conn, order_id, "analyst01").finalized
    return order_id


def _make_cancelled_order(conn, mrn: str, accession: str) -> int:
    order_id = _make_open_order(conn, mrn, accession)
    conn.execute(
        "UPDATE lab_order SET status = 'CANCELLED' WHERE order_id = ?",
        (order_id,),
    )
    conn.commit()
    return order_id


def _make_open_queue_item(conn, case: Recoverable) -> inbound_hl7.IngestResult:
    """Create the target order and ingest the original so an OPEN item exists."""
    _make_open_order(conn, case.mrn, case.accession)
    result = inbound_hl7.ingest_message(conn, _original(case.fixture))
    assert result.filed is False
    assert result.queue_id is not None
    return result


def _queue_row(conn, queue_id: int):
    return conn.execute(
        "SELECT status, resolved_at, terminal_at, reason, raw_payload, "
        "failure_code, failure_category, recovery_policy "
        "FROM interface_error_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()


def _message_row(conn, message_id: int):
    return conn.execute(
        "SELECT message_id, payload, control_id, status, created_at, order_id "
        "FROM interface_message WHERE message_id = ?",
        (message_id,),
    ).fetchone()


def _count(conn, sql: str, params=()) -> int:
    return conn.execute(sql, params).fetchone()[0]


def _succeeded_attempts(conn, queue_id: int) -> int:
    return _count(
        conn,
        "SELECT COUNT(*) FROM interface_recovery_attempt "
        "WHERE queue_id = ? AND outcome = 'SUCCEEDED'",
        (queue_id,),
    )


def _filing_events(conn) -> int:
    return _count(
        conn,
        "SELECT COUNT(*) FROM audit_event WHERE action = 'INBOUND_RESULT_FILED'",
    )


def _fk_ok(conn) -> None:
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


# ===========================================================================
# 1. Corrected re-drive succeeds for all twelve recoverable corrected fixtures.
# ===========================================================================

@pytest.mark.parametrize("case", RECOVERABLE, ids=[c.case_id for c in RECOVERABLE])
def test_corrected_redrive_succeeds_for_every_recoverable_case(case: Recoverable):
    conn = create_database(":memory:")
    try:
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id
        original_before = dict(_message_row(conn, ingest.message_id))
        raw_before = _queue_row(conn, queue_id)["raw_payload"]

        corrected = _corrected(case.fixture)
        attempt = recovery.redrive_queue_item(
            conn, queue_id, corrected, request_id=f"{case.case_id}-A", actor="analyst01"
        )

        # One distinct new message, holding the corrected payload exactly, FILED.
        assert attempt.outcome == "SUCCEEDED"
        assert attempt.action == "REDRIVE_CORRECTED"
        assert attempt.resulting_message_id is not None
        assert attempt.resulting_message_id != ingest.message_id
        assert attempt.payload_sha256 == _sha256(corrected)
        new_msg = _message_row(conn, attempt.resulting_message_id)
        assert new_msg["payload"] == corrected
        assert new_msg["status"] == "FILED"

        # Exactly one SUCCEEDED attempt for the item, linked to the new message.
        assert _succeeded_attempts(conn, queue_id) == 1

        # Queue RESOLVED with resolved_at set and terminal_at null.
        q = _queue_row(conn, queue_id)
        assert q["status"] == "RESOLVED"
        assert q["resolved_at"] is not None
        assert q["terminal_at"] is None

        # Original message and raw queue payload unchanged; original stays ERRORED.
        assert dict(_message_row(conn, ingest.message_id)) == original_before
        assert original_before["status"] == "ERRORED"
        assert _queue_row(conn, queue_id)["raw_payload"] == raw_before

        # No second queue item.
        assert _count(conn, "SELECT COUNT(*) FROM interface_error_queue") == 1

        # Expected FISH results and exactly one filing event were produced.
        assert _count(
            conn,
            "SELECT COUNT(*) FROM fish_result WHERE order_id = ?",
            (new_msg["order_id"],),
        ) == _EXPECTED_PROBES_PER_REDRIVE
        assert _filing_events(conn) == 1

        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 2. Unchanged retry succeeds for ORDER_NOT_FOUND once a matching order exists.
# ===========================================================================

def test_unchanged_retry_succeeds_after_order_becomes_available():
    conn = create_database(":memory:")
    try:
        original = _original("05_order_not_found.hl7")
        ingest = inbound_hl7.ingest_message(conn, original)
        queue_id = ingest.queue_id
        assert _queue_row(conn, queue_id)["failure_code"] == "ORDER_NOT_FOUND"
        original_before = dict(_message_row(conn, ingest.message_id))
        raw_before = _queue_row(conn, queue_id)["raw_payload"]

        # The matching order (ACC-REC-0500-NOMATCH) becomes available.
        order_id = _make_open_order(conn, "SYN-8500", "ACC-REC-0500-NOMATCH")

        attempt = recovery.retry_queue_item(
            conn, queue_id, request_id="RETRY-OK", actor="analyst01"
        )
        assert attempt.outcome == "SUCCEEDED"
        assert attempt.action == "RETRY_ORIGINAL"

        # Exact byte-for-byte reuse of the immutable original payload, new id.
        new_msg = _message_row(conn, attempt.resulting_message_id)
        assert new_msg["payload"] == original
        assert attempt.payload_sha256 == _sha256(original)
        assert attempt.resulting_message_id != ingest.message_id
        assert new_msg["status"] == "FILED"
        assert new_msg["order_id"] == order_id

        # One SUCCEEDED attempt; queue resolved; original immutable.
        assert _succeeded_attempts(conn, queue_id) == 1
        q = _queue_row(conn, queue_id)
        assert q["status"] == "RESOLVED"
        assert q["resolved_at"] is not None and q["terminal_at"] is None
        assert dict(_message_row(conn, ingest.message_id)) == original_before
        assert _queue_row(conn, queue_id)["raw_payload"] == raw_before
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 3. RETRY_ORIGINAL against each REDRIVE_ONLY class is rejected (no message).
# ===========================================================================

@pytest.mark.parametrize(
    "case", REDRIVE_ONLY_CASES, ids=[c.case_id for c in REDRIVE_ONLY_CASES]
)
def test_retry_original_rejected_for_redrive_only_classes(case: Recoverable):
    conn = create_database(":memory:")
    try:
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id
        messages_before = _count(conn, "SELECT COUNT(*) FROM interface_message")

        attempt = recovery.retry_queue_item(
            conn, queue_id, request_id=f"{case.case_id}-RETRY", actor="analyst01"
        )
        assert attempt.outcome == "REJECTED"
        assert attempt.action == "RETRY_ORIGINAL"
        assert attempt.resulting_message_id is None

        # No processing message was created and the queue item stays OPEN.
        assert _count(conn, "SELECT COUNT(*) FROM interface_message") == messages_before
        q = _queue_row(conn, queue_id)
        assert q["status"] == "OPEN"
        assert q["resolved_at"] is None and q["terminal_at"] is None
        assert _count(conn, "SELECT COUNT(*) FROM fish_result") == 0
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 4. Requests against ORDER_FINALIZED / ORDER_CANCELLED terminal items rejected.
# ===========================================================================

@pytest.mark.parametrize(
    "fixture, mrn, accession, builder, order_status",
    [
        ("06_order_finalized.hl7", "SYN-8601", "ACC-REC-0601",
         _make_finalized_order, "FINALIZED"),
        ("07_order_cancelled.hl7", "SYN-8701", "ACC-REC-0701",
         _make_cancelled_order, "CANCELLED"),
    ],
    ids=["ORDER_FINALIZED", "ORDER_CANCELLED"],
)
def test_recovery_rejected_for_terminal_queue_item(
    fixture, mrn, accession, builder, order_status
):
    conn = create_database(":memory:")
    try:
        order_id = builder(conn, mrn, accession)
        ingest = inbound_hl7.ingest_message(conn, _original(fixture))
        queue_id = ingest.queue_id
        assert _queue_row(conn, queue_id)["status"] == "TERMINAL"
        messages_before = _count(conn, "SELECT COUNT(*) FROM interface_message")
        fish_before = _count(conn, "SELECT COUNT(*) FROM fish_result")

        # A corrected re-drive against the TERMINAL item is REJECTED.
        attempt = recovery.redrive_queue_item(
            conn, queue_id, _corrected("09_specimen_incompatible.hl7"),
            request_id="TERM-A", actor="analyst01",
        )
        assert attempt.outcome == "REJECTED"
        assert attempt.resulting_message_id is None

        # No message, no result, no queue reopening, no order-state change.
        assert _count(conn, "SELECT COUNT(*) FROM interface_message") == messages_before
        assert _count(conn, "SELECT COUNT(*) FROM fish_result") == fish_before
        q = _queue_row(conn, queue_id)
        assert q["status"] == "TERMINAL"
        assert q["terminal_at"] is not None and q["resolved_at"] is None
        assert conn.execute(
            "SELECT status FROM lab_order WHERE order_id = ?", (order_id,)
        ).fetchone()["status"] == order_status
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 5. Dynamic OPEN -> TERMINAL when processing finds the order FINALIZED/CANCELLED.
# ===========================================================================

@pytest.mark.parametrize(
    "make_terminal, order_status",
    [(_make_finalized_order, "FINALIZED"), (_make_cancelled_order, "CANCELLED")],
    ids=["now-FINALIZED", "now-CANCELLED"],
)
def test_open_to_terminal_when_target_order_now_terminal(
    make_terminal, order_status
):
    conn = create_database(":memory:")
    try:
        # An OPEN ORDER_NOT_FOUND item; retry is a permitted action.
        original = _original("05_order_not_found.hl7")
        ingest = inbound_hl7.ingest_message(conn, original)
        queue_id = ingest.queue_id
        assert _queue_row(conn, queue_id)["status"] == "OPEN"

        # The matching order now exists but is already terminal.
        order_id = make_terminal(conn, "SYN-8500", "ACC-REC-0500-NOMATCH")
        messages_before = _count(conn, "SELECT COUNT(*) FROM interface_message")
        fish_before = _count(conn, "SELECT COUNT(*) FROM fish_result")
        filing_before = _filing_events(conn)

        attempt = recovery.retry_queue_item(
            conn, queue_id, request_id="DYN-TERM", actor="analyst01"
        )
        # Treated as REJECTED with no processing message; queue OPEN -> TERMINAL.
        assert attempt.outcome == "REJECTED"
        assert attempt.resulting_message_id is None
        assert _count(conn, "SELECT COUNT(*) FROM interface_message") == messages_before

        q = _queue_row(conn, queue_id)
        assert q["status"] == "TERMINAL"
        assert q["terminal_at"] is not None and q["resolved_at"] is None
        # Original classification/policy/payload not rewritten.
        assert q["failure_code"] == "ORDER_NOT_FOUND"
        assert q["recovery_policy"] == "RETRY_OR_REDRIVE"
        assert q["raw_payload"] == original
        # Order neither reopened, unfinalized, nor uncancelled.
        assert conn.execute(
            "SELECT status FROM lab_order WHERE order_id = ?", (order_id,)
        ).fetchone()["status"] == order_status
        # Recovery itself files nothing and emits no filing event.
        assert _count(conn, "SELECT COUNT(*) FROM fish_result") == fish_before
        assert _filing_events(conn) == filing_before
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 6. Still-invalid payload -> FAILED; a later valid request may then succeed.
# ===========================================================================

def test_failed_then_later_success_with_new_request_id():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-11")
        ingest = _make_open_queue_item(conn, case)  # UNKNOWN_PROBE_CODE, OPEN
        queue_id = ingest.queue_id

        # A corrected re-drive whose payload is still invalid (unknown probe).
        still_invalid = _original("11_unknown_probe_code.hl7")
        failed = recovery.redrive_queue_item(
            conn, queue_id, still_invalid, request_id="F1", actor="analyst01"
        )
        assert failed.outcome == "FAILED"
        assert failed.resulting_message_id is not None
        assert _message_row(conn, failed.resulting_message_id)["status"] == "ERRORED"

        # Exactly one new (ERRORED) message from the attempt; queue still OPEN,
        # no second queue item, no filing side effect.
        q = _queue_row(conn, queue_id)
        assert q["status"] == "OPEN"
        assert q["resolved_at"] is None and q["terminal_at"] is None
        assert _count(conn, "SELECT COUNT(*) FROM interface_error_queue") == 1
        assert _filing_events(conn) == 0
        assert _count(conn, "SELECT COUNT(*) FROM fish_result") == 0

        # A later valid request with a new request_id may succeed.
        good = recovery.redrive_queue_item(
            conn, queue_id, _corrected(case.fixture),
            request_id="F2", actor="analyst01",
        )
        assert good.outcome == "SUCCEEDED"
        assert _message_row(conn, good.resulting_message_id)["status"] == "FILED"
        assert _succeeded_attempts(conn, queue_id) == 1
        assert _queue_row(conn, queue_id)["status"] == "RESOLVED"
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 7. Handled mid-operation failure after >=1 result write -> full rollback.
# ===========================================================================

def test_handled_mid_operation_failure_rolls_back_all_side_effects(monkeypatch):
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id

        real_enter = workflow.enter_fish_result
        state = {"calls": 0}

        def flaky_enter(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:
                # A handled inbound-style failure after the first result wrote.
                raise inbound_hl7.InboundError(
                    "INVALID_INTERPRETATION", "injected handled mid-op failure"
                )
            return real_enter(*args, **kwargs)

        monkeypatch.setattr(workflow, "enter_fish_result", flaky_enter)
        attempt = recovery.redrive_queue_item(
            conn, queue_id, _corrected(case.fixture),
            request_id="MID", actor="analyst01",
        )
        monkeypatch.undo()

        assert attempt.outcome == "FAILED"

        # Rolled back: FISH results, RESULT_ENTERED, INBOUND_RESULT_FILED,
        # order-state change, and queue resolution.
        assert _count(conn, "SELECT COUNT(*) FROM fish_result") == 0
        assert _count(
            conn, "SELECT COUNT(*) FROM audit_event WHERE action = 'RESULT_ENTERED'"
        ) == 0
        assert _filing_events(conn) == 0
        assert conn.execute(
            "SELECT status FROM lab_order WHERE accession_number = ?",
            (case.accession,),
        ).fetchone()["status"] == "IN_PROCESS"
        q = _queue_row(conn, queue_id)
        assert q["status"] == "OPEN"
        assert q["resolved_at"] is None and q["terminal_at"] is None

        # Preserved: only the ERRORED attempted message and the FAILED attempt.
        assert attempt.resulting_message_id is not None
        assert _message_row(conn, attempt.resulting_message_id)["status"] == "ERRORED"
        assert _count(
            conn,
            "SELECT COUNT(*) FROM interface_recovery_attempt "
            "WHERE queue_id = ? AND outcome = 'FAILED'",
            (queue_id,),
        ) == 1
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 8. Unexpected runtime failure -> whole request rolls back and re-raises.
# ===========================================================================

def test_unexpected_failure_rolls_back_and_reraises(monkeypatch):
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id
        messages_before = _count(conn, "SELECT COUNT(*) FROM interface_message")

        def boom(*args, **kwargs):
            raise RuntimeError("unexpected non-inbound failure")

        monkeypatch.setattr(workflow, "enter_fish_result", boom)
        with pytest.raises(RuntimeError):
            recovery.redrive_queue_item(
                conn, queue_id, _corrected(case.fixture),
                request_id="BOOM", actor="analyst01",
            )
        monkeypatch.undo()

        # Nothing persisted: no new message, no attempt, queue OPEN, no dangling txn.
        assert _count(conn, "SELECT COUNT(*) FROM interface_message") == messages_before
        assert _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt") == 0
        assert _queue_row(conn, queue_id)["status"] == "OPEN"
        assert _count(conn, "SELECT COUNT(*) FROM fish_result") == 0
        assert conn.in_transaction is False
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 9. Invariant I-01 - Original-message immutability (design record sec 11).
# ===========================================================================

def test_invariant_I01_original_message_immutability():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id

        # 1. Snapshot the original message and queue payload.
        snap = dict(_message_row(conn, ingest.message_id))
        original_message_id = snap["message_id"]
        original_payload = snap["payload"]
        original_control_id = snap["control_id"]
        original_status = snap["status"]
        original_created_at = snap["created_at"]
        original_raw_payload = _queue_row(conn, queue_id)["raw_payload"]

        # 2. Perform a corrected re-drive.
        corrected = _corrected(case.fixture)
        attempt = recovery.redrive_queue_item(
            conn, queue_id, corrected, request_id="I01", actor="analyst01"
        )

        # 3. Every snapshotted original value is unchanged.
        after = dict(_message_row(conn, original_message_id))
        assert after["message_id"] == original_message_id
        assert after["payload"] == original_payload
        assert after["control_id"] == original_control_id
        assert after["status"] == original_status == "ERRORED"
        assert after["created_at"] == original_created_at
        assert _queue_row(conn, queue_id)["raw_payload"] == original_raw_payload

        # 4. The corrected payload has a distinct message ID.
        assert attempt.resulting_message_id != original_message_id
        new_msg = _message_row(conn, attempt.resulting_message_id)
        assert new_msg["payload"] == corrected

        # 5. The recovery attempt links the queue case to the new message.
        assert attempt.queue_id == queue_id
        assert attempt.resulting_message_id == new_msg["message_id"]

        # 6. Only the new message can become FILED.
        assert new_msg["status"] == "FILED"
        assert after["status"] == "ERRORED"
        filed_ids = {
            r["message_id"]
            for r in conn.execute(
                "SELECT message_id FROM interface_message WHERE status = 'FILED'"
            ).fetchall()
        }
        assert filed_ids == {new_msg["message_id"]}
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 10. Invariant I-02 - Duplicate and replay protection (design record sec 11).
# ===========================================================================

def test_invariant_I02_duplicate_and_replay_protection():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id
        corrected = _corrected(case.fixture)

        # 2. Recover successfully using request_id A.
        first = recovery.redrive_queue_item(
            conn, queue_id, corrected, request_id="A", actor="analyst01"
        )
        assert first.outcome == "SUCCEEDED"

        # 3. Snapshot messages, FISH results, attempts, and filing audit events.
        def snapshot():
            return (
                [tuple(r) for r in conn.execute(
                    "SELECT message_id, payload, status, order_id "
                    "FROM interface_message ORDER BY message_id"
                ).fetchall()],
                [tuple(r) for r in conn.execute(
                    "SELECT result_id, order_id, probe_id, cells_scored, "
                    "cells_abnormal, interpretation FROM fish_result "
                    "ORDER BY result_id"
                ).fetchall()],
                [tuple(r) for r in conn.execute(
                    "SELECT attempt_id, queue_id, resulting_message_id, request_id, "
                    "action, payload_sha256, outcome, actor FROM "
                    "interface_recovery_attempt ORDER BY attempt_id"
                ).fetchall()],
                _filing_events(conn),
            )

        before = snapshot()

        # 4-5. Replay request_id A with identical parameters -> no changes.
        replay = recovery.redrive_queue_item(
            conn, queue_id, corrected, request_id="A", actor="analyst01"
        )
        assert replay.attempt_id == first.attempt_id
        assert replay.outcome == "SUCCEEDED"
        assert snapshot() == before

        # 6-7. A different request_id B against the RESOLVED item is REJECTED.
        rejected = recovery.redrive_queue_item(
            conn, queue_id, corrected, request_id="B", actor="analyst01"
        )
        assert rejected.outcome == "REJECTED"
        assert rejected.resulting_message_id is None
        # No processing message, no FISH change, no second filing event.
        after_msgs, after_fish, _after_attempts, after_filing = snapshot()
        assert after_msgs == before[0]
        assert after_fish == before[1]
        assert after_filing == before[3] == 1

        # 8. Exactly one SUCCEEDED recovery exists for the queue item.
        assert _succeeded_attempts(conn, queue_id) == 1
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 11. REQUEST_ID_CONFLICT tested independently per mismatch dimension.
# ===========================================================================

def _record_baseline_attempt(conn, queue_id, corrected):
    """A recorded REDRIVE_CORRECTED attempt under request_id 'BASE'."""
    return recovery.redrive_queue_item(
        conn, queue_id, corrected, request_id="BASE", actor="analyst01"
    )


def _assert_no_conflict_side_effects(conn, queue_id, before_attempt, before_counts):
    # No message, attempt, or FISH result created.
    assert (
        _count(conn, "SELECT COUNT(*) FROM interface_message"),
        _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
        _count(conn, "SELECT COUNT(*) FROM fish_result"),
    ) == before_counts
    # Original attempt byte-for-byte unchanged.
    row = conn.execute(
        "SELECT attempt_id, queue_id, resulting_message_id, request_id, action, "
        "payload_sha256, outcome, actor, outcome_detail, attempted_at "
        "FROM interface_recovery_attempt WHERE request_id = 'BASE'"
    ).fetchone()
    assert tuple(row) == before_attempt
    # Exactly one REQUEST_ID_CONFLICT audit event was added.
    assert _count(
        conn,
        "SELECT COUNT(*) FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'",
    ) == 1


def test_conflict_on_different_queue_id():
    conn = create_database(":memory:")
    try:
        c1 = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        c2 = next(c for c in RECOVERABLE if c.case_id == "RC-08")
        q1 = _make_open_queue_item(conn, c1).queue_id
        q2 = _make_open_queue_item(conn, c2).queue_id
        base = _record_baseline_attempt(conn, q1, _corrected(c1.fixture))
        base_row = tuple(conn.execute(
            "SELECT attempt_id, queue_id, resulting_message_id, request_id, action, "
            "payload_sha256, outcome, actor, outcome_detail, attempted_at "
            "FROM interface_recovery_attempt WHERE request_id = 'BASE'"
        ).fetchone())
        counts = (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM fish_result"),
        )
        # Same request_id, different queue_id.
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.redrive_queue_item(
                conn, q2, _corrected(c1.fixture), request_id="BASE", actor="analyst01"
            )
        assert base.outcome == "SUCCEEDED"
        _assert_no_conflict_side_effects(conn, q1, base_row, counts)
        _fk_ok(conn)
    finally:
        conn.close()


def test_conflict_on_different_action():
    conn = create_database(":memory:")
    try:
        # RC-05 is RETRY_OR_REDRIVE, so both actions are otherwise permitted; the
        # conflict is purely the action mismatch under a reused request_id.
        original = _original("05_order_not_found.hl7")
        ingest = inbound_hl7.ingest_message(conn, original)
        queue_id = ingest.queue_id
        _make_open_order(conn, "SYN-8500", "ACC-REC-0500-NOMATCH")
        base = recovery.retry_queue_item(
            conn, queue_id, request_id="BASE", actor="analyst01"
        )
        assert base.outcome == "SUCCEEDED"
        base_row = tuple(conn.execute(
            "SELECT attempt_id, queue_id, resulting_message_id, request_id, action, "
            "payload_sha256, outcome, actor, outcome_detail, attempted_at "
            "FROM interface_recovery_attempt WHERE request_id = 'BASE'"
        ).fetchone())
        counts = (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM fish_result"),
        )
        # Same request_id, different action (REDRIVE_CORRECTED vs the recorded
        # RETRY_ORIGINAL).
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.redrive_queue_item(
                conn, queue_id, _corrected("05_order_not_found.hl7"),
                request_id="BASE", actor="analyst01",
            )
        _assert_no_conflict_side_effects(conn, queue_id, base_row, counts)
        _fk_ok(conn)
    finally:
        conn.close()


def test_conflict_on_different_payload_sha256():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        queue_id = _make_open_queue_item(conn, case).queue_id
        base = _record_baseline_attempt(conn, queue_id, _corrected(case.fixture))
        assert base.outcome == "SUCCEEDED"
        base_row = tuple(conn.execute(
            "SELECT attempt_id, queue_id, resulting_message_id, request_id, action, "
            "payload_sha256, outcome, actor, outcome_detail, attempted_at "
            "FROM interface_recovery_attempt WHERE request_id = 'BASE'"
        ).fetchone())
        counts = (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM fish_result"),
        )
        # Same request_id and action, different payload (distinct fingerprint).
        different_payload = _corrected(case.fixture) + "\nOBX|3|ST|EXTRA\n"
        assert _sha256(different_payload) != base.payload_sha256
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.redrive_queue_item(
                conn, queue_id, different_payload, request_id="BASE", actor="analyst01"
            )
        _assert_no_conflict_side_effects(conn, queue_id, base_row, counts)
        _fk_ok(conn)
    finally:
        conn.close()


def test_conflict_on_different_actor():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        queue_id = _make_open_queue_item(conn, case).queue_id
        base = _record_baseline_attempt(conn, queue_id, _corrected(case.fixture))
        assert base.outcome == "SUCCEEDED"
        base_row = tuple(conn.execute(
            "SELECT attempt_id, queue_id, resulting_message_id, request_id, action, "
            "payload_sha256, outcome, actor, outcome_detail, attempted_at "
            "FROM interface_recovery_attempt WHERE request_id = 'BASE'"
        ).fetchone())
        counts = (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM fish_result"),
        )
        # Same request_id, action, payload, different actor.
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.redrive_queue_item(
                conn, queue_id, _corrected(case.fixture),
                request_id="BASE", actor="someone-else",
            )
        _assert_no_conflict_side_effects(conn, queue_id, base_row, counts)
        _fk_ok(conn)
    finally:
        conn.close()


def test_conflict_checked_before_queue_or_action_eligibility():
    """A reused request_id on a now-RESOLVED item conflicts, not an ordinary
    closed-queue REJECTED: the conflict check precedes eligibility."""
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        queue_id = _make_open_queue_item(conn, case).queue_id
        base = _record_baseline_attempt(conn, queue_id, _corrected(case.fixture))
        assert base.outcome == "SUCCEEDED"
        assert _queue_row(conn, queue_id)["status"] == "RESOLVED"
        attempts_before = _count(
            conn, "SELECT COUNT(*) FROM interface_recovery_attempt"
        )
        # Reuse BASE with a different actor. Were eligibility checked first, the
        # RESOLVED item would yield a REJECTED attempt; instead it conflicts and
        # creates no attempt row.
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.redrive_queue_item(
                conn, queue_id, _corrected(case.fixture),
                request_id="BASE", actor="different",
            )
        assert _count(
            conn, "SELECT COUNT(*) FROM interface_recovery_attempt"
        ) == attempts_before
        assert _count(
            conn,
            "SELECT COUNT(*) FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'",
        ) == 1
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 12. Matching replay is proven for prior FAILED and REJECTED attempts.
# ===========================================================================

def test_matching_replay_of_prior_failed_attempt():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-11")
        queue_id = _make_open_queue_item(conn, case).queue_id
        still_invalid = _original("11_unknown_probe_code.hl7")
        failed = recovery.redrive_queue_item(
            conn, queue_id, still_invalid, request_id="FAIL", actor="analyst01"
        )
        assert failed.outcome == "FAILED"

        before = (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM audit_event"),
        )
        replay = recovery.redrive_queue_item(
            conn, queue_id, still_invalid, request_id="FAIL", actor="analyst01"
        )
        assert replay.attempt_id == failed.attempt_id
        assert replay.outcome == "FAILED"
        assert replay.resulting_message_id == failed.resulting_message_id
        assert (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM audit_event"),
        ) == before
        _fk_ok(conn)
    finally:
        conn.close()


def test_matching_replay_of_prior_rejected_attempt():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-08")
        queue_id = _make_open_queue_item(conn, case).queue_id
        # RETRY_ORIGINAL is prohibited for this REDRIVE_ONLY item -> REJECTED.
        rejected = recovery.retry_queue_item(
            conn, queue_id, request_id="REJ", actor="analyst01"
        )
        assert rejected.outcome == "REJECTED"

        before = (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM audit_event"),
        )
        replay = recovery.retry_queue_item(
            conn, queue_id, request_id="REJ", actor="analyst01"
        )
        assert replay.attempt_id == rejected.attempt_id
        assert replay.outcome == "REJECTED"
        assert replay.resulting_message_id is None
        assert (
            _count(conn, "SELECT COUNT(*) FROM interface_message"),
            _count(conn, "SELECT COUNT(*) FROM interface_recovery_attempt"),
            _count(conn, "SELECT COUNT(*) FROM audit_event"),
        ) == before
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 13. get_recovery_history returns attempts in order and excludes conflicts.
# ===========================================================================

def test_get_recovery_history_orders_attempts_and_excludes_conflicts():
    conn = create_database(":memory:")
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-05")
        # Build the OPEN ORDER_NOT_FOUND item without any matching order.
        ingest = inbound_hl7.ingest_message(conn, _original(case.fixture))
        queue_id = ingest.queue_id

        # Attempt 1: FAILED (order still not found).
        a1 = recovery.retry_queue_item(
            conn, queue_id, request_id="H1", actor="analyst01"
        )
        assert a1.outcome == "FAILED"
        # Attempt 2: REJECTED (RETRY prohibited? no -- redrive against wrong order).
        # Use a corrected payload that still won't match any order -> FAILED again;
        # instead force a REJECTED by re-driving after resolution below. First make
        # the order available and succeed, then a post-resolution request REJECTS.
        order_id = _make_open_order(conn, case.mrn, "ACC-REC-0500-NOMATCH")
        a2 = recovery.retry_queue_item(
            conn, queue_id, request_id="H2", actor="analyst01"
        )
        assert a2.outcome == "SUCCEEDED"
        # Attempt 3: REJECTED (queue now RESOLVED).
        a3 = recovery.retry_queue_item(
            conn, queue_id, request_id="H3", actor="analyst01"
        )
        assert a3.outcome == "REJECTED"

        # A conflicting reuse creates only an audit event, never a history row.
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.retry_queue_item(
                conn, queue_id, request_id="H1", actor="different-actor"
            )

        history = recovery.get_recovery_history(conn, queue_id)
        assert [h.attempt_id for h in history] == sorted(h.attempt_id for h in history)
        assert [h.attempt_id for h in history] == [a1.attempt_id, a2.attempt_id, a3.attempt_id]
        assert [h.outcome for h in history] == ["FAILED", "SUCCEEDED", "REJECTED"]
        # No conflict pseudo-attempt leaked into history.
        assert all(h.action in ("RETRY_ORIGINAL", "REDRIVE_CORRECTED") for h in history)
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 14. File-backed durability after success and after a handled failure; no
#     dangling transaction after public service calls.
# ===========================================================================

def test_file_backed_durability_after_success(tmp_path, monkeypatch):
    db_path = str(tmp_path / "recovery_success.db")
    conn = create_database(db_path)
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id
        attempt = recovery.redrive_queue_item(
            conn, queue_id, _corrected(case.fixture),
            request_id="DUR-OK", actor="analyst01",
        )
        assert attempt.outcome == "SUCCEEDED"
        assert conn.in_transaction is False  # no dangling transaction
    finally:
        conn.close()

    # Reopen the on-disk database and prove the committed state is durable.
    reopened = create_database(db_path)
    try:
        q = _queue_row(reopened, queue_id)
        assert q["status"] == "RESOLVED"
        assert q["resolved_at"] is not None and q["terminal_at"] is None
        assert _succeeded_attempts(reopened, queue_id) == 1
        assert reopened.execute(
            "SELECT status FROM interface_message WHERE message_id = ?",
            (attempt.resulting_message_id,),
        ).fetchone()["status"] == "FILED"
        _fk_ok(reopened)
    finally:
        reopened.close()


def test_file_backed_durability_after_handled_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "recovery_failure.db")
    conn = create_database(db_path)
    try:
        case = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        ingest = _make_open_queue_item(conn, case)
        queue_id = ingest.queue_id
        real_enter = workflow.enter_fish_result
        state = {"calls": 0}

        def flaky_enter(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 2:
                raise inbound_hl7.InboundError(
                    "INVALID_INTERPRETATION", "injected durable handled failure"
                )
            return real_enter(*args, **kwargs)

        monkeypatch.setattr(workflow, "enter_fish_result", flaky_enter)
        attempt = recovery.redrive_queue_item(
            conn, queue_id, _corrected(case.fixture),
            request_id="DUR-FAIL", actor="analyst01",
        )
        monkeypatch.undo()
        assert attempt.outcome == "FAILED"
        assert conn.in_transaction is False
    finally:
        conn.close()

    reopened = create_database(db_path)
    try:
        # Durable: the ERRORED attempted message and FAILED attempt persist; the
        # queue is still OPEN and nothing filed.
        assert _queue_row(reopened, queue_id)["status"] == "OPEN"
        assert reopened.execute(
            "SELECT status FROM interface_message WHERE message_id = ?",
            (attempt.resulting_message_id,),
        ).fetchone()["status"] == "ERRORED"
        assert _count(
            reopened,
            "SELECT COUNT(*) FROM interface_recovery_attempt "
            "WHERE queue_id = ? AND outcome = 'FAILED'",
            (queue_id,),
        ) == 1
        assert _count(reopened, "SELECT COUNT(*) FROM fish_result") == 0
        assert _filing_events(reopened) == 0
        _fk_ok(reopened)
    finally:
        reopened.close()


# ===========================================================================
# 15. PRAGMA foreign_key_check stays empty across every outcome in one database.
# ===========================================================================

def test_foreign_key_check_empty_across_all_scenarios(monkeypatch):
    conn = create_database(":memory:")
    try:
        # Success + replay + post-resolution rejection + conflict on one item.
        c9 = next(c for c in RECOVERABLE if c.case_id == "RC-09")
        q9 = _make_open_queue_item(conn, c9).queue_id
        recovery.redrive_queue_item(
            conn, q9, _corrected(c9.fixture), request_id="S1", actor="analyst01"
        )
        recovery.redrive_queue_item(
            conn, q9, _corrected(c9.fixture), request_id="S1", actor="analyst01"
        )  # replay
        recovery.redrive_queue_item(
            conn, q9, _corrected(c9.fixture), request_id="S2", actor="analyst01"
        )  # rejected (resolved)
        with pytest.raises(recovery.RequestIdConflictError):
            recovery.redrive_queue_item(
                conn, q9, _corrected(c9.fixture), request_id="S1", actor="other"
            )
        _fk_ok(conn)

        # Handled failure on another item.
        c11 = next(c for c in RECOVERABLE if c.case_id == "RC-11")
        q11 = _make_open_queue_item(conn, c11).queue_id
        recovery.redrive_queue_item(
            conn, q11, _original("11_unknown_probe_code.hl7"),
            request_id="FAIL", actor="analyst01",
        )
        _fk_ok(conn)

        # Terminalization (dynamic OPEN -> TERMINAL) on a third item.
        ingest = inbound_hl7.ingest_message(conn, _original("05_order_not_found.hl7"))
        _make_finalized_order(conn, "SYN-8500", "ACC-REC-0500-NOMATCH")
        recovery.retry_queue_item(
            conn, ingest.queue_id, request_id="TERM", actor="analyst01"
        )
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# 16. Existing inbound ingestion continues to behave unchanged through the seam.
# ===========================================================================

def test_normal_inbound_ingestion_still_files_unchanged():
    conn = create_database(":memory:")
    try:
        _make_open_order(conn, "SYN-7001", "ACC-INBOUND-0001")
        sample = (
            _REPO_ROOT / "sample_messages" / "inbound" / "aml_mds_valid_oru.hl7"
        ).read_text(encoding="utf-8")
        result = inbound_hl7.ingest_message(conn, sample)
        assert result.filed is True
        assert result.queue_id is None
        assert _count(conn, "SELECT COUNT(*) FROM interface_error_queue") == 0
        assert _count(
            conn,
            "SELECT COUNT(*) FROM fish_result WHERE order_id = ?",
            (result.order_id,),
        ) == 9
        assert conn.execute(
            "SELECT status FROM interface_message WHERE message_id = ?",
            (result.message_id,),
        ).fetchone()["status"] == "FILED"
        _fk_ok(conn)
    finally:
        conn.close()


# ===========================================================================
# Manifest cross-check (locate/verify fixtures only; not a behavior assertion).
# ===========================================================================

def test_manifest_locates_the_recoverable_fixtures():
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8"))
    by_code = {c["failure_code"]: c for c in manifest["cases"]}
    for case in RECOVERABLE:
        entry = by_code[case.failure_code]
        assert entry["recovery_policy"] == case.recovery_policy
        assert entry["corrected_payload"] == f"corrected/{case.fixture}"
        assert (_ORIGINAL_DIR / case.fixture).exists()
        assert (_CORRECTED_DIR / case.fixture).exists()
