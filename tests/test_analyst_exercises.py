"""Tests for the analyst-lab exercise runner and verifier (src.analyst.exercises).

Verifier first, independent of the CLI: each exercise's end state is built
through the PUBLIC services only (workflow, inbound_hl7.ingest_message,
outbound store functions, recovery retry/redrive, and the read-only preview),
mirroring the answer key's steps and reading the fixtures from
``exercises/<EXID>/inputs``, into a file database created by
``store.init_database``. ``verify_exercise`` must pass on it. Copies of those
databases are then tampered with by direct SQL (test-only) to prove the
verifier trusts nothing but the persisted rows and the fixture file bytes: each
tamper must fail the specific, named check(s).

Then the runner, end to end through the in-process CLI: every exercise run in a
fresh database passes; a rerun is deterministic; exercises do not
cross-contaminate; ``run_all`` passes and refuses a reused work directory.

Answer keys stay separate from analyst instructions. All data is synthetic. No
PHI. Passing these tests is automated testing of the simulator, not manual UAT.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Callable

import pytest

from src import recovery, workflow
from src.analyst import exercises, preview, store
from src.analyst.common import AnalystError, read_payload_file, sha256_file
from src.interfaces import inbound_hl7, outbound_fhir, outbound_hl7

EXERCISE_IDS = [
    "EX01-valid-result-export",
    "EX02-unknown-accession-retry",
    "EX03-invalid-result-redrive",
    "EX04-finalized-order-terminal",
    "EX05-replay-and-conflict",
    "EX06-finalize-blocked-correction",
    "EX07-failed-recovery-then-success",
]

FIRST_NAMES = {
    1: "ExerciseOne", 2: "ExerciseTwo", 3: "ExerciseThree", 4: "ExerciseFour",
    5: "ExerciseFive", 6: "ExerciseSix", 7: "ExerciseSeven",
}


# ---------------------------------------------------------------------------
# Building exercise end states through the public services only (no CLI)
# ---------------------------------------------------------------------------

def _payload(ex_id: str, name: str) -> str:
    """Fixture text read exactly (strict UTF-8, no newline translation)."""
    text, _raw = read_payload_file(exercises.EXERCISES_DIR / ex_id / "inputs" / name)
    return text


def _case(conn: sqlite3.Connection, n: int, sex: str) -> int:
    """Mirror setup.json case-create: patient, order, accessioned specimen."""
    patient_id = workflow.create_patient(
        conn, f"SYN-EX0{n}-01", "Synthetic", FIRST_NAMES[n], f"1970-01-0{n}", sex
    )
    order_id = workflow.create_order(conn, patient_id, f"ACC-EX0{n}-0001", "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(
        conn, order_id, "BONE_MARROW", f"BM-EX0{n}-01"
    )
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def _ingest(conn, ex_id: str, name: str, *, filed: bool) -> inbound_hl7.IngestResult:
    result = inbound_hl7.ingest_message(conn, _payload(ex_id, name), actor="interface")
    assert result.filed is filed, result.reason
    return result


def _preview(path: Path, queue_id: int, action: str, request_id: str, actor: str,
             payload: str | None = None) -> dict:
    """The read-only preview the answer key runs before an apply."""
    before = sha256_file(path)
    result = preview.preview_recovery(
        path, queue_id, action=action, request_id=request_id, actor=actor,
        corrected_payload=payload,
    )
    assert sha256_file(path) == before
    return result


def build_ex01(conn, path):
    ex = EXERCISE_IDS[0]
    oid = _case(conn, 1, "F")
    _ingest(conn, ex, "valid_result.hl7", filed=True)
    assert workflow.finalize_order(conn, oid, "analyst01").finalized
    outbound_hl7.store_oru(conn, oid, "2026-10-25 09:00:00")
    outbound_fhir.store_diagnostic_report(conn, oid)


def build_ex02(conn, path):
    ex = EXERCISE_IDS[1]
    queued = _ingest(conn, ex, "early_result.hl7", filed=False)
    _case(conn, 2, "M")
    sim = _preview(path, queued.queue_id, "RETRY_ORIGINAL", "EX02-RETRY-1", "analyst01")
    assert sim["simulated"]["status"] == "SUCCEEDED"
    attempt = recovery.retry_queue_item(
        conn, queued.queue_id, request_id="EX02-RETRY-1", actor="analyst01"
    )
    assert attempt.outcome == "SUCCEEDED"


def build_ex03(conn, path):
    ex = EXERCISE_IDS[2]
    _case(conn, 3, "F")
    queued = _ingest(conn, ex, "original_result.hl7", filed=False)
    corrected = _payload(ex, "corrected_result.hl7")
    sim = _preview(path, queued.queue_id, "REDRIVE_CORRECTED", "EX03-REDRIVE-1",
                   "analyst01", corrected)
    assert sim["simulated"]["status"] == "SUCCEEDED"
    attempt = recovery.redrive_queue_item(
        conn, queued.queue_id, corrected, request_id="EX03-REDRIVE-1", actor="analyst01"
    )
    assert attempt.outcome == "SUCCEEDED"


def build_ex04(conn, path):
    ex = EXERCISE_IDS[3]
    oid = _case(conn, 4, "M")
    _ingest(conn, ex, "first_result.hl7", filed=True)
    assert workflow.finalize_order(conn, oid, "analyst01").finalized
    queued = _ingest(conn, ex, "late_result.hl7", filed=False)
    late = _payload(ex, "late_result.hl7")
    sim = _preview(path, queued.queue_id, "REDRIVE_CORRECTED", "EX04-REDRIVE-1",
                   "analyst01", late)
    assert sim["simulated"]["status"] == "REJECTED"
    first = recovery.redrive_queue_item(
        conn, queued.queue_id, late, request_id="EX04-REDRIVE-1", actor="analyst01"
    )
    second = recovery.retry_queue_item(
        conn, queued.queue_id, request_id="EX04-RETRY-1", actor="analyst01"
    )
    assert (first.outcome, second.outcome) == ("REJECTED", "REJECTED")


def build_ex05(conn, path):
    ex = EXERCISE_IDS[4]
    _case(conn, 5, "F")
    queued = _ingest(conn, ex, "original_result.hl7", filed=False)
    corrected = _payload(ex, "corrected_result.hl7")
    qid = queued.queue_id
    first = recovery.redrive_queue_item(
        conn, qid, corrected, request_id="EX05-REDRIVE-1", actor="analyst01"
    )
    replay = recovery.redrive_queue_item(
        conn, qid, corrected, request_id="EX05-REDRIVE-1", actor="analyst01"
    )
    assert first.outcome == "SUCCEEDED" and replay.attempt_id == first.attempt_id
    with pytest.raises(recovery.RequestIdConflictError):
        recovery.redrive_queue_item(
            conn, qid, corrected, request_id="EX05-REDRIVE-1", actor="analyst02"
        )
    later = recovery.redrive_queue_item(
        conn, qid, corrected, request_id="EX05-REDRIVE-2", actor="analyst01"
    )
    assert later.outcome == "REJECTED"


def build_ex06(conn, path):
    ex = EXERCISE_IDS[5]
    oid = _case(conn, 6, "M")
    _ingest(conn, ex, "incomplete_result.hl7", filed=True)
    assert not workflow.finalize_order(conn, oid, "analyst01").finalized
    workflow.enter_fish_result(conn, oid, "TP53_17p", 200, 2, "2R2G", "NORMAL", "tech01")
    workflow.enter_fish_result(conn, oid, "CEP8", 200, 12, "3 aqua", "ABNORMAL", "tech01")
    assert workflow.finalize_order(conn, oid, "analyst01").finalized


def build_ex07(conn, path):
    ex = EXERCISE_IDS[6]
    _case(conn, 7, "F")
    queued = _ingest(conn, ex, "original_result.hl7", filed=False)
    failed = recovery.redrive_queue_item(
        conn, queued.queue_id, _payload(ex, "correction_v1.hl7"),
        request_id="EX07-REDRIVE-1", actor="analyst01",
    )
    assert failed.outcome == "FAILED"
    ok = recovery.redrive_queue_item(
        conn, queued.queue_id, _payload(ex, "correction_v2.hl7"),
        request_id="EX07-REDRIVE-2", actor="analyst01",
    )
    assert ok.outcome == "SUCCEEDED"


BUILDERS: dict[str, Callable] = dict(zip(EXERCISE_IDS, [
    build_ex01, build_ex02, build_ex03, build_ex04, build_ex05, build_ex06, build_ex07,
]))


def _build(directory: Path, ex_id: str) -> Path:
    path = store.init_database(directory / f"{ex_id}.services.db")
    conn = store.open_read_write(path)
    try:
        BUILDERS[ex_id](conn, path)
    finally:
        conn.close()
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> dict[str, Path]:
    """Every exercise's end state, built once through the public services."""
    directory = tmp_path_factory.mktemp("services_built")
    return {ex: _build(directory, ex) for ex in EXERCISE_IDS}


