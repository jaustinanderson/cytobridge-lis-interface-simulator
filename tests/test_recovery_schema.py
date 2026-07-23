"""Schema-level tests for the v1.1 recovery data model (task P3-001).

These tests exercise only the database shape and database-enforced constraints
added by P3-001: the new interface_error_queue classification columns and
expanded state model, and the new interface_recovery_attempt table. They do not
exercise recovery services, failure classification logic, or any application
behavior - none of which exist yet.

Every constraint is proven directly against the schema loaded by
``src.db.create_database``. All data is synthetic. No PHI.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.db import create_database, init_db
from src.interfaces import inbound_hl7


# ---------------------------------------------------------------------------
# Low-level helpers: build just enough valid rows to satisfy foreign keys.
# ---------------------------------------------------------------------------

def _new_message(conn: sqlite3.Connection, status: str = "RECEIVED") -> int:
    """Insert a minimal INBOUND interface_message and return its message_id."""
    cur = conn.execute(
        "INSERT INTO interface_message "
        "(direction, message_type, format, payload, status) "
        "VALUES ('INBOUND', 'ORU', 'HL7', 'MSH|synthetic', ?)",
        (status,),
    )
    conn.commit()
    return cur.lastrowid


def _new_queue(
    conn: sqlite3.Connection,
    *,
    message_id: int | None = None,
    status: str = "OPEN",
    resolved_at: str | None = None,
    terminal_at: str | None = None,
    failure_code: str | None = None,
    failure_category: str | None = None,
    recovery_policy: str | None = None,
) -> int:
    """Insert an interface_error_queue row and return its queue_id."""
    cur = conn.execute(
        "INSERT INTO interface_error_queue "
        "(message_id, reason, status, resolved_at, terminal_at, "
        " failure_code, failure_category, recovery_policy) "
        "VALUES (?, 'synthetic reason', ?, ?, ?, ?, ?, ?)",
        (
            message_id,
            status,
            resolved_at,
            terminal_at,
            failure_code,
            failure_category,
            recovery_policy,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _insert_attempt(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    request_id: str,
    action: str = "REDRIVE_CORRECTED",
    outcome: str = "SUCCEEDED",
    resulting_message_id: int | None = None,
    payload_sha256: str = "a" * 64,
    actor: str = "analyst01",
    outcome_detail: str = "synthetic outcome detail",
) -> int:
    """Insert an interface_recovery_attempt row and return its attempt_id."""
    cur = conn.execute(
        "INSERT INTO interface_recovery_attempt "
        "(queue_id, resulting_message_id, request_id, action, payload_sha256, "
        " outcome, actor, outcome_detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            queue_id,
            resulting_message_id,
            request_id,
            action,
            payload_sha256,
            outcome,
            actor,
            outcome_detail,
        ),
    )
    conn.commit()
    return cur.lastrowid


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r["name"] for r in rows}


# ---------------------------------------------------------------------------
# Column shape
# ---------------------------------------------------------------------------

def test_queue_has_expected_columns(conn):
    cols = _columns(conn, "interface_error_queue")
    # Preserved existing columns.
    for existing in (
        "queue_id", "message_id", "direction", "reason", "raw_payload",
        "status", "created_at", "resolved_at",
    ):
        assert existing in cols, f"missing preserved column {existing}"
    # New P3-001 columns.
    for added in (
        "failure_code", "failure_category", "recovery_policy", "terminal_at",
    ):
        assert added in cols, f"missing new column {added}"


def test_recovery_attempt_has_exactly_approved_fields(conn):
    cols = _columns(conn, "interface_recovery_attempt")
    assert cols == {
        "attempt_id",
        "queue_id",
        "resulting_message_id",
        "request_id",
        "action",
        "payload_sha256",
        "outcome",
        "actor",
        "outcome_detail",
        "attempted_at",
    }


# ---------------------------------------------------------------------------
# Queue state / timestamp combinations
# ---------------------------------------------------------------------------

def test_valid_queue_state_timestamp_combinations(conn):
    # OPEN: both timestamps null.
    _new_queue(conn, status="OPEN")
    # RESOLVED: resolved_at set, terminal_at null.
    _new_queue(conn, status="RESOLVED", resolved_at="2026-07-23 00:00:00")
    # TERMINAL: terminal_at set, resolved_at null.
    _new_queue(conn, status="TERMINAL", terminal_at="2026-07-23 00:00:00")
    counts = conn.execute(
        "SELECT status, COUNT(*) AS n FROM interface_error_queue GROUP BY status"
    ).fetchall()
    by_status = {r["status"]: r["n"] for r in counts}
    assert by_status == {"OPEN": 1, "RESOLVED": 1, "TERMINAL": 1}


@pytest.mark.parametrize(
    "status, resolved_at, terminal_at",
    [
        # OPEN must have both null.
        ("OPEN", "2026-07-23 00:00:00", None),
        ("OPEN", None, "2026-07-23 00:00:00"),
        # RESOLVED must have resolved_at set and terminal_at null.
        ("RESOLVED", None, None),
        ("RESOLVED", "2026-07-23 00:00:00", "2026-07-23 00:00:00"),
        # TERMINAL must have terminal_at set and resolved_at null.
        ("TERMINAL", None, None),
        ("TERMINAL", "2026-07-23 00:00:00", "2026-07-23 00:00:00"),
    ],
)
def test_invalid_queue_state_timestamp_combinations_fail(
    conn, status, resolved_at, terminal_at
):
    with pytest.raises(sqlite3.IntegrityError):
        _new_queue(
            conn, status=status, resolved_at=resolved_at, terminal_at=terminal_at
        )


def test_invalid_queue_status_value_fails(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _new_queue(conn, status="QUEUED")


# ---------------------------------------------------------------------------
# Classification vocabulary
# ---------------------------------------------------------------------------

def test_null_classification_allowed(conn):
    # Nullable at this schema stage: an unclassified queue item is valid.
    qid = _new_queue(conn)
    row = conn.execute(
        "SELECT failure_code, failure_category, recovery_policy "
        "FROM interface_error_queue WHERE queue_id = ?",
        (qid,),
    ).fetchone()
    assert (row["failure_code"], row["failure_category"], row["recovery_policy"]) == (
        None, None, None,
    )


def test_approved_classification_values_allowed(conn):
    _new_queue(
        conn,
        failure_code="ORDER_NOT_FOUND",
        failure_category="ORDER_MATCHING",
        recovery_policy="RETRY_OR_REDRIVE",
    )


def test_invalid_failure_code_fails(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _new_queue(conn, failure_code="NOT_A_REAL_CODE")


def test_invalid_failure_category_fails(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _new_queue(conn, failure_category="NOT_A_REAL_CATEGORY")


def test_invalid_recovery_policy_fails(conn):
    with pytest.raises(sqlite3.IntegrityError):
        _new_queue(conn, recovery_policy="ALWAYS_RETRY")


# ---------------------------------------------------------------------------
# Recovery-attempt foreign keys
# ---------------------------------------------------------------------------

def test_queue_foreign_key_enforced(conn):
    msg_id = _new_message(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn,
            queue_id=999999,          # no such queue item
            request_id="req-badfk-queue",
            outcome="SUCCEEDED",
            resulting_message_id=msg_id,
        )


def test_resulting_message_foreign_key_enforced(conn):
    qid = _new_queue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn,
            queue_id=qid,
            request_id="req-badfk-msg",
            outcome="SUCCEEDED",
            resulting_message_id=999999,   # no such message
        )


# ---------------------------------------------------------------------------
# request_id uniqueness and single-success invariant
# ---------------------------------------------------------------------------

def test_duplicate_request_id_fails(conn):
    qid = _new_queue(conn)
    _insert_attempt(
        conn, queue_id=qid, request_id="req-dup", outcome="REJECTED",
    )
    # A second, otherwise-valid attempt reusing the request_id is rejected,
    # even on a different queue item.
    other = _new_queue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=other, request_id="req-dup", outcome="REJECTED",
        )


def test_second_succeeded_for_same_queue_fails(conn):
    qid = _new_queue(conn)
    m1 = _new_message(conn)
    m2 = _new_message(conn)
    _insert_attempt(
        conn, queue_id=qid, request_id="req-s1",
        outcome="SUCCEEDED", resulting_message_id=m1,
    )
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=qid, request_id="req-s2",
            outcome="SUCCEEDED", resulting_message_id=m2,
        )


def test_multiple_failed_and_rejected_attempts_allowed(conn):
    qid = _new_queue(conn)
    m1 = _new_message(conn)
    m2 = _new_message(conn)
    # Two FAILED attempts (each needs a resulting message) plus two REJECTED
    # attempts (each without one) are all permitted for the same queue item.
    _insert_attempt(
        conn, queue_id=qid, request_id="req-f1",
        outcome="FAILED", resulting_message_id=m1,
    )
    _insert_attempt(
        conn, queue_id=qid, request_id="req-f2",
        outcome="FAILED", resulting_message_id=m2,
    )
    _insert_attempt(
        conn, queue_id=qid, request_id="req-r1", outcome="REJECTED",
    )
    _insert_attempt(
        conn, queue_id=qid, request_id="req-r2", outcome="REJECTED",
    )
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM interface_recovery_attempt WHERE queue_id = ?",
        (qid,),
    ).fetchone()["n"]
    assert n == 4
    # And a single SUCCEEDED is still allowed alongside them.
    m3 = _new_message(conn)
    _insert_attempt(
        conn, queue_id=qid, request_id="req-s",
        outcome="SUCCEEDED", resulting_message_id=m3,
    )


# ---------------------------------------------------------------------------
# Enumerated action / outcome values
# ---------------------------------------------------------------------------

def test_invalid_action_fails(conn):
    qid = _new_queue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=qid, request_id="req-badaction",
            action="REPROCESS", outcome="REJECTED",
        )


def test_invalid_outcome_fails(conn):
    qid = _new_queue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=qid, request_id="req-badoutcome",
            outcome="PENDING",
        )


def test_valid_actions_allowed(conn):
    qid = _new_queue(conn)
    _insert_attempt(
        conn, queue_id=qid, request_id="req-retry",
        action="RETRY_ORIGINAL", outcome="REJECTED",
    )
    _insert_attempt(
        conn, queue_id=qid, request_id="req-redrive",
        action="REDRIVE_CORRECTED", outcome="REJECTED",
    )


# ---------------------------------------------------------------------------
# Resulting-message presence by outcome
# ---------------------------------------------------------------------------

def test_succeeded_without_resulting_message_fails(conn):
    qid = _new_queue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=qid, request_id="req-succ-noms",
            outcome="SUCCEEDED", resulting_message_id=None,
        )


def test_failed_without_resulting_message_fails(conn):
    qid = _new_queue(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=qid, request_id="req-fail-noms",
            outcome="FAILED", resulting_message_id=None,
        )


def test_rejected_with_resulting_message_fails(conn):
    qid = _new_queue(conn)
    msg_id = _new_message(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _insert_attempt(
            conn, queue_id=qid, request_id="req-rej-withms",
            outcome="REJECTED", resulting_message_id=msg_id,
        )


# ---------------------------------------------------------------------------
# Schema initialization and existing ingestion behavior
# ---------------------------------------------------------------------------

def test_schema_reinit_is_repeatable(conn):
    # Re-running schema.sql on an already-initialized connection is safe
    # (IF NOT EXISTS / INSERT OR IGNORE) and leaves the seed data unduplicated.
    init_db(conn)
    init_db(conn)
    panels = conn.execute("SELECT COUNT(*) AS n FROM panel").fetchone()["n"]
    probes = conn.execute("SELECT COUNT(*) AS n FROM probe").fetchone()["n"]
    assert panels == 1
    assert probes == 9
    # Foreign-key graph is intact after repeated initialization.
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fresh_database_initializes_cleanly():
    fresh = create_database(":memory:")
    try:
        assert fresh.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            r["name"]
            for r in fresh.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "interface_error_queue" in tables
        assert "interface_recovery_attempt" in tables
    finally:
        fresh.close()


def test_existing_inbound_ingestion_still_routes_to_queue(conn):
    # Session 3 ingestion inserts queue rows without the new classification
    # columns; those must remain null and the row must be a valid OPEN item.
    message = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        "ORU^R01|SCHEMA1|T|2.5.1\n"
        "PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M\n"
        "OBR|1||ACC-NOMATCH-0001|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|BM-7001||BMA^Bone Marrow^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^1^2F2G^NORMAL||||||F\n"
    )
    result = inbound_hl7.ingest_message(conn, message)
    assert result.filed is False
    row = conn.execute(
        "SELECT status, resolved_at, terminal_at, failure_code, "
        "failure_category, recovery_policy "
        "FROM interface_error_queue WHERE queue_id = ?",
        (result.queue_id,),
    ).fetchone()
    assert row["status"] == "OPEN"
    assert row["resolved_at"] is None
    assert row["terminal_at"] is None
    assert row["failure_code"] is None
    assert row["failure_category"] is None
    assert row["recovery_policy"] is None
