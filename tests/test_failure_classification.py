"""Structured failure classification for inbound ingestion (task P3-002).

These tests prove that the existing Session 3 inbound path now records, for each
failed message, the approved structured classification (failure_code /
failure_category / recovery_policy) and the correct *initial* error-queue state,
and nothing more. They deliberately do not exercise recovery requests, retries,
corrected re-drives, recovery attempts, queue resolution, or any later Phase 3
behavior - none of which this task implements.

Source of truth for every expectation below is the frozen design record
(``validation/v1.1-design-record.md``): section 6 (failure taxonomy and recovery
mapping) and section 8 (queue state model). The expected failure_code /
failure_category / recovery_policy triples and the expected initial queue states
are transcribed by hand from that frozen design - never generated from the
implementation mapping, ``schema.sql``, observed application output, or parser
reason strings. The approved corpus manifest is used only to locate the fourteen
original fixtures; the expectations here are asserted independently of it.

All data is synthetic. No PHI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from src import workflow
from src.db import create_database, execute
from src.interfaces import inbound_hl7
from tests.conftest import enter_all_results

_REPO_ROOT = Path(__file__).resolve().parent.parent
ORIGINAL_DIR = _REPO_ROOT / "sample_messages" / "recovery" / "original"
INBOUND_DIR = _REPO_ROOT / "sample_messages" / "inbound"


# ---------------------------------------------------------------------------
# Frozen expectations (transcribed from the design record, not the code)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Case:
    """One approved corpus case and its frozen expected classification."""

    case_id: str
    filename: str
    failure_code: str
    failure_category: str
    recovery_policy: str
    expected_queue_status: str


# Design record section 6 (failure code -> category / recovery policy) combined
# with section 8 (recoverable -> OPEN, terminal -> TERMINAL). Written out by hand
# from the frozen design; do not regenerate from any runtime source.
CASES = [
    Case("RC-01", "01_empty_message.hl7",
         "EMPTY_MESSAGE", "MESSAGE_STRUCTURE", "REDRIVE_ONLY", "OPEN"),
    Case("RC-02", "02_missing_required_segment.hl7",
         "MISSING_REQUIRED_SEGMENT", "MESSAGE_STRUCTURE", "REDRIVE_ONLY", "OPEN"),
    Case("RC-03", "03_no_obx.hl7",
         "NO_OBX", "MESSAGE_STRUCTURE", "REDRIVE_ONLY", "OPEN"),
    Case("RC-04", "04_missing_accession.hl7",
         "MISSING_ACCESSION", "ORDER_MATCHING", "REDRIVE_ONLY", "OPEN"),
    Case("RC-05", "05_order_not_found.hl7",
         "ORDER_NOT_FOUND", "ORDER_MATCHING", "RETRY_OR_REDRIVE", "OPEN"),
    Case("RC-06", "06_order_finalized.hl7",
         "ORDER_FINALIZED", "ORDER_STATE", "TERMINAL", "TERMINAL"),
    Case("RC-07", "07_order_cancelled.hl7",
         "ORDER_CANCELLED", "ORDER_STATE", "TERMINAL", "TERMINAL"),
    Case("RC-08", "08_specimen_unrecognized.hl7",
         "SPECIMEN_UNRECOGNIZED", "SPECIMEN", "REDRIVE_ONLY", "OPEN"),
    Case("RC-09", "09_specimen_incompatible.hl7",
         "SPECIMEN_INCOMPATIBLE", "SPECIMEN", "REDRIVE_ONLY", "OPEN"),
    Case("RC-10", "10_missing_probe_code.hl7",
         "MISSING_PROBE_CODE", "FISH_RESULT_CONTENT", "REDRIVE_ONLY", "OPEN"),
    Case("RC-11", "11_unknown_probe_code.hl7",
         "UNKNOWN_PROBE_CODE", "FISH_RESULT_CONTENT", "REDRIVE_ONLY", "OPEN"),
    Case("RC-12", "12_invalid_cell_count.hl7",
         "INVALID_CELL_COUNT", "FISH_RESULT_CONTENT", "REDRIVE_ONLY", "OPEN"),
    Case("RC-13", "13_abnormal_exceeds_scored.hl7",
         "ABNORMAL_EXCEEDS_SCORED", "FISH_RESULT_CONTENT", "REDRIVE_ONLY", "OPEN"),
    Case("RC-14", "14_invalid_interpretation.hl7",
         "INVALID_INTERPRETATION", "FISH_RESULT_CONTENT", "REDRIVE_ONLY", "OPEN"),
]

# The fourteen approved failure codes, transcribed from design record section 6.
APPROVED_CODES = {
    "EMPTY_MESSAGE",
    "MISSING_REQUIRED_SEGMENT",
    "NO_OBX",
    "MISSING_ACCESSION",
    "ORDER_NOT_FOUND",
    "ORDER_FINALIZED",
    "ORDER_CANCELLED",
    "SPECIMEN_UNRECOGNIZED",
    "SPECIMEN_INCOMPATIBLE",
    "MISSING_PROBE_CODE",
    "UNKNOWN_PROBE_CODE",
    "INVALID_CELL_COUNT",
    "ABNORMAL_EXCEEDS_SCORED",
    "INVALID_INTERPRETATION",
}


# ---------------------------------------------------------------------------
# Synthetic database setup per fixture
# ---------------------------------------------------------------------------
#
# Each fixture is built so exactly one condition fails at ingest time; some of
# those conditions are only reached once the message matches an order, so a
# matching order must exist first. Accessions and MRNs mirror the corpus
# fixtures. RC-01..RC-05 fail at or before order matching and must NOT match any
# order (RC-05 in particular relies on there being no matching order).

_OPEN_ORDER_SETUP = {
    "SPECIMEN_UNRECOGNIZED":   ("SYN-8801", "ACC-REC-0801"),
    "SPECIMEN_INCOMPATIBLE":   ("SYN-8901", "ACC-REC-0901"),
    "MISSING_PROBE_CODE":      ("SYN-9001", "ACC-REC-1001"),
    "UNKNOWN_PROBE_CODE":      ("SYN-9101", "ACC-REC-1101"),
    "INVALID_CELL_COUNT":      ("SYN-9201", "ACC-REC-1201"),
    "ABNORMAL_EXCEEDS_SCORED": ("SYN-9301", "ACC-REC-1301"),
    "INVALID_INTERPRETATION":  ("SYN-9401", "ACC-REC-1401"),
}
_FINALIZED_ORDER = ("SYN-8601", "ACC-REC-0601")
_CANCELLED_ORDER = ("SYN-8701", "ACC-REC-0701")


def _make_open_order(conn, mrn: str, accession: str) -> int:
    """Create an accessioned, non-terminal AML/MDS FISH bone-marrow order."""
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
    execute(
        conn,
        "UPDATE lab_order SET status = 'CANCELLED' WHERE order_id = ?",
        (order_id,),
    )
    return order_id


def _setup_for_case(conn, case: Case) -> int | None:
    """Create only the state this fixture needs, returning the matched order_id.

    Returns None for cases that fail before matching an order.
    """
    code = case.failure_code
    if code == "ORDER_FINALIZED":
        return _make_finalized_order(conn, *_FINALIZED_ORDER)
    if code == "ORDER_CANCELLED":
        return _make_cancelled_order(conn, *_CANCELLED_ORDER)
    if code in _OPEN_ORDER_SETUP:
        return _make_open_order(conn, *_OPEN_ORDER_SETUP[code])
    return None


# ---------------------------------------------------------------------------
# Snapshot / lookup helpers
# ---------------------------------------------------------------------------

def _original(filename: str) -> str:
    return (ORIGINAL_DIR / filename).read_text(encoding="utf-8")


def _fish_snapshot(conn) -> list[tuple]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT result_id, order_id, probe_id, cells_scored, cells_abnormal, "
            "signal_pattern, interpretation FROM fish_result ORDER BY result_id"
        ).fetchall()
    ]


def _inbound_filed_audit_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM audit_event "
        "WHERE action = 'INBOUND_RESULT_FILED'"
    ).fetchone()["n"]


def _recovery_attempt_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM interface_recovery_attempt"
    ).fetchone()["n"]


def _order_status(conn, order_id: int) -> str:
    return conn.execute(
        "SELECT status FROM lab_order WHERE order_id = ?", (order_id,)
    ).fetchone()["status"]


def _queue_code(conn, queue_id: int) -> str:
    return conn.execute(
        "SELECT failure_code FROM interface_error_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()["failure_code"]


def _ingest_case_fresh(case: Case) -> dict:
    """Ingest one corpus original in a fresh database; return its queue row."""
    conn = create_database(":memory:")
    try:
        _setup_for_case(conn, case)
        result = inbound_hl7.ingest_message(conn, _original(case.filename))
        row = conn.execute(
            "SELECT failure_code, failure_category, recovery_policy, status, "
            "resolved_at, terminal_at FROM interface_error_queue "
            "WHERE queue_id = ?",
            (result.queue_id,),
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Per-case: full classification and initial state for each original fixture
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CASES, ids=[c.case_id for c in CASES])
def test_original_corpus_case_is_classified(conn, case: Case):
    matched_order_id = _setup_for_case(conn, case)

    fish_before = _fish_snapshot(conn)
    inbound_filed_before = _inbound_filed_audit_count(conn)

    payload = _original(case.filename)
    result = inbound_hl7.ingest_message(conn, payload)

    # Ingestion does not file.
    assert result.filed is False
    assert result.order_id is None
    assert result.queue_id is not None
    assert result.reason  # populated, human-readable reason preserved

    # Exactly one original interface_message is stored, ERRORED, payload exact.
    messages = conn.execute(
        "SELECT message_id, direction, status, payload FROM interface_message"
    ).fetchall()
    assert len(messages) == 1
    message = messages[0]
    assert message["message_id"] == result.message_id
    assert message["direction"] == "INBOUND"
    assert message["status"] == "ERRORED"
    assert message["payload"] == payload

    # Exactly one linked queue item; raw_payload is an exact copy; reason kept.
    queue_items = conn.execute(
        "SELECT * FROM interface_error_queue WHERE message_id = ?",
        (result.message_id,),
    ).fetchall()
    assert len(queue_items) == 1
    item = queue_items[0]
    assert item["queue_id"] == result.queue_id
    assert item["raw_payload"] == payload
    assert item["reason"] == result.reason
    assert item["reason"]

    # Classification equals the frozen mapping transcribed above, and no field
    # is null for an inbound failure.
    assert item["failure_code"] == case.failure_code
    assert item["failure_category"] == case.failure_category
    assert item["recovery_policy"] == case.recovery_policy
    assert item["failure_code"] is not None
    assert item["failure_category"] is not None
    assert item["recovery_policy"] is not None

    # Initial queue state per the frozen state model.
    assert item["status"] == case.expected_queue_status
    if case.expected_queue_status == "TERMINAL":
        assert item["terminal_at"] is not None
        assert item["resolved_at"] is None
    else:
        assert item["status"] == "OPEN"
        assert item["resolved_at"] is None
        assert item["terminal_at"] is None

    # No FISH result or inbound-filing side effect is produced.
    assert _fish_snapshot(conn) == fish_before
    assert _inbound_filed_audit_count(conn) == inbound_filed_before

    # This task creates no recovery-attempt rows.
    assert _recovery_attempt_count(conn) == 0

    # A matched terminal order keeps its original status (never reopened).
    if case.failure_code == "ORDER_FINALIZED":
        assert _order_status(conn, matched_order_id) == "FINALIZED"
    if case.failure_code == "ORDER_CANCELLED":
        assert _order_status(conn, matched_order_id) == "CANCELLED"


# ---------------------------------------------------------------------------
# Across the full parameterized corpus
# ---------------------------------------------------------------------------

def test_corpus_exercises_exactly_14_unique_approved_codes():
    codes = [_ingest_case_fresh(c)["failure_code"] for c in CASES]
    assert len(codes) == 14
    assert len(set(codes)) == 14
    assert set(codes) == APPROVED_CODES


def test_no_classification_field_is_null_across_corpus():
    for case in CASES:
        row = _ingest_case_fresh(case)
        assert row["failure_code"] is not None, case.case_id
        assert row["failure_category"] is not None, case.case_id
        assert row["recovery_policy"] is not None, case.case_id


def test_twelve_recoverable_cases_initialize_open():
    recoverable = [c for c in CASES if c.expected_queue_status != "TERMINAL"]
    assert len(recoverable) == 12
    for case in recoverable:
        row = _ingest_case_fresh(case)
        assert row["status"] == "OPEN", case.case_id
        assert row["resolved_at"] is None, case.case_id
        assert row["terminal_at"] is None, case.case_id


def test_two_terminal_cases_initialize_terminal_with_timestamp():
    terminal = [c for c in CASES if c.expected_queue_status == "TERMINAL"]
    assert len(terminal) == 2
    assert {c.failure_code for c in terminal} == {
        "ORDER_FINALIZED",
        "ORDER_CANCELLED",
    }
    for case in terminal:
        row = _ingest_case_fresh(case)
        assert row["status"] == "TERMINAL", case.case_id
        assert row["terminal_at"] is not None, case.case_id
        assert row["resolved_at"] is None, case.case_id


# ---------------------------------------------------------------------------
# Supplemental: both bad-count shapes share one code; valid path unchanged
# ---------------------------------------------------------------------------

def test_noninteger_and_negative_counts_both_map_to_invalid_cell_count(conn):
    # A non-integer count (as in RC-12) and a negative count must both classify
    # as INVALID_CELL_COUNT; the frozen taxonomy has no separate negative-count
    # code, so exactly one distinct code may appear across the two.
    _make_open_order(conn, "SYN-9251", "ACC-REC-CNT-0001")
    noninteger = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260715101200||"
        "ORU^R01|RCO-CNT1|T|2.5.1\n"
        "PID|1||SYN-9251^^^CYTO_LAB^MR||Synthetic^Recovery||19700101|M\n"
        "OBR|1||ACC-REC-CNT-0001|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|BM-9251||BMA^Bone Marrow^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^2^2F2G^NORMAL||||||F\n"
        "OBX|2|ST|PML_RARA^PML/RARA^L||xx^2^2R2G^NORMAL||||||F\n"
    )
    res_noninteger = inbound_hl7.ingest_message(conn, noninteger)

    _make_open_order(conn, "SYN-9252", "ACC-REC-CNT-0002")
    negative = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260715101200||"
        "ORU^R01|RCO-CNT2|T|2.5.1\n"
        "PID|1||SYN-9252^^^CYTO_LAB^MR||Synthetic^Recovery||19700101|M\n"
        "OBR|1||ACC-REC-CNT-0002|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|BM-9252||BMA^Bone Marrow^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^2^2F2G^NORMAL||||||F\n"
        "OBX|2|ST|PML_RARA^PML/RARA^L||-5^2^2R2G^NORMAL||||||F\n"
    )
    res_negative = inbound_hl7.ingest_message(conn, negative)

    assert res_noninteger.filed is False
    assert res_negative.filed is False
    assert "not a valid integer" in res_noninteger.reason
    assert "cannot be negative" in res_negative.reason

    codes = {
        _queue_code(conn, res_noninteger.queue_id),
        _queue_code(conn, res_negative.queue_id),
    }
    assert codes == {"INVALID_CELL_COUNT"}

    # Both retain the recoverable REDRIVE_ONLY classification and OPEN state.
    for queue_id in (res_noninteger.queue_id, res_negative.queue_id):
        row = conn.execute(
            "SELECT failure_category, recovery_policy, status, resolved_at, "
            "terminal_at FROM interface_error_queue WHERE queue_id = ?",
            (queue_id,),
        ).fetchone()
        assert row["failure_category"] == "FISH_RESULT_CONTENT"
        assert row["recovery_policy"] == "REDRIVE_ONLY"
        assert row["status"] == "OPEN"
        assert row["resolved_at"] is None
        assert row["terminal_at"] is None


def test_valid_inbound_still_files_unchanged(conn):
    patient_id = workflow.create_patient(
        conn, "SYN-7001", "Synthetic", "Ingest", "1966-03-12", "M"
    )
    order_id = workflow.create_order(
        conn, patient_id, "ACC-INBOUND-0001", "Dr. Synthetic"
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)

    sample = (INBOUND_DIR / "aml_mds_valid_oru.hl7").read_text(encoding="utf-8")
    result = inbound_hl7.ingest_message(conn, sample)

    assert result.filed is True
    assert result.order_id == order_id
    assert result.queue_id is None

    # A filed message produces no error-queue row (hence no classification).
    assert conn.execute(
        "SELECT COUNT(*) AS n FROM interface_error_queue"
    ).fetchone()["n"] == 0

    # All nine probe results are on the order and the message is FILED.
    filed = conn.execute(
        "SELECT COUNT(*) AS n FROM fish_result WHERE order_id = ?", (order_id,)
    ).fetchone()["n"]
    assert filed == 9
    status = conn.execute(
        "SELECT status FROM interface_message WHERE message_id = ?",
        (result.message_id,),
    ).fetchone()["status"]
    assert status == "FILED"
