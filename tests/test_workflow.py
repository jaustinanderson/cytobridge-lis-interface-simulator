"""Tests for the core order/specimen/result/finalize workflow."""

from __future__ import annotations

import pytest

from src import workflow
from tests.conftest import enter_all_results


def _order_status(conn, order_id):
    return conn.execute(
        "SELECT status FROM lab_order WHERE order_id = ?", (order_id,)
    ).fetchone()["status"]


def test_reference_panel_and_probes_seeded(conn):
    panel = conn.execute(
        "SELECT panel_code FROM panel WHERE panel_code = 'AML_MDS_FISH'"
    ).fetchone()
    assert panel is not None
    required = conn.execute(
        "SELECT COUNT(*) AS n FROM probe WHERE is_required = 1"
    ).fetchone()["n"]
    assert required == 9


def test_create_patient_and_order(conn):
    patient_id = workflow.create_patient(
        conn, "SYN-1", "Doe", "A", "1970-01-01", "F"
    )
    order_id = workflow.create_order(conn, patient_id, "ACC-1", "Dr. X")
    assert _order_status(conn, order_id) == "ORDERED"


def test_receive_and_accession_specimen(conn, accessioned_order):
    specimen = conn.execute(
        "SELECT status FROM specimen WHERE order_id = ?", (accessioned_order,)
    ).fetchone()
    assert specimen["status"] == "ACCESSIONED"
    # Receiving a specimen advances the order out of ORDERED.
    assert _order_status(conn, accessioned_order) == "IN_PROCESS"


def test_entering_result_moves_order_to_pending_review(conn, accessioned_order):
    workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 200, 1, "2F", "NORMAL", "tech01"
    )
    assert _order_status(conn, accessioned_order) == "PENDING_REVIEW"


def test_re_entering_probe_updates_existing_result(conn, accessioned_order):
    workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 200, 1, "2F", "NORMAL", "tech01"
    )
    workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 300, 2, "2F", "NORMAL", "tech02"
    )
    rows = conn.execute(
        "SELECT cells_scored, entered_by FROM fish_result "
        "WHERE order_id = ? AND probe_id = (SELECT probe_id FROM probe "
        "WHERE probe_code = 'CBFB')",
        (accessioned_order,),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["cells_scored"] == 300
    assert rows[0]["entered_by"] == "tech02"


def test_enter_result_returns_persisted_id_and_links_both_audits(
    conn, accessioned_order
):
    inserted_id = workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 200, 1, "2F", "NORMAL", "tech01"
    )
    inserted_row = conn.execute(
        "SELECT result_id FROM fish_result WHERE order_id = ? "
        "AND probe_id = (SELECT probe_id FROM probe WHERE probe_code = 'CBFB')",
        (accessioned_order,),
    ).fetchone()
    assert inserted_id == inserted_row["result_id"]
    inserted_audit = conn.execute(
        "SELECT entity_id FROM audit_event WHERE order_id = ? "
        "AND action = 'RESULT_ENTERED' ORDER BY event_id DESC LIMIT 1",
        (accessioned_order,),
    ).fetchone()
    assert inserted_audit["entity_id"] == inserted_id

    workflow.enter_fish_result(
        conn, accessioned_order, "PML_RARA", 200, 2, "2R2G", "NORMAL", "tech01"
    )
    before_update_count = conn.execute(
        "SELECT COUNT(*) AS n FROM fish_result WHERE order_id = ?",
        (accessioned_order,),
    ).fetchone()["n"]
    updated_id = workflow.enter_fish_result(
        conn, accessioned_order, "CBFB", 300, 2, "2F", "NORMAL", "tech02"
    )
    after_update = conn.execute(
        "SELECT result_id, cells_scored, entered_by FROM fish_result "
        "WHERE order_id = ? AND probe_id = (SELECT probe_id FROM probe "
        "WHERE probe_code = 'CBFB')",
        (accessioned_order,),
    ).fetchone()
    assert updated_id == after_update["result_id"] == inserted_id
    assert after_update["cells_scored"] == 300
    assert after_update["entered_by"] == "tech02"
    assert conn.execute(
        "SELECT COUNT(*) FROM fish_result WHERE order_id = ?", (accessioned_order,)
    ).fetchone()[0] == before_update_count

    latest_audit = conn.execute(
        "SELECT entity_id FROM audit_event WHERE order_id = ? "
        "AND action = 'RESULT_ENTERED' ORDER BY event_id DESC LIMIT 1",
        (accessioned_order,),
    ).fetchone()
    assert latest_audit["entity_id"] == updated_id


