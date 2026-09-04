"""Replay UAT-004 on synthetic in-memory data; no human acceptance claim.

Run from any directory with Python 3.11+ and Git. The application inputs must
match SOURCE_COMMIT; documentation and this helper may differ from that commit.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "2c5a69cbb364a85bcaa6ca4da1e39211de7cbf38"
sys.path.insert(0, str(ROOT))

def require(condition: bool, message: str) -> None:
    """Keep checks active even when Python runs with optimization enabled."""
    if not condition:
        raise RuntimeError(message)


def replay() -> dict:
    # Pin application inputs, not this helper's future integration commit.
    # Missing history or changed inputs stops the replay before database work.
    subprocess.run(
        ["git", "diff", "--quiet", SOURCE_COMMIT, "--", "src", "schema.sql"],
        cwd=ROOT, check=True,
    )
    untracked = subprocess.check_output(
        ["git", "ls-files", "--others", "--exclude-standard", "src"],
        cwd=ROOT, text=True,
    )
    require(not untracked.strip(), "Untracked application inputs require review")
    from src import workflow
    from src.db import create_database
    from src.interfaces import OutboundError, outbound_fhir

    conn = create_database(":memory:")
    try:
        pid = workflow.create_patient(
            conn, "SYN-UAT004-20260904", "Synthetic", "Replay", "1980-01-01", "U"
        )
        oid = workflow.create_order(
            conn, pid, "ACC-UAT004-20260904", "Synthetic Provider"
        )
        sid = workflow.receive_specimen(conn, oid)
        workflow.accession_specimen(conn, sid)
        try:
            outbound_fhir.build_diagnostic_report(conn, oid)
        except OutboundError as exc:
            rejection = str(exc)
        else:
            raise RuntimeError("Non-finalized order was incorrectly exportable")
        require(
            conn.execute("SELECT COUNT(*) FROM interface_message").fetchone()[0] == 0,
            "Rejected export unexpectedly stored an interface message",
        )

        results = [
            ("RUNX1T1_RUNX1", 200, 38, "2F1R1G", "ABNORMAL"),
            ("CBFB", 200, 1, "2 orange/green", "NORMAL"),
            ("PML_RARA", 200, 2, "2R2G", "NORMAL"),
            ("KMT2A", 200, 1, "2 fusion", "NORMAL"),
            ("EGR1_5q", 200, 3, "2 orange 2 green", "NORMAL"),
            ("D7S486_7q", 200, 2, "2 signals", "NORMAL"),
            ("CEP8", 200, 4, "2 aqua", "NORMAL"),
            ("D20S108_20q", 200, 1, "2 signals", "NORMAL"),
            ("TP53_17p", 200, 2, "2R2G", "NORMAL"),
        ]
        for row in results:
            workflow.enter_fish_result(conn, oid, *row, entered_by="synthetic-replay")
        final = workflow.finalize_order(conn, oid, finalized_by="synthetic-replay")
        require(final.finalized is True, "Synthetic setup did not finalize")
        stored_report = conn.execute(
            "SELECT summary_text FROM report WHERE order_id=?", (oid,)
        ).fetchone()[0]
        bundle = outbound_fhir.build_diagnostic_report(conn, oid)
        require(bundle["resourceType"] == "Bundle", "Expected Bundle")
        counts = Counter(e["resource"]["resourceType"] for e in bundle["entry"])
        require(counts == {"Patient": 1, "Specimen": 1, "Observation": 9,
                           "DiagnosticReport": 1}, "Unexpected resource counts")
        report = next(e["resource"] for e in bundle["entry"]
                      if e["resource"]["resourceType"] == "DiagnosticReport")
        require(report["status"] == "final", "Report status was not final")
        require(report["identifier"][0]["value"] == "ACC-UAT004-20260904",
                "Accession identifier did not match")
        refs = {e["fullUrl"] for e in bundle["entry"]
                if e["resource"]["resourceType"] == "Observation"}
        require(len(refs) == 9 and len(report["result"]) == 9
                and {r["reference"] for r in report["result"]} == refs,
                "DiagnosticReport must reference all nine distinct observations")
        require(report["conclusion"] == "Overall: ABNORMAL \u2014 t(8;21)",
                "Conclusion must contain only the expected overall impression")
        attachment = report["presentedForm"][0]
        require(attachment["contentType"] == "text/plain; charset=utf-8",
                "Unexpected presented-form content type")
        decoded = base64.b64decode(attachment["data"], validate=True).decode("utf-8")
        require(decoded == stored_report, "Presented form differs from stored report")
        require(json.loads(outbound_fhir.generate_diagnostic_report_json(conn, oid))
                == bundle, "Serialized Bundle differs")
        mid = outbound_fhir.store_diagnostic_report(conn, oid)
        message = dict(conn.execute(
            "SELECT * FROM interface_message WHERE message_id=?", (mid,)
        ).fetchone())
        require(message["direction"] == "OUTBOUND" and message["format"] == "FHIR"
                and message["status"] == "GENERATED", "Stored message metadata differs")
        require(json.loads(message.pop("payload")) == bundle, "Stored payload differs")
        require(conn.execute("PRAGMA foreign_key_check").fetchall() == [],
                "Foreign-key integrity failed")
        return {
            "observed_at_utc": datetime.now(timezone.utc).isoformat(),
            "application_source_commit": SOURCE_COMMIT,
            "procedure": "UAT-004 (R-009), with UAT-001 setup",
            "execution_type": "automated functional replay; not human acceptance",
            "result": "PASS",
            "resource_counts": dict(counts),
            "observation_reference_count": len(refs),
            "diagnostic_report_status": report["status"],
            "conclusion": report["conclusion"],
            "presented_form_content_type": attachment["contentType"],
            "presented_form_matches_stored_report": True,
            "presented_form_sha256": hashlib.sha256(decoded.encode()).hexdigest(),
            "decoded_presented_form": decoded,
            "stored_interface_message": message,
            "negative_result": {"exception": "OutboundError", "detail": rejection,
                                "message_count_after_rejection": 0},
            "json_roundtrip_and_stored_payload_match": True,
            "foreign_key_check": [],
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(replay(), indent=2, ensure_ascii=True))
