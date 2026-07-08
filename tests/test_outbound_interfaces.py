"""Tests for outbound HL7 ORU / FHIR DiagnosticReport generation.

These exercise the Session 2 outbound interface package: that a finalized order
renders well-formed HL7 and FHIR, that generation is blocked for non-finalized
orders, that generated messages persist to interface_message, and that the
output stays synthetic (no real patient data).

All data is synthetic. No PHI.
"""

from __future__ import annotations

import json

import pytest

from src import workflow
from src.interfaces import (
    OutboundError,
    build_diagnostic_report,
    collect_report_data,
    generate_oru,
    store_diagnostic_report,
    store_oru,
)
from src.interfaces import outbound_fhir, outbound_hl7
from tests.conftest import enter_all_results

# A fixed message time so control ids / timestamps are deterministic in tests.
MSG_TIME = "2026-07-08 14:30:00"


@pytest.fixture()
def finalized_order(conn, accessioned_order):
    """A fully entered, validated, FINALIZED order ready for export."""
    enter_all_results(conn, accessioned_order)
    result = workflow.finalize_order(conn, accessioned_order, "analyst01")
    assert result.finalized is True
    return accessioned_order


def _hl7_segments(message: str) -> list[str]:
    return message.split("\r")


def _segment_ids(message: str) -> list[str]:
    return [seg.split("|", 1)[0] for seg in _hl7_segments(message)]


# ---------------------------------------------------------------------------
# HL7 ORU
# ---------------------------------------------------------------------------

def test_hl7_includes_required_segments(conn, finalized_order):
    message = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    ids = _segment_ids(message)
    for expected in ("MSH", "PID", "OBR", "SPM", "OBX"):
        assert expected in ids, f"missing {expected} segment"


def test_hl7_is_oru_r01_message(conn, finalized_order):
    message = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    msh = _hl7_segments(message)[0].split("|")
    assert msh[0] == "MSH"
    assert msh[1] == r"^~\&"          # encoding characters
    assert msh[8] == "ORU^R01"        # MSH-9 message type
    assert msh[11] == "2.5.1"         # MSH-12 version


def test_hl7_includes_accession_and_mrn(conn, finalized_order):
    message = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    # Accession appears as the OBR filler order number.
    assert "ACC-TEST-0001" in message
    # Synthetic MRN appears in PID-3.
    pid = next(s for s in _hl7_segments(message) if s.startswith("PID|"))
    assert "SYN-9001" in pid


def test_hl7_has_one_obx_per_probe_plus_summary(conn, finalized_order):
    message = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    obx = [s for s in _hl7_segments(message) if s.startswith("OBX|")]
    # 9 required probes + a report-summary OBX + an overall-impression OBX.
    assert len(obx) == 9 + 2
    # The per-probe OBX carry the abnormal-flag field (OBX-8).
    abnormal_probe = next(s for s in obx if s.startswith("OBX|3|"))
    assert abnormal_probe.split("|")[8] == "A"


def test_hl7_reports_final_status(conn, finalized_order):
    message = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    obr = next(s for s in _hl7_segments(message) if s.startswith("OBR|"))
    assert obr.split("|")[25] == "F"   # OBR-25 result status: Final


def test_hl7_control_id_is_deterministic(conn, finalized_order):
    a = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    b = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    assert a == b


# ---------------------------------------------------------------------------
# FHIR DiagnosticReport
# ---------------------------------------------------------------------------

def test_fhir_bundle_contains_diagnostic_report(conn, finalized_order):
    bundle = build_diagnostic_report(conn, finalized_order)
    assert bundle["resourceType"] == "Bundle"
    types = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert "DiagnosticReport" in types
    assert "Patient" in types
    assert "Specimen" in types


def test_fhir_diagnostic_report_fields(conn, finalized_order):
    bundle = build_diagnostic_report(conn, finalized_order)
    report = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "DiagnosticReport"
    )
    assert report["status"] == "final"
    assert report["identifier"][0]["value"] == "ACC-TEST-0001"
    assert report["code"]["coding"][0]["code"] == "AML_MDS_FISH"
    assert "AML/MDS FISH Panel" in report["conclusion"]


