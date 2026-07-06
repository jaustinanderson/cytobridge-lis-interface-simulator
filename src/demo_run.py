"""Headless demo of the CytoBridge AML/MDS FISH workflow.

Runs two scenarios against a fresh in-memory database:

  1. Happy path — a complete order that passes validation and finalizes.
  2. Missing-probe failure — an order missing a required probe, whose
     finalization is blocked with recorded validation errors.

Run with:  python -m src.demo_run
All data is synthetic. No PHI.
"""

from __future__ import annotations

import sqlite3

from . import workflow
from .db import create_database, run_query

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


def main() -> None:
    conn = create_database(":memory:")
    scenario_happy_path(conn)
    scenario_missing_probe(conn)

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