def _tampered(source: Path, tmp_path: Path, *statements: tuple[str, tuple]) -> Path:
    """Copy a database and apply direct SQL to it (test-only broken states)."""
    target = tmp_path / f"tampered-{source.name}"
    shutil.copyfile(source, target)
    with closing(sqlite3.connect(target)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for sql, params in statements:
            assert conn.execute(sql, params).rowcount != 0, sql
        conn.commit()
    return target


def _failed(result: dict) -> set[str]:
    return {c["name"] for c in result["checks"] if not c["passed"]}


def _check(result: dict, name: str) -> dict:
    matches = [c for c in result["checks"] if c["name"] == name]
    assert len(matches) == 1, (name, [c["name"] for c in result["checks"]])
    return matches[0]


# ---------------------------------------------------------------------------
# Verifier against service-built databases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ex_id", EXERCISE_IDS)
def test_verify_passes_on_service_built_end_state(built, ex_id):
    result = exercises.verify_exercise(ex_id, built[ex_id])
    assert result["exercise"] == ex_id
    assert set(result) == {"exercise", "passed", "checks"}
    assert _failed(result) == set()
    assert result["passed"] is True
    names = [c["name"] for c in result["checks"]]
    assert len(names) == len(set(names)), "check names must be unique"
    prefix = ex_id.split("-")[0] + " "
    for c in result["checks"]:
        assert set(c) == {"name", "passed", "expected", "observed"}
        assert c["name"].startswith(prefix)
    # Every EXPECTED section the answer key carries produced checks.
    expected = exercises.load_exercise(ex_id)["answer_key"]["expected"]
    if expected.get("queue_items"):
        assert any("payload_fixture sha256" in n for n in names)
        assert any("raw_payload sha256" in n for n in names)
    if expected.get("outbound"):
        assert any(n.endswith("matches_results") for n in names)
    assert prefix + "reconcile_error_count" in names


def test_verify_is_read_only_and_never_creates(built, tmp_path):
    source = built["EX05-replay-and-conflict"]
    before = sha256_file(source)
    siblings = sorted(p.name for p in source.parent.iterdir())
    exercises.verify_exercise("EX05", source)
    assert sha256_file(source) == before
    assert sorted(p.name for p in source.parent.iterdir()) == siblings
    missing = tmp_path / "missing.db"
    with pytest.raises(AnalystError) as exc:
        exercises.verify_exercise("EX05", missing)
    assert exc.value.kind == "NOT_FOUND"
    assert not missing.exists()


def test_verify_never_consults_the_cli(built, monkeypatch):
    from src.analyst import cli

    def _forbidden(*_a, **_k):
        raise AssertionError("verify_exercise must not run CLI commands")

    monkeypatch.setattr(cli, "main", _forbidden)
    assert exercises.verify_exercise("EX02", built["EX02-unknown-accession-retry"])["passed"]


def test_verify_wrong_exercise_database_fails(built):
    result = exercises.verify_exercise("EX01", built["EX03-invalid-result-redrive"])
    assert result["passed"] is False
    assert "EX01 order[ACC-EX01-0001] exists" in _failed(result)
    assert "EX01 outbound[ACC-EX01-0001 HL7] order exists" in _failed(result)


# ---------------------------------------------------------------------------
# Tampering: the verifier trusts only persisted rows and fixture bytes
# ---------------------------------------------------------------------------

def _verify_tampered(built, tmp_path, ex_id, *statements) -> dict:
    result = exercises.verify_exercise(ex_id, _tampered(built[ex_id], tmp_path, *statements))
    assert result["passed"] is False
    return result


def test_tamper_attempt_outcome(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX03-invalid-result-redrive",
        ("UPDATE interface_recovery_attempt SET outcome = 'FAILED' "
         "WHERE request_id = 'EX03-REDRIVE-1'", ()),
    )
    failed = _failed(result)
    assert "EX03 queue[EX03-ORIG] attempt[1] outcome" in failed
    assert "EX03 queue[EX03-ORIG] attempt[1] request_id" not in failed
    check = _check(result, "EX03 queue[EX03-ORIG] attempt[1] outcome")
    assert (check["expected"], check["observed"]) == ("SUCCEEDED", "FAILED")


def test_tamper_fish_result_value(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("UPDATE fish_result SET cells_abnormal = 5 WHERE probe_id = "
         "(SELECT probe_id FROM probe WHERE probe_code = 'CEP8')", ()),
    )
    failed = _failed(result)
    assert "EX01 order[ACC-EX01-0001] result[CEP8] cells_abnormal" in failed
    assert "EX01 order[ACC-EX01-0001] result[CEP8] cells_scored" not in failed
    # The stored exports no longer match the current fish_result rows.
    assert "EX01 outbound[ACC-EX01-0001 HL7] matches_results" in failed
    assert "EX01 outbound[ACC-EX01-0001 FHIR] matches_results" in failed


def test_tamper_original_message_payload(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX03-invalid-result-redrive",
        ("UPDATE interface_message SET payload = replace(payload, '2OO', '200') "
         "WHERE message_id = (SELECT message_id FROM interface_error_queue)", ()),
    )
    failed = _failed(result)
    assert "EX03 queue[EX03-ORIG] original message payload sha256" in failed
    assert "EX03 queue[EX03-ORIG] raw_payload sha256" not in failed


def test_tamper_queue_raw_payload(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX03-invalid-result-redrive",
        ("UPDATE interface_error_queue SET raw_payload = raw_payload || ?", ("\n",)),
    )
    failed = _failed(result)
    assert "EX03 queue[EX03-ORIG] raw_payload sha256" in failed
    assert "EX03 queue[EX03-ORIG] original message payload sha256" not in failed


def test_tamper_resulting_payload_is_hashed_from_text_not_stored_fingerprint(
    built, tmp_path
):
    # attempt.payload_sha256 is left alone; only the stored payload TEXT changes.
    result = _verify_tampered(
        built, tmp_path, "EX03-invalid-result-redrive",
        ("UPDATE interface_message SET payload = payload || ? WHERE message_id = "
         "(SELECT resulting_message_id FROM interface_recovery_attempt)", ("\n",)),
    )
    failed = _failed(result)
    assert "EX03 queue[EX03-ORIG] attempt[1] resulting message payload sha256" in failed
    assert "EX03 queue[EX03-ORIG] attempt[1] payload_fixture sha256" not in failed


def test_tamper_extra_attempt(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX03-invalid-result-redrive",
        ("INSERT INTO interface_recovery_attempt (queue_id, resulting_message_id, "
         "request_id, action, payload_sha256, outcome, actor, outcome_detail) "
         "VALUES (1, NULL, 'EX03-EXTRA', 'REDRIVE_CORRECTED', 'x', 'REJECTED', "
         "'analyst01', 'synthetic tamper')", ()),
    )
    failed = _failed(result)
    assert "EX03 queue[EX03-ORIG] attempt count" in failed
    assert "EX03 totals[interface_recovery_attempt]" in failed
    check = _check(result, "EX03 queue[EX03-ORIG] attempt count")
    assert (check["expected"], check["observed"]) == (1, 2)


def test_tamper_delete_inbound_filing_audit(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX03-invalid-result-redrive",
        ("DELETE FROM audit_event WHERE action = 'INBOUND_RESULT_FILED'", ()),
    )
    failed = _failed(result)
    assert "EX03 order[ACC-EX03-0001] audit_action_counts[INBOUND_RESULT_FILED]" in failed
    assert "EX03 totals[audit_event]" in failed
    # AUD-001: a FILED inbound message with no filing audit event is an ERROR.
    assert "EX03 reconcile_error_count" in failed


def test_tamper_outbound_hl7_obx_value(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("UPDATE interface_message SET payload = replace(payload, "
         "'4/200 nuclei (2.0%) NORMAL', '5/200 nuclei (2.5%) NORMAL') "
         "WHERE direction = 'OUTBOUND' AND format = 'HL7'", ()),
    )
    failed = _failed(result)
    assert "EX01 outbound[ACC-EX01-0001 HL7] matches_results" in failed
    per_message = [n for n in failed if n.startswith("EX01 outbound[ACC-EX01-0001 HL7] message[")]
    assert len(per_message) == 1
    detail = _check(result, per_message[0])
    assert detail["expected"]["EGR1_5q"]["cells_abnormal"] == 4
    assert detail["observed"]["EGR1_5q"]["cells_abnormal"] == 5
    assert "EX01 outbound[ACC-EX01-0001 FHIR] matches_results" not in failed
    assert not any("result[" in n for n in failed)


def test_tamper_outbound_hl7_missing_probe_obx(built, tmp_path):
    # Drop the TP53_17p OBX segment: the export no longer covers every probe.
    src = built["EX01-valid-result-export"]
    with closing(store.open_read_only(src)) as conn:
        mid, payload = conn.execute(
            "SELECT message_id, payload FROM interface_message "
            "WHERE direction = 'OUTBOUND' AND format = 'HL7'"
        ).fetchone()
    segments = payload.split("\r")
    kept = [s for s in segments if not (s.startswith("OBX|") and "|TP53_17p^" in s)]
    assert len(kept) == len(segments) - 1
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("UPDATE interface_message SET payload = ? WHERE message_id = ?",
         ("\r".join(kept), mid)),
    )
    assert "EX01 outbound[ACC-EX01-0001 HL7] matches_results" in _failed(result)
    detail = _check(result, f"EX01 outbound[ACC-EX01-0001 HL7] message[{mid}] per-probe values")
    assert "TP53_17p" in detail["expected"] and "TP53_17p" not in detail["observed"]


