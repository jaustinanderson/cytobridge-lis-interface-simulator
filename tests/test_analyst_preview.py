"""Tests for the analyst-lab non-mutating recovery preview (src.analyst.preview).

The preview copies a CytoBridge database file into an isolated in-memory
database with the sqlite3 backup API and runs the UNCHANGED public recovery
service (``recovery.retry_queue_item`` / ``recovery.redrive_queue_item``) on that
copy. These tests prove, against real file databases built only through the
public services (direct SQL is used solely to construct an impossible-by-service
state for the BLOCKED case):

- the source file bytes, every table row, and the request id are untouched;
- the preview's change set equals the change set of a real apply from the same
  baseline (timestamps masked), and its simulated status equals the real
  outcome;
- a later apply is decided by the then-current state, not by the preview;
- handled-failure rollback and unexpected-exception behavior are the service's
  own (exercised through test-only monkeypatching, never in src/).

All data is synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import pytest

from src import recovery, workflow
from src.analyst import preview, store
from src.analyst.common import LIMITATIONS, AnalystError
from src.interfaces import inbound_hl7
from tests.conftest import enter_all_results

RETRY = "RETRY_ORIGINAL"
REDRIVE = "REDRIVE_CORRECTED"

# Synthetic accessions / MRNs for this module only.
ACC_OPEN = "ACC-PRV-0001"      # open order; its first message fails INVALID_CELL_COUNT
ACC_RETRY = "ACC-PRV-0002"     # no order at baseline; message fails ORDER_NOT_FOUND
ACC_FINAL = "ACC-PRV-0003"     # finalized order; message queued TERMINAL
ACC_RESOLVED = "ACC-PRV-0004"  # open order; its queue item resolved at baseline

BASE_REQUEST = "REQ-PRV-BASE-04"
BASE_ACTOR = "analyst.base"

ATTEMPT_KEYS = {
    "attempt_id", "queue_id", "request_id", "action", "payload_sha256",
    "outcome", "actor", "outcome_detail", "attempted_at", "resulting_message_id",
}


def _oru(control_id: str, accession: str, obx: list[tuple[str, str]]) -> str:
    """A synthetic inbound ORU-style FISH message (CR segment terminators)."""
    lines = [
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260715100500||"
        f"ORU^R01|{control_id}|T|2.5.1",
        "PID|1||SYN-PRV-01^^^CYTO_LAB^MR||Synthetic^Preview||19700101|U",
        f"OBR|1||{accession}|AML_MDS_FISH^AML/MDS FISH Panel^L|||20260714090000"
        "|||||||||^Dr. Synthetic||||||20260715100000|||F",
        "SPM|1|BM-PRV||BMA^Bone Marrow^L|||||||||||||20260714090000|20260714093000",
    ]
    for set_id, (probe, value) in enumerate(obx, start=1):
        lines.append(f"OBX|{set_id}|ST|{probe}^{probe}^L||{value}||||||F")
    return "\r".join(lines) + "\r"


_TWO_VALID = [
    ("RUNX1T1_RUNX1", "200^2^2F2G^NORMAL"),
    ("PML_RARA", "200^2^2R2G^NORMAL"),
]

ORIG_BAD_COUNT = _oru(
    "PRV-01-ORIG", ACC_OPEN,
    [("RUNX1T1_RUNX1", "200^2^2F2G^NORMAL"), ("PML_RARA", "xx^2^2R2G^NORMAL")],
)
CORRECTED_OPEN = _oru("PRV-01-CORR", ACC_OPEN, _TWO_VALID)
STILL_BAD_OPEN = _oru(
    "PRV-01-BAD2", ACC_OPEN,
    [("RUNX1T1_RUNX1", "200^2^2F2G^NORMAL"), ("PML_RARA", "200^250^2R2G^NORMAL")],
)
ORIG_NOT_FOUND = _oru("PRV-02-ORIG", ACC_RETRY, _TWO_VALID)
ORIG_FINAL = _oru("PRV-03-ORIG", ACC_FINAL, _TWO_VALID)
CORRECTED_FINAL = _oru("PRV-03-CORR", ACC_FINAL, _TWO_VALID)
ORIG_UNKNOWN_PROBE = _oru(
    "PRV-04-ORIG", ACC_RESOLVED, [("NOT_A_PANEL_PROBE", "200^2^2F2G^NORMAL")]
)
CORRECTED_RESOLVED = _oru(
    "PRV-04-CORR", ACC_RESOLVED, [("CBFB", "200^1^2 orange/green^NORMAL")]
)


# ---------------------------------------------------------------------------
# Baseline database (built once through public services, copied per test)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Baseline:
    path: Path
    orders: dict[str, int]
    queue: dict[str, int]


def _make_order(conn: sqlite3.Connection, mrn: str, accession: str) -> int:
    patient_id = workflow.create_patient(
        conn, mrn, "Synthetic", "Preview", "1970-01-01", "U"
    )
    order_id = workflow.create_order(conn, patient_id, accession, "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def _queue_from(conn: sqlite3.Connection, payload: str) -> int:
    result = inbound_hl7.ingest_message(conn, payload)
    assert not result.filed and result.queue_id is not None
    return result.queue_id


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> Baseline:
    path = store.init_database(tmp_path_factory.mktemp("preview") / "baseline.db")
    with closing(store.open_read_write(path)) as conn:
        open_order = _make_order(conn, "SYN-PRV-01", ACC_OPEN)
        q_redrive = _queue_from(conn, ORIG_BAD_COUNT)

        q_retry = _queue_from(conn, ORIG_NOT_FOUND)

        final_order = _make_order(conn, "SYN-PRV-03", ACC_FINAL)
        enter_all_results(conn, final_order)
        assert workflow.finalize_order(conn, final_order, "pathologist.syn").finalized
        q_terminal = _queue_from(conn, ORIG_FINAL)

        resolved_order = _make_order(conn, "SYN-PRV-04", ACC_RESOLVED)
        q_resolved = _queue_from(conn, ORIG_UNKNOWN_PROBE)
        base = recovery.redrive_queue_item(
            conn, q_resolved, CORRECTED_RESOLVED,
            request_id=BASE_REQUEST, actor=BASE_ACTOR,
        )
        assert base.outcome == "SUCCEEDED"

        statuses = dict(conn.execute(
            "SELECT queue_id, status FROM interface_error_queue"
        ).fetchall())
    assert statuses == {
        q_redrive: "OPEN", q_retry: "OPEN",
        q_terminal: "TERMINAL", q_resolved: "RESOLVED",
    }
    return Baseline(
        path=path,
        orders={"open": open_order, "final": final_order, "resolved": resolved_order},
        queue={
            "redrive": q_redrive, "retry": q_retry,
            "terminal": q_terminal, "resolved": q_resolved,
        },
    )


def _copy(b: Baseline, tmp_path: Path, name: str = "source.db") -> Path:
    target = tmp_path / name
    shutil.copyfile(b.path, target)
    return target


def _rw(path: Path):
    return closing(store.open_read_write(path))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ro_snapshot(path: Path) -> dict:
    """Full table snapshot through a separate read-only connection."""
    with closing(store.open_read_only(path)) as conn:
        return preview.snapshot_tables(conn)


def _counts(path: Path) -> dict[str, int]:
    with closing(store.open_read_only(path)) as conn:
        return {
            table: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("interface_recovery_attempt", "audit_event",
                          "interface_message")
        }


# ---------------------------------------------------------------------------
# Case table
# ---------------------------------------------------------------------------

def _prep_create_retry_order(path: Path, b: Baseline) -> None:
    with _rw(path) as conn:
        _make_order(conn, "SYN-PRV-02", ACC_RETRY)


def _prep_finalize_open_order(path: Path, b: Baseline) -> None:
    with _rw(path) as conn:
        enter_all_results(conn, b.orders["open"])
        result = workflow.finalize_order(conn, b.orders["open"], "pathologist.syn")
        assert result.finalized


def _prep_null_classification(path: Path, b: Baseline) -> None:
    # Impossible by service: the inbound path always stores a classification.
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(
            "UPDATE interface_error_queue SET failure_code = NULL, "
            "failure_category = NULL, recovery_policy = NULL WHERE queue_id = ?",
            (b.queue["redrive"],),
        )
        conn.commit()


@dataclass(frozen=True)
class Case:
    name: str
    queue: str
    action: str
    payload: str | None
    request_id: str
    actor: str
    expected: str
    prep: Callable[[Path, Baseline], None] | None = None


SUCCEEDED_REDRIVE = Case(
    "succeeded-redrive", "redrive", REDRIVE, CORRECTED_OPEN,
    "REQ-PRV-01", "analyst.one", "SUCCEEDED",
)
SUCCEEDED_RETRY = Case(
    "succeeded-retry", "retry", RETRY, None,
    "REQ-PRV-02", "analyst.one", "SUCCEEDED", _prep_create_retry_order,
)
FAILED_REDRIVE = Case(
    "failed-redrive", "redrive", REDRIVE, STILL_BAD_OPEN,
    "REQ-PRV-03", "analyst.one", "FAILED",
)
REJECTED_TERMINAL_ITEM = Case(
    "rejected-terminal-item", "terminal", REDRIVE, CORRECTED_FINAL,
    "REQ-PRV-04", "analyst.one", "REJECTED",
)
REJECTED_RESOLVED_ITEM = Case(
    "rejected-resolved-item", "resolved", REDRIVE, CORRECTED_RESOLVED,
    "REQ-PRV-05", "analyst.one", "REJECTED",
)
REJECTED_RETRY_NOT_PERMITTED = Case(
    "rejected-retry-redrive-only", "redrive", RETRY, None,
    "REQ-PRV-06", "analyst.one", "REJECTED",
)
REJECTED_DYNAMIC_TERMINAL = Case(
    "rejected-dynamic-terminal", "redrive", REDRIVE, CORRECTED_OPEN,
    "REQ-PRV-07", "analyst.one", "REJECTED", _prep_finalize_open_order,
)
REPLAYED = Case(
    "replayed", "resolved", REDRIVE, CORRECTED_RESOLVED,
    BASE_REQUEST, BASE_ACTOR, "REPLAYED",
)
REQUEST_ID_CONFLICT = Case(
    "request-id-conflict", "resolved", REDRIVE, CORRECTED_RESOLVED,
    BASE_REQUEST, "analyst.other", "REQUEST_ID_CONFLICT",
)
BLOCKED = Case(
    "blocked-null-classification", "redrive", REDRIVE, CORRECTED_OPEN,
    "REQ-PRV-08", "analyst.one", "BLOCKED", _prep_null_classification,
)

NON_MUTATION_CASES = [
    SUCCEEDED_REDRIVE, SUCCEEDED_RETRY, FAILED_REDRIVE, REJECTED_TERMINAL_ITEM,
    REJECTED_RESOLVED_ITEM, REPLAYED, REQUEST_ID_CONFLICT, BLOCKED,
]
CONSISTENCY_CASES = [
    SUCCEEDED_REDRIVE, SUCCEEDED_RETRY, FAILED_REDRIVE, REJECTED_RETRY_NOT_PERMITTED,
    REJECTED_DYNAMIC_TERMINAL, REJECTED_RESOLVED_ITEM, REPLAYED,
    REQUEST_ID_CONFLICT, BLOCKED,
]


def _preview(path: Path, b: Baseline, case: Case, **overrides) -> dict:
    kwargs = dict(
        action=case.action, request_id=case.request_id, actor=case.actor,
        corrected_payload=case.payload,
    )
    kwargs.update(overrides)
    return preview.preview_recovery(path, b.queue[case.queue], **kwargs)


def _apply(conn: sqlite3.Connection, b: Baseline, case: Case, **overrides):
    request_id = overrides.get("request_id", case.request_id)
    actor = overrides.get("actor", case.actor)
    if case.action == RETRY:
        return recovery.retry_queue_item(
            conn, b.queue[case.queue], request_id=request_id, actor=actor
        )
    return recovery.redrive_queue_item(
        conn, b.queue[case.queue], case.payload, request_id=request_id, actor=actor
    )


def _apply_status(conn: sqlite3.Connection, b: Baseline, case: Case):
    """Apply for real; return (status in the preview vocabulary, attempt|None)."""
    prior = conn.execute(
        "SELECT attempt_id FROM interface_recovery_attempt WHERE request_id = ?",
        (case.request_id,),
    ).fetchone()
    try:
        attempt = _apply(conn, b, case)
    except recovery.RequestIdConflictError:
        return "REQUEST_ID_CONFLICT", None
    except recovery.RecoveryError:
        return "BLOCKED", None
    if prior is not None and prior[0] == attempt.attempt_id:
        return "REPLAYED", attempt
    return attempt.outcome, attempt


def _queue_status(path: Path, queue_id: int) -> str:
    with closing(store.open_read_only(path)) as conn:
        return conn.execute(
            "SELECT status FROM interface_error_queue WHERE queue_id = ?",
            (queue_id,),
        ).fetchone()[0]


def _mask(attempt: dict) -> dict:
    return {**attempt, "attempted_at": "<timestamp>"}


# ---------------------------------------------------------------------------
# Non-mutation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", NON_MUTATION_CASES, ids=lambda c: c.name)
def test_preview_never_mutates_source(baseline, tmp_path, case):
    source = _copy(baseline, tmp_path)
    if case.prep:
        case.prep(source, baseline)
    sha_before = _file_sha(source)
    snap_before = _ro_snapshot(source)
    counts_before = _counts(source)
    files_before = sorted(p.name for p in tmp_path.iterdir())

    result = _preview(source, baseline, case)

    assert result["simulated"]["status"] == case.expected
    assert _file_sha(source) == sha_before
    assert _ro_snapshot(source) == snap_before
    assert _counts(source) == counts_before
    assert sorted(p.name for p in tmp_path.iterdir()) == files_before
    assert result["snapshot"]["source_sha256_before"] == sha_before
    assert result["snapshot"]["source_sha256_after"] == sha_before
    assert result["snapshot"]["source_unchanged"] is True


def test_blocked_preview_reports_service_error_and_records_nothing(baseline, tmp_path):
    source = _copy(baseline, tmp_path)
    _prep_null_classification(source, baseline)
    result = _preview(source, baseline, BLOCKED)
    sim = result["simulated"]
    assert sim["status"] == "BLOCKED"
    assert "incomplete classification" in sim["error"]
    assert sim["attempt"] is None and sim["recorded_outcome"] is None
    assert result["changes"] == {} and result["change_counts"] == {}
    assert sim["queue_status_before"] == sim["queue_status_after"] == "OPEN"


def test_preview_does_not_consume_request_id(baseline, tmp_path):
    source = _copy(baseline, tmp_path)
    result = _preview(source, baseline, SUCCEEDED_REDRIVE)
    assert result["simulated"]["status"] == "SUCCEEDED"

    with _rw(source) as conn:
        prior = {r[0] for r in conn.execute(
            "SELECT attempt_id FROM interface_recovery_attempt").fetchall()}
        assert conn.execute(
            "SELECT count(*) FROM interface_recovery_attempt WHERE request_id = ?",
            (SUCCEEDED_REDRIVE.request_id,),
        ).fetchone()[0] == 0
        attempt = _apply(conn, baseline, SUCCEEDED_REDRIVE)
        after = {r[0] for r in conn.execute(
            "SELECT attempt_id FROM interface_recovery_attempt").fetchall()}

    # Processed fresh: a new attempt row, not a replay of anything recorded.
    assert attempt.attempt_id not in prior
    assert after == prior | {attempt.attempt_id}
    assert attempt.outcome == "SUCCEEDED"
    assert attempt.request_id == SUCCEEDED_REDRIVE.request_id
    # Same baseline, same service: the preview simulated this very attempt row.
    assert _mask(result["simulated"]["attempt"]) == _mask(asdict(attempt))
    assert _queue_status(source, baseline.queue["redrive"]) == "RESOLVED"


# ---------------------------------------------------------------------------
# Consistency with a real apply from the same baseline
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case", CONSISTENCY_CASES, ids=lambda c: c.name)
def test_preview_matches_real_apply_from_same_baseline(baseline, tmp_path, case):
    base = _copy(baseline, tmp_path, "base.db")
    if case.prep:
        case.prep(base, baseline)
    copy_a = tmp_path / "a.db"
    copy_b = tmp_path / "b.db"
    shutil.copyfile(base, copy_a)
    shutil.copyfile(base, copy_b)
    snap_a_before = _ro_snapshot(copy_a)

    result = _preview(copy_a, baseline, case)

    with _rw(copy_b) as conn:
        b_before = preview.snapshot_tables(conn)
        real_status, real_attempt = _apply_status(conn, baseline, case)
        b_after = preview.snapshot_tables(conn)
    real_changes = preview.diff_snapshots(b_before, b_after)

    sim = result["simulated"]
    assert sim["status"] == case.expected
    assert real_status == case.expected
    assert preview.normalize_changes(result["changes"]) == preview.normalize_changes(
        real_changes
    )
    assert result["change_counts"] == preview.change_counts(real_changes)
    assert sim["queue_status_after"] == _queue_status(copy_b, baseline.queue[case.queue])
    if real_attempt is None:
        assert sim["attempt"] is None and sim["recorded_outcome"] is None
    else:
        assert _mask(sim["attempt"]) == _mask(asdict(real_attempt))
        assert sim["recorded_outcome"] == real_attempt.outcome
    # The preview never wrote its own source.
    assert _ro_snapshot(copy_a) == snap_a_before


def test_conflict_preview_shows_audit_row_that_only_a_real_apply_records(
    baseline, tmp_path
):
    copy_a = _copy(baseline, tmp_path, "a.db")
    copy_b = _copy(baseline, tmp_path, "b.db")

    result = _preview(copy_a, baseline, REQUEST_ID_CONFLICT)
    assert result["simulated"]["status"] == "REQUEST_ID_CONFLICT"
    assert BASE_REQUEST in result["simulated"]["error"]
    assert set(result["changes"]) == {"audit_event"}
    inserted = result["changes"]["audit_event"]["inserted"]
    assert [(r["action"], r["entity_type"], r["entity_id"]) for r in inserted] == [
        ("REQUEST_ID_CONFLICT", "interface_error_queue", baseline.queue["resolved"])
    ]

    with _rw(copy_b) as conn:
        b_before = preview.snapshot_tables(conn)
        with pytest.raises(recovery.RequestIdConflictError):
            _apply(conn, baseline, REQUEST_ID_CONFLICT)
        b_after = preview.snapshot_tables(conn)
    real = preview.diff_snapshots(b_before, b_after)
    assert preview.normalize_changes(real) == preview.normalize_changes(result["changes"])

    def conflict_rows(path: Path) -> int:
        with closing(store.open_read_only(path)) as conn:
            return conn.execute(
                "SELECT count(*) FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'"
            ).fetchone()[0]

    assert conflict_rows(copy_b) == 1
    assert conflict_rows(copy_a) == 0


def test_replayed_preview_reports_recorded_outcome(baseline, tmp_path):
    source = _copy(baseline, tmp_path)
    result = _preview(source, baseline, REPLAYED)
    sim = result["simulated"]
    assert sim["status"] == "REPLAYED"
    assert sim["recorded_outcome"] == "SUCCEEDED"
    assert sim["attempt"]["request_id"] == BASE_REQUEST
    assert result["changes"] == {}


# ---------------------------------------------------------------------------
# Staleness / race: current rules win at apply time, not the preview
# ---------------------------------------------------------------------------

def test_stale_success_preview_then_order_finalized_apply_is_rejected(
    baseline, tmp_path
):
    source = _copy(baseline, tmp_path)
    result = _preview(source, baseline, SUCCEEDED_REDRIVE)
    assert result["simulated"]["status"] == "SUCCEEDED"

    order_id = baseline.orders["open"]
    with _rw(source) as conn:
        enter_all_results(conn, order_id)
        assert workflow.finalize_order(conn, order_id, "pathologist.syn").finalized
        assert workflow.get_order(conn, order_id)["status"] == "FINALIZED"
        attempt = _apply(conn, baseline, SUCCEEDED_REDRIVE)

    assert attempt.outcome == "REJECTED"
    assert attempt.resulting_message_id is None
    assert _queue_status(source, baseline.queue["redrive"]) == "TERMINAL"


def test_stale_failed_retry_preview_then_order_created_apply_succeeds(
    baseline, tmp_path
):
    source = _copy(baseline, tmp_path)
    case = Case("retry-before-order", "retry", RETRY, None,
                "REQ-PRV-20", "analyst.one", "FAILED")
    result = _preview(source, baseline, case)
    assert result["simulated"]["status"] == "FAILED"
    assert "ORDER_NOT_FOUND" in result["simulated"]["attempt"]["outcome_detail"]

    _prep_create_retry_order(source, baseline)
    with _rw(source) as conn:
        attempt = _apply(conn, baseline, case)
    assert attempt.outcome == "SUCCEEDED"
    assert _queue_status(source, baseline.queue["retry"]) == "RESOLVED"


def test_stale_success_preview_then_other_request_resolves_apply_is_rejected(
    baseline, tmp_path
):
    source = _copy(baseline, tmp_path)
    result = _preview(source, baseline, SUCCEEDED_REDRIVE)
    assert result["simulated"]["status"] == "SUCCEEDED"

    with _rw(source) as conn:
        other = _apply(conn, baseline, SUCCEEDED_REDRIVE, request_id="REQ-PRV-OTHER")
        assert other.outcome == "SUCCEEDED"
        attempt = _apply(conn, baseline, SUCCEEDED_REDRIVE)
    assert attempt.outcome == "REJECTED"
    assert attempt.attempt_id != other.attempt_id
    assert _queue_status(source, baseline.queue["redrive"]) == "RESOLVED"


# ---------------------------------------------------------------------------
# Handled rollback and unexpected exceptions (test-only injection)
# ---------------------------------------------------------------------------

def test_handled_failure_rolls_back_filing_in_preview(baseline, tmp_path, monkeypatch):
    source = _copy(baseline, tmp_path)
    sha_before = _file_sha(source)
    snap_before = _ro_snapshot(source)
    real_enter = workflow.enter_fish_result
    calls = {"n": 0}

    def fail_on_second_call(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise inbound_hl7.InboundError(
                "INVALID_CELL_COUNT", "Injected test-only failure on second probe."
            )
        return real_enter(*args, **kwargs)

    with monkeypatch.context() as mp:
        mp.setattr(workflow, "enter_fish_result", fail_on_second_call)
        result = _preview(source, baseline, SUCCEEDED_REDRIVE)

    assert calls["n"] == 2
    sim = result["simulated"]
    assert sim["status"] == "FAILED" and sim["recorded_outcome"] == "FAILED"
    assert sim["queue_status_after"] == "OPEN"
    changes = result["changes"]
    assert set(changes) == {"interface_message", "interface_recovery_attempt"}
    (message,) = changes["interface_message"]["inserted"]
    assert message["status"] == "ERRORED"
    assert message["order_id"] is None
    assert message["payload"] == CORRECTED_OPEN
    (attempt,) = changes["interface_recovery_attempt"]["inserted"]
    assert attempt["outcome"] == "FAILED"
    assert attempt["resulting_message_id"] == message["message_id"]
    assert "fish_result" not in changes
    audit_inserts = changes.get("audit_event", {}).get("inserted", [])
    assert not [r for r in audit_inserts
                if r["action"] in ("RESULT_ENTERED", "INBOUND_RESULT_FILED")]
    assert _file_sha(source) == sha_before
    assert _ro_snapshot(source) == snap_before

    # Without the injection the same (unconsumed) request previews SUCCEEDED,
    # and a real apply with a new request id succeeds.
    again = _preview(source, baseline, SUCCEEDED_REDRIVE)
    assert again["simulated"]["status"] == "SUCCEEDED"
    assert _file_sha(source) == sha_before
    with _rw(source) as conn:
        applied = _apply(conn, baseline, SUCCEEDED_REDRIVE, request_id="REQ-PRV-NEW")
    assert applied.outcome == "SUCCEEDED"


def test_unexpected_exception_propagates_and_source_unchanged(
    baseline, tmp_path, monkeypatch
):
    source = _copy(baseline, tmp_path)
    sha_before = _file_sha(source)
    snap_before = _ro_snapshot(source)
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    def boom(*args, **kwargs):
        raise RuntimeError("injected unexpected failure")

    with monkeypatch.context() as mp:
        mp.setattr(sqlite3, "connect", tracking_connect)
        mp.setattr(workflow, "enter_fish_result", boom)
        with pytest.raises(RuntimeError, match="injected unexpected failure"):
            _preview(source, baseline, SUCCEEDED_REDRIVE)

    assert len(opened) == 2  # the read-only source and the in-memory copy
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            conn.execute("SELECT 1")
    assert _file_sha(source) == sha_before
    assert _ro_snapshot(source) == snap_before


# ---------------------------------------------------------------------------
# Argument and lookup errors
# ---------------------------------------------------------------------------

def test_unknown_queue_is_not_found(baseline, tmp_path):
    source = _copy(baseline, tmp_path)
    sha_before = _file_sha(source)
    with pytest.raises(AnalystError) as exc:
        preview.preview_recovery(
            source, 9999, action=RETRY, request_id="REQ-PRV-X", actor="analyst.one"
        )
    assert exc.value.kind == "NOT_FOUND"
    assert _file_sha(source) == sha_before


def test_missing_database_is_not_found_and_not_created(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(AnalystError) as exc:
        preview.preview_recovery(
            missing, 1, action=RETRY, request_id="REQ-PRV-X", actor="analyst.one"
        )
    assert exc.value.kind == "NOT_FOUND"
    assert not missing.exists()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "action, payload",
    [
        (RETRY, CORRECTED_OPEN),
        (RETRY, ""),
        (REDRIVE, None),
        ("RETRY", None),
        ("retry_original", None),
        ("REPLAY", CORRECTED_OPEN),
    ],
)
def test_bad_arguments_are_usage_errors(baseline, tmp_path, action, payload):
    source = _copy(baseline, tmp_path)
    sha_before = _file_sha(source)
    with pytest.raises(AnalystError) as exc:
        preview.preview_recovery(
            source, baseline.queue["redrive"], action=action,
            request_id="REQ-PRV-X", actor="analyst.one", corrected_payload=payload,
        )
    assert exc.value.kind == "USAGE"
    assert _file_sha(source) == sha_before


def test_usage_error_precedes_database_lookup(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(AnalystError) as exc:
        preview.preview_recovery(
            missing, 1, action=REDRIVE, request_id="REQ-PRV-X", actor="analyst.one"
        )
    assert exc.value.kind == "USAGE"


def test_non_cytobridge_file_is_invalid_input(tmp_path):
    bogus = tmp_path / "notes.db"
    bogus.write_bytes(b"synthetic text, not a SQLite database\n")
    with pytest.raises(AnalystError) as exc:
        preview.preview_recovery(
            bogus, 1, action=RETRY, request_id="REQ-PRV-X", actor="analyst.one"
        )
    assert exc.value.kind == "INVALID_INPUT"
    assert bogus.read_bytes() == b"synthetic text, not a SQLite database\n"


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

def test_preview_result_shape(baseline, tmp_path):
    source = _copy(baseline, tmp_path)
    result = _preview(source, baseline, SUCCEEDED_REDRIVE)

    assert set(result) == {
        "mode", "request", "snapshot", "simulated", "changes", "change_counts",
        "notice",
    }
    assert result["mode"] == "PREVIEW"
    assert result["notice"] == LIMITATIONS["PREVIEW_SNAPSHOT"]
    assert result["request"] == {
        "queue_id": baseline.queue["redrive"],
        "action": REDRIVE,
        "request_id": SUCCEEDED_REDRIVE.request_id,
        "actor": SUCCEEDED_REDRIVE.actor,
        "corrected_payload_sha256": hashlib.sha256(
            CORRECTED_OPEN.encode("utf-8")
        ).hexdigest(),
    }
    assert result["snapshot"]["method"] == (
        "sqlite3 backup API into an isolated in-memory database"
    )
    sim = result["simulated"]
    assert set(sim) == {
        "status", "recorded_outcome", "attempt", "error",
        "queue_status_before", "queue_status_after",
    }
    assert set(sim["attempt"]) == ATTEMPT_KEYS
    assert sim["attempt"]["payload_sha256"] == result["request"]["corrected_payload_sha256"]
    assert sim["error"] is None
    assert (sim["queue_status_before"], sim["queue_status_after"]) == ("OPEN", "RESOLVED")

    changes = result["changes"]
    assert set(changes) == {
        "audit_event", "fish_result", "interface_error_queue", "interface_message",
        "interface_recovery_attempt", "lab_order",
    }
    for table, change in changes.items():
        assert set(change) == {"inserted", "updated", "deleted"}
        assert result["change_counts"][table] == {
            k: len(v) for k, v in change.items()
        }
    assert set(result["change_counts"]) == set(changes)
    queue_update = changes["interface_error_queue"]["updated"]
    assert queue_update == [{
        "key": baseline.queue["redrive"],
        "before": {"resolved_at": None, "status": "OPEN"},
        "after": {"resolved_at": queue_update[0]["after"]["resolved_at"],
                  "status": "RESOLVED"},
    }]
    assert queue_update[0]["after"]["resolved_at"] is not None
    assert [r["probe_id"] for r in changes["fish_result"]["inserted"]] == sorted(
        r["probe_id"] for r in changes["fish_result"]["inserted"]
    )


def test_retry_request_has_no_corrected_payload_fingerprint(baseline, tmp_path):
    source = _copy(baseline, tmp_path)
    _prep_create_retry_order(source, baseline)
    result = _preview(source, baseline, SUCCEEDED_RETRY)
    assert result["request"]["corrected_payload_sha256"] is None
    assert result["simulated"]["status"] == "SUCCEEDED"
    # RETRY_ORIGINAL fingerprints the stored original payload.
    assert result["simulated"]["attempt"]["payload_sha256"] == hashlib.sha256(
        ORIG_NOT_FOUND.encode("utf-8")
    ).hexdigest()


def test_preview_tables_cover_every_cytobridge_table():
    assert set(preview.PREVIEW_TABLES) == set(store.REQUIRED_TABLES)


# ---------------------------------------------------------------------------
# Snapshot / diff / normalize helpers
# ---------------------------------------------------------------------------

def test_diff_snapshots_reports_inserts_updates_deletes_by_key():
    before = {
        "t": {
            1: {"id": 1, "status": "OPEN", "note": "a"},
            2: {"id": 2, "status": "OPEN", "note": "b"},
            3: {"id": 3, "status": "OPEN", "note": "c"},
        },
        "unchanged": {1: {"id": 1, "v": 1}},
    }
    after = {
        "t": {
            1: {"id": 1, "status": "OPEN", "note": "a"},
            3: {"id": 3, "status": "RESOLVED", "note": "c"},
            5: {"id": 5, "status": "OPEN", "note": "e"},
            4: {"id": 4, "status": "OPEN", "note": "d"},
        },
        "unchanged": {1: {"id": 1, "v": 1}},
    }
    assert preview.diff_snapshots(before, after) == {
        "t": {
            "inserted": [
                {"id": 4, "status": "OPEN", "note": "d"},
                {"id": 5, "status": "OPEN", "note": "e"},
            ],
            "updated": [
                {"key": 3, "before": {"status": "OPEN"},
                 "after": {"status": "RESOLVED"}},
            ],
            "deleted": [{"id": 2, "status": "OPEN", "note": "b"}],
        }
    }
    assert preview.diff_snapshots(before, before) == {}


def test_snapshot_tables_keys_rows_by_integer_primary_key(baseline):
    with closing(store.open_read_only(baseline.path)) as conn:
        snap = preview.snapshot_tables(conn)
    assert set(snap) == set(store.REQUIRED_TABLES)
    for queue_id, row in snap["interface_error_queue"].items():
        assert row["queue_id"] == queue_id
    assert sorted(snap["probe"]) == list(snap["probe"])
    assert len(snap["probe"]) == 9


def test_normalize_changes_replaces_exactly_the_listed_timestamp_columns():
    listed = [
        "created_at", "attempted_at", "resolved_at", "terminal_at", "entered_at",
        "ordered_at", "finalized_at", "received_at",
    ]
    row = {col: "2026-07-15 10:00:00" for col in listed}
    # Unlisted columns, including timestamp-looking ones, are left alone.
    row.update({
        "collected_at": "2026-07-14 09:00:00",
        "status": "OPEN",
        "payload": "MSH|created_at",
        "message_id": 7,
    })
    changes = {
        "x": {
            "inserted": [dict(row)],
            "updated": [{
                "key": 7,
                "before": {"resolved_at": None, "status": "OPEN"},
                "after": {"resolved_at": "2026-07-15 10:00:01", "status": "RESOLVED"},
            }],
            "deleted": [dict(row, terminal_at=None)],
        }
    }
    original = {
        "x": {
            "inserted": [dict(r) for r in changes["x"]["inserted"]],
            "updated": [
                {"key": u["key"], "before": dict(u["before"]), "after": dict(u["after"])}
                for u in changes["x"]["updated"]
            ],
            "deleted": [dict(r) for r in changes["x"]["deleted"]],
        }
    }

    out = preview.normalize_changes(changes)

    assert changes == original  # input not modified
    (ins,) = out["x"]["inserted"]
    assert {c for c, v in ins.items() if v == "<timestamp>"} == set(listed)
    assert ins["collected_at"] == "2026-07-14 09:00:00"
    assert ins["status"] == "OPEN"
    assert ins["payload"] == "MSH|created_at"
    assert ins["message_id"] == 7
    assert out["x"]["updated"] == [{
        "key": 7,
        "before": {"resolved_at": None, "status": "OPEN"},
        "after": {"resolved_at": "<timestamp>", "status": "RESOLVED"},
    }]
    (dele,) = out["x"]["deleted"]
    assert dele["terminal_at"] is None
    assert {c for c, v in dele.items() if v == "<timestamp>"} == set(listed) - {
        "terminal_at"
    }
    assert preview.TIMESTAMP_COLUMNS == frozenset(listed)
