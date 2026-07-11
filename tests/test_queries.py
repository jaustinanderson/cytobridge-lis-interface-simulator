"""Result-level tests for every analyst SQL view under ``queries/``.

The interface-error-queue view is asserted in ``test_inbound_interfaces.py``;
this module closes the remaining query-coverage gap with deterministic,
synthetic workflow scenarios.
"""

from __future__ import annotations

from src import workflow
from src.db import run_query
from tests.conftest import enter_all_results


def _create_accessioned_order(
    conn,
    suffix: str,
    *,
    priority: str = "ROUTINE",
) -> int:
    patient_id = workflow.create_patient(
        conn,
        f"SYN-{suffix}",
        f"Last{suffix}",
        f"First{suffix}",
        "1980-01-01",
        "U",
    )
    order_id = workflow.create_order(
        conn,
        patient_id,
        f"ACC-{suffix}",
        "Dr. Query",
        priority=priority,
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def test_pending_review_returns_stat_first_with_result_counts(
    conn,
    accessioned_order,
):
    workflow.enter_fish_result(
        conn,
        accessioned_order,
        "CBFB",
        200,
        1,
        "2F",
        "NORMAL",
        "tech01",
    )
    stat_order = _create_accessioned_order(conn, "QUERY-STAT", priority="STAT")
    workflow.enter_fish_result(
        conn,
        stat_order,
        "CBFB",
        200,
        1,
        "2F",
        "NORMAL",
        "tech01",
    )
    workflow.enter_fish_result(
        conn,
        stat_order,
        "PML_RARA",
        200,
        2,
        "2R2G",
        "NORMAL",
        "tech01",
    )

    rows = run_query(conn, "pending_review")

    assert [row["accession_number"] for row in rows] == [
        "ACC-QUERY-STAT",
        "ACC-TEST-0001",
    ]
    assert rows[0]["priority"] == "STAT"
    assert rows[0]["results_entered"] == 2
    assert rows[1]["results_entered"] == 1
    assert all(row["required_probes"] == 9 for row in rows)


def test_stat_pending_includes_open_stat_and_excludes_finalized(conn):
    open_order = _create_accessioned_order(conn, "STAT-OPEN", priority="STAT")
    finalized_order = _create_accessioned_order(
        conn,
        "STAT-FINAL",
        priority="STAT",
    )
    enter_all_results(conn, finalized_order)
    assert workflow.finalize_order(conn, finalized_order, "analyst01").finalized

    rows = run_query(conn, "stat_pending")
    accessions = {row["accession_number"] for row in rows}

    assert accessions == {"ACC-STAT-OPEN"}
    assert rows[0]["order_id"] == open_order
    assert rows[0]["status"] == "IN_PROCESS"
    assert rows[0]["hours_elapsed"] >= 0


def test_turnaround_time_returns_finalized_order_and_nonnegative_hours(
    conn,
    accessioned_order,
):
    enter_all_results(conn, accessioned_order)
    assert workflow.finalize_order(conn, accessioned_order, "analyst01").finalized

    rows = run_query(conn, "turnaround_time")

    assert len(rows) == 1
    assert rows[0]["order_id"] == accessioned_order
    assert rows[0]["accession_number"] == "ACC-TEST-0001"
    assert rows[0]["panel_code"] == "AML_MDS_FISH"
    assert rows[0]["tat_hours"] >= 0


def test_validation_error_rate_counts_distinct_orders(conn, accessioned_order):
    blocked = workflow.finalize_order(conn, accessioned_order, "analyst01")
    assert blocked.finalized is False
    _create_accessioned_order(conn, "NO-ERROR")

    row = run_query(conn, "validation_error_rate")[0]

    assert row["total_orders"] == 2
    assert row["orders_with_errors"] == 1
    assert row["error_rate_pct"] == 50.0


def test_audit_lookup_is_order_scoped_and_chronological(conn, accessioned_order):
    _create_accessioned_order(conn, "OTHER-AUDIT")

    rows = run_query(conn, "audit_lookup", {"order_id": accessioned_order})
    expected_ids = [
        row["event_id"]
        for row in conn.execute(
            "SELECT event_id FROM audit_event WHERE order_id = ? "
            "ORDER BY event_id",
            (accessioned_order,),
        ).fetchall()
    ]

    assert [row["event_id"] for row in rows] == expected_ids
    assert [row["event_id"] for row in rows] == sorted(expected_ids)
    assert {row["action"] for row in rows} >= {
        "ORDERED",
        "RECEIVED",
        "ACCESSIONED",
    }
