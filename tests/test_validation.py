"""Tests for validation rules."""

from __future__ import annotations

from src import validation, workflow
from tests.conftest import enter_all_results


def _codes(findings, severity=None):
    return {
        f.rule_code
        for f in findings
        if severity is None or f.severity == severity
    }


def test_complete_valid_order_has_no_errors(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    findings = validation.validate_order(conn, accessioned_order)
    assert not validation.has_blocking_errors(findings)


def test_missing_required_probe_is_error(conn, accessioned_order):
    enter_all_results(conn, accessioned_order, skip=("KMT2A",))
    findings = validation.validate_order(conn, accessioned_order)
    assert "MISSING_PROBE" in _codes(findings, "ERROR")
    assert validation.has_blocking_errors(findings)


def test_unaccessioned_specimen_is_error(conn):
    # Order + received (not accessioned) specimen, all results entered.
    patient_id = workflow.create_patient(
        conn, "SYN-5", "Doe", "C", "1980-01-01", "F"
    )
    order_id = workflow.create_order(conn, patient_id, "ACC-5", "Dr. X")
    workflow.receive_specimen(conn, order_id)  # left in RECEIVED
    enter_all_results(conn, order_id)
    findings = validation.validate_order(conn, order_id)
    assert "SPEC_ACCESSIONED" in _codes(findings, "ERROR")


def test_rejected_specimen_is_error(conn):
    patient_id = workflow.create_patient(
        conn, "SYN-6", "Doe", "D", "1980-01-01", "M"
    )
    order_id = workflow.create_order(conn, patient_id, "ACC-6", "Dr. X")
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.reject_specimen(conn, specimen_id, "insufficient sample")
    findings = validation.validate_order(conn, order_id)
    assert "SPEC_ACCESSIONED" in _codes(findings, "ERROR")


def test_low_cell_count_is_warning_not_blocking(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    # Overwrite one probe with a below-minimum cell count, still below cutoff.
    workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 50, 0, "2F", "NORMAL", "tech01"
    )
    findings = validation.validate_order(conn, accessioned_order)
    assert "CELL_COUNT_LOW" in _codes(findings, "WARNING")
    assert not validation.has_blocking_errors(findings)


def test_abnormal_above_cutoff_called_normal_is_error(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    # CBFB cutoff is 2.5%; 20/200 = 10% called NORMAL -> ERROR.
    workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 200, 20, "abnormal", "NORMAL", "tech01"
    )
    findings = validation.validate_order(conn, accessioned_order)
    assert "INTERP_CONSISTENCY" in _codes(findings, "ERROR")
    assert validation.has_blocking_errors(findings)


def test_below_cutoff_called_abnormal_is_warning(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    # CBFB cutoff 2.5%; 1/200 = 0.5% called ABNORMAL -> WARNING (not blocking).
    workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 200, 1, "2F", "ABNORMAL", "tech01"
    )
    findings = validation.validate_order(conn, accessioned_order)
    assert "INTERP_CONSISTENCY" in _codes(findings, "WARNING")
    assert not validation.has_blocking_errors(findings)


def test_validate_nonexistent_order(conn):
    findings = validation.validate_order(conn, 999999)
    assert "ORDER_NOT_FOUND" in _codes(findings, "ERROR")