def test_tamper_outbound_fhir_observation(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("UPDATE interface_message SET payload = replace(payload, "
         "'4/200 nuclei scored', '5/200 nuclei scored') "
         "WHERE direction = 'OUTBOUND' AND format = 'FHIR'", ()),
    )
    failed = _failed(result)
    assert "EX01 outbound[ACC-EX01-0001 FHIR] matches_results" in failed
    assert "EX01 outbound[ACC-EX01-0001 HL7] matches_results" not in failed


def _outbound_message(source: Path, fmt: str) -> tuple[int, str]:
    with closing(store.open_read_only(source)) as conn:
        row = conn.execute(
            "SELECT message_id, payload FROM interface_message "
            "WHERE direction = 'OUTBOUND' AND format = ?",
            (fmt,),
        ).fetchone()
    return row[0], row[1]


def _edit_cep8_obx(edit_fields: Callable[[list[str]], None]) -> Callable[[str], str]:
    """Edit the fields of the stored HL7 export's CEP8 OBX segment."""
    def edit(payload: str) -> str:
        segments = payload.split("\r")
        hits = [i for i, s in enumerate(segments) if s.startswith("OBX|") and "|CEP8^" in s]
        assert len(hits) == 1
        fields = segments[hits[0]].split("|")
        edit_fields(fields)
        segments[hits[0]] = "|".join(fields)
        return "\r".join(segments)
    return edit