def test_fhir_has_probe_level_observations(conn, finalized_order):
    bundle = build_diagnostic_report(conn, finalized_order)
    observations = [
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "Observation"
    ]
    # One Observation per required probe.
    assert len(observations) == 9
    for obs in observations:
        assert obs["status"] == "final"
        assert obs["valueQuantity"]["unit"] == "%"
        assert obs["code"]["coding"][0]["system"] == "urn:cytobridge:fish-probe"
    # DiagnosticReport.result references every Observation.
    report = next(
        e["resource"] for e in bundle["entry"]
        if e["resource"]["resourceType"] == "DiagnosticReport"
    )
    assert len(report["result"]) == len(observations)


def test_fhir_json_is_valid_and_serializable(conn, finalized_order):
    text = outbound_fhir.generate_diagnostic_report_json(conn, finalized_order)
    parsed = json.loads(text)
    assert parsed["resourceType"] == "Bundle"


# ---------------------------------------------------------------------------
# Validation behavior: export blocked for non-finalized / missing data
# ---------------------------------------------------------------------------

def test_hl7_export_blocked_for_non_finalized_order(conn, accessioned_order):
    # Order is accessioned but not finalized (no results, not FINALIZED).
    with pytest.raises(OutboundError):
        generate_oru(conn, accessioned_order)


def test_fhir_export_blocked_for_non_finalized_order(conn, accessioned_order):
    with pytest.raises(OutboundError):
        build_diagnostic_report(conn, accessioned_order)


def test_export_blocked_when_finalization_was_blocked(conn, accessioned_order):
    # Missing a required probe -> finalize is blocked -> order stays exportable=False.
    enter_all_results(conn, accessioned_order, skip=("TP53_17p",))
    result = workflow.finalize_order(conn, accessioned_order, "analyst01")
    assert result.finalized is False
    with pytest.raises(OutboundError):
        generate_oru(conn, accessioned_order)


def test_export_blocked_for_missing_order(conn):
    with pytest.raises(OutboundError):
        collect_report_data(conn, 999999)


# ---------------------------------------------------------------------------
# Storage into interface_message
# ---------------------------------------------------------------------------

def test_store_oru_persists_outbound_hl7_row(conn, finalized_order):
    message_id = store_oru(conn, finalized_order, generated_at=MSG_TIME)
    row = conn.execute(
        "SELECT direction, message_type, format, order_id, status, payload "
        "FROM interface_message WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    assert row["direction"] == "OUTBOUND"
    assert row["message_type"] == "ORU"
    assert row["format"] == "HL7"
    assert row["order_id"] == finalized_order
    assert row["status"] == "GENERATED"
    assert row["payload"].startswith("MSH|")


def test_store_diagnostic_report_persists_outbound_fhir_row(conn, finalized_order):
    message_id = store_diagnostic_report(conn, finalized_order)
    row = conn.execute(
        "SELECT direction, message_type, format, payload FROM interface_message "
        "WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    assert row["direction"] == "OUTBOUND"
    assert row["message_type"] == "DiagnosticReport"
    assert row["format"] == "FHIR"
    assert json.loads(row["payload"])["resourceType"] == "Bundle"


# ---------------------------------------------------------------------------
# Synthetic-data guardrail
# ---------------------------------------------------------------------------

def test_messages_are_synthetic(conn, finalized_order):
    """The generated identifiers are the synthetic test values, nothing else."""
    hl7 = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    fhir = outbound_fhir.generate_diagnostic_report_json(conn, finalized_order)
    for message in (hl7, fhir):
        # The only patient identifiers present are the synthetic ones.
        assert "SYN-9001" in message
        assert "ACC-TEST-0001" in message
    # FHIR code systems are the synthetic urn:cytobridge namespace, not real ones.
    assert "urn:cytobridge:" in fhir


def test_hl7_processing_id_marks_non_production(conn, finalized_order):
    message = generate_oru(conn, finalized_order, generated_at=MSG_TIME)
    msh = _hl7_segments(message)[0].split("|")
    assert msh[10] == "T"   # MSH-11 T = training (not production 'P')
