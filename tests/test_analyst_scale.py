"""BOUNDED SCALE CHECK for the analyst lab (trace, reconcile, worklist, evidence).

One deterministic synthetic scenario, built only through the public services
(workflow, inbound ingestion, v1.1 recovery), at a fixed, bounded size:

- 50 synthetic orders: one heavily used order (ACC-SC-0001), 39 open orders,
  and 10 orders that are filed and finalized first;
- exactly 1,000 inbound messages ingested with ``inbound_hl7.ingest_message``:
  valid filings (including re-filings of the same order and probes),
  ORDER_NOT_FOUND, INVALID_CELL_COUNT, UNKNOWN_PROBE_CODE, NO_OBX,
  SPECIMEN_UNRECOGNIZED, and late messages to finalized orders
  (ORDER_FINALIZED, queued TERMINAL);
- eight recovery requests: three SUCCEEDED, two FAILED, three REJECTED.

The same generator also builds a 200-message database over the same 50 orders,
so the statement counts of the set-based views can be compared between sizes
(``sqlite3.Connection.set_trace_callback``): they must not grow with the number
of messages (no N+1 query loops).

Timings are measured with ``time.perf_counter`` and reported through pytest's
``record_property`` and a printed line (visible with ``-s``). There is
deliberately NO timing threshold: this check proves bounded behavior and
correctness at size, not machine-specific speed. All data is synthetic. No PHI.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

import pytest

from src import recovery, workflow
from src.analyst import evidence, reconcile, trace
from src.analyst.common import canonical_json
from src.db import create_database, run_query
from src.interfaces import inbound_hl7

N_ORDERS = 50
HEAVY = 1
OPEN_ORDERS = tuple(range(2, 41))     # 39 open orders
FINAL_ORDERS = tuple(range(41, 51))   # 10 orders finalized before the main plan
LARGE = 1000
SMALL = 200

PROBES = (
    "RUNX1T1_RUNX1", "CBFB", "PML_RARA", "KMT2A", "EGR1_5q",
    "D7S486_7q", "CEP8", "D20S108_20q", "TP53_17p",
)

# The repeating 10-message block of the main plan: (kind, expected failure code
# or None when the message files).
KINDS: tuple[tuple[str, str | None], ...] = (
    ("FULL_HEAVY", None),                     # (re-)filing of all probes, heavy order
    ("PARTIAL_OPEN", None),                   # 3-probe filing, open order
    ("INVALID_HEAVY", "INVALID_CELL_COUNT"),  # malformed count, heavy order
    ("NOT_FOUND", "ORDER_NOT_FOUND"),         # accession with no order
    ("FULL_OPEN", None),                      # (re-)filing of all probes, open order
    ("UNKNOWN_PROBE", "UNKNOWN_PROBE_CODE"),
    ("NO_OBX", "NO_OBX"),
    ("SPECIMEN_BAD", "SPECIMEN_UNRECOGNIZED"),
    ("LATE_FINAL", "ORDER_FINALIZED"),        # valid payload, order already FINALIZED
    ("INVALID_OPEN", "INVALID_CELL_COUNT"),
)

EXPECTED_OUTCOMES = Counter({"SUCCEEDED": 3, "FAILED": 2, "REJECTED": 3})
# SUCCEEDED and FAILED attempts each store one new INBOUND message; REJECTED none.
EXPECTED_RECOVERY_MESSAGES = EXPECTED_OUTCOMES["SUCCEEDED"] + EXPECTED_OUTCOMES["FAILED"]


def accession(n: int) -> str:
    return f"ACC-SC-{n:04d}"


def oru(control_id: str, acc: str, obx, *, specimen: str = "BMA^Bone Marrow^L") -> str:
    """A small synthetic ORU-style inbound message (CR segment terminators)."""
    segments = [
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20261024101500||"
        f"ORU^R01|{control_id}|T|2.5.1",
        "PID|1||SYN-SC-PID^^^CYTO_LAB^MR||Synthetic^Scale||19700101|U",
        f"OBR|1||{acc}|AML_MDS_FISH^AML/MDS FISH Panel^L",
        f"SPM|1|SPM-SC||{specimen}",
    ]
    for i, (code, value) in enumerate(obx, 1):
        segments.append(f"OBX|{i}|ST|{code}^{code}^L||{value}||||||F")
    return "\r".join(segments)


def full_obx(variant: int) -> list[tuple[str, str]]:
    # 1 or 2 abnormal of 200 scored: below every synthetic cutoff, called NORMAL.
    return [
        (code, f"200^{1 + (variant + i) % 2}^2 signals^NORMAL")
        for i, code in enumerate(PROBES)
    ]


def partial_obx(variant: int) -> list[tuple[str, str]]:
    start = variant % len(PROBES)
    return [
        (PROBES[(start + k) % len(PROBES)], f"200^{1 + k % 2}^2 signals^NORMAL")
        for k in range(3)
    ]


def invalid_obx(variant: int) -> list[tuple[str, str]]:
    obx = full_obx(variant)
    obx[2] = (obx[2][0], "2OO^3^2 signals^NORMAL")  # letter O: not an integer
    return obx


@dataclass
class Built:
    conn: Any
    n_messages: int
    order_ids: dict[int, int]
    expected_codes: Counter = field(default_factory=Counter)
    filed: int = 0
    queue_by_kind: dict[str, list[int]] = field(default_factory=dict)
    attempt_outcomes: Counter = field(default_factory=Counter)
    anchor_queue_id: int = 0
    build_seconds: float = 0.0


def build(n_messages: int) -> Built:
    """Build the deterministic scenario with exactly ``n_messages`` inbound messages."""
    started = time.perf_counter()
    conn = create_database(":memory:")
    order_ids: dict[int, int] = {}
    for n in range(1, N_ORDERS + 1):
        patient_id = workflow.create_patient(
            conn, f"SYN-SC-{n:04d}", "Synthetic", f"Scale{n}", "1970-01-01", "U"
        )
        order_ids[n] = workflow.create_order(conn, patient_id, accession(n), "Dr. Synthetic")
        specimen_id = workflow.receive_specimen(conn, order_ids[n], "BONE_MARROW", f"BM-SC-{n}")
        workflow.accession_specimen(conn, specimen_id)
    built = Built(conn=conn, n_messages=n_messages, order_ids=order_ids)
    ingested = 0

    def ingest(kind: str, payload: str, code: str | None) -> None:
        nonlocal ingested
        result = inbound_hl7.ingest_message(conn, payload)
        ingested += 1
        assert result.filed == (code is None), (kind, result.reason)
        if code is None:
            built.filed += 1
        else:
            built.expected_codes[code] += 1
            built.queue_by_kind.setdefault(kind, []).append(result.queue_id)

    # Phase 1: file and finalize the FINAL_ORDERS group (10 messages).
    for n in FINAL_ORDERS:
        ingest("FINAL_SETUP", oru(f"SC-FIN-{n:04d}", accession(n), full_obx(n)), None)
        assert workflow.finalize_order(conn, order_ids[n], "pathologist01").finalized

    # Phase 2: the repeating block plan.
    for i in range(n_messages - len(FINAL_ORDERS)):
        kind, code = KINDS[i % len(KINDS)]
        r = i // len(KINDS)
        cid = f"SC-{i:04d}"
        open_n = OPEN_ORDERS[r % len(OPEN_ORDERS)]
        if kind == "FULL_HEAVY":
            payload = oru(cid, accession(HEAVY), full_obx(r))
        elif kind == "PARTIAL_OPEN":
            payload = oru(cid, accession(open_n), partial_obx(r))
        elif kind == "INVALID_HEAVY":
            payload = oru(cid, accession(HEAVY), invalid_obx(r))
        elif kind == "NOT_FOUND":
            payload = oru(cid, f"ACC-SC-NF-{r:04d}", full_obx(r))
        elif kind == "FULL_OPEN":
            payload = oru(cid, accession(OPEN_ORDERS[(r * 7) % len(OPEN_ORDERS)]), full_obx(r))
        elif kind == "UNKNOWN_PROBE":
            payload = oru(cid, accession(open_n), [("NOT_A_PROBE", "200^1^2 signals^NORMAL")])
        elif kind == "NO_OBX":
            payload = oru(cid, accession(open_n), [])
        elif kind == "SPECIMEN_BAD":
            payload = oru(cid, accession(open_n), partial_obx(r), specimen="XYZ^Unknown^L")
        elif kind == "LATE_FINAL":
            payload = oru(cid, accession(FINAL_ORDERS[r % len(FINAL_ORDERS)]), full_obx(r))
        else:  # INVALID_OPEN
            payload = oru(cid, accession(open_n), invalid_obx(r))
        ingest(kind, payload, code)
    assert ingested == n_messages

    # Phase 3: recoveries through the unchanged v1.1 service.
    heavy_invalid = built.queue_by_kind["INVALID_HEAVY"]
    not_found = built.queue_by_kind["NOT_FOUND"]
    late = built.queue_by_kind["LATE_FINAL"]
    corrected_heavy = oru("SC-RCV-OK", accession(HEAVY), full_obx(7))
    requests: list[tuple[Callable[[], recovery.RecoveryAttempt], str]] = [
        (lambda: recovery.redrive_queue_item(
            conn, heavy_invalid[0], corrected_heavy, request_id="SC-R1", actor="analyst01"),
         "SUCCEEDED"),
        (lambda: recovery.redrive_queue_item(
            conn, heavy_invalid[1], oru("SC-RCV-BAD", accession(HEAVY), invalid_obx(3)),
            request_id="SC-R2", actor="analyst01"),
         "FAILED"),
        (lambda: recovery.redrive_queue_item(
            conn, heavy_invalid[1], corrected_heavy, request_id="SC-R3", actor="analyst01"),
         "SUCCEEDED"),
        (lambda: recovery.retry_queue_item(
            conn, heavy_invalid[2], request_id="SC-R4", actor="analyst01"),
         "REJECTED"),   # INVALID_CELL_COUNT is REDRIVE_ONLY
        (lambda: recovery.retry_queue_item(
            conn, not_found[0], request_id="SC-R5", actor="analyst02"),
         "FAILED"),     # the accession still matches no order
        (lambda: recovery.redrive_queue_item(
            conn, not_found[1], oru("SC-RCV-NF", accession(OPEN_ORDERS[0]), partial_obx(1)),
            request_id="SC-R6", actor="analyst02"),
         "SUCCEEDED"),
        (lambda: recovery.redrive_queue_item(
            conn, late[0], oru("SC-RCV-LATE", accession(FINAL_ORDERS[0]), full_obx(1)),
            request_id="SC-R7", actor="analyst02"),
         "REJECTED"),   # TERMINAL item
        (lambda: recovery.redrive_queue_item(
            conn, heavy_invalid[0], corrected_heavy, request_id="SC-R8", actor="analyst01"),
         "REJECTED"),   # already RESOLVED
    ]
    for request, expected in requests:
        attempt = request()
        assert attempt.outcome == expected, (attempt.request_id, attempt.outcome_detail)
        built.attempt_outcomes[attempt.outcome] += 1
    built.anchor_queue_id = heavy_invalid[0]
    built.build_seconds = time.perf_counter() - started
    return built


@pytest.fixture(scope="module")
def scale():
    large = build(LARGE)
    small = build(SMALL)
    yield large, small
    large.conn.close()
    small.conn.close()


def scalar(conn, sql: str, params=()) -> Any:
    return conn.execute(sql, params).fetchone()[0]


def obr3(payload: str) -> str | None:
    """Independent OBR-3 reader for the checks below (first OBR segment)."""
    for line in payload.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        fields = line.split("|")
        if fields and fields[0].strip() == "OBR":
            value = fields[3].split("^")[0].strip() if len(fields) > 3 else ""
            return value or None
    return None


def statement_count(conn, fn: Callable[[], Any]) -> int:
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        fn()
    finally:
        conn.set_trace_callback(None)
    return len(statements)


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = fn()
    return value, time.perf_counter() - started


# ---------------------------------------------------------------------------
# Counts, queue routing, worklist
# ---------------------------------------------------------------------------

def test_scale_message_and_queue_counts(scale):
    large, _small = scale
    conn = large.conn
    planned_failures = sum(large.expected_codes.values())
    assert planned_failures + large.filed == LARGE
    assert large.attempt_outcomes == EXPECTED_OUTCOMES
    for code in ("ORDER_NOT_FOUND", "INVALID_CELL_COUNT", "UNKNOWN_PROBE_CODE", "NO_OBX",
                 "SPECIMEN_UNRECOGNIZED", "ORDER_FINALIZED"):
        assert large.expected_codes[code] > 0

    recovery_ids = {
        r[0] for r in conn.execute(
            "SELECT resulting_message_id FROM interface_recovery_attempt "
            "WHERE resulting_message_id IS NOT NULL"
        )
    }
    assert len(recovery_ids) == EXPECTED_RECOVERY_MESSAGES
    inbound = conn.execute(
        "SELECT message_id, status FROM interface_message WHERE direction = 'INBOUND'"
    ).fetchall()
    assert len(inbound) == LARGE + EXPECTED_RECOVERY_MESSAGES
    originals = [m for m in inbound if m["message_id"] not in recovery_ids]
    assert len(originals) == LARGE
    assert Counter(m["status"] for m in originals) == Counter(
        {"FILED": large.filed, "ERRORED": planned_failures}
    )
    assert scalar(conn, "SELECT COUNT(*) FROM interface_message WHERE direction = 'OUTBOUND'") == 0

    # Every ERRORED original has exactly one queue item; recovery messages have none.
    per_message = dict(conn.execute(
        "SELECT im.message_id, COUNT(eq.queue_id) FROM interface_message AS im "
        "LEFT JOIN interface_error_queue AS eq ON eq.message_id = im.message_id "
        "WHERE im.direction = 'INBOUND' GROUP BY im.message_id"
    ).fetchall())
    errored_originals = [m["message_id"] for m in originals if m["status"] == "ERRORED"]
    assert all(per_message[mid] == 1 for mid in errored_originals)
    assert all(per_message[mid] == 0 for mid in recovery_ids)
    assert scalar(conn, "SELECT COUNT(*) FROM interface_error_queue") == planned_failures
    codes = Counter(
        r[0] for r in conn.execute("SELECT failure_code FROM interface_error_queue")
    )
    assert codes == large.expected_codes
    statuses = Counter(r[0] for r in conn.execute("SELECT status FROM interface_error_queue"))
    assert statuses["RESOLVED"] == EXPECTED_OUTCOMES["SUCCEEDED"]
    assert statuses["TERMINAL"] == large.expected_codes["ORDER_FINALIZED"]


def test_scale_worklist_lists_every_queue_item_once_in_order(scale, record_property):
    large, _small = scale
    conn = large.conn
    rows, seconds = timed(lambda: run_query(conn, "analyst_queue_worklist", {"status": None}))
    expected = sorted(
        ((r["created_at"], r["queue_id"]) for r in conn.execute(
            "SELECT queue_id, created_at FROM interface_error_queue"
        )),
    )
    assert [(r["created_at"], r["queue_id"]) for r in rows] == expected
    assert len({r["queue_id"] for r in rows}) == len(rows) == sum(large.expected_codes.values())
    attempts = Counter(
        r[0] for r in conn.execute("SELECT queue_id FROM interface_recovery_attempt")
    )
    listed = {r["queue_id"]: r["attempt_count"] for r in rows if r["attempt_count"]}
    assert listed == dict(attempts)
    record_property("worklist_seconds", round(seconds, 4))
    print(f"\nscale worklist: {len(rows)} rows in {seconds:.3f}s")


# ---------------------------------------------------------------------------
# Reconciliation, case view, evidence at size
# ---------------------------------------------------------------------------

def test_scale_reconcile_clean_and_deterministic(scale, record_property):
    large, _small = scale
    first, seconds = timed(lambda: reconcile.reconcile_database(large.conn))
    second = reconcile.reconcile_database(large.conn)
    assert canonical_json(first) == canonical_json(second)
    summary = reconcile.summarize(first)
    assert summary["ERROR"] == 0 and summary["WARNING"] == 0, [
        (f["rule_id"], f["subjects"][:3]) for f in first if f["severity"] != "INFO"
    ]
    assert {f["rule_id"] for f in first} <= {"WF-001", "WF-003"}
    record_property("reconcile_database_seconds", round(seconds, 4))
    print(f"\nscale reconcile_database: {len(first)} INFO findings in {seconds:.3f}s")


def test_scale_case_for_heavy_order_is_complete_and_deterministic(scale, record_property):
    large, _small = scale
    conn = large.conn
    heavy_id = large.order_ids[HEAVY]
    case, seconds = timed(lambda: trace.build_case(conn, order_id=heavy_id))
    again = trace.build_case(conn, order_id=heavy_id)
    assert canonical_json(case) == canonical_json(again)

    # Independent SQL: every message with the order FK, plus NULL-order inbound
    # messages whose payload OBR-3 accession is the heavy order's accession.
    direct = {
        r[0] for r in conn.execute(
            "SELECT message_id FROM interface_message WHERE order_id = ?", (heavy_id,)
        )
    } | {
        r["message_id"] for r in conn.execute(
            "SELECT message_id, payload FROM interface_message "
            "WHERE direction = 'INBOUND' AND order_id IS NULL"
        ) if obr3(r["payload"]) == accession(HEAVY)
    }
    case_messages = [m["message_id"] for m in case["messages"]]
    assert case_messages == sorted(direct)
    assert len(direct) > 150
    expected_queues = {
        r[0] for r in conn.execute(
            "SELECT queue_id FROM interface_error_queue "
            "WHERE message_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(direct)),),
        )
    }
    assert {q["queue_id"] for q in case["queue_items"]} == expected_queues
    expected_attempts = {
        r[0] for r in conn.execute(
            "SELECT attempt_id FROM interface_recovery_attempt "
            "WHERE queue_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(expected_queues)),),
        )
    }
    assert {a["attempt_id"] for a in case["recovery_attempts"]} == expected_attempts
    assert [o["order_id"] for o in case["orders"]] == [heavy_id]
    patient_id = scalar(conn, "SELECT patient_id FROM lab_order WHERE order_id = ?", (heavy_id,))
    expected_events = [
        r[0] for r in conn.execute(
            "SELECT event_id FROM audit_event WHERE order_id = ? "
            "OR (entity_type = 'patient' AND entity_id = ?) "
            "OR (entity_type = 'interface_error_queue' "
            "    AND entity_id IN (SELECT value FROM json_each(?))) ORDER BY event_id",
            (heavy_id, patient_id, json.dumps(sorted(expected_queues))),
        )
    ]
    assert [a["event_id"] for a in case["audit_events"]] == expected_events
    record_property("build_case_heavy_order_seconds", round(seconds, 4))
    print(
        f"\nscale build_case(order {heavy_id}): {len(case_messages)} messages, "
        f"{len(case['audit_events'])} audit events in {seconds:.3f}s"
    )


def test_scale_evidence_for_one_queue_item(scale, record_property):
    large, _small = scale
    conn = large.conn
    ev, seconds = timed(lambda: evidence.build_evidence(
        conn, queue_id=large.anchor_queue_id, source_revision="SCALE-CHECK"
    ))
    again = evidence.build_evidence(
        conn, queue_id=large.anchor_queue_id, source_revision="SCALE-CHECK"
    )
    assert canonical_json(ev) == canonical_json(again)
    assert canonical_json(ev).isascii()
    assert ev["synthetic"] is True
    assert ev["anchor"] == {"kind": "queue", "id": large.anchor_queue_id}
    assert all(c["passed"] for f in ev["fingerprints"] for c in f["checks"])
    assert ev["reconciliation"]["summary"]["ERROR"] == 0
    report = evidence.render_report(ev)
    assert report.isascii()
    for attempt in ev["recovery_history"]:
        assert attempt["request_id"] in report
    record_property("build_evidence_seconds", round(seconds, 4))
    print(f"\nscale build_evidence(queue {large.anchor_queue_id}): {seconds:.3f}s")


# ---------------------------------------------------------------------------
# No N+1: statement counts do not grow from 200 to 1,000 messages
# ---------------------------------------------------------------------------

def test_scale_statement_counts_do_not_grow_with_message_count(scale, record_property):
    large, small = scale
    assert len(large.order_ids) == len(small.order_ids) == N_ORDERS
    assert scalar(large.conn, "SELECT COUNT(*) FROM interface_message") > 4 * scalar(
        small.conn, "SELECT COUNT(*) FROM interface_message"
    )
    measurements = {
        "reconcile_database(order_ids=set())": lambda b: reconcile.reconcile_database(
            b.conn, order_ids=set()),
        "reconcile_database()": lambda b: reconcile.reconcile_database(b.conn),
        "build_case(queue anchor)": lambda b: trace.build_case(
            b.conn, queue_id=b.anchor_queue_id),
        "build_evidence(queue anchor)": lambda b: evidence.build_evidence(
            b.conn, queue_id=b.anchor_queue_id, source_revision="SCALE-CHECK"),
    }
    counts: dict[str, tuple[int, int]] = {}
    for name, fn in measurements.items():
        small_count = statement_count(small.conn, lambda: fn(small))
        large_count = statement_count(large.conn, lambda: fn(large))
        counts[name] = (small_count, large_count)
    for name, (small_count, large_count) in counts.items():
        assert large_count == small_count, (name, small_count, large_count)
    timings = {
        f"build_{SMALL}_seconds": round(small.build_seconds, 4),
        f"build_{LARGE}_seconds": round(large.build_seconds, 4),
    }
    record_property("statement_counts", json.dumps(counts, sort_keys=True))
    record_property("build_timings", json.dumps(timings, sort_keys=True))
    print(f"\nscale statement counts (200 vs 1000 messages): {counts}; build timings: {timings}")
