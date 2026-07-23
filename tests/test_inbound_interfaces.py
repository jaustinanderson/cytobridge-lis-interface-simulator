"""Tests for inbound HL7 ORU-style ingestion and the interface error queue.

These exercise the Session 3 inbound package: that a valid instrument message
files per-probe results to an open order (with an audit trail), that every
inbound message is stored in ``interface_message``, and that invalid, unmatched,
or non-fileable messages are routed to ``interface_error_queue`` with a clear
reason instead of being silently dropped or partially filed.

All data is synthetic. No PHI.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import workflow
from src.interfaces import inbound_hl7
from src.interfaces.inbound_hl7 import ingest_message
from tests.conftest import enter_all_results

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_messages" / "inbound"

# The valid sample is addressed to this accession / MRN.
SAMPLE_ACCESSION = "ACC-INBOUND-0001"
SAMPLE_MRN = "SYN-7001"


def _sample(name: str) -> str:
    return (SAMPLE_DIR / name).read_text(encoding="utf-8")


@pytest.fixture()
def open_inbound_order(conn) -> int:
    """An open (accessioned, non-finalized) order matching the valid sample."""
    patient_id = workflow.create_patient(
        conn, SAMPLE_MRN, "Synthetic", "Ingest", "1966-03-12", "M"
    )
    order_id = workflow.create_order(conn, patient_id, SAMPLE_ACCESSION, "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def _filed_probe_codes(conn, order_id: int) -> set[str]:
    rows = conn.execute(
        "SELECT pr.probe_code FROM fish_result fr "
        "JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ?",
        (order_id,),
    ).fetchall()
    return {r["probe_code"] for r in rows}


def _open_queue(conn) -> list:
    return conn.execute(
        "SELECT * FROM interface_error_queue WHERE status = 'OPEN' "
        "ORDER BY queue_id"
    ).fetchall()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------

def test_valid_inbound_files_probe_results(conn, open_inbound_order):
    result = inbound_hl7.ingest_message(conn, _sample("aml_mds_valid_oru.hl7"))
    assert result.filed is True
    assert result.order_id == open_inbound_order
    assert result.queue_id is None
    # All nine probe results from the sample are now on the order.
    filed = _filed_probe_codes(conn, open_inbound_order)
    assert "RUNX1T1_RUNX1" in filed
    assert len(filed) == 9
    # The abnormal probe filed with the reported counts/interpretation.
    row = conn.execute(
        "SELECT fr.cells_scored, fr.cells_abnormal, fr.interpretation "
        "FROM fish_result fr JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? AND pr.probe_code = 'RUNX1T1_RUNX1'",
        (open_inbound_order,),
    ).fetchone()
    assert (row["cells_scored"], row["cells_abnormal"], row["interpretation"]) == (
        200, 36, "ABNORMAL",
    )


def test_valid_inbound_updates_existing_probe_result(conn, open_inbound_order):
    # A prior (e.g. manually entered) result for a probe is updated in place by
    # the inbound message rather than duplicated.
    workflow.enter_fish_result(
        conn, open_inbound_order, "CBFB", 100, 0, "old", "NORMAL", "tech01"
    )
    inbound_hl7.ingest_message(conn, _sample("aml_mds_valid_oru.hl7"))
    rows = conn.execute(
        "SELECT fr.cells_scored, fr.signal_pattern FROM fish_result fr "
        "JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? AND pr.probe_code = 'CBFB'",
        (open_inbound_order,),
    ).fetchall()
    assert len(rows) == 1                      # updated, not duplicated
    assert rows[0]["cells_scored"] == 200      # value came from the message


def test_audit_event_recorded_for_inbound_filing(conn, open_inbound_order):
    result = inbound_hl7.ingest_message(conn, _sample("aml_mds_valid_oru.hl7"))
    audit = conn.execute(
        "SELECT action, detail, order_id FROM audit_event "
        "WHERE action = 'INBOUND_RESULT_FILED' AND order_id = ?",
        (open_inbound_order,),
    ).fetchone()
    assert audit is not None
    assert f"message_id={result.message_id}" in audit["detail"]


# ---------------------------------------------------------------------------
# Every inbound message is stored
# ---------------------------------------------------------------------------

def test_valid_inbound_stored_in_interface_message(conn, open_inbound_order):
    result = inbound_hl7.ingest_message(conn, _sample("aml_mds_valid_oru.hl7"))
    row = conn.execute(
        "SELECT direction, message_type, format, order_id, status, payload "
        "FROM interface_message WHERE message_id = ?",
        (result.message_id,),
    ).fetchone()
    assert row["direction"] == "INBOUND"
    assert row["message_type"] == "ORU"
    assert row["format"] == "HL7"
    assert row["order_id"] == open_inbound_order
    assert row["status"] == "FILED"
    assert row["payload"].startswith("MSH|")


def test_failed_inbound_still_stored_in_interface_message(conn):
    # No matching order -> routed to error queue, but still stored (as ERRORED).
    result = inbound_hl7.ingest_message(
        conn, _sample("aml_mds_unmatched_accession.hl7")
    )
    row = conn.execute(
        "SELECT direction, status FROM interface_message WHERE message_id = ?",
        (result.message_id,),
    ).fetchone()
    assert row["direction"] == "INBOUND"
    assert row["status"] == "ERRORED"


# ---------------------------------------------------------------------------
# Error-queue routing
# ---------------------------------------------------------------------------

def test_unmatched_accession_goes_to_error_queue(conn):
    result = inbound_hl7.ingest_message(
        conn, _sample("aml_mds_unmatched_accession.hl7")
    )
    assert result.filed is False
    assert result.queue_id is not None
    queue = _open_queue(conn)
    assert len(queue) == 1
    assert "ACC-NOMATCH-9999" in queue[0]["reason"]


def test_missing_obx_goes_to_error_queue(conn, open_inbound_order):
    result = inbound_hl7.ingest_message(conn, _sample("aml_mds_missing_obx.hl7"))
    assert result.filed is False
    # Nothing was filed to the (matched) order.
    assert _filed_probe_codes(conn, open_inbound_order) == set()
    assert "OBX" in _open_queue(conn)[0]["reason"]


def test_invalid_numeric_result_goes_to_error_queue(conn, open_inbound_order):
    result = inbound_hl7.ingest_message(
        conn, _sample("aml_mds_invalid_result_value.hl7")
    )
    assert result.filed is False
    # All-or-nothing: the valid OBX in the same message is not filed either.
    assert _filed_probe_codes(conn, open_inbound_order) == set()
    assert "not a valid integer" in _open_queue(conn)[0]["reason"]


def test_already_finalized_order_goes_to_error_queue(conn, open_inbound_order):
    enter_all_results(conn, open_inbound_order)
    assert workflow.finalize_order(conn, open_inbound_order, "analyst01").finalized

    result = inbound_hl7.ingest_message(conn, _sample("aml_mds_valid_oru.hl7"))
    assert result.filed is False
    # A finalized order is a terminal failure, so P3-002 initializes the queue
    # item as TERMINAL rather than OPEN; retrieve it directly by queue_id
    # instead of through the OPEN-only _open_queue helper.
    entry = conn.execute(
        "SELECT reason, status, terminal_at, resolved_at, failure_code, "
        "failure_category, recovery_policy "
        "FROM interface_error_queue WHERE queue_id = ?",
        (result.queue_id,),
    ).fetchone()
    assert entry is not None
    assert "finalized" in entry["reason"].lower()
    assert entry["status"] == "TERMINAL"
    assert entry["terminal_at"] is not None
    assert entry["resolved_at"] is None
    assert entry["failure_code"] == "ORDER_FINALIZED"
    assert entry["failure_category"] == "ORDER_STATE"
    assert entry["recovery_policy"] == "TERMINAL"


def test_unknown_probe_code_goes_to_error_queue(conn, open_inbound_order):
    message = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        "ORU^R01|INBNDX|T|2.5.1\n"
        "PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M\n"
        f"OBR|1||{SAMPLE_ACCESSION}|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|BM-7001||BMA^Bone Marrow^L\n"
        "OBX|1|ST|NOTAPROBE^Unknown probe^L||200^1^2F2G^NORMAL||||||F\n"
    )
    result = ingest_message(conn, message)
    assert result.filed is False
    assert "NOTAPROBE" in _open_queue(conn)[0]["reason"]


def test_cells_abnormal_exceeds_scored_goes_to_error_queue(conn, open_inbound_order):
    message = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        "ORU^R01|INBNDY|T|2.5.1\n"
        "PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M\n"
        f"OBR|1||{SAMPLE_ACCESSION}|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|BM-7001||BMA^Bone Marrow^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^250^2F1R1G^ABNORMAL||||||F\n"
    )
    result = ingest_message(conn, message)
    assert result.filed is False
    assert "exceeds" in _open_queue(conn)[0]["reason"]


def test_missing_required_segment_goes_to_error_queue(conn, open_inbound_order):
    # A message with no SPM segment (SPM is required).
    message = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        "ORU^R01|INBNDZ|T|2.5.1\n"
        "PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M\n"
        f"OBR|1||{SAMPLE_ACCESSION}|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^1^2F2G^NORMAL||||||F\n"
    )
    result = ingest_message(conn, message)
    assert result.filed is False
    assert "SPM" in _open_queue(conn)[0]["reason"]


def test_incompatible_specimen_type_goes_to_error_queue(conn, open_inbound_order):
    # Peripheral blood against the bone-marrow-only AML/MDS panel.
    message = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        "ORU^R01|INBNDPB|T|2.5.1\n"
        "PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M\n"
        f"OBR|1||{SAMPLE_ACCESSION}|AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|PB-7001||PB^Peripheral Blood^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^1^2F2G^NORMAL||||||F\n"
    )
    result = ingest_message(conn, message)
    assert result.filed is False
    assert "incompatible" in _open_queue(conn)[0]["reason"]


def test_missing_accession_goes_to_error_queue(conn, open_inbound_order):
    # OBR present but OBR-3 (accession) is empty.
    message = (
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        "ORU^R01|INBNDNOACC|T|2.5.1\n"
        "PID|1||SYN-7001^^^CYTO_LAB^MR||Synthetic^Ingest||19660312|M\n"
        "OBR|1|||AML_MDS_FISH^AML/MDS FISH Panel^L\n"
        "SPM|1|BM-7001||BMA^Bone Marrow^L\n"
        "OBX|1|ST|RUNX1T1_RUNX1^RUNX1T1/RUNX1^L||200^1^2F2G^NORMAL||||||F\n"
    )
    result = ingest_message(conn, message)
    assert result.filed is False
    assert "ccession" in _open_queue(conn)[0]["reason"]


# ---------------------------------------------------------------------------
# Error-queue entry shape
# ---------------------------------------------------------------------------

def test_error_queue_entry_has_expected_fields(conn):
    result = inbound_hl7.ingest_message(
        conn, _sample("aml_mds_unmatched_accession.hl7")
    )
    entry = conn.execute(
        "SELECT message_id, direction, reason, status, created_at "
        "FROM interface_error_queue WHERE queue_id = ?",
        (result.queue_id,),
    ).fetchone()
    assert entry["message_id"] == result.message_id
    assert entry["direction"] == "INBOUND"
    assert entry["reason"]                      # non-empty, clear reason
    assert entry["status"] == "OPEN"
    assert entry["created_at"]                  # timestamped on creation


def test_error_queue_query_lists_open_items(conn):
    from src.db import run_query

    inbound_hl7.ingest_message(conn, _sample("aml_mds_unmatched_accession.hl7"))
    rows = run_query(conn, "interface_error_queue")
    assert len(rows) == 1
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["direction"] == "INBOUND"