def _edit_cep8_observation(edit_resource: Callable[[dict], None]) -> Callable[[str], str]:
    """Edit the stored FHIR export's CEP8 Observation resource."""
    def edit(payload: str) -> str:
        bundle = json.loads(payload)
        hits = [
            e["resource"] for e in bundle["entry"]
            if e["resource"]["resourceType"] == "Observation"
            and e["resource"]["code"]["coding"][0]["code"] == "CEP8"
        ]
        assert len(hits) == 1
        edit_resource(hits[0])
        return json.dumps(bundle, indent=2)
    return edit


def _obx5(old: str, new: str) -> Callable[[list[str]], None]:
    def edit(fields: list[str]) -> None:
        assert old in fields[5]
        fields[5] = fields[5].replace(old, new)
    return edit


def _obx8(new: str) -> Callable[[list[str]], None]:
    def edit(fields: list[str]) -> None:
        assert fields[8] == "N"
        fields[8] = new
    return edit


def _set_value_quantity(res: dict) -> None:
    assert res["valueQuantity"]["value"] == 1.5
    res["valueQuantity"]["value"] = 42.0


def _set_note_pattern(res: dict) -> None:
    res["note"][0]["text"] = res["note"][0]["text"].replace("2 aqua", "4 aqua")


def _set_interpretation_code(res: dict) -> None:
    assert res["interpretation"][0]["coding"][0]["code"] == "N"
    res["interpretation"][0]["coding"][0]["code"] = "A"


@pytest.mark.parametrize("fmt, edit, field, want, got", [
    ("HL7", _edit_cep8_obx(_obx5("(1.5%)", "(9.9%)")), "percent_abnormal", "1.5", "9.9"),
    ("HL7", _edit_cep8_obx(_obx5("signal 2 aqua", "signal 4 aqua")),
     "signal_pattern", "2 aqua", "4 aqua"),
    ("HL7", _edit_cep8_obx(_obx8("A")), "flag", "N", "A"),
    ("FHIR", _edit_cep8_observation(_set_value_quantity), "percent_abnormal", 1.5, 42.0),
    ("FHIR", _edit_cep8_observation(_set_note_pattern), "signal_pattern", "2 aqua", "4 aqua"),
    ("FHIR", _edit_cep8_observation(_set_interpretation_code), "flag", "N", "A"),
], ids=[
    "hl7-percent", "hl7-signal-pattern", "hl7-flag",
    "fhir-value-quantity", "fhir-signal-pattern", "fhir-interpretation-code",
])
def test_tamper_outbound_percent_pattern_and_flag(built, tmp_path, fmt, edit, field, want, got):
    # Counts and interpretation text are untouched: only the percent abnormal,
    # the signal pattern, or the N/A/I flag/code of one export differs.
    ex = "EX01-valid-result-export"
    mid, payload = _outbound_message(built[ex], fmt)
    tampered = edit(payload)
    assert tampered != payload
    result = _verify_tampered(
        built, tmp_path, ex,
        ("UPDATE interface_message SET payload = ? WHERE message_id = ?", (tampered, mid)),
    )
    label = f"EX01 outbound[ACC-EX01-0001 {fmt}]"
    per_message = f"{label} message[{mid}] per-probe values"
    assert _failed(result) == {f"{label} matches_results", per_message}
    detail = _check(result, per_message)
    assert detail["expected"]["CEP8"][field] == want
    assert detail["observed"]["CEP8"][field] == got
    others = {k: v for k, v in detail["observed"]["CEP8"].items() if k != field}
    assert others == {k: v for k, v in detail["expected"]["CEP8"].items() if k != field}


def test_outbound_expected_values_are_recomputed_from_fish_result(built, tmp_path):
    # A fish_result signal_pattern change makes both stored exports stale.
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("UPDATE fish_result SET signal_pattern = '9 zzz' WHERE probe_id = "
         "(SELECT probe_id FROM probe WHERE probe_code = 'CEP8')", ()),
    )
    failed = _failed(result)
    assert "EX01 order[ACC-EX01-0001] result[CEP8] signal_pattern" in failed
    for fmt in ("HL7", "FHIR"):
        assert f"EX01 outbound[ACC-EX01-0001 {fmt}] matches_results" in failed
    hl7 = [c for c in result["checks"]
           if c["name"].startswith("EX01 outbound[ACC-EX01-0001 HL7] message[")]
    assert hl7[0]["expected"]["CEP8"] == {
        "cells_scored": 200, "cells_abnormal": 3, "percent_abnormal": "1.5",
        "interpretation": "NORMAL", "flag": "N", "signal_pattern": "9 zzz",
    }
    assert hl7[0]["observed"]["CEP8"]["signal_pattern"] == "2 aqua"


