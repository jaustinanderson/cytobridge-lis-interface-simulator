"""Tests for the analyst-lab case evidence export (``src/analyst/evidence.py``).

Databases are built only through the public services (workflow, inbound
ingestion, outbound export, recovery). Payload fingerprints are checked against
``hashlib.sha256`` computed here from the fixture FILE BYTES under
``exercises/EX03-invalid-result-redrive/inputs`` and
``sample_messages/recovery``. Direct SQL is used only on throwaway copies to
build tampered states. All data is synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path

import pytest

from src import recovery, workflow
from src.analyst import evidence, store
from src.analyst.common import (
    LIMITATIONS,
    REPO_ROOT,
    SYNTHETIC_NOTICE,
    AnalystError,
    canonical_json,
)
from src.db import create_database
from src.interfaces import inbound_hl7, outbound_fhir, outbound_hl7

EX03_INPUTS = REPO_ROOT / "exercises" / "EX03-invalid-result-redrive" / "inputs"
EX03_ORIGINAL = EX03_INPUTS / "original_result.hl7"
EX03_CORRECTED = EX03_INPUTS / "corrected_result.hl7"
RC05_ORIGINAL = REPO_ROOT / "sample_messages" / "recovery" / "original" / "05_order_not_found.hl7"
RC05_ACCESSION = "ACC-REC-0500-NOMATCH"

EX03_ACCESSION = "ACC-EX03-0001"
SCENARIO_ID = "EX03-invalid-result-redrive"
SCENARIO_TITLE = "Invalid result value and corrected re-drive"
TEST_REVISION = "0123456789abcdef0123456789abcdef01234567"

REQ_REJECTED = "EVD-RETRY-REJECTED-1"
REQ_FAILED = "EVD-REDRIVE-FAILED-1"
REQ_SUCCEEDED = "EVD-REDRIVE-OK-1"
REQ_RC05_RETRY = "EVD-RC05-RETRY-1"


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_text(path: Path) -> str:
    """Exact fixture text: strict UTF-8, no newline translation."""
    return path.read_bytes().decode("utf-8")


# ---------------------------------------------------------------------------
# Synthetic builders (public services only)
# ---------------------------------------------------------------------------

def build_ex03(conn, *, actor: str = "analyst01", corrected: str | None = None) -> dict:
    """EX03 plus a blocked finalize, a REJECTED retry, a FAILED and a SUCCEEDED
    re-drive, a request-id conflict, finalization, and outbound exports.

    Returns the ids the tests need.
    """
    patient_id = workflow.create_patient(
        conn, "SYN-EX03-01", "Synthetic", "ExerciseThree", "1970-01-03", "F"
    )
    order_id = workflow.create_order(conn, patient_id, EX03_ACCESSION, "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id, "BONE_MARROW", "BM-EX03-01")
    workflow.accession_specimen(conn, specimen_id)
    # Blocked finalize: stores MISSING_PROBE validation_error rows.
    assert not workflow.finalize_order(conn, order_id, "pathologist01").finalized

    ingest = inbound_hl7.ingest_message(conn, fixture_text(EX03_ORIGINAL))
    assert not ingest.filed
    queue_id = ingest.queue_id

    rejected = recovery.retry_queue_item(conn, queue_id, request_id=REQ_REJECTED, actor=actor)
    assert rejected.outcome == "REJECTED"
    failed = recovery.redrive_queue_item(
        conn, queue_id, fixture_text(EX03_ORIGINAL), request_id=REQ_FAILED, actor=actor
    )
    assert failed.outcome == "FAILED"
    corrected_text = fixture_text(EX03_CORRECTED) if corrected is None else corrected
    succeeded = recovery.redrive_queue_item(
        conn, queue_id, corrected_text, request_id=REQ_SUCCEEDED, actor=actor
    )
    assert succeeded.outcome == "SUCCEEDED"
    # Reusing a request id with a different payload: REQUEST_ID_CONFLICT audit.
    with pytest.raises(recovery.RequestIdConflictError):
        recovery.redrive_queue_item(
            conn, queue_id, fixture_text(EX03_ORIGINAL), request_id=REQ_SUCCEEDED, actor=actor
        )

    assert workflow.finalize_order(conn, order_id, "pathologist01").finalized
    # No generated_at: the stored outbound payloads embed the current time.
    outbound_hl7.store_oru(conn, order_id)
    outbound_fhir.store_diagnostic_report(conn, order_id)
    return {
        "order_id": order_id,
        "queue_id": queue_id,
        "attempts": [rejected, failed, succeeded],
    }


def build_rc05_retry(conn) -> dict:
    """RC-05: ORDER_NOT_FOUND, then the order is created and an unchanged retry files."""
    ingest = inbound_hl7.ingest_message(conn, fixture_text(RC05_ORIGINAL))
    assert not ingest.filed
    patient_id = workflow.create_patient(
        conn, "SYN-8501", "Synthetic", "Recovery", "1970-01-01", "M"
    )
    order_id = workflow.create_order(conn, patient_id, RC05_ACCESSION, "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id, "BONE_MARROW", "BM-8501")
    workflow.accession_specimen(conn, specimen_id)
    attempt = recovery.retry_queue_item(
        conn, ingest.queue_id, request_id=REQ_RC05_RETRY, actor="analyst02"
    )
    assert attempt.outcome == "SUCCEEDED"
    return {"order_id": order_id, "queue_id": ingest.queue_id, "attempt": attempt}


@pytest.fixture()
def ex03():
    conn = create_database(":memory:")
    ids = build_ex03(conn)
    yield conn, ids
    conn.close()


def file_db(tmp_path: Path, name: str, builder) -> tuple[Path, dict]:
    """A file database created with store.init_database and filled by ``builder``."""
    path = store.init_database(tmp_path / name)
    with closing(store.open_read_write(path)) as conn:
        ids = builder(conn)
    return path, ids


def entry(ev: dict, subject: str, role: str) -> dict:
    matches = [f for f in ev["fingerprints"] if f["subject"] == subject and f["role"] == role]
    assert len(matches) == 1, (subject, role)
    return matches[0]


def checks(fp_entry: dict) -> dict[str, bool]:
    return {c["check"]: c["passed"] for c in fp_entry["checks"]}


# ---------------------------------------------------------------------------
# (1) JSON parses and is byte-identical across builds from the same database
# ---------------------------------------------------------------------------

def test_json_is_ascii_parses_and_is_byte_identical_across_builds(ex03):
    conn, ids = ex03
    for selector in ({"queue_id": ids["queue_id"]}, {"order_id": ids["order_id"]}):
        first = canonical_json(evidence.build_evidence(
            conn, **selector, scenario_id=SCENARIO_ID, scenario_title=SCENARIO_TITLE,
            source_revision=TEST_REVISION,
        ))
        second = canonical_json(evidence.build_evidence(
            conn, **selector, scenario_id=SCENARIO_ID, scenario_title=SCENARIO_TITLE,
            source_revision=TEST_REVISION,
        ))
        assert first == second
        assert first.isascii()
        parsed = json.loads(first)
        assert parsed["format"] == evidence.EVIDENCE_FORMAT
        assert parsed["anchor"]["id"] == next(iter(selector.values()))
        # The fingerprint is recomputable from the exported JSON alone.
        assert evidence.content_fingerprint(parsed) == parsed["content_fingerprint"]


def test_evidence_sections_and_source(ex03):
    conn, ids = ex03
    ev = evidence.build_evidence(conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION)
    assert set(ev) == {
        "format", "synthetic", "notice", "source", "scenario", "anchor",
        "relationships", "records", "fingerprints", "validations", "outcomes",
        "recovery_history", "timeline", "reconciliation", "provenance",
        "limitations", "content_fingerprint",
    }
    assert ev["source"] == {
        "revision": TEST_REVISION,
        "schema_sha256": file_sha256(REPO_ROOT / "schema.sql"),
        "generator": "src.analyst.evidence",
    }
    assert ev["scenario"] == {"id": None, "title": None}
    assert ev["anchor"] == {"kind": "queue", "id": ids["queue_id"]}
    assert set(ev["records"]) == {
        "orders", "messages", "queue_items", "recovery_attempts", "audit_events",
    }
    assert ev["recovery_history"] == ev["records"]["recovery_attempts"]
    assert [a["request_id"] for a in ev["recovery_history"]] == [
        REQ_REJECTED, REQ_FAILED, REQ_SUCCEEDED,
    ]
    assert ev["outcomes"]["attempts"] == [
        {"attempt_id": a.attempt_id, "request_id": a.request_id,
         "action": a.action, "outcome": a.outcome}
        for a in ids["attempts"]
    ]
    assert ev["outcomes"]["orders"] == [
        {"order_id": ids["order_id"], "accession_number": EX03_ACCESSION, "status": "FINALIZED"}
    ]
    assert ev["outcomes"]["queue_items"] == [
        {"queue_id": ids["queue_id"], "failure_code": "INVALID_CELL_COUNT", "status": "RESOLVED"}
    ]
    # Current validation is clean; the stored rows are the later successful
    # finalize run (workflow.run_validation replaces findings on each run).
    (val,) = ev["validations"]
    assert val["order_id"] == ids["order_id"]
    assert val["current_findings"] == [] and val["blocking"] is False
    stored = conn.execute(
        "SELECT error_id, rule_code, severity, message FROM validation_error "
        "WHERE order_id = ? ORDER BY error_id", (ids["order_id"],),
    ).fetchall()
    assert val["stored_findings"] == [dict(r) for r in stored]
    # The request-id conflict surfaces as a REC-008 INFO finding.
    rule_ids = [f["rule_id"] for f in ev["reconciliation"]["findings"]]
    assert "REC-008" in rule_ids
    assert ev["reconciliation"]["summary"]["ERROR"] == 0
    codes = [lim["code"] for lim in ev["limitations"]]
    assert "EDUCATIONAL_INVARIANTS" in codes
    assert {"RESULTS_UPSERTED", "NO_RESULT_SOURCE_FK", "TIMESTAMP_RESOLUTION"} <= set(codes)


def test_blocking_validation_is_reported_for_an_open_order():
    conn = create_database(":memory:")
    patient_id = workflow.create_patient(conn, "SYN-EVD-02", "Synthetic", "Evd", "1970-01-01", "U")
    order_id = workflow.create_order(conn, patient_id, "ACC-EVD-0002", "Dr. Synthetic")
    workflow.receive_specimen(conn, order_id)
    assert not workflow.finalize_order(conn, order_id, "pathologist01").finalized
    ev = evidence.build_evidence(conn, order_id=order_id, source_revision=TEST_REVISION)
    (val,) = ev["validations"]
    assert val["blocking"] is True
    current = {(f["rule_code"], f["severity"]) for f in val["current_findings"]}
    assert ("SPEC_ACCESSIONED", "ERROR") in current
    assert ("MISSING_PROBE", "ERROR") in current
    assert len(val["stored_findings"]) == len(val["current_findings"])
    conn.close()


# ---------------------------------------------------------------------------
# (2) Lists are deterministically ordered
# ---------------------------------------------------------------------------

SEVERITY_RANK = {"ERROR": 0, "WARNING": 1, "INFO": 2}


def assert_sorted(items, key):
    keys = [key(i) for i in items]
    assert keys == sorted(keys)


def test_every_list_has_its_documented_order(ex03):
    conn, ids = ex03
    rc05 = build_rc05_retry(conn)
    for selector in (
        {"queue_id": ids["queue_id"]},
        {"order_id": ids["order_id"]},
        {"queue_id": rc05["queue_id"]},
    ):
        ev = evidence.build_evidence(conn, **selector, source_revision=TEST_REVISION)
        rec = ev["records"]
        assert len(rec["messages"]) >= 2
        assert_sorted(ev["relationships"], lambda l: (l["from"], l["to"], l["relation"]))
        assert_sorted(rec["orders"], lambda o: o["order_id"])
        assert_sorted(rec["messages"], lambda m: m["message_id"])
        assert_sorted(rec["queue_items"], lambda q: q["queue_id"])
        assert_sorted(rec["recovery_attempts"], lambda a: a["attempt_id"])
        assert_sorted(rec["audit_events"], lambda a: a["event_id"])
        for o in rec["orders"]:
            assert_sorted(o["specimens"], lambda s: s["specimen_id"])
            assert_sorted(o["current_results"], lambda r: r["probe_id"])
            assert_sorted(o["stored_validation_errors"], lambda v: v["error_id"])
        assert_sorted(ev["fingerprints"], lambda f: (f["subject"], f["role"]))
        for f in ev["fingerprints"]:
            assert_sorted(f["checks"], lambda c: c["check"])
            assert f["role"] in evidence.FINGERPRINT_ROLES
        assert_sorted(ev["validations"], lambda v: v["order_id"])
        for v in ev["validations"]:
            assert_sorted(
                v["current_findings"], lambda f: (f["rule_code"], f["severity"], f["message"])
            )
            assert_sorted(v["stored_findings"], lambda f: f["error_id"])
        assert_sorted(ev["outcomes"]["orders"], lambda o: o["order_id"])
        assert_sorted(ev["outcomes"]["queue_items"], lambda q: q["queue_id"])
        assert_sorted(ev["outcomes"]["attempts"], lambda a: a["attempt_id"])
        assert_sorted(ev["recovery_history"], lambda a: a["attempt_id"])
        assert_sorted(
            ev["timeline"], lambda e: (e["at"], e["rank"], e["table"], e["id"], e["kind"])
        )
        assert_sorted(ev["reconciliation"]["findings"],
                      lambda f: (SEVERITY_RANK[f["severity"]], f["rule_id"], f["subjects"]))
        for f in ev["reconciliation"]["findings"]:
            assert f["subjects"] == sorted(f["subjects"])
        assert_sorted(ev["provenance"], lambda n: (n["subject"], n["code"], n["detail"]))
        assert_sorted(ev["limitations"], lambda lim: lim["code"])


# ---------------------------------------------------------------------------
# (3) Fingerprints equal sha256 of the fixture FILE BYTES; tampering fails a check
# ---------------------------------------------------------------------------

def test_fingerprints_match_fixture_file_bytes_and_checks_pass(ex03):
    conn, ids = ex03
    original_sha = file_sha256(EX03_ORIGINAL)
    corrected_sha = file_sha256(EX03_CORRECTED)
    ev = evidence.build_evidence(conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION)
    rejected, failed, succeeded = ids["attempts"]
    queue = conn.execute(
        "SELECT message_id FROM interface_error_queue WHERE queue_id = ?", (ids["queue_id"],)
    ).fetchone()
    original_ref = f"interface_message:{queue['message_id']}"

    orig = entry(ev, original_ref, "QUEUE_ORIGINAL")
    assert orig["sha256"] == original_sha
    assert orig["bytes"] == len(EX03_ORIGINAL.read_bytes())
    raw = entry(ev, f"interface_error_queue:{ids['queue_id']}", "QUEUE_RAW_COPY")
    assert raw["sha256"] == original_sha
    assert raw["bytes"] == len(EX03_ORIGINAL.read_bytes())
    assert checks(raw) == {evidence.CHECK_RAW_COPY_MATCHES_ORIGINAL: True}

    result = entry(ev, f"interface_message:{succeeded.resulting_message_id}", "RECOVERY_RESULT")
    assert result["sha256"] == corrected_sha
    assert result["bytes"] == len(EX03_CORRECTED.read_bytes())
    ok = entry(ev, f"interface_recovery_attempt:{succeeded.attempt_id}", "ATTEMPT_PAYLOAD")
    assert ok["sha256"] == corrected_sha and ok["bytes"] is None
    assert checks(ok) == {evidence.CHECK_ATTEMPT_MATCHES_RESULT: True}

    # The FAILED re-drive re-sent the original bytes; its message is preserved ERRORED.
    bad = entry(ev, f"interface_recovery_attempt:{failed.attempt_id}", "ATTEMPT_PAYLOAD")
    assert bad["sha256"] == original_sha
    assert checks(bad) == {evidence.CHECK_ATTEMPT_MATCHES_RESULT: True}
    assert entry(
        ev, f"interface_message:{failed.resulting_message_id}", "RECOVERY_RESULT"
    )["sha256"] == original_sha

    # The REJECTED unchanged retry has no resulting message; its fingerprint is
    # the original payload's (RETRY_ORIGINAL reads the original message payload).
    rej = entry(ev, f"interface_recovery_attempt:{rejected.attempt_id}", "ATTEMPT_PAYLOAD")
    assert rej["sha256"] == original_sha
    assert checks(rej) == {evidence.CHECK_RETRY_MATCHES_ORIGINAL: True}

    # Outbound exports are fingerprinted too.
    outbound = [f for f in ev["fingerprints"] if f["role"] == "OUTBOUND_EXPORT"]
    assert len(outbound) == 2
    assert all(c["passed"] for f in ev["fingerprints"] for c in f["checks"])


def test_retry_original_fingerprint_matches_recovery_fixture_bytes():
    conn = create_database(":memory:")
    ids = build_rc05_retry(conn)
    fixture_sha = file_sha256(RC05_ORIGINAL)
    ev = evidence.build_evidence(conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION)
    attempt = ids["attempt"]
    a = entry(ev, f"interface_recovery_attempt:{attempt.attempt_id}", "ATTEMPT_PAYLOAD")
    assert a["sha256"] == fixture_sha
    assert checks(a) == {
        evidence.CHECK_ATTEMPT_MATCHES_RESULT: True,
        evidence.CHECK_RETRY_MATCHES_ORIGINAL: True,
    }
    assert entry(
        ev, f"interface_message:{attempt.resulting_message_id}", "RECOVERY_RESULT"
    )["sha256"] == fixture_sha
    assert entry(ev, f"interface_error_queue:{ids['queue_id']}", "QUEUE_RAW_COPY")["sha256"] == (
        fixture_sha
    )
    conn.close()


def tampered_copy(source: Path, target: Path, sql: str, params=()) -> Path:
    shutil.copyfile(source, target)
    with closing(sqlite3.connect(target)) as raw:
        raw.execute(sql, params)
        raw.commit()
    return target


def test_tampered_raw_payload_fails_the_raw_copy_check(tmp_path):
    path, ids = file_db(tmp_path, "ex03.db", build_ex03)
    with closing(store.open_read_only(path)) as conn:
        clean = evidence.build_evidence(
            conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION
        )
    copy_path = tampered_copy(
        path, tmp_path / "tampered.db",
        "UPDATE interface_error_queue SET raw_payload = raw_payload || 'X' WHERE queue_id = ?",
        (ids["queue_id"],),
    )
    with closing(store.open_read_only(copy_path)) as conn:
        ev = evidence.build_evidence(conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION)
    raw = entry(ev, f"interface_error_queue:{ids['queue_id']}", "QUEUE_RAW_COPY")
    assert raw["sha256"] == hashlib.sha256(EX03_ORIGINAL.read_bytes() + b"X").hexdigest()
    assert raw["bytes"] == len(EX03_ORIGINAL.read_bytes()) + 1
    assert checks(raw) == {evidence.CHECK_RAW_COPY_MATCHES_ORIGINAL: False}
    # Only that check fails, reconciliation reports it, and the fingerprint moves.
    failing = [
        (f["subject"], c["check"]) for f in ev["fingerprints"] for c in f["checks"]
        if not c["passed"]
    ]
    assert failing == [(raw["subject"], evidence.CHECK_RAW_COPY_MATCHES_ORIGINAL)]
    assert "QUE-005" in [f["rule_id"] for f in ev["reconciliation"]["findings"]]
    assert ev["content_fingerprint"] != clean["content_fingerprint"]


def test_tampered_attempt_fingerprint_fails_the_attempt_check(tmp_path):
    path, ids = file_db(tmp_path, "ex03.db", build_ex03)
    succeeded = ids["attempts"][2]
    copy_path = tampered_copy(
        path, tmp_path / "tampered.db",
        "UPDATE interface_recovery_attempt SET payload_sha256 = ? WHERE attempt_id = ?",
        ("0" * 64, succeeded.attempt_id),
    )
    with closing(store.open_read_only(copy_path)) as conn:
        ev = evidence.build_evidence(conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION)
    a = entry(ev, f"interface_recovery_attempt:{succeeded.attempt_id}", "ATTEMPT_PAYLOAD")
    assert checks(a) == {evidence.CHECK_ATTEMPT_MATCHES_RESULT: False}


# ---------------------------------------------------------------------------
# (4) content_fingerprint ignores wall-clock values, tracks business values
# ---------------------------------------------------------------------------

def test_content_fingerprint_is_stable_across_wall_clock_seconds():
    first = create_database(":memory:")
    ids_a = build_ex03(first)
    time.sleep(1.1)  # every datetime('now') stamp in the second build is later
    second = create_database(":memory:")
    ids_b = build_ex03(second)
    assert ids_a["queue_id"] == ids_b["queue_id"] and ids_a["order_id"] == ids_b["order_id"]
    for selector in ({"queue_id": ids_a["queue_id"]}, {"order_id": ids_a["order_id"]}):
        ev_a = evidence.build_evidence(first, **selector, source_revision=TEST_REVISION)
        ev_b = evidence.build_evidence(second, **selector, source_revision=TEST_REVISION)
        assert canonical_json(ev_a) != canonical_json(ev_b)
        ordered_a = ev_a["records"]["orders"][0]["ordered_at"]
        assert ordered_a != ev_b["records"]["orders"][0]["ordered_at"]
        assert ev_a["content_fingerprint"] == ev_b["content_fingerprint"]
        # The source revision is not content.
        ev_c = evidence.build_evidence(second, **selector, source_revision="UNKNOWN")
        assert ev_c["content_fingerprint"] == ev_b["content_fingerprint"]
    first.close()
    second.close()


def test_content_fingerprint_changes_with_business_values(ex03):
    conn, ids = ex03
    baseline = evidence.build_evidence(
        conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION
    )

    # A different result value in the corrected payload.
    corrected = fixture_text(EX03_CORRECTED)
    changed_value = corrected.replace("200^2^2R2G^NORMAL", "200^1^2R2G^NORMAL", 1)
    assert changed_value != corrected
    other = create_database(":memory:")
    build_ex03(other, corrected=changed_value)
    ev_value = evidence.build_evidence(
        other, queue_id=ids["queue_id"], source_revision=TEST_REVISION
    )
    assert ev_value["content_fingerprint"] != baseline["content_fingerprint"]
    other.close()

    # A different actor on the recovery requests.
    other = create_database(":memory:")
    build_ex03(other, actor="analyst02")
    ev_actor = evidence.build_evidence(
        other, queue_id=ids["queue_id"], source_revision=TEST_REVISION
    )
    assert ev_actor["content_fingerprint"] != baseline["content_fingerprint"]
    other.close()

    # Scenario labels are part of the content.
    labelled = evidence.build_evidence(
        conn, queue_id=ids["queue_id"], scenario_id=SCENARIO_ID, source_revision=TEST_REVISION
    )
    assert labelled["content_fingerprint"] != baseline["content_fingerprint"]

    # Editing one business value of an exported document moves the fingerprint.
    edited = json.loads(canonical_json(baseline))
    edited["records"]["orders"][0]["current_results"][0]["cells_abnormal"] += 1
    assert evidence.content_fingerprint(edited) != baseline["content_fingerprint"]


def test_fingerprint_view_drops_volatile_and_outbound_payload_facts(ex03):
    conn, ids = ex03
    ev = evidence.build_evidence(conn, order_id=ids["order_id"], source_revision=TEST_REVISION)
    view = evidence.fingerprint_view(ev)
    assert "source" not in view and "content_fingerprint" not in view

    def keys(value):
        if isinstance(value, dict):
            for k, v in value.items():
                yield k
                yield from keys(v)
        elif isinstance(value, list):
            for v in value:
                yield from keys(v)

    assert not (set(keys(view)) & evidence.VOLATILE_KEYS)
    outbound = [m for m in view["records"]["messages"] if m["direction"] == "OUTBOUND"]
    inbound = [m for m in view["records"]["messages"] if m["direction"] == "INBOUND"]
    assert len(outbound) == 2 and inbound
    for m in outbound:
        assert not (set(m) & set(evidence.OUTBOUND_VOLATILE_KEYS))
    for m in inbound:
        assert {"payload", "payload_sha256", "payload_bytes", "control_id"} <= set(m)
    stamp = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")
    for f in view["reconciliation"]["findings"]:
        assert not any(stamp.search(o) for o in f["observations"])
    # The real document keeps the REC-008 observation's stored time.
    rec008 = [f for f in ev["reconciliation"]["findings"] if f["rule_id"] == "REC-008"]
    assert rec008 and stamp.search(rec008[0]["observations"][0])


# ---------------------------------------------------------------------------
# (5) Read-only connection leaves the database file unchanged
# ---------------------------------------------------------------------------

def test_build_on_read_only_connection_leaves_file_bytes_unchanged(tmp_path):
    path, ids = file_db(tmp_path, "ex03.db", build_ex03)
    before = file_sha256(path)
    with closing(store.open_read_only(path)) as conn:
        ev_queue = evidence.build_evidence(conn, queue_id=ids["queue_id"])
        ev_order = evidence.build_evidence(conn, order_id=ids["order_id"])
        evidence.render_report(ev_queue)
        evidence.render_report(ev_order)
    assert file_sha256(path) == before
    assert ev_queue["records"]["queue_items"][0]["queue_id"] == ids["queue_id"]


# ---------------------------------------------------------------------------
# (6) Report: ASCII, notice, scenario, revision, request ids, limitations
# ---------------------------------------------------------------------------

def test_report_is_short_ascii_markdown_with_required_content(ex03):
    conn, ids = ex03
    ev = evidence.build_evidence(
        conn, queue_id=ids["queue_id"], scenario_id=SCENARIO_ID,
        scenario_title=SCENARIO_TITLE, source_revision=TEST_REVISION,
    )
    report = evidence.render_report(ev)
    assert report.isascii()
    assert report == evidence.render_report(ev)
    assert report.endswith("\n")
    lines = report.splitlines()
    assert lines[0] == "# CytoBridge synthetic case evidence"
    assert len(lines) < 150
    assert SYNTHETIC_NOTICE in report
    assert SCENARIO_ID in report and SCENARIO_TITLE in report
    assert f"Source revision: {TEST_REVISION}" in report
    assert ev["source"]["schema_sha256"] in report
    assert report.count(ev["content_fingerprint"]) == 2
    for attempt in ev["recovery_history"]:
        assert attempt["request_id"] in report
    for lim in ev["limitations"]:
        assert f"- {lim['code']}: {lim['text']}" in report
    assert "- EDUCATIONAL_INVARIANTS: " + LIMITATIONS["EDUCATIONAL_INVARIANTS"] in report
    # One relationship line per link, each with its basis.
    for link in ev["relationships"]:
        assert (
            f"- {link['from']} -> {link['to']}: {link['relation']} [{link['basis']}]" in report
        )
    for heading in (
        "## Relationships", "## Payload fingerprints", "## Validation (current vs stored)",
        "## Observed outcomes", "## Recovery history", "## Reconciliation",
        "## Provenance notes", "## Limitations",
    ):
        assert heading in lines


def test_report_escapes_stored_non_ascii():
    conn = create_database(":memory:")
    build_ex03(conn, actor="analyst-\u00e9")
    ev = evidence.build_evidence(
        conn, queue_id=1, scenario_title="Synthetic \u2014 title", source_revision=TEST_REVISION
    )
    # Outbound payloads carry the report summary's em dash; JSON stays ASCII.
    assert any("\u2014" in m["payload"] for m in ev["records"]["messages"])
    assert canonical_json(ev).isascii()
    report = evidence.render_report(ev)
    assert report.isascii()
    assert "analyst-\\xe9" in report
    assert "Synthetic \\u2014 title" in report
    conn.close()


def test_report_renders_exported_json_and_escapes_control_characters(ex03):
    conn, ids = ex03
    ev = evidence.build_evidence(conn, queue_id=ids["queue_id"], source_revision=TEST_REVISION)
    report = evidence.render_report(ev)
    # A document read back from its exported JSON renders identically.
    assert evidence.render_report(json.loads(canonical_json(ev))) == report
    # Stored text with table pipes, newlines, or control characters stays on
    # one line and cannot break the Markdown table.
    edited = json.loads(canonical_json(ev))
    edited["recovery_history"][0]["request_id"] = "REQ|A\nB\x01"
    edited_report = evidence.render_report(edited)
    assert edited_report.isascii()
    assert "REQ\\|A\\nB\\x01" in edited_report
    assert len(edited_report.splitlines()) == len(report.splitlines())


# ---------------------------------------------------------------------------
# (7) Source revision
# ---------------------------------------------------------------------------

REVISION_RE = re.compile(r"[0-9a-f]{40}(-dirty)?")


def test_resolve_source_revision_in_this_repository():
    revision = evidence.resolve_source_revision()
    assert REVISION_RE.fullmatch(revision), revision
    head = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert revision.split("-")[0] == head


def test_resolve_source_revision_unknown_when_git_is_unavailable(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(evidence.subprocess, "run", missing)
    assert evidence.resolve_source_revision() == "UNKNOWN"


def test_resolve_source_revision_unknown_on_failure_or_timeout(monkeypatch):
    def failing(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal")

    monkeypatch.setattr(evidence.subprocess, "run", failing)
    assert evidence.resolve_source_revision() == "UNKNOWN"

    def slow(cmd, **kwargs):
        assert kwargs.get("timeout") is not None
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr(evidence.subprocess, "run", slow)
    assert evidence.resolve_source_revision() == "UNKNOWN"

    def garbage(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="not-a-commit\n", stderr="")

    monkeypatch.setattr(evidence.subprocess, "run", garbage)
    assert evidence.resolve_source_revision() == "UNKNOWN"


@pytest.mark.parametrize(("status", "expected_suffix"), [("", ""), (" M schema.sql\n", "-dirty")])
def test_resolve_source_revision_dirty_suffix(monkeypatch, status, expected_suffix):
    sha = "a" * 40
    seen = []

    def fake(cmd, **kwargs):
        seen.append(cmd)
        out = f"{sha}\n" if "rev-parse" in cmd else status
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(evidence.subprocess, "run", fake)
    assert evidence.resolve_source_revision("/repo") == sha + expected_suffix
    assert seen == [
        ["git", "-C", "/repo", "rev-parse", "HEAD"],
        ["git", "-C", "/repo", "status", "--porcelain", "--untracked-files=no"],
    ]


def test_build_evidence_defaults_to_the_resolved_revision(ex03, monkeypatch):
    conn, ids = ex03
    monkeypatch.setattr(evidence, "resolve_source_revision", lambda *a, **k: "b" * 40 + "-dirty")
    ev = evidence.build_evidence(conn, queue_id=ids["queue_id"])
    assert ev["source"]["revision"] == "b" * 40 + "-dirty"


# ---------------------------------------------------------------------------
# (8) Selector usage and unknown ids
# ---------------------------------------------------------------------------

def test_exactly_one_selector_else_usage(ex03):
    conn, ids = ex03
    for kwargs in ({}, {"queue_id": ids["queue_id"], "order_id": ids["order_id"]}):
        with pytest.raises(AnalystError) as err:
            evidence.build_evidence(conn, **kwargs)
        assert err.value.kind == "USAGE" and err.value.exit_code == 2


@pytest.mark.parametrize("selector", ["queue_id", "order_id"])
def test_unknown_id_is_not_found(ex03, selector):
    conn, _ids = ex03
    with pytest.raises(AnalystError) as err:
        evidence.build_evidence(conn, **{selector: 999})
    assert err.value.kind == "NOT_FOUND" and err.value.exit_code == 3


# ---------------------------------------------------------------------------
# (9) Every evidence document is marked synthetic
# ---------------------------------------------------------------------------

def test_every_evidence_document_is_marked_synthetic(ex03):
    conn, ids = ex03
    rc05 = build_rc05_retry(conn)
    documents = [
        evidence.build_evidence(conn, queue_id=ids["queue_id"]),
        evidence.build_evidence(conn, order_id=ids["order_id"], source_revision="UNKNOWN"),
        evidence.build_evidence(conn, queue_id=rc05["queue_id"], scenario_id="RC-05"),
        evidence.build_evidence(conn, order_id=rc05["order_id"]),
    ]
    for ev in documents:
        for doc in (ev, json.loads(canonical_json(ev))):
            assert doc["synthetic"] is True
            assert doc["notice"] == SYNTHETIC_NOTICE
            assert doc["format"] == evidence.EVIDENCE_FORMAT
        assert SYNTHETIC_NOTICE in evidence.render_report(ev)
