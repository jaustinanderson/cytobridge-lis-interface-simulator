"""Outbound interface generation for CytoBridge.

This package turns a **finalized** AML/MDS FISH order into outbound
interface messages:

    outbound_hl7.py   an HL7 ORU^R01-style pipe-delimited text message
    outbound_fhir.py  a FHIR R4-style DiagnosticReport JSON Bundle

Both generators pull the same underlying data through
``collect_report_data`` (defined here) so the HL7 and FHIR outputs stay in
lock-step, and both can persist the result to the ``interface_message`` table
via ``store_message``.

Design notes
------------
- **Educational simulator, not a certified engine.** The messages are
  *shaped like* HL7 v2 ORU and FHIR R4 DiagnosticReport so an analyst can read
  the mapping, but this is not a conformance-tested HL7/FHIR implementation.
- **Outbound only, finalized only.** ``collect_report_data`` refuses to build a
  message for a non-finalized order and fails loudly when required data
  (order / report / specimen / results) is missing, so a caller can never emit
  a silently incomplete report.
- **Synthetic data only. No PHI.** Everything downstream is derived from the
  synthetic records seeded by the workflow.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..db import execute


class OutboundError(Exception):
    """Raised when an outbound message cannot be generated for an order.

    Covers both "order is not in an exportable state" (e.g. not finalized) and
    "required data is missing" (no report, specimen, or results). The message
    always names the order and the reason so the failure is never silent.
    """


# HL7/FHIR display + coding for the specimen types this simulator supports.
# (code, human display) — synthetic local codes, not a clinical value set.
SPECIMEN_TYPE = {
    "BONE_MARROW": ("BMA", "Bone Marrow"),
    "PERIPHERAL_BLOOD": ("PB", "Peripheral Blood"),
}

# HL7 abnormal-flag / FHIR interpretation code per structured interpretation.
INTERPRETATION_FLAG = {
    "NORMAL": "N",
    "ABNORMAL": "A",
    "INDETERMINATE": "I",
}


@dataclass(frozen=True)
class ProbeResult:
    """One per-probe FISH observation, ready for HL7/FHIR rendering."""

    probe_code: str
    probe_name: str
    locus: str
    target: str
    cells_scored: int
    cells_abnormal: int
    percent_abnormal: float
    signal_pattern: str
    interpretation: str
    abnormal_cutoff_percent: float

    @property
    def flag(self) -> str:
        """HL7 abnormal flag / FHIR interpretation code (A / N / I)."""
        return INTERPRETATION_FLAG.get(self.interpretation, "N")


@dataclass(frozen=True)
class OutboundReportData:
    """Everything the HL7 and FHIR generators need for one finalized order.

    Built by ``collect_report_data``; both generators consume this so the two
    outbound formats describe exactly the same report.
    """

    order_id: int
    accession_number: str
    priority: str
    ordering_provider: str
    ordered_at: str | None
    collected_at: str | None
    finalized_at: str | None
    panel_code: str
    panel_name: str
    specimen_type: str
    external_specimen_id: str | None
    received_at: str | None
    mrn: str
    last_name: str
    first_name: str
    date_of_birth: str
    sex: str
    report_status: str
    summary_text: str
    results: list[ProbeResult]

    @property
    def abnormal_results(self) -> list[ProbeResult]:
        return [r for r in self.results if r.interpretation == "ABNORMAL"]

    @property
    def is_abnormal(self) -> bool:
        return bool(self.abnormal_results)

    @property
    def specimen_code(self) -> str:
        return SPECIMEN_TYPE.get(self.specimen_type, ("UNK", "Unknown"))[0]

    @property
    def specimen_display(self) -> str:
        return SPECIMEN_TYPE.get(self.specimen_type, ("UNK", "Unknown"))[1]


def collect_report_data(
    conn: sqlite3.Connection, order_id: int
) -> OutboundReportData:
    """Gather all data for an outbound message, enforcing export preconditions.

    Raises ``OutboundError`` (never returns a partial object) when:
      - the order does not exist,
      - the order is not ``FINALIZED``,
      - no finalized report row exists,
      - no specimen is on file, or
      - no FISH results are on file.
    """
    order = conn.execute(
        "SELECT o.order_id, o.accession_number, o.priority, o.status, "
        "       o.ordering_provider, o.ordered_at, o.collected_at, o.finalized_at, "
        "       pa.panel_code, pa.panel_name, "
        "       pt.mrn, pt.last_name, pt.first_name, pt.date_of_birth, pt.sex "
        "FROM lab_order o "
        "JOIN panel pa   ON pa.panel_id = o.panel_id "
        "JOIN patient pt ON pt.patient_id = o.patient_id "
        "WHERE o.order_id = ?",
        (order_id,),
    ).fetchone()
    if order is None:
        raise OutboundError(
            f"Cannot export order {order_id}: order does not exist."
        )
    if order["status"] != "FINALIZED":
        raise OutboundError(
            f"Cannot export order {order_id}: status is '{order['status']}'. "
            "Outbound messages are only generated for FINALIZED orders."
        )

    report = conn.execute(
        "SELECT summary_text, status FROM report WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    if report is None:
        raise OutboundError(
            f"Cannot export order {order_id}: no report row for a finalized order."
        )
    if report["status"] != "FINALIZED":
        raise OutboundError(
            f"Cannot export order {order_id}: report status is "
            f"'{report['status']}', not FINALIZED."
        )

    specimen = conn.execute(
        "SELECT specimen_type, external_specimen_id, received_at "
        "FROM specimen WHERE order_id = ? ORDER BY specimen_id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    if specimen is None:
        raise OutboundError(
            f"Cannot export order {order_id}: no specimen on file."
        )

    result_rows = conn.execute(
        "SELECT pr.probe_code, pr.probe_name, pr.locus, pr.target, "
        "       pr.abnormal_cutoff_percent, "
        "       fr.cells_scored, fr.cells_abnormal, fr.signal_pattern, "
        "       fr.interpretation "
        "FROM fish_result fr "
        "JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? "
        "ORDER BY pr.probe_id",
        (order_id,),
    ).fetchall()
    if not result_rows:
        raise OutboundError(
            f"Cannot export order {order_id}: no FISH results on file."
        )

    results: list[ProbeResult] = []
    for r in result_rows:
        scored = r["cells_scored"]
        abnormal = r["cells_abnormal"]
        pct = round(100.0 * abnormal / scored, 1) if scored else 0.0
        results.append(
            ProbeResult(
                probe_code=r["probe_code"],
                probe_name=r["probe_name"],
                locus=r["locus"],
                target=r["target"],
                cells_scored=scored,
                cells_abnormal=abnormal,
                percent_abnormal=pct,
                signal_pattern=r["signal_pattern"],
                interpretation=r["interpretation"],
                abnormal_cutoff_percent=r["abnormal_cutoff_percent"],
            )
        )

    return OutboundReportData(
        order_id=order["order_id"],
        accession_number=order["accession_number"],
        priority=order["priority"],
        ordering_provider=order["ordering_provider"],
        ordered_at=order["ordered_at"],
        collected_at=order["collected_at"],
        finalized_at=order["finalized_at"],
        panel_code=order["panel_code"],
        panel_name=order["panel_name"],
        specimen_type=specimen["specimen_type"],
        external_specimen_id=specimen["external_specimen_id"],
        received_at=specimen["received_at"],
        mrn=order["mrn"],
        last_name=order["last_name"],
        first_name=order["first_name"],
        date_of_birth=order["date_of_birth"],
        sex=order["sex"],
        report_status=report["status"],
        summary_text=report["summary_text"],
        results=results,
    )


def store_message(
    conn: sqlite3.Connection,
    *,
    direction: str,
    message_type: str,
    message_format: str,
    order_id: int,
    control_id: str,
    payload: str,
    status: str = "GENERATED",
    commit: bool = True,
) -> int:
    """Persist a generated message to ``interface_message`` and return its id.

    The existing schema already carries outbound messages (``direction`` allows
    ``OUTBOUND``; ``format`` allows ``HL7``/``FHIR``), so no schema change is
    needed to store what these generators produce.

    ``commit`` defaults to ``True`` (unchanged behavior); pass ``commit=False``
    to store the message inside the caller's open transaction so a multi-step
    operation commits or rolls back as a unit.
    """
    return execute(
        conn,
        "INSERT INTO interface_message "
        "(direction, message_type, format, order_id, control_id, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (direction, message_type, message_format, order_id, control_id,
         payload, status),
        commit=commit,
    )


# Re-export the public generators. Imported at the bottom so the shared names
# above are defined before the submodules import them (avoids a circular import).
from .outbound_hl7 import generate_oru, store_oru  # noqa: E402
from .outbound_fhir import (  # noqa: E402
    build_diagnostic_report,
    generate_diagnostic_report_json,
    store_diagnostic_report,
)
from .inbound_hl7 import (  # noqa: E402
    InboundError,
    IngestResult,
    ingest_file,
    ingest_message,
    parse_message,
)

__all__ = [
    "OutboundError",
    "OutboundReportData",
    "ProbeResult",
    "SPECIMEN_TYPE",
    "INTERPRETATION_FLAG",
    "collect_report_data",
    "store_message",
    "generate_oru",
    "store_oru",
    "build_diagnostic_report",
    "generate_diagnostic_report_json",
    "store_diagnostic_report",
    "InboundError",
    "IngestResult",
    "ingest_file",
    "ingest_message",
    "parse_message",
]