@pytest.mark.parametrize("ex_id, step, option, value, failing", [
    ("EX01-valid-result-export", 1, "--by", "someone_else",
     "EX01 order[ACC-EX01-0001] report_finalized_by"),
    ("EX06-finalize-blocked-correction", 0, "--pattern", "NOT RECORDED",
     "EX06 order[ACC-EX06-0001] result[TP53_17p] signal_pattern"),
    ("EX06-finalize-blocked-correction", 1, "--entered-by", "tech02",
     "EX06 order[ACC-EX06-0001] result[CEP8] entered_by"),
])
def test_task_specified_actor_and_pattern_are_verified(
    exercise_copy, tmp_path, ex_id, step, option, value, failing
):
    # The analyst follows every step but uses the wrong identity or worksheet
    # pattern: the run still matches every exit code, and verification fails on
    # exactly the stated value.
    def edit(d: dict) -> None:
        argv = d["solution_steps"][step]["argv"]
        argv[argv.index(option) + 1] = value

    _edit_json(exercise_copy / "answer-keys" / f"{ex_id}.json", edit)
    run = exercises.run_exercise(ex_id, tmp_path / "run.db")
    assert all(s["matched"] for s in run["steps"])
    assert run["verification"]["passed"] is False
    assert _failed(run["verification"]) == {failing}
    assert _check(run["verification"], failing)["observed"] == value


def test_tamper_recovery_filing_actor(built, tmp_path):
    # EX02's results were filed by the analyst01 retry, not by the interface.
    result = _verify_tampered(
        built, tmp_path, "EX02-unknown-accession-retry",
        ("UPDATE fish_result SET entered_by = 'interface'", ()),
    )
    failed = _failed(result)
    assert len(failed) == 9
    assert all(n.startswith("EX02 order[ACC-EX02-0001] result[") and n.endswith("] entered_by")
               for n in failed)
    check = _check(result, "EX02 order[ACC-EX02-0001] result[CEP8] entered_by")
    assert (check["expected"], check["observed"]) == ("analyst01", "interface")


def test_verify_reads_one_snapshot(built, tmp_path, monkeypatch):
    # verify_exercise holds one read transaction for all of its queries, so a
    # writer cannot commit in the middle of a verification.
    source = tmp_path / "snapshot.db"
    shutil.copyfile(built["EX05-replay-and-conflict"], source)
    seen = []
    original = exercises.reconcile.reconcile_database

    def spy(conn, *args, **kwargs):
        seen.append(conn.in_transaction)
        with closing(sqlite3.connect(source, timeout=0)) as writer:
            writer.execute(
                "INSERT INTO patient (mrn, last_name, first_name, date_of_birth, sex) "
                "VALUES ('SYN-EX05-99', 'Synthetic', 'Writer', '1970-01-05', 'U')"
            )
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                writer.commit()
            writer.rollback()
        return original(conn, *args, **kwargs)

    monkeypatch.setattr(exercises.reconcile, "reconcile_database", spy)
    assert exercises.verify_exercise("EX05", source)["passed"] is True
    assert seen == [True]


def test_unsupported_result_key_fails_instead_of_being_ignored(built, exercise_copy):
    ex = "EX03-invalid-result-redrive"
    _edit_json(
        exercise_copy / "answer-keys" / f"{ex}.json",
        lambda d: d["expected"]["orders"][0]["results"]["CEP8"].update(
            {"entered_at": "2026-10-25 09:00:00"}
        ),
    )
    result = exercises.verify_exercise(ex, built[ex])
    assert result["passed"] is False
    assert _failed(result) == {
        "EX03 answer key order[ACC-EX03-0001] result[CEP8] key 'entered_at' is supported"
    }


def test_tamper_outbound_count(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("DELETE FROM interface_message WHERE direction = 'OUTBOUND' AND format = 'FHIR'", ()),
    )
    failed = _failed(result)
    assert "EX01 outbound[ACC-EX01-0001 FHIR] count" in failed
    # No stored message means nothing matches the results.
    assert "EX01 outbound[ACC-EX01-0001 FHIR] matches_results" in failed


def test_tamper_queue_status(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX04-finalized-order-terminal",
        ("UPDATE interface_error_queue SET status = 'OPEN', terminal_at = NULL", ()),
    )
    assert "EX04 queue[EX04-LATE] status" in _failed(result)


def test_tamper_conflict_audit_event(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX05-replay-and-conflict",
        ("DELETE FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'", ()),
    )
    failed = _failed(result)
    assert "EX05 queue[EX05-ORIG] conflict_audit_events" in failed
    check = _check(result, "EX05 queue[EX05-ORIG] conflict_audit_events")
    assert (check["expected"], check["observed"]) == (1, 0)


def test_tamper_resulting_message_status(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX07-failed-recovery-then-success",
        ("UPDATE interface_message SET status = 'RECEIVED' WHERE message_id = "
         "(SELECT resulting_message_id FROM interface_recovery_attempt "
         " WHERE request_id = 'EX07-REDRIVE-1')", ()),
    )
    assert "EX07 queue[EX07-ORIG] attempt[1] resulting_message_status" in _failed(result)


def test_null_report_status_is_asserted(built, tmp_path):
    # report_status null in the answer key means NO report row.
    result = _verify_tampered(
        built, tmp_path, "EX02-unknown-accession-retry",
        ("INSERT INTO report (order_id, summary_text) VALUES (1, 'synthetic tamper')", ()),
    )
    check = _check(result, "EX02 order[ACC-EX02-0001] report_status")
    assert (check["passed"], check["expected"], check["observed"]) == (False, None, "DRAFT")


