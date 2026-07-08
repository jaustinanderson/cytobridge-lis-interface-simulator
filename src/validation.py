"""Validation rules for CytoBridge orders.

``validate_order`` inspects an order's specimen and per-probe FISH results and
returns a list of findings. Findings with severity ``ERROR`` block
finalization; ``WARNING`` findings are advisory and do not block.

Rules are intentionally explicit and each carries a stable ``rule_code`` so
they can be traced back to requirements and covered by tests.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

# Labs typically require a minimum number of scored nuclei per FISH probe.
# Synthetic threshold for this simulator.
MIN_CELLS_SCORED = 100


@dataclass(frozen=True)
class Finding:
    """A single validation result."""

    rule_code: str
    severity: str  # 'ERROR' or 'WARNING'
    message: str

    @property
    def is_error(self) -> bool:
        return self.severity == "ERROR"


def _required_probes(conn: sqlite3.Connection, panel_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT probe_id, probe_code, probe_name FROM probe "
        "WHERE panel_id = ? AND is_required = 1 ORDER BY probe_id",
        (panel_id,),
    ).fetchall()


def validate_order(conn: sqlite3.Connection, order_id: int) -> list[Finding]:
    """Return all validation findings for an order.

    Checks, in order:
      SPEC_ACCESSIONED   specimen received and accessioned (not rejected/missing)
      MISSING_PROBE      every required probe has a result (ERROR — blocks finalize)
      CELL_COUNT_LOW     each result meets the minimum scored-cell count
      ABN_EXCEEDS_SCORED abnormal cells never exceed scored cells
      INTERP_CONSISTENCY interpretation matches percent-abnormal vs the probe cutoff

    The consistency check is cutoff-aware: a percent-abnormal at or above the
    probe's abnormal_cutoff_percent that is still called NORMAL is a blocking
    ERROR (a missed abnormal), while an ABNORMAL call below cutoff is a WARNING
    (background-level signal called abnormal).
    """
    findings: list[Finding] = []

    order = conn.execute(
        "SELECT order_id, panel_id FROM lab_order WHERE order_id = ?",
        (order_id,),
    ).fetchone()
    if order is None:
        return [Finding("ORDER_NOT_FOUND", "ERROR", f"Order {order_id} does not exist.")]

    # --- Specimen must be accessioned -------------------------------------
    specimen = conn.execute(
        "SELECT status FROM specimen WHERE order_id = ? ORDER BY specimen_id DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    if specimen is None:
        findings.append(
            Finding("SPEC_ACCESSIONED", "ERROR", "No specimen received for order.")
        )
    elif specimen["status"] == "REJECTED":
        findings.append(
            Finding("SPEC_ACCESSIONED", "ERROR", "Specimen was rejected.")
        )
    elif specimen["status"] != "ACCESSIONED":
        findings.append(
            Finding(
                "SPEC_ACCESSIONED",
                "ERROR",
                f"Specimen is '{specimen['status']}', not ACCESSIONED.",
            )
        )

    # --- Every required probe must have a result --------------------------
    results = {
        r["probe_id"]: r
        for r in conn.execute(
            "SELECT fr.probe_id, fr.cells_scored, fr.cells_abnormal, "
            "       fr.interpretation, pr.probe_code, pr.abnormal_cutoff_percent "
            "FROM fish_result fr "
            "JOIN probe pr ON pr.probe_id = fr.probe_id "
            "WHERE fr.order_id = ?",
            (order_id,),
        ).fetchall()
    }
    for probe in _required_probes(conn, order["panel_id"]):
        if probe["probe_id"] not in results:
            findings.append(
                Finding(
                    "MISSING_PROBE",
                    "ERROR",
                    f"Required probe {probe['probe_code']} "
                    f"({probe['probe_name']}) has no result.",
                )
            )

    # --- Per-result data-integrity checks ---------------------------------
    for r in results.values():
        code = r["probe_code"]
        scored = r["cells_scored"]
        abnormal = r["cells_abnormal"]
        interp = r["interpretation"]
        cutoff = r["abnormal_cutoff_percent"]
        pct = (100.0 * abnormal / scored) if scored else 0.0

        if scored < MIN_CELLS_SCORED:
            findings.append(
                Finding(
                    "CELL_COUNT_LOW",
                    "WARNING",
                    f"Probe {code}: only {scored} cells scored "
                    f"(minimum {MIN_CELLS_SCORED}).",
                )
            )

        # The schema CHECK also guards this; validated here for defense in depth.
        if abnormal > scored:
            findings.append(
                Finding(
                    "ABN_EXCEEDS_SCORED",
                    "ERROR",
                    f"Probe {code}: abnormal cells ({abnormal}) "
                    f"exceed scored cells ({scored}).",
                )
            )

        # At/above cutoff but called NORMAL: a missed abnormal (blocking).
        if pct >= cutoff and interp == "NORMAL":
            findings.append(
                Finding(
                    "INTERP_CONSISTENCY",
                    "ERROR",
                    f"Probe {code}: {pct:.1f}% abnormal is at/above the "
                    f"{cutoff:.1f}% cutoff but interpretation is NORMAL.",
                )
            )
        # Below cutoff but called ABNORMAL: background-level call (advisory).
        if pct < cutoff and interp == "ABNORMAL":
            findings.append(
                Finding(
                    "INTERP_CONSISTENCY",
                    "WARNING",
                    f"Probe {code}: {pct:.1f}% abnormal is below the "
                    f"{cutoff:.1f}% cutoff but interpretation is ABNORMAL.",
                )
            )

    return findings


def has_blocking_errors(findings: list[Finding]) -> bool:
    """True if any finding is an ERROR (i.e. finalization must be blocked)."""
    return any(f.is_error for f in findings)
