"""Core laboratory workflow for CytoBridge.

Functions to move an AML/MDS FISH order through its lifecycle:

    create_patient -> create_order -> receive_specimen -> accession_specimen
                   -> enter_fish_result (per probe) -> finalize_order

Every state change writes an audit_event. Finalization runs validation and is
blocked when any ERROR-severity finding is present; the findings are persisted
to validation_error either way.

All data is synthetic. No PHI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from . import reports, validation
from .db import execute

PANEL_CODE = "AML_MDS_FISH"


class WorkflowError(Exception):
    """Raised when a workflow operation is not allowed in the current state."""


@dataclass(frozen=True)
class FinalizeResult:
    """Outcome of a finalize attempt."""

    order_id: int
    finalized: bool
    findings: list[validation.Finding]
    report_id: int | None = None


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------

def record_audit(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    action: str,
    *,
    order_id: int | None = None,
    detail: str | None = None,
    actor: str = "system",
) -> int:
    """Insert an audit_event row for a state change."""
    return execute(
        conn,
        "INSERT INTO audit_event "
        "(entity_type, entity_id, order_id, action, detail, actor) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (entity_type, entity_id, order_id, action, detail, actor),
    )


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def get_panel_id(conn: sqlite3.Connection, panel_code: str = PANEL_CODE) -> int:
    row = conn.execute(
        "SELECT panel_id FROM panel WHERE panel_code = ?", (panel_code,)
    ).fetchone()
    if row is None:
        raise WorkflowError(f"Unknown panel_code: {panel_code}")
    return row["panel_id"]


def get_probe_id(
    conn: sqlite3.Connection, probe_code: str, panel_code: str = PANEL_CODE
) -> int:
    row = conn.execute(
        "SELECT pr.probe_id FROM probe pr "
        "JOIN panel pa ON pa.panel_id = pr.panel_id "
        "WHERE pa.panel_code = ? AND pr.probe_code = ?",
        (panel_code, probe_code),
    ).fetchone()
    if row is None:
        raise WorkflowError(f"Unknown probe_code: {probe_code}")
    return row["probe_id"]


def get_order(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM lab_order WHERE order_id = ?", (order_id,)
    ).fetchone()
    if row is None:
        raise WorkflowError(f"Order {order_id} does not exist.")
    return row


# ---------------------------------------------------------------------------
# Patient / order / specimen / result
# ---------------------------------------------------------------------------

def create_patient(
    conn: sqlite3.Connection,
    mrn: str,
    last_name: str,
    first_name: str,
    date_of_birth: str,
    sex: str,
) -> int:
    """Create a synthetic patient and return patient_id."""
    patient_id = execute(
        conn,
        "INSERT INTO patient (mrn, last_name, first_name, date_of_birth, sex) "
        "VALUES (?, ?, ?, ?, ?)",
        (mrn, last_name, first_name, date_of_birth, sex),
    )
    record_audit(conn, "patient", patient_id, "CREATED", detail=f"mrn={mrn}")
    return patient_id


def create_order(
    conn: sqlite3.Connection,
    patient_id: int,
    accession_number: str,
    ordering_provider: str,
    priority: str = "ROUTINE",
    panel_code: str = PANEL_CODE,
) -> int:
    """Create an AML/MDS FISH order and return order_id."""
    panel_id = get_panel_id(conn, panel_code)
    order_id = execute(
        conn,
        "INSERT INTO lab_order "
        "(patient_id, panel_id, accession_number, priority, ordering_provider) "
        "VALUES (?, ?, ?, ?, ?)",
        (patient_id, panel_id, accession_number, priority, ordering_provider),
    )
    record_audit(
        conn,
        "lab_order",
        order_id,
        "ORDERED",
        order_id=order_id,
        detail=f"accession={accession_number} priority={priority}",
    )
    return order_id


def receive_specimen(
    conn: sqlite3.Connection,
    order_id: int,
    specimen_type: str = "BONE_MARROW",
    external_specimen_id: str | None = None,
    collected_at: str | None = None,
) -> int:
    """Record receipt of a specimen against an order and return specimen_id."""
    get_order(conn, order_id)  # existence check
    specimen_id = execute(
        conn,
        "INSERT INTO specimen "
        "(order_id, specimen_type, external_specimen_id, collected_at, status) "
        "VALUES (?, ?, ?, ?, 'RECEIVED')",
        (order_id, specimen_type, external_specimen_id, collected_at),
    )
    execute(
        conn,
        "UPDATE lab_order SET status = 'IN_PROCESS', collected_at = ? "
        "WHERE order_id = ? AND status = 'ORDERED'",
        (collected_at, order_id),
    )
    record_audit(
        conn,
        "specimen",
        specimen_id,
        "RECEIVED",
        order_id=order_id,
        detail=f"type={specimen_type}",
    )
    return specimen_id


def accession_specimen(conn: sqlite3.Connection, specimen_id: int) -> None:
    """Accept and accession a received specimen."""
    row = conn.execute(
        "SELECT order_id, status FROM specimen WHERE specimen_id = ?",
        (specimen_id,),
    ).fetchone()
    if row is None:
        raise WorkflowError(f"Specimen {specimen_id} does not exist.")
    if row["status"] != "RECEIVED":
        raise WorkflowError(
            f"Specimen {specimen_id} is '{row['status']}', cannot accession."
        )
    execute(
        conn,
        "UPDATE specimen SET status = 'ACCESSIONED' WHERE specimen_id = ?",
        (specimen_id,),
    )
    record_audit(
        conn, "specimen", specimen_id, "ACCESSIONED", order_id=row["order_id"]
    )


def reject_specimen(
    conn: sqlite3.Connection, specimen_id: int, reason: str
) -> None:
    """Reject a received specimen with a reason."""
    row = conn.execute(
        "SELECT order_id FROM specimen WHERE specimen_id = ?", (specimen_id,)
    ).fetchone()
    if row is None:
        raise WorkflowError(f"Specimen {specimen_id} does not exist.")
    execute(
        conn,
        "UPDATE specimen SET status = 'REJECTED', rejection_reason = ? "
        "WHERE specimen_id = ?",
        (reason, specimen_id),
    )
    record_audit(
        conn,
        "specimen",
        specimen_id,
        "REJECTED",
        order_id=row["order_id"],
        detail=reason,
    )


def enter_fish_result(
    conn: sqlite3.Connection,
    order_id: int,
    probe_code: str,
    cells_scored: int,
    cells_abnormal: int,
    signal_pattern: str,
    interpretation: str,
    entered_by: str,
) -> int:
    """Enter (or replace) a structured per-probe FISH result; return result_id.

    Re-entering a probe updates the existing result (one result per probe/order).
    Entering results is only allowed while the order is in process or already
    pending review — not after finalization.
    """
    order = get_order(conn, order_id)
    if order["status"] in ("FINALIZED", "CANCELLED"):
        raise WorkflowError(
            f"Cannot enter results: order {order_id} is {order['status']}."
        )
    probe_id = get_probe_id(conn, probe_code)
    result_id = execute(
        conn,
        "INSERT INTO fish_result "
        "(order_id, probe_id, cells_scored, cells_abnormal, signal_pattern, "
        " interpretation, entered_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(order_id, probe_id) DO UPDATE SET "
        "  cells_scored = excluded.cells_scored, "
        "  cells_abnormal = excluded.cells_abnormal, "
        "  signal_pattern = excluded.signal_pattern, "
        "  interpretation = excluded.interpretation, "
        "  entered_by = excluded.entered_by, "
        "  entered_at = datetime('now')",
        (
            order_id,
            probe_id,
            cells_scored,
            cells_abnormal,
            signal_pattern,
            interpretation,
            entered_by,
        ),
    )
    # Move ORDERED/IN_PROCESS orders forward to PENDING_REVIEW once results flow.
    execute(
        conn,
        "UPDATE lab_order SET status = 'PENDING_REVIEW' "
        "WHERE order_id = ? AND status IN ('ORDERED', 'IN_PROCESS')",
        (order_id,),
    )
    record_audit(
        conn,
        "fish_result",
        result_id,
        "RESULT_ENTERED",
        order_id=order_id,
        detail=f"probe={probe_code} interp={interpretation}",
    )
    return result_id


# ---------------------------------------------------------------------------
# Validation persistence + finalization
# ---------------------------------------------------------------------------

def _persist_findings(
    conn: sqlite3.Connection, order_id: int, findings: list[validation.Finding]
) -> None:
    """Replace stored validation findings for an order with the latest run."""
    execute(conn, "DELETE FROM validation_error WHERE order_id = ?", (order_id,))
    for f in findings:
        execute(
            conn,
            "INSERT INTO validation_error (order_id, rule_code, severity, message) "
            "VALUES (?, ?, ?, ?)",
            (order_id, f.rule_code, f.severity, f.message),
        )


def run_validation(
    conn: sqlite3.Connection, order_id: int
) -> list[validation.Finding]:
    """Run validation for an order, persist the findings, and audit the run."""
    get_order(conn, order_id)  # existence check
    findings = validation.validate_order(conn, order_id)
    _persist_findings(conn, order_id, findings)
    errors = sum(1 for f in findings if f.is_error)
    record_audit(
        conn,
        "lab_order",
        order_id,
        "VALIDATION_RUN",
        order_id=order_id,
        detail=f"findings={len(findings)} errors={errors}",
    )
    return findings


def finalize_order(
    conn: sqlite3.Connection, order_id: int, finalized_by: str
) -> FinalizeResult:
    """Validate then finalize an order.

    If validation produces any ERROR finding, finalization is blocked, the
    findings are recorded, and the order is left in its current state. If
    validation passes, a report is created, the order is marked FINALIZED, and
    the events are audited.
    """
    get_order(conn, order_id)
    findings = run_validation(conn, order_id)

    if validation.has_blocking_errors(findings):
        record_audit(
            conn,
            "lab_order",
            order_id,
            "FINALIZE_BLOCKED",
            order_id=order_id,
            detail=f"{sum(1 for f in findings if f.is_error)} blocking error(s)",
        )
        return FinalizeResult(order_id, finalized=False, findings=findings)

    summary = reports.build_report_summary(conn, order_id)
    report_id = execute(
        conn,
        "INSERT INTO report "
        "(order_id, summary_text, status, finalized_by, finalized_at) "
        "VALUES (?, ?, 'FINALIZED', ?, datetime('now'))",
        (order_id, summary, finalized_by),
    )
    execute(
        conn,
        "UPDATE lab_order SET status = 'FINALIZED', finalized_at = datetime('now') "
        "WHERE order_id = ?",
        (order_id,),
    )
    record_audit(
        conn,
        "report",
        report_id,
        "REPORT_FINALIZED",
        order_id=order_id,
        actor=finalized_by,
    )
    record_audit(
        conn,
        "lab_order",
        order_id,
        "FINALIZED",
        order_id=order_id,
        actor=finalized_by,
    )
    return FinalizeResult(
        order_id, finalized=True, findings=findings, report_id=report_id
    )