def test_tamper_validation_blocking(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX06-finalize-blocked-correction",
        ("UPDATE fish_result SET interpretation = 'NORMAL' WHERE probe_id = "
         "(SELECT probe_id FROM probe WHERE probe_code = 'CEP8')", ()),
    )
    failed = _failed(result)
    assert "EX06 order[ACC-EX06-0001] validation_blocking" in failed
    assert "EX06 order[ACC-EX06-0001] result[CEP8] interpretation" in failed


def test_tamper_blob_payload_is_reported_not_crashed(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX01-valid-result-export",
        ("UPDATE interface_message SET payload = CAST(payload AS BLOB) "
         "WHERE direction = 'OUTBOUND' AND format = 'HL7'", ()),
    )
    assert "EX01 outbound[ACC-EX01-0001 HL7] matches_results" in _failed(result)
    json.dumps(result)  # every observed value stays JSON-safe


def test_tamper_order_status_and_totals(built, tmp_path):
    result = _verify_tampered(
        built, tmp_path, "EX02-unknown-accession-retry",
        ("UPDATE lab_order SET status = 'IN_PROCESS'", ()),
        ("INSERT INTO patient (mrn, last_name, first_name, date_of_birth, sex) "
         "VALUES ('SYN-EX02-99', 'Synthetic', 'Extra', '1970-01-02', 'U')", ()),
    )
    failed = _failed(result)
    assert "EX02 order[ACC-EX02-0001] status" in failed
    assert "EX02 totals[patient]" in failed


# ---------------------------------------------------------------------------
# Fixture bytes and answer-key vocabulary (copied exercises tree)
# ---------------------------------------------------------------------------

@pytest.fixture()
def exercise_copy(tmp_path, monkeypatch) -> Path:
    """A private copy of exercises/ that the module is pointed at."""
    root = tmp_path / "exercises"
    shutil.copytree(exercises.EXERCISES_DIR, root)
    monkeypatch.setattr(exercises, "EXERCISES_DIR", root)
    monkeypatch.setattr(exercises, "ANSWER_KEYS_DIR", root / "answer-keys")
    return root


def _edit_json(path: Path, edit: Callable[[dict], None]) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_fixture_fingerprints_come_from_file_bytes(built, exercise_copy):
    ex = "EX03-invalid-result-redrive"
    fixture = exercise_copy / ex / "inputs" / "corrected_result.hl7"
    fixture.write_bytes(fixture.read_bytes() + b"\n")
    failed = _failed(exercises.verify_exercise(ex, built[ex]))
    assert failed == {
        "EX03 queue[EX03-ORIG] attempt[1] payload_fixture sha256",
        "EX03 queue[EX03-ORIG] attempt[1] resulting message payload sha256",
    }


def test_unsupported_expected_key_fails_instead_of_being_ignored(built, exercise_copy):
    ex = "EX03-invalid-result-redrive"
    _edit_json(
        exercise_copy / "answer-keys" / f"{ex}.json",
        lambda d: d["expected"]["orders"][0].update({"priority": "STAT"}),
    )
    result = exercises.verify_exercise(ex, built[ex])
    assert result["passed"] is False
    assert _failed(result) == {
        "EX03 answer key orders[0] key 'priority' is supported"
    }


def test_answer_key_unknown_top_level_keys_are_ignored(built, exercise_copy):
    ex = "EX03-invalid-result-redrive"
    _edit_json(
        exercise_copy / "answer-keys" / f"{ex}.json",
        lambda d: d.update({"instructor_notes": "ignored"}),
    )
    assert exercises.verify_exercise(ex, built[ex])["passed"] is True


def test_empty_expected_never_passes(built, exercise_copy):
    ex = "EX03-invalid-result-redrive"
    _edit_json(
        exercise_copy / "answer-keys" / f"{ex}.json",
        lambda d: d.update({"expected": {}}),
    )
    result = exercises.verify_exercise(ex, built[ex])
    assert result == {"exercise": ex, "passed": False, "checks": []}


def test_fixture_path_outside_exercise_is_refused(built, exercise_copy):
    ex = "EX03-invalid-result-redrive"
    _edit_json(
        exercise_copy / "answer-keys" / f"{ex}.json",
        lambda d: d["expected"]["queue_items"][0].update(
            {"original_payload_fixture": "../EX02-unknown-accession-retry/inputs/early_result.hl7"}
        ),
    )
    with pytest.raises(AnalystError) as exc:
        exercises.verify_exercise(ex, built[ex])
    assert exc.value.kind == "INVALID_INPUT"


# ---------------------------------------------------------------------------
# Exercise ids, listing, instructions, and answer-key separation
# ---------------------------------------------------------------------------

def test_list_exercises_reads_every_setup_sorted():
    items = exercises.list_exercises()
    assert [e["id"] for e in items] == EXERCISE_IDS
    for item in items:
        setup = json.loads(
            (exercises.EXERCISES_DIR / item["id"] / "setup.json").read_text(encoding="utf-8")
        )
        assert item == {"id": setup["id"], "title": setup["title"], "summary": setup["summary"]}


