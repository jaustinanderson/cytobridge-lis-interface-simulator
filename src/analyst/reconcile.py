"""Deterministic reconciliation findings for the CytoBridge analyst lab.

``reconcile_database`` checks a CytoBridge database against a fixed catalogue of
documented, educational invariants (``RULES``) and returns findings. Each rule
cites where in this repository the expectation comes from: a ``schema.sql``
constraint, a requirement ID in ``validation/requirements.md`` (R-001..R-019) or
``validation/v1.1-requirements.md`` (R-020..R-041), a section of the frozen
``validation/v1.1-design-record.md``, or the docstring of the service that
writes the rows. These are educational invariants derived from the repository's
documented design; they are not clinical or regulatory requirements.

Properties:

- Read-only: every statement is a SELECT or a read-only PRAGMA, so it runs on a
  ``store.open_read_only`` connection. ``validation.validate_order`` (SELECT
  only) is reused for the WF-* checks; no validation result is persisted.
- Set-based: the non-WF checks issue a constant number of SQL statements no
  matter how many messages, queue items, attempts, or audit events exist. The
  WF-* checks call ``validate_order`` once per order in scope.
- Deterministic: findings are sorted by severity (ERROR, WARNING, INFO), rule
  ID, subjects, and observations; subjects are sorted ``table:id`` refs.

This is an educational, synthetic simulator. All data is synthetic. No PHI.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from typing import Any, Iterable

from .. import validation
from ..interfaces import inbound_hl7
from .common import ref, sha256_text
from .trace import INBOUND_RESULT_FILED, audit_message_ids, case_record_refs

SEVERITY_ORDER = ("ERROR", "WARNING", "INFO")

_AMBIGUITY_AUDIT_TEXT = (
    "Matched on free-text audit detail (message_id=N parsed with "
    r"\bmessage_id=(\d+)\b), not on a foreign key."
)

# rule_id -> {"severity", "title", "documented_rule"}. IDs are stable.
RULES: dict[str, dict[str, str]] = {
    "REL-001": {
        "severity": "ERROR",
        "title": "Foreign-key violation",
        "documented_rule": (
            "schema.sql declares these columns with REFERENCES and src/db.py "
            "connect() enables PRAGMA foreign_keys, so every non-NULL foreign-key "
            "value names an existing parent row. Checked with PRAGMA "
            "foreign_key_check."
        ),
    },
    "REL-002": {
        "severity": "ERROR",
        "title": "Queue item without an existing original message",
        "documented_rule": (
            "R-010 stores every inbound message in interface_message and R-018 "
            "has each error-queue entry carry its message_id; the inbound path "
            "(inbound_hl7.ingest_message) always links the stored message."
        ),
    },
    "MSG-001": {
        "severity": "ERROR",
        "title": "FILED inbound message without an order link",
        "documented_rule": (
            "R-011 files a matched inbound message to its order, and "
            "inbound_hl7.ingest_message sets order_id when it marks the message "
            "FILED."
        ),
    },
    "MSG-002": {
        "severity": "WARNING",
        "title": "Inbound message still RECEIVED",
        "documented_rule": (
            "inbound_hl7.ingest_message stores each message as RECEIVED and then "
            "marks it FILED or ERRORED; the recovery service commits its new "
            "message as FILED or ERRORED. A RECEIVED message means processing did "
            "not complete."
        ),
    },
    "MSG-003": {
        "severity": "WARNING",
        "title": "ERRORED inbound message not tracked by the queue or a recovery attempt",
        "documented_rule": (
            "R-012..R-015 route inbound failures to interface_error_queue; R-032 "
            "keeps a FAILED recovery attempt's resulting message as ERRORED. An "
            "ERRORED inbound message is expected to be a queue original or an "
            "attempt's resulting message."
        ),
    },
    "MSG-004": {
        "severity": "WARNING",
        "title": "Inbound message referenced by more than one queue item",
        "documented_rule": (
            "inbound_hl7.ingest_message routes one error-queue item per failed "
            "message (R-012..R-015, R-018)."
        ),
    },
    "MSG-005": {
        "severity": "ERROR",
        "title": "Outbound message for an order that is not FINALIZED",
        "documented_rule": (
            "R-008 and R-009 generate outbound HL7 and FHIR messages only for a "
            "FINALIZED order (interfaces.collect_report_data)."
        ),
    },
    "QUE-001": {
        "severity": "ERROR",
        "title": "Queue status and timestamps inconsistent",
        "documented_rule": (
            "R-031 and design record sec 5.1 (also a schema.sql CHECK): OPEN has "
            "NULL resolved_at and terminal_at; RESOLVED has resolved_at and NULL "
            "terminal_at; TERMINAL has terminal_at and NULL resolved_at."
        ),
    },
    "QUE-002": {
        "severity": "ERROR",
        "title": "Queue classification missing or not the authoritative triple",
        "documented_rule": (
            "R-020 and R-021 (design record sec 6): each queue item carries a "
            "failure_code, failure_category, and recovery_policy equal to the "
            "single authoritative mapping in inbound_hl7._FAILURE_CLASSIFICATION."
        ),
    },
    "QUE-003": {
        "severity": "ERROR",
        "title": "OPEN queue item with a TERMINAL recovery policy",
        "documented_rule": (
            "Design record sec 6 and 8: a TERMINAL policy initializes the item "
            "TERMINAL (inbound_hl7._route_to_error_queue); an OPEN item carries "
            "REDRIVE_ONLY or RETRY_OR_REDRIVE."
        ),
    },
    "QUE-004": {
        "severity": "WARNING",
        "title": "TERMINAL queue item without a recorded explanation",
        "documented_rule": (
            "R-029 and src/recovery.py: an item with a non-TERMINAL policy becomes "
            "TERMINAL only through the dynamic OPEN -> TERMINAL path, which records "
            "a REJECTED recovery attempt in the same transaction."
        ),
    },
    "QUE-005": {
        "severity": "ERROR",
        "title": "Queue raw_payload differs from the original message payload",
        "documented_rule": (
            "R-022 (design record sec 4, 5.1, 5.3): the queue item's raw_payload "
            "copy and the original message payload are both immutable copies of "
            "the same inbound payload."
        ),
    },
    "QUE-006": {
        "severity": "ERROR",
        "title": "Queue item's original message is FILED",
        "documented_rule": (
            "R-023 (design record sec 4): the original failed message is never "
            "updated to FILED; only a new recovery message may reach FILED."
        ),
    },
    "REC-001": {
        "severity": "ERROR",
        "title": "RESOLVED queue item without exactly one SUCCEEDED attempt",
        "documented_rule": (
            "R-030 and R-035 (design record sec 8, 9): OPEN -> RESOLVED happens "
            "after one successful recovery, and an item has at most one SUCCEEDED "
            "attempt."
        ),
    },
    "REC-002": {
        "severity": "ERROR",
        "title": "OPEN or TERMINAL queue item with a SUCCEEDED attempt",
        "documented_rule": (
            "R-030 and R-038 (design record sec 8, 9): a SUCCEEDED attempt and the "
            "queue OPEN -> RESOLVED transition commit together."
        ),
    },
    "REC-003": {
        "severity": "ERROR",
        "title": "SUCCEEDED attempt without a FILED inbound resulting message",
        "documented_rule": (
            "R-032 and R-023: a SUCCEEDED attempt's resulting message is the new "
            "INBOUND message that reached FILED and is linked to its order."
        ),
    },
    "REC-004": {
        "severity": "ERROR",
        "title": "FAILED attempt without an ERRORED resulting message",
        "documented_rule": (
            "R-032 and R-038 (design record sec 8, 9): a handled recovery failure "
            "preserves the resulting message with status ERRORED."
        ),
    },
    "REC-005": {
        "severity": "ERROR",
        "title": "Resulting message reused",
        "documented_rule": (
            "R-024, R-026, and R-028: each processed recovery creates a distinct "
            "new interface_message row, never the original and never one shared "
            "with another attempt."
        ),
    },
    "REC-006": {
        "severity": "ERROR",
        "title": "Attempt payload fingerprint does not match the stored payload",
        "documented_rule": (
            "R-026, R-028, and R-040: payload_sha256 is the SHA-256 of the exact "
            "payload processed (the resulting message payload); an unchanged "
            "retry processes an exact copy of the original message payload."
        ),
    },
    "REC-007": {
        "severity": "ERROR",
        "title": "Unchanged retry processed for a failure other than ORDER_NOT_FOUND",
        "documented_rule": (
            "R-025 (design record sec 7): RETRY_ORIGINAL is permitted only for an "
            "OPEN ORDER_NOT_FOUND item; any other failure is REJECTED without "
            "processing."
        ),
    },
    "REC-008": {
        "severity": "INFO",
        "title": "REQUEST_ID_CONFLICT recorded for a queue item",
        "documented_rule": (
            "R-041 (design record sec 9): a conflicting request_id reuse creates no "
            "message or attempt and is recorded only as a REQUEST_ID_CONFLICT audit "
            "event. Reported as evidence, not as a defect."
        ),
    },
    "AUD-001": {
        "severity": "ERROR",
        "title": "FILED inbound message without its INBOUND_RESULT_FILED audit event",
        "documented_rule": (
            "R-016 and R-040: successful inbound filing records an "
            "INBOUND_RESULT_FILED audit event on the order naming the source "
            "interface_message.message_id."
        ),
    },
    "AUD-002": {
        "severity": "ERROR",
        "title": "INBOUND_RESULT_FILED event does not name a message filed to its order",
        "documented_rule": (
            "R-016 and R-038: the INBOUND_RESULT_FILED event names its source "
            "message and commits with the filing, so the message it names exists, "
            "is INBOUND and FILED, and is filed to the event's order."
        ),
    },
    "AUD-003": {
        "severity": "ERROR",
        "title": "FISH result without a RESULT_ENTERED audit event",
        "documented_rule": (
            "R-007: every result state change writes an audit event; "
            "workflow.enter_fish_result records RESULT_ENTERED for the result id."
        ),
    },
    "AUD-004": {
        "severity": "ERROR",
        "title": "FINALIZED order missing its finalization records",
        "documented_rule": (
            "R-007 and workflow.finalize_order: finalization creates a FINALIZED "
            "report row and records REPORT_FINALIZED and FINALIZED audit events."
        ),
    },
    "AUD-005": {
        "severity": "ERROR",
        "title": "Order or specimen missing its lifecycle audit event",
        "documented_rule": (
            "R-007: workflow.create_order records ORDERED, "
            "workflow.receive_specimen records RECEIVED, and "
            "workflow.accession_specimen records ACCESSIONED."
        ),
    },
    "WF-001": {
        "severity": "INFO",
        "title": "Finalization prerequisites outstanding",
        "documented_rule": (
            "R-003..R-006: finalization is blocked while current validation "
            "(validation.validate_order) reports any ERROR finding. Reported for "
            "orders that are not FINALIZED or CANCELLED."
        ),
    },
    "WF-002": {
        "severity": "WARNING",
        "title": "FINALIZED order fails current validation",
        "documented_rule": (
            "R-006: workflow.finalize_order finalizes only when validation has no "
            "ERROR finding at that time, and workflow.enter_fish_result refuses "
            "result changes after finalization. Specimen records can still change "
            "after finalization (workflow.receive_specimen and "
            "workflow.reject_specimen do not check order status), and validation "
            "reads the latest specimen."
        ),
    },
    "WF-003": {
        "severity": "INFO",
        "title": "Stored validation findings differ from current validation",
        "documented_rule": (
            "workflow.run_validation replaces an order's validation_error rows on "
            "each run, so stored rows reflect the last run, which may predate the "
            "current results."
        ),
    },
}

_AMBIGUITY: dict[str, str] = {
    "AUD-001": _AMBIGUITY_AUDIT_TEXT,
    "AUD-002": _AMBIGUITY_AUDIT_TEXT,
    "QUE-004": (
        "Any REJECTED attempt on the item is accepted as the recorded explanation; "
        "its outcome_detail text is not parsed."
    ),
    "WF-002": (
        "The database does not record the validation state at finalization, so "
        "this finding cannot distinguish a specimen received or rejected after "
        "finalization through the public services from a finalization or result "
        "defect."
    ),
    "WF-003": (
        "Compared as a multiset of (rule_code, severity, message); stored rows "
        "carry no run identifier, so when they were produced is not recorded."
    ),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _select(
    conn: sqlite3.Connection, sql: str, params: Any = ()
) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]
    finally:
        cur.close()


def _ascii(text: Any) -> str:
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _null(value: Any) -> str:
    return "NULL" if value is None else str(value)


def _finding(
    rule_id: str, subjects: Iterable[str], observations: Iterable[str]
) -> dict[str, Any]:
    rule = RULES[rule_id]
    return {
        "rule_id": rule_id,
        "severity": rule["severity"],
        "title": rule["title"],
        "documented_rule": rule["documented_rule"],
        "subjects": sorted(set(subjects)),
        "observations": [_ascii(o) for o in observations],
        "ambiguity": _AMBIGUITY.get(rule_id),
    }


def _sort_key(f: dict[str, Any]) -> tuple:
    return (
        SEVERITY_ORDER.index(f["severity"]),
        f["rule_id"],
        f["subjects"],
        f["observations"],
    )


def _msg(mid: Any) -> str:
    return ref("interface_message", mid)


def _queue(qid: Any) -> str:
    return ref("interface_error_queue", qid)


def _attempt(aid: Any) -> str:
    return ref("interface_recovery_attempt", aid)


def _order(oid: Any) -> str:
    return ref("lab_order", oid)


# ---------------------------------------------------------------------------
# Set-based checks (constant statement count)
# ---------------------------------------------------------------------------

def _check_relational(conn: sqlite3.Connection, out: list[dict[str, Any]]) -> None:
    violations: dict[tuple[str, int], list[str]] = {}
    for v in _select(
        conn,
        'SELECT fk."table" AS child_table, fk.rowid AS child_rowid, '
        "fk.parent AS parent_table, fk.fkid AS fkid, "
        'fl."from" AS from_col, fl."to" AS to_col '
        "FROM pragma_foreign_key_check AS fk "
        'LEFT JOIN pragma_foreign_key_list(fk."table") AS fl ON fl.id = fk.fkid '
        "ORDER BY child_table, child_rowid, fkid",
    ):
        key = (v["child_table"], v["child_rowid"])
        violations.setdefault(key, []).append(
            f"{v['child_table']} row {v['child_rowid']}: {_null(v['from_col'])} "
            f"references {v['parent_table']}({_null(v['to_col'])}), but no such "
            f"parent row exists."
        )
    for (table, rowid), obs in sorted(violations.items()):
        out.append(_finding("REL-001", [ref(table, rowid)], obs))

    for q in _select(
        conn,
        "SELECT eq.queue_id, eq.message_id FROM interface_error_queue AS eq "
        "LEFT JOIN interface_message AS im ON im.message_id = eq.message_id "
        "WHERE eq.message_id IS NULL OR im.message_id IS NULL ORDER BY eq.queue_id",
    ):
        if q["message_id"] is None:
            obs = f"interface_error_queue {q['queue_id']} has NULL message_id."
        else:
            obs = (
                f"interface_error_queue {q['queue_id']} names message_id "
                f"{q['message_id']}, which does not exist."
            )
        out.append(_finding("REL-002", [_queue(q["queue_id"])], [obs]))


def _check_messages(conn: sqlite3.Connection, out: list[dict[str, Any]]) -> None:
    for m in _select(
        conn,
        "SELECT message_id FROM interface_message WHERE direction = 'INBOUND' "
        "AND status = 'FILED' AND order_id IS NULL ORDER BY message_id",
    ):
        out.append(_finding(
            "MSG-001", [_msg(m["message_id"])],
            [f"interface_message {m['message_id']} is INBOUND and FILED with NULL order_id."],
        ))

    for m in _select(
        conn,
        "SELECT message_id, created_at FROM interface_message "
        "WHERE direction = 'INBOUND' AND status = 'RECEIVED' ORDER BY message_id",
    ):
        out.append(_finding(
            "MSG-002", [_msg(m["message_id"])],
            [f"interface_message {m['message_id']} (stored {m['created_at']}) is "
             f"INBOUND with status RECEIVED."],
        ))

    for m in _select(
        conn,
        "SELECT im.message_id FROM interface_message AS im "
        "WHERE im.direction = 'INBOUND' AND im.status = 'ERRORED' "
        "AND NOT EXISTS (SELECT 1 FROM interface_error_queue AS eq "
        "                WHERE eq.message_id = im.message_id) "
        "AND NOT EXISTS (SELECT 1 FROM interface_recovery_attempt AS ra "
        "                WHERE ra.resulting_message_id = im.message_id) "
        "ORDER BY im.message_id",
    ):
        out.append(_finding(
            "MSG-003", [_msg(m["message_id"])],
            [f"interface_message {m['message_id']} is INBOUND and ERRORED, but no "
             f"queue item names it and no recovery attempt resulted in it."],
        ))

    shared: dict[int, list[int]] = {}
    for row in _select(
        conn,
        "SELECT eq.message_id, eq.queue_id FROM interface_error_queue AS eq "
        "JOIN interface_message AS im ON im.message_id = eq.message_id "
        "WHERE im.direction = 'INBOUND' AND eq.message_id IN ("
        "  SELECT message_id FROM interface_error_queue "
        "  WHERE message_id IS NOT NULL GROUP BY message_id HAVING COUNT(*) > 1) "
        "ORDER BY eq.message_id, eq.queue_id",
    ):
        shared.setdefault(row["message_id"], []).append(row["queue_id"])
    for mid, qids in sorted(shared.items()):
        out.append(_finding(
            "MSG-004", [_msg(mid)] + [_queue(q) for q in qids],
            [f"interface_message {mid} is named by {len(qids)} queue items: "
             f"{', '.join(str(q) for q in qids)}."],
        ))

    for m in _select(
        conn,
        "SELECT im.message_id, im.order_id, im.format, o.order_id AS found_order, "
        "o.status AS order_status FROM interface_message AS im "
        "LEFT JOIN lab_order AS o ON o.order_id = im.order_id "
        "WHERE im.direction = 'OUTBOUND' "
        "AND (o.order_id IS NULL OR o.status <> 'FINALIZED') ORDER BY im.message_id",
    ):
        subjects = [_msg(m["message_id"])]
        if m["found_order"] is not None:
            subjects.append(_order(m["found_order"]))
            obs = (
                f"OUTBOUND {m['format']} interface_message {m['message_id']} belongs "
                f"to order {m['order_id']}, whose status is {m['order_status']}."
            )
        elif m["order_id"] is None:
            obs = (
                f"OUTBOUND {m['format']} interface_message {m['message_id']} has "
                f"NULL order_id."
            )
        else:
            obs = (
                f"OUTBOUND {m['format']} interface_message {m['message_id']} names "
                f"order {m['order_id']}, which does not exist."
            )
        out.append(_finding("MSG-005", subjects, [obs]))


def _mapping_cte() -> tuple[str, list[str]]:
    """A VALUES CTE of the authoritative classification mapping (read-only reuse)."""
    items = sorted(inbound_hl7._FAILURE_CLASSIFICATION.items())
    values = ", ".join("(?, ?, ?)" for _ in items)
    params: list[str] = []
    for code, c in items:
        params.extend([code, c.category, c.policy])
    return f"WITH mapping(code, category, policy) AS (VALUES {values}) ", params


def _check_queue(conn: sqlite3.Connection, out: list[dict[str, Any]]) -> None:
    for q in _select(
        conn,
        "SELECT queue_id, status, resolved_at, terminal_at FROM interface_error_queue "
        "WHERE NOT ("
        " (status = 'OPEN' AND resolved_at IS NULL AND terminal_at IS NULL)"
        " OR (status = 'RESOLVED' AND resolved_at IS NOT NULL AND terminal_at IS NULL)"
        " OR (status = 'TERMINAL' AND terminal_at IS NOT NULL AND resolved_at IS NULL)"
        ") ORDER BY queue_id",
    ):
        out.append(_finding(
            "QUE-001", [_queue(q["queue_id"])],
            [f"interface_error_queue {q['queue_id']} status {q['status']} with "
             f"resolved_at={_null(q['resolved_at'])} and "
             f"terminal_at={_null(q['terminal_at'])}."],
        ))

    cte, params = _mapping_cte()
    for q in _select(
        conn,
        cte
        + "SELECT eq.queue_id, eq.failure_code, eq.failure_category, "
        "eq.recovery_policy, m.code AS mapped_code, m.category AS mapped_category, "
        "m.policy AS mapped_policy FROM interface_error_queue AS eq "
        "LEFT JOIN mapping AS m ON m.code = eq.failure_code "
        "WHERE eq.failure_code IS NULL OR eq.failure_category IS NULL "
        "OR eq.recovery_policy IS NULL OR m.code IS NULL "
        "OR m.category IS NOT eq.failure_category "
        "OR m.policy IS NOT eq.recovery_policy ORDER BY eq.queue_id",
        params,
    ):
        stored = (
            f"(failure_code={_null(q['failure_code'])}, "
            f"failure_category={_null(q['failure_category'])}, "
            f"recovery_policy={_null(q['recovery_policy'])})"
        )
        if q["failure_code"] is None or q["failure_category"] is None or q[
            "recovery_policy"
        ] is None:
            obs = f"interface_error_queue {q['queue_id']} has incomplete classification {stored}."
        elif q["mapped_code"] is None:
            obs = (
                f"interface_error_queue {q['queue_id']} failure_code "
                f"{q['failure_code']} is not in the authoritative mapping {stored}."
            )
        else:
            obs = (
                f"interface_error_queue {q['queue_id']} stores {stored}; the "
                f"authoritative triple is (failure_code={q['mapped_code']}, "
                f"failure_category={q['mapped_category']}, "
                f"recovery_policy={q['mapped_policy']})."
            )
        out.append(_finding("QUE-002", [_queue(q["queue_id"])], [obs]))

    for q in _select(
        conn,
        "SELECT queue_id, failure_code FROM interface_error_queue "
        "WHERE status = 'OPEN' AND recovery_policy = 'TERMINAL' ORDER BY queue_id",
    ):
        out.append(_finding(
            "QUE-003", [_queue(q["queue_id"])],
            [f"interface_error_queue {q['queue_id']} is OPEN with recovery_policy "
             f"TERMINAL (failure_code {_null(q['failure_code'])})."],
        ))

    for q in _select(
        conn,
        "SELECT eq.queue_id, eq.recovery_policy, eq.failure_code "
        "FROM interface_error_queue AS eq WHERE eq.status = 'TERMINAL' "
        "AND eq.recovery_policy IS NOT NULL AND eq.recovery_policy <> 'TERMINAL' "
        "AND NOT EXISTS (SELECT 1 FROM interface_recovery_attempt AS ra "
        "                WHERE ra.queue_id = eq.queue_id AND ra.outcome = 'REJECTED') "
        "ORDER BY eq.queue_id",
    ):
        out.append(_finding(
            "QUE-004", [_queue(q["queue_id"])],
            [f"interface_error_queue {q['queue_id']} is TERMINAL with recovery_policy "
             f"{q['recovery_policy']} (failure_code {_null(q['failure_code'])}) and "
             f"has no REJECTED recovery attempt."],
        ))

    for q in _select(
        conn,
        "SELECT eq.queue_id, eq.message_id, eq.raw_payload, im.payload "
        "FROM interface_error_queue AS eq "
        "JOIN interface_message AS im ON im.message_id = eq.message_id "
        "WHERE eq.raw_payload IS NOT im.payload ORDER BY eq.queue_id",
    ):
        raw = q["raw_payload"]
        raw_desc = (
            "NULL" if raw is None
            else f"sha256 {sha256_text(raw)} ({len(raw.encode('utf-8'))} bytes)"
        )
        out.append(_finding(
            "QUE-005", [_queue(q["queue_id"]), _msg(q["message_id"])],
            [f"interface_error_queue {q['queue_id']} raw_payload is {raw_desc}; "
             f"interface_message {q['message_id']} payload is sha256 "
             f"{sha256_text(q['payload'])} ({len(q['payload'].encode('utf-8'))} bytes)."],
        ))

    for q in _select(
        conn,
        "SELECT eq.queue_id, eq.message_id FROM interface_error_queue AS eq "
        "JOIN interface_message AS im ON im.message_id = eq.message_id "
        "WHERE im.status = 'FILED' ORDER BY eq.queue_id",
    ):
        out.append(_finding(
            "QUE-006", [_queue(q["queue_id"]), _msg(q["message_id"])],
            [f"interface_error_queue {q['queue_id']} original interface_message "
             f"{q['message_id']} has status FILED."],
        ))


def _check_recovery(conn: sqlite3.Connection, out: list[dict[str, Any]]) -> None:
    for q in _select(
        conn,
        "SELECT eq.queue_id, COUNT(ra.attempt_id) AS succeeded, "
        "group_concat(ra.attempt_id) AS attempt_ids "
        "FROM interface_error_queue AS eq "
        "LEFT JOIN interface_recovery_attempt AS ra "
        "  ON ra.queue_id = eq.queue_id AND ra.outcome = 'SUCCEEDED' "
        "WHERE eq.status = 'RESOLVED' GROUP BY eq.queue_id "
        "HAVING COUNT(ra.attempt_id) <> 1 ORDER BY eq.queue_id",
    ):
        ids = sorted(int(x) for x in (q["attempt_ids"] or "").split(",") if x)
        out.append(_finding(
            "REC-001", [_queue(q["queue_id"])] + [_attempt(a) for a in ids],
            [f"interface_error_queue {q['queue_id']} is RESOLVED with "
             f"{q['succeeded']} SUCCEEDED recovery attempt(s)."],
        ))

    for row in _select(
        conn,
        "SELECT eq.queue_id, eq.status, ra.attempt_id FROM interface_error_queue AS eq "
        "JOIN interface_recovery_attempt AS ra ON ra.queue_id = eq.queue_id "
        "WHERE ra.outcome = 'SUCCEEDED' AND eq.status IN ('OPEN', 'TERMINAL') "
        "ORDER BY eq.queue_id, ra.attempt_id",
    ):
        out.append(_finding(
            "REC-002", [_queue(row["queue_id"]), _attempt(row["attempt_id"])],
            [f"interface_error_queue {row['queue_id']} is {row['status']} but "
             f"recovery attempt {row['attempt_id']} SUCCEEDED."],
        ))

    for a in _select(
        conn,
        "SELECT ra.attempt_id, ra.outcome, ra.resulting_message_id, "
        "im.message_id AS found, im.direction, im.status, im.order_id "
        "FROM interface_recovery_attempt AS ra "
        "LEFT JOIN interface_message AS im ON im.message_id = ra.resulting_message_id "
        "WHERE (ra.outcome = 'SUCCEEDED' AND (im.message_id IS NULL "
        "   OR im.direction <> 'INBOUND' OR im.status <> 'FILED' OR im.order_id IS NULL)) "
        "OR (ra.outcome = 'FAILED' AND (im.message_id IS NULL OR im.status <> 'ERRORED')) "
        "ORDER BY ra.attempt_id",
    ):
        rule = "REC-003" if a["outcome"] == "SUCCEEDED" else "REC-004"
        subjects = [_attempt(a["attempt_id"])]
        if a["found"] is None:
            obs = (
                f"{a['outcome']} recovery attempt {a['attempt_id']} names "
                f"resulting_message_id {_null(a['resulting_message_id'])}, which "
                f"does not exist."
            )
        else:
            subjects.append(_msg(a["found"]))
            obs = (
                f"{a['outcome']} recovery attempt {a['attempt_id']} resulting "
                f"interface_message {a['found']} is {a['direction']} with status "
                f"{a['status']} and order_id {_null(a['order_id'])}."
            )
        out.append(_finding(rule, subjects, [obs]))

    for a in _select(
        conn,
        "SELECT ra.attempt_id, ra.queue_id, ra.resulting_message_id "
        "FROM interface_recovery_attempt AS ra "
        "JOIN interface_error_queue AS eq ON eq.queue_id = ra.queue_id "
        "WHERE ra.resulting_message_id IS NOT NULL "
        "AND ra.resulting_message_id = eq.message_id ORDER BY ra.attempt_id",
    ):
        out.append(_finding(
            "REC-005",
            [_attempt(a["attempt_id"]), _queue(a["queue_id"]),
             _msg(a["resulting_message_id"])],
            [f"recovery attempt {a['attempt_id']} resulting message "
             f"{a['resulting_message_id']} is the original message of "
             f"interface_error_queue {a['queue_id']}."],
        ))
    shared: dict[int, list[int]] = {}
    for a in _select(
        conn,
        "SELECT ra.attempt_id, ra.resulting_message_id "
        "FROM interface_recovery_attempt AS ra WHERE ra.resulting_message_id IN ("
        "  SELECT resulting_message_id FROM interface_recovery_attempt "
        "  WHERE resulting_message_id IS NOT NULL "
        "  GROUP BY resulting_message_id HAVING COUNT(*) > 1) "
        "ORDER BY ra.resulting_message_id, ra.attempt_id",
    ):
        shared.setdefault(a["resulting_message_id"], []).append(a["attempt_id"])
    for mid, aids in sorted(shared.items()):
        out.append(_finding(
            "REC-005", [_msg(mid)] + [_attempt(x) for x in aids],
            [f"interface_message {mid} is the resulting message of {len(aids)} "
             f"recovery attempts: {', '.join(str(x) for x in aids)}."],
        ))

    for a in _select(
        conn,
        "SELECT ra.attempt_id, ra.action, ra.payload_sha256, ra.resulting_message_id, "
        "rm.payload AS resulting_payload, eq.message_id AS original_message_id, "
        "om.payload AS original_payload FROM interface_recovery_attempt AS ra "
        "LEFT JOIN interface_message AS rm ON rm.message_id = ra.resulting_message_id "
        "LEFT JOIN interface_error_queue AS eq ON eq.queue_id = ra.queue_id "
        "LEFT JOIN interface_message AS om "
        "  ON om.message_id = eq.message_id AND ra.action = 'RETRY_ORIGINAL' "
        "WHERE rm.message_id IS NOT NULL OR om.message_id IS NOT NULL "
        "ORDER BY ra.attempt_id",
    ):
        subjects = [_attempt(a["attempt_id"])]
        obs: list[str] = []
        if a["resulting_payload"] is not None:
            digest = sha256_text(a["resulting_payload"])
            if digest != a["payload_sha256"]:
                subjects.append(_msg(a["resulting_message_id"]))
                obs.append(
                    f"recovery attempt {a['attempt_id']} payload_sha256 "
                    f"{a['payload_sha256']} differs from resulting interface_message "
                    f"{a['resulting_message_id']} payload sha256 {digest}."
                )
        if a["original_payload"] is not None:
            digest = sha256_text(a["original_payload"])
            if digest != a["payload_sha256"]:
                subjects.append(_msg(a["original_message_id"]))
                obs.append(
                    f"RETRY_ORIGINAL recovery attempt {a['attempt_id']} payload_sha256 "
                    f"{a['payload_sha256']} differs from original interface_message "
                    f"{a['original_message_id']} payload sha256 {digest}."
                )
        if obs:
            out.append(_finding("REC-006", subjects, obs))

    for a in _select(
        conn,
        "SELECT ra.attempt_id, ra.queue_id, ra.outcome, eq.failure_code "
        "FROM interface_recovery_attempt AS ra "
        "JOIN interface_error_queue AS eq ON eq.queue_id = ra.queue_id "
        "WHERE ra.action = 'RETRY_ORIGINAL' AND ra.outcome IN ('SUCCEEDED', 'FAILED') "
        "AND (eq.failure_code IS NULL OR eq.failure_code <> 'ORDER_NOT_FOUND') "
        "ORDER BY ra.attempt_id",
    ):
        out.append(_finding(
            "REC-007", [_attempt(a["attempt_id"]), _queue(a["queue_id"])],
            [f"RETRY_ORIGINAL recovery attempt {a['attempt_id']} was processed "
             f"({a['outcome']}) on interface_error_queue {a['queue_id']} with "
             f"failure_code {_null(a['failure_code'])}."],
        ))

    for e in _select(
        conn,
        "SELECT event_id, entity_id, actor, detail, created_at FROM audit_event "
        "WHERE action = 'REQUEST_ID_CONFLICT' AND entity_type = 'interface_error_queue' "
        "ORDER BY event_id",
    ):
        out.append(_finding(
            "REC-008", [ref("audit_event", e["event_id"]), _queue(e["entity_id"])],
            [f"audit_event {e['event_id']} at {e['created_at']} by {e['actor']}: "
             f"{_null(e['detail'])}"],
        ))


def _check_audit(conn: sqlite3.Connection, out: list[dict[str, Any]]) -> None:
    filed_events = _select(
        conn,
        "SELECT event_id, order_id, detail FROM audit_event "
        "WHERE action = ? ORDER BY event_id",
        (INBOUND_RESULT_FILED,),
    )
    named: dict[int, list[dict[str, Any]]] = {}
    for e in filed_events:
        for mid in audit_message_ids(e["detail"]):
            named.setdefault(mid, []).append(e)

    filed_messages = _select(
        conn,
        "SELECT message_id, order_id FROM interface_message "
        "WHERE direction = 'INBOUND' AND status = 'FILED' ORDER BY message_id",
    )
    for m in filed_messages:
        events = named.get(m["message_id"], [])
        if not any(e["order_id"] == m["order_id"] and m["order_id"] is not None
                   for e in events):
            out.append(_finding(
                "AUD-001", [_msg(m["message_id"])],
                [f"FILED inbound interface_message {m['message_id']} (order_id "
                 f"{_null(m['order_id'])}) has no {INBOUND_RESULT_FILED} audit event "
                 f"on its order naming message_id={m['message_id']}."],
            ))

    targets = {
        m["message_id"]: m
        for m in _select(
            conn,
            "SELECT message_id, direction, status, order_id FROM interface_message "
            "WHERE message_id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(named)),),
        )
    }
    for e in filed_events:
        subject = ref("audit_event", e["event_id"])
        if not audit_message_ids(e["detail"]):
            out.append(_finding("AUD-002", [subject], [
                f"audit_event {e['event_id']} ({INBOUND_RESULT_FILED}, order "
                f"{_null(e['order_id'])}) names no message_id; detail: "
                f"{_null(e['detail'])}"
            ]))
            continue
        for mid in audit_message_ids(e["detail"]):
            t = targets.get(mid)
            if t is None:
                obs = (
                    f"audit_event {e['event_id']} ({INBOUND_RESULT_FILED}, order "
                    f"{_null(e['order_id'])}) names message_id={mid}, which does not "
                    f"exist."
                )
                subjects = [subject]
            elif t["direction"] != "INBOUND":
                obs = (
                    f"audit_event {e['event_id']} ({INBOUND_RESULT_FILED}, order "
                    f"{_null(e['order_id'])}) names {t['direction']} interface_message "
                    f"{mid}, not INBOUND."
                )
                subjects = [subject, _msg(mid)]
            elif t["status"] != "FILED":
                obs = (
                    f"audit_event {e['event_id']} ({INBOUND_RESULT_FILED}, order "
                    f"{_null(e['order_id'])}) names {t['direction']} interface_message "
                    f"{mid}, whose status is {t['status']}, not FILED."
                )
                subjects = [subject, _msg(mid)]
            elif t["order_id"] != e["order_id"]:
                obs = (
                    f"audit_event {e['event_id']} ({INBOUND_RESULT_FILED}, order "
                    f"{_null(e['order_id'])}) names interface_message {mid}, which is "
                    f"FILED to order {_null(t['order_id'])}."
                )
                subjects = [subject, _msg(mid)]
            else:
                continue
            out.append(_finding("AUD-002", subjects, [obs]))

    for r in _select(
        conn,
        "SELECT fr.result_id, fr.order_id FROM fish_result AS fr "
        "WHERE NOT EXISTS (SELECT 1 FROM audit_event AS ae "
        "  WHERE ae.entity_type = 'fish_result' AND ae.entity_id = fr.result_id "
        "  AND ae.action = 'RESULT_ENTERED') ORDER BY fr.result_id",
    ):
        out.append(_finding(
            "AUD-003", [ref("fish_result", r["result_id"]), _order(r["order_id"])],
            [f"fish_result {r['result_id']} (order {r['order_id']}) has no "
             f"RESULT_ENTERED audit event for entity fish_result {r['result_id']}."],
        ))

    for o in _select(
        conn,
        "SELECT o.order_id, "
        "EXISTS (SELECT 1 FROM report AS r WHERE r.order_id = o.order_id "
        "        AND r.status = 'FINALIZED') AS has_report, "
        "EXISTS (SELECT 1 FROM audit_event AS ae WHERE ae.order_id = o.order_id "
        "        AND ae.entity_type = 'report' AND ae.action = 'REPORT_FINALIZED') "
        "        AS has_report_event, "
        "EXISTS (SELECT 1 FROM audit_event AS ae WHERE ae.entity_type = 'lab_order' "
        "        AND ae.entity_id = o.order_id AND ae.action = 'FINALIZED') "
        "        AS has_finalized_event "
        "FROM lab_order AS o WHERE o.status = 'FINALIZED' ORDER BY o.order_id",
    ):
        missing = [
            label
            for label, present in (
                ("a FINALIZED report row", o["has_report"]),
                ("a REPORT_FINALIZED audit event", o["has_report_event"]),
                ("a FINALIZED audit event", o["has_finalized_event"]),
            )
            if not present
        ]
        if missing:
            out.append(_finding(
                "AUD-004", [_order(o["order_id"])],
                [f"FINALIZED order {o['order_id']} is missing {m}." for m in missing],
            ))

    for row in _select(
        conn,
        "SELECT 'lab_order' AS tbl, o.order_id AS row_id, o.order_id AS order_id, "
        "'ORDERED' AS action FROM lab_order AS o WHERE NOT EXISTS ("
        "  SELECT 1 FROM audit_event AS ae WHERE ae.entity_type = 'lab_order' "
        "  AND ae.entity_id = o.order_id AND ae.action = 'ORDERED') "
        "UNION ALL "
        "SELECT 'specimen', s.specimen_id, s.order_id, 'RECEIVED' FROM specimen AS s "
        "WHERE NOT EXISTS (SELECT 1 FROM audit_event AS ae "
        "  WHERE ae.entity_type = 'specimen' AND ae.entity_id = s.specimen_id "
        "  AND ae.action = 'RECEIVED') "
        "UNION ALL "
        "SELECT 'specimen', s.specimen_id, s.order_id, 'ACCESSIONED' FROM specimen AS s "
        "WHERE s.status = 'ACCESSIONED' AND NOT EXISTS (SELECT 1 FROM audit_event AS ae "
        "  WHERE ae.entity_type = 'specimen' AND ae.entity_id = s.specimen_id "
        "  AND ae.action = 'ACCESSIONED') "
        "ORDER BY tbl, row_id, action",
    ):
        subjects = [ref(row["tbl"], row["row_id"])]
        if row["tbl"] == "specimen":
            subjects.append(_order(row["order_id"]))
        out.append(_finding(
            "AUD-005", subjects,
            [f"{row['tbl']} {row['row_id']} has no {row['action']} audit event."],
        ))


# ---------------------------------------------------------------------------
# Workflow checks (validate_order once per order in scope)
# ---------------------------------------------------------------------------

def _check_workflow(
    conn: sqlite3.Connection,
    out: list[dict[str, Any]],
    order_ids: set[int] | None,
) -> None:
    if order_ids is None:
        orders = _select(conn, "SELECT order_id, status FROM lab_order ORDER BY order_id")
        stored_rows = _select(
            conn,
            "SELECT error_id, order_id, rule_code, severity, message "
            "FROM validation_error ORDER BY error_id",
        )
    else:
        scope = json.dumps(sorted(int(o) for o in order_ids))
        orders = _select(
            conn,
            "SELECT order_id, status FROM lab_order "
            "WHERE order_id IN (SELECT value FROM json_each(?)) ORDER BY order_id",
            (scope,),
        )
        stored_rows = _select(
            conn,
            "SELECT error_id, order_id, rule_code, severity, message "
            "FROM validation_error WHERE order_id IN (SELECT value FROM json_each(?)) "
            "ORDER BY error_id",
            (scope,),
        )
    stored_by_order: dict[int, list[dict[str, Any]]] = {}
    for s in stored_rows:
        stored_by_order.setdefault(s["order_id"], []).append(s)

    for o in orders:
        oid = o["order_id"]
        current = validation.validate_order(conn, oid)
        errors = [f for f in current if f.is_error]
        if errors and o["status"] not in ("FINALIZED", "CANCELLED"):
            out.append(_finding(
                "WF-001", [_order(oid)],
                [f"order {oid} ({o['status']}) blocking {f.rule_code}: {f.message}"
                 for f in errors],
            ))
        if errors and o["status"] == "FINALIZED":
            out.append(_finding(
                "WF-002", [_order(oid)],
                [f"FINALIZED order {oid} now fails {f.rule_code}: {f.message}"
                 for f in errors],
            ))

        stored = stored_by_order.get(oid, [])
        remaining = Counter((f.rule_code, f.severity, f.message) for f in current)
        stored_only: list[dict[str, Any]] = []
        for s in stored:
            key = (s["rule_code"], s["severity"], s["message"])
            if remaining[key] > 0:
                remaining[key] -= 1
            else:
                stored_only.append(s)
        current_only = sorted(remaining.elements())
        if stored_only or current_only:
            obs = [f"order {oid}: {len(stored)} stored validation_error row(s); "
                   f"{len(current)} current finding(s)."]
            obs += [
                f"stored only (validation_error {s['error_id']}): "
                f"{s['rule_code']} {s['severity']}: {s['message']}"
                for s in stored_only
            ]
            obs += [f"current only: {c[0]} {c[1]}: {c[2]}" for c in current_only]
            out.append(_finding(
                "WF-003",
                [_order(oid)] + [ref("validation_error", s["error_id"]) for s in stored_only],
                obs,
            ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reconcile_database(
    conn: sqlite3.Connection, *, order_ids: set[int] | None = None
) -> list[dict[str, Any]]:
    """Run every rule in ``RULES`` and return sorted findings (read-only).

    All non-WF checks cover the whole database with a constant number of
    statements. WF-* checks run for ``order_ids`` when given (an empty set
    means none), otherwise for every order.
    """
    findings: list[dict[str, Any]] = []
    _check_relational(conn, findings)
    _check_messages(conn, findings)
    _check_queue(conn, findings)
    _check_recovery(conn, findings)
    _check_audit(conn, findings)
    _check_workflow(conn, findings, order_ids)
    findings.sort(key=_sort_key)
    return findings


def reconcile_case(conn: sqlite3.Connection, case: dict[str, Any]) -> list[dict[str, Any]]:
    """Findings that touch a case view built by ``trace.build_case``.

    Runs ``reconcile_database`` with WF-* checks scoped to the case orders and
    keeps findings with at least one subject among the case's record refs.
    """
    refs = case_record_refs(case)
    order_ids = {o["order_id"] for o in case.get("orders", [])}
    return [
        f
        for f in reconcile_database(conn, order_ids=order_ids)
        if any(s in refs for s in f["subjects"])
    ]


def summarize(findings: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Count findings by severity: ``{"ERROR": n, "WARNING": n, "INFO": n}``."""
    counts = {s: 0 for s in SEVERITY_ORDER}
    for f in findings:
        counts[f["severity"]] += 1
    return counts