def test_enter_result_commit_false_rolls_back_without_hidden_commit(
    conn, accessioned_order
):
    before = {
        "results": conn.execute(
            "SELECT COUNT(*) FROM fish_result WHERE order_id = ?",
            (accessioned_order,),
        ).fetchone()[0],
        "audits": conn.execute(
            "SELECT COUNT(*) FROM audit_event WHERE order_id = ?",
            (accessioned_order,),
        ).fetchone()[0],
        "status": _order_status(conn, accessioned_order),
    }
    workflow.enter_fish_result(
        conn,
        accessioned_order,
        "CBFB",
        200,
        1,
        "2F",
        "NORMAL",
        "tech01",
        commit=False,
    )
    conn.rollback()

    assert conn.execute(
        "SELECT COUNT(*) FROM fish_result WHERE order_id = ?", (accessioned_order,)
    ).fetchone()[0] == before["results"]
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_event WHERE order_id = ?", (accessioned_order,)
    ).fetchone()[0] == before["audits"]
    assert _order_status(conn, accessioned_order) == before["status"]


def test_happy_path_finalizes_and_creates_report(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    result = workflow.finalize_order(conn, accessioned_order, "analyst01")
    assert result.finalized is True
    assert result.report_id is not None
    assert _order_status(conn, accessioned_order) == "FINALIZED"
    report = conn.execute(
        "SELECT status, summary_text FROM report WHERE order_id = ?",
        (accessioned_order,),
    ).fetchone()
    assert report["status"] == "FINALIZED"
    assert "AML/MDS FISH Panel" in report["summary_text"]


def test_missing_probe_blocks_finalization(conn, accessioned_order):
    enter_all_results(conn, accessioned_order, skip=("TP53_17p",))
    result = workflow.finalize_order(conn, accessioned_order, "analyst01")
    assert result.finalized is False
    assert result.report_id is None
    assert _order_status(conn, accessioned_order) != "FINALIZED"
    codes = {f.rule_code for f in result.findings}
    assert "MISSING_PROBE" in codes


def test_blocked_finalize_persists_validation_errors(conn, accessioned_order):
    enter_all_results(conn, accessioned_order, skip=("TP53_17p",))
    workflow.finalize_order(conn, accessioned_order, "analyst01")
    stored = conn.execute(
        "SELECT rule_code FROM validation_error WHERE order_id = ? "
        "AND severity = 'ERROR'",
        (accessioned_order,),
    ).fetchall()
    assert any(r["rule_code"] == "MISSING_PROBE" for r in stored)


def test_cannot_enter_results_after_finalization(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    workflow.finalize_order(conn, accessioned_order, "analyst01")
    with pytest.raises(workflow.WorkflowError):
        workflow.enter_fish_result(
            conn, accessioned_order, "CBFB", 200, 1, "2F", "NORMAL", "tech01"
        )


def test_audit_trail_records_state_changes(conn, accessioned_order):
    enter_all_results(conn, accessioned_order)
    workflow.finalize_order(conn, accessioned_order, "analyst01")
    actions = [
        r["action"]
        for r in conn.execute(
            "SELECT action FROM audit_event WHERE order_id = ? ORDER BY event_id",
            (accessioned_order,),
        ).fetchall()
    ]
    for expected in ("ORDERED", "RECEIVED", "ACCESSIONED", "RESULT_ENTERED",
                     "VALIDATION_RUN", "FINALIZED"):
        assert expected in actions


def test_accession_number_is_unique(conn):
    patient_id = workflow.create_patient(
        conn, "SYN-2", "Doe", "B", "1970-01-01", "M"
    )
    workflow.create_order(conn, patient_id, "ACC-DUP", "Dr. X")
    with pytest.raises(Exception):
        workflow.create_order(conn, patient_id, "ACC-DUP", "Dr. X")


def test_schema_rejects_abnormal_exceeding_scored(conn, accessioned_order):
    # CHECK (cells_abnormal <= cells_scored) must be enforced by SQLite.
    with pytest.raises(Exception):
        workflow.enter_fish_result(
            conn, accessioned_order, "CBFB", 100, 150, "2F", "ABNORMAL", "tech01"
        )