@pytest.mark.parametrize("alias, resolved", [
    ("EX03-invalid-result-redrive", "EX03-invalid-result-redrive"),
    ("EX03", "EX03-invalid-result-redrive"),
    ("ex03", "EX03-invalid-result-redrive"),
    ("ex07-FAILED", "EX07-failed-recovery-then-success"),
    ("EX03-INVALID-RESULT-REDRIVE", "EX03-invalid-result-redrive"),
])
def test_exercise_id_aliases(alias, resolved):
    loaded = exercises.load_exercise(alias)
    assert loaded["id"] == resolved
    assert loaded["setup"]["id"] == resolved
    assert loaded["answer_key"]["id"] == resolved
    assert exercises.instructions_text(alias) == (
        exercises.EXERCISES_DIR / resolved / "README.md"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize("bad, kind", [
    ("EX99", "NOT_FOUND"),
    ("EX03-nope", "NOT_FOUND"),
    ("answer-keys", "NOT_FOUND"),
    ("../EX03-invalid-result-redrive", "NOT_FOUND"),
    ("EX0", "USAGE"),
    ("ex", "USAGE"),
    ("", "USAGE"),
])
def test_unknown_or_ambiguous_exercise_ids(bad, kind, tmp_path):
    for call in (
        lambda: exercises.load_exercise(bad),
        lambda: exercises.instructions_text(bad),
        lambda: exercises.verify_exercise(bad, tmp_path / "x.db"),
        lambda: exercises.setup_exercise(bad, tmp_path / "x.db"),
        lambda: exercises.run_exercise(bad, tmp_path / "x.db"),
    ):
        with pytest.raises(AnalystError) as exc:
            call()
        assert exc.value.kind == kind
    assert not (tmp_path / "x.db").exists()


@pytest.mark.parametrize("ex_id", EXERCISE_IDS)
def test_setup_files_carry_no_answer_key_content(ex_id):
    setup = json.loads(
        (exercises.EXERCISES_DIR / ex_id / "setup.json").read_text(encoding="utf-8")
    )
    assert not set(exercises.ANSWER_KEY_ONLY_KEYS) & set(setup)
    # Answer keys live outside the analyst-facing exercise directory.
    assert sorted(p.name for p in (exercises.EXERCISES_DIR / ex_id).glob("*.json")) == [
        "setup.json"
    ]
    assert (exercises.ANSWER_KEYS_DIR / f"{ex_id}.json").is_file()
    assert exercises.ANSWER_KEYS_DIR.parent == exercises.EXERCISES_DIR


def _answer_only_fragments(key: dict) -> list[str]:
    """Sentence-sized fragments of instructor-only text in an answer key."""
    texts = [qa["answer"] for qa in key.get("question_answers", [])]
    texts += [step["note"] for step in key.get("solution_steps", [])]
    fragments = []
    for text in texts:
        for fragment in re.split(r"(?<=[.;:])\s+", text):
            if len(fragment.strip()) >= 40:
                fragments.append(fragment.strip())
    return fragments


def test_analyst_instructions_contain_no_answer_key_text():
    readmes = {"exercises/README.md": (exercises.EXERCISES_DIR / "README.md").read_text(
        encoding="utf-8")}
    for ex_id in EXERCISE_IDS:
        readmes[ex_id] = exercises.instructions_text(ex_id)
    checked = 0
    for ex_id in EXERCISE_IDS:
        key = exercises.load_exercise(ex_id)["answer_key"]
        assert key.get("question_answers"), ex_id
        for fragment in _answer_only_fragments(key):
            checked += 1
            for where, text in readmes.items():
                assert fragment not in text, (ex_id, where, fragment)
    assert checked > 50


def test_setup_refuses_existing_database_path(tmp_path):
    existing = tmp_path / "existing.db"
    existing.write_bytes(b"synthetic placeholder - keep these bytes")
    before = existing.read_bytes()
    for call in (exercises.setup_exercise, exercises.run_exercise):
        with pytest.raises(AnalystError) as exc:
            call("EX03", existing)
        assert exc.value.kind == "REFUSED"
        assert existing.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["existing.db"]


def test_setup_refuses_missing_parent_directory(tmp_path):
    with pytest.raises(AnalystError) as exc:
        exercises.setup_exercise("EX03", tmp_path / "no-such-dir" / "x.db")
    assert exc.value.kind == "NOT_FOUND"
    assert not (tmp_path / "no-such-dir").exists()


def test_run_all_refuses_bad_workdirs(tmp_path):
    as_file = tmp_path / "a-file"
    as_file.write_text("synthetic", encoding="utf-8")
    with pytest.raises(AnalystError) as exc:
        exercises.run_all(as_file)
    assert exc.value.kind == "INVALID_INPUT"
    with pytest.raises(AnalystError) as exc:
        exercises.run_all(tmp_path / "missing-parent" / "work")
    assert exc.value.kind == "NOT_FOUND"
    assert not (tmp_path / "missing-parent").exists()
    # One pre-existing target database refuses the whole run before anything runs.
    work = tmp_path / "work"
    work.mkdir()
    (work / f"{EXERCISE_IDS[4]}.db").write_bytes(b"keep")
    with pytest.raises(AnalystError) as exc:
        exercises.run_all(work)
    assert exc.value.kind == "REFUSED"
    assert sorted(p.name for p in work.iterdir()) == [f"{EXERCISE_IDS[4]}.db"]
    assert (work / f"{EXERCISE_IDS[4]}.db").read_bytes() == b"keep"


# ---------------------------------------------------------------------------
# Runner end to end through the in-process CLI
# ---------------------------------------------------------------------------

def _normalized_snapshot(path: Path) -> dict:
    """All tables with timestamps and outbound generation-time values masked."""
    with closing(store.open_read_only(path)) as conn:
        snapshot = preview.snapshot_tables(conn)
    for table, rows in snapshot.items():
        for row in rows.values():
            for col in preview.TIMESTAMP_COLUMNS & set(row):
                if row[col] is not None:
                    row[col] = preview.TIMESTAMP_PLACEHOLDER
            if table == "interface_message" and row["direction"] == "OUTBOUND":
                row["payload"] = "<outbound payload>"
                row["control_id"] = "<outbound control id>"
    return snapshot


def _row_count(path: Path, sql: str) -> list[tuple]:
    with closing(store.open_read_only(path)) as conn:
        return [tuple(r) for r in conn.execute(sql).fetchall()]


def test_setup_runs_only_setup_steps(tmp_path):
    db_path = tmp_path / "ex03.db"
    result = exercises.setup_exercise("ex03", db_path)
    setup = exercises.load_exercise("EX03")["setup"]
    assert result["exercise"] == "EX03-invalid-result-redrive"
    assert result["database"] == str(db_path)
    assert len(result["steps"]) == len(setup["steps"])
    inputs = str(exercises.EXERCISES_DIR / "EX03-invalid-result-redrive" / "inputs")
    for step, spec in zip(result["steps"], setup["steps"]):
        assert set(step) == {"argv", "exit_code", "expect_exit", "matched", "ok", "error"}
        assert step["matched"] is True
        assert step["expect_exit"] == spec["expect_exit"] == step["exit_code"]
        assert step["ok"] is (step["exit_code"] == 0)
        assert "--json" not in step["argv"]
        assert not any("{db}" in a or "{inputs}" in a for a in step["argv"])
    assert result["steps"][0]["argv"] == ["init", "--db", str(db_path)]
    assert f"{inputs}/original_result.hl7" in result["steps"][-1]["argv"]
    assert result["steps"][-1]["error"]["type"] == "REFUSED"
    # Only the incident exists: an OPEN queue item and no recovery attempts.
    assert _row_count(db_path, "SELECT status FROM interface_error_queue") == [("OPEN",)]
    assert _row_count(db_path, "SELECT COUNT(*) FROM interface_recovery_attempt") == [(0,)]
    assert exercises.verify_exercise("EX03", db_path)["passed"] is False


def test_unmatched_step_stops_the_run_but_verification_still_runs(exercise_copy, tmp_path):
    ex = "EX03-invalid-result-redrive"
    _edit_json(
        exercise_copy / ex / "setup.json",
        lambda d: d["steps"][1].update({"expect_exit": 3}),
    )
    setup = exercises.setup_exercise(ex, tmp_path / "setup.db")
    assert [s["matched"] for s in setup["steps"]] == [True, False]
    run = exercises.run_exercise(ex, tmp_path / "run.db")
    assert [s["matched"] for s in run["steps"]] == [True, False]
    assert run["steps"][1]["exit_code"] == 0
    assert run["verification"]["passed"] is False
    assert run["verification"]["checks"], "verification ran on the persisted rows"
    assert "EX03 queue[EX03-ORIG] matching queue items" in _failed(run["verification"])


def test_run_reports_unverifiable_database_as_failed_check(exercise_copy, tmp_path):
    ex = "EX03-invalid-result-redrive"
    # init creates a different file, so the exercise database never exists.
    _edit_json(
        exercise_copy / ex / "setup.json",
        lambda d: d["steps"][0].update({"argv": ["init", "--db", "{db}.other"]}),
    )
    run = exercises.run_exercise(ex, tmp_path / "never.db")
    assert [(s["exit_code"], s["matched"]) for s in run["steps"]] == [(0, True), (3, False)]
    assert run["steps"][1]["error"]["type"] == "NOT_FOUND"
    assert not (tmp_path / "never.db").exists()
    assert run["verification"]["passed"] is False
    assert [c["name"] for c in run["verification"]["checks"]] == ["EX03 database readable"]


@pytest.mark.parametrize("ex_id", EXERCISE_IDS)
def test_run_exercise_end_to_end_is_deterministic(ex_id, tmp_path):
    first = exercises.run_exercise(ex_id, tmp_path / "first.db")
    assert set(first) == {"exercise", "database", "steps", "verification"}
    assert first["exercise"] == ex_id
    loaded = exercises.load_exercise(ex_id)
    assert len(first["steps"]) == (
        len(loaded["setup"]["steps"]) + len(loaded["answer_key"]["solution_steps"])
    )
    assert [s for s in first["steps"] if not s["matched"]] == []
    assert _failed(first["verification"]) == set()
    assert first["verification"]["passed"] is True
    # The CLI-built database also verifies standalone, and matches the
    # service-built verification check for check.
    standalone = exercises.verify_exercise(ex_id, tmp_path / "first.db")
    assert standalone == first["verification"]

    second = exercises.run_exercise(ex_id, tmp_path / "second.db")
    assert second["verification"] == first["verification"]
    assert _masked_steps(second) == _masked_steps(first)
    assert _normalized_snapshot(tmp_path / "first.db") == _normalized_snapshot(
        tmp_path / "second.db"
    )


def _masked_steps(result: dict) -> list:
    """Step outcomes with the (run-specific) database path masked."""
    db_path = result["database"]
    return [
        (
            [a.replace(db_path, "<db>") for a in s["argv"]],
            s["exit_code"], s["expect_exit"], s["matched"], s["ok"],
            json.dumps(s["error"], sort_keys=True).replace(db_path, "<db>"),
        )
        for s in result["steps"]
    ]


def test_cli_built_and_service_built_end_states_agree(built, tmp_path):
    for ex_id in EXERCISE_IDS:
        run = exercises.run_exercise(ex_id, tmp_path / f"{ex_id}.db")
        assert run["verification"] == exercises.verify_exercise(ex_id, built[ex_id])


def test_exercises_do_not_cross_contaminate(tmp_path):
    accessions = {ex: f"ACC-{ex.split('-')[0]}-0001" for ex in EXERCISE_IDS}
    for ex_id in ("EX01-valid-result-export", "EX02-unknown-accession-retry"):
        db_path = tmp_path / f"{ex_id}.db"
        assert exercises.run_exercise(ex_id, db_path)["verification"]["passed"]
        own = accessions[ex_id]
        assert _row_count(db_path, "SELECT accession_number FROM lab_order") == [(own,)]
        assert _row_count(db_path, "SELECT mrn FROM patient") == [
            (f"SYN-{ex_id.split('-')[0]}-01",)
        ]
        payloads = [p for (p,) in _row_count(db_path, "SELECT payload FROM interface_message")]
        assert payloads
        for payload in payloads:
            assert own in payload
            assert not any(acc in payload for ex, acc in accessions.items() if ex != ex_id)


def test_run_all_passes_and_refuses_a_reused_workdir(tmp_path):
    work = tmp_path / "work"
    result = exercises.run_all(work)
    assert set(result) == {"passed", "exercises"}
    assert result["passed"] is True
    assert [e["exercise"] for e in result["exercises"]] == EXERCISE_IDS
    for item in result["exercises"]:
        assert item["passed"] is True
        assert item["verification"]["passed"] is True
        assert item["database"] == str(work / f"{item['exercise']}.db")
    files = sorted(p.name for p in work.iterdir())
    assert files == sorted(f"{ex}.db" for ex in EXERCISE_IDS)
    hashes = {name: sha256_file(work / name) for name in files}
    with pytest.raises(AnalystError) as exc:
        exercises.run_all(work)
    assert exc.value.kind == "REFUSED"
    assert {name: sha256_file(work / name) for name in files} == hashes
    assert sorted(p.name for p in work.iterdir()) == files
