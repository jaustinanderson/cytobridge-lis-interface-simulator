"""Report generation for CytoBridge.

v1 provides a plain-text report summary built from structured FISH results.
It also defines the seam where a future ISCN (International System for human
Cytogenetic Nomenclature) parser will plug in, so that karyotype/ISCN strings
can be validated and turned into structured findings later without reworking
callers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field


def build_report_summary(conn: sqlite3.Connection, order_id: int) -> str:
    """Build a human-readable summary of an order's FISH results.

    Lists each probe with its scored/abnormal cell counts and interpretation,
    then an overall line noting whether any probe was abnormal.
    """
    header = conn.execute(
        "SELECT o.accession_number, o.priority, pa.panel_name, "
        "       pt.mrn, pt.last_name, pt.first_name "
        "FROM lab_order o "
        "JOIN panel pa   ON pa.panel_id = o.panel_id "
        "JOIN patient pt ON pt.patient_id = o.patient_id "
        "WHERE o.order_id = ?",
        (order_id,),
    ).fetchone()
    if header is None:
        raise ValueError(f"Order {order_id} does not exist.")

    rows = conn.execute(
        "SELECT pr.probe_code, pr.probe_name, pr.target, "
        "       fr.cells_scored, fr.cells_abnormal, fr.interpretation, "
        "       fr.signal_pattern "
        "FROM fish_result fr "
        "JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? "
        "ORDER BY pr.probe_id",
        (order_id,),
    ).fetchall()

    lines: list[str] = []
    lines.append(f"{header['panel_name']} — Accession {header['accession_number']}")
    lines.append(
        f"Patient: {header['last_name']}, {header['first_name']} "
        f"(MRN {header['mrn']})   Priority: {header['priority']}"
    )
    lines.append("")
    lines.append("Probe results:")

    abnormal_targets: list[str] = []
    for r in rows:
        pct = (
            (100.0 * r["cells_abnormal"] / r["cells_scored"])
            if r["cells_scored"]
            else 0.0
        )
        lines.append(
            f"  - {r['probe_name']} ({r['target']}): "
            f"{r['cells_abnormal']}/{r['cells_scored']} cells "
            f"({pct:.1f}%) — {r['interpretation']}"
        )
        if r["interpretation"] == "ABNORMAL":
            abnormal_targets.append(r["target"])

    lines.append("")
    if abnormal_targets:
        lines.append("Overall: ABNORMAL — " + "; ".join(abnormal_targets))
    else:
        lines.append("Overall: No abnormality detected by this FISH panel.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ISCN parser seam
# ---------------------------------------------------------------------------
#
# Cytogenetics reports frequently carry an ISCN nomenclature string, e.g.:
#   "46,XX,t(8;21)(q22;q22)[20]"
# A future session will implement a real parser that validates and decomposes
# these strings into structured clonal findings. For v1 we define the return
# shape and a stub so callers can wire against a stable interface today.


@dataclass
class ISCNParseResult:
    """Structured output of parsing an ISCN string (shape only in v1)."""

    raw: str
    is_valid: bool = False
    abnormalities: list[str] = field(default_factory=list)
    notes: str = "ISCN parsing not implemented in v1 (seam only)."


def parse_iscn(iscn_text: str) -> ISCNParseResult:
    """Seam for a future ISCN parser.

    v1 does not parse ISCN; it returns the raw string wrapped in the result
    shape with ``is_valid=False``. Replace this body in a later session with a
    real tokenizer/validator without changing the signature.
    """
    return ISCNParseResult(raw=iscn_text)
