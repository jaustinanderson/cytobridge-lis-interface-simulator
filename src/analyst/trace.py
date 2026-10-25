"""Case view ("trace") for the CytoBridge analyst lab.

``build_case`` assembles everything the database records about one incident -
an order, an error-queue item, an interface message, or an accession - into a
single deterministic, JSON-serializable case view:

- the records themselves (orders with patient, specimens, current results,
  stored validation findings and report; interface messages; queue items;
  recovery attempts; audit events);
- typed links between them, each labelled with its evidential ``basis``
  (a real foreign key, a payload-derived accession match, free-text audit
  detail, or a denormalized column);
- a chronological timeline;
- provenance notes stating what the schema does NOT record (for example which
  message produced a current FISH result), and the standard limitations.

The case closure is a fixpoint over these edges, starting from the anchor:

    queue   -> original message           (interface_error_queue.message_id, FK)
    queue   -> recovery attempts          (interface_recovery_attempt.queue_id, FK)
    attempt -> resulting message          (interface_recovery_attempt.resulting_message_id, FK)
    message -> queue items naming it      (FK)
    message -> attempt that produced it -> that attempt's queue item (FK)
    message -> order                      (interface_message.order_id, FK, when not NULL)
    inbound HL7 message with NULL order_id -> order whose accession_number equals
               the payload OBR-3 accession (PAYLOAD_DERIVED, only if such an order exists)
    order   -> every interface_message with that order_id (FK)
    order   -> inbound HL7 messages with NULL order_id whose payload OBR-3
               accession equals the order accession (PAYLOAD_DERIVED)

Every query is a read-only SELECT gathered set-wise (JSON id lists through
``json_each``), never one query per message, attempt, or audit row, so the view
works on a ``store.open_read_only`` connection and stays cheap as the database
grows. Nothing here infers causation from timestamps, and nothing claims to
reconstruct earlier result values: ``fish_result`` is upserted and has no
foreign key to the message that wrote it.

This is an educational, synthetic simulator - not a production LIS or a
certified interface engine. All data is synthetic. No PHI.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Iterable

from ..interfaces import inbound_hl7
from .common import AnalystError, limitation_list, ref, sha256_text

# ---------------------------------------------------------------------------
# Link vocabulary (exact strings; see the analyst-lab contract, section 2)
# ---------------------------------------------------------------------------

BASIS_FOREIGN_KEY = "FOREIGN_KEY"
BASIS_PAYLOAD_DERIVED = "PAYLOAD_DERIVED"
BASIS_AUDIT_DETAIL_TEXT = "AUDIT_DETAIL_TEXT"
BASIS_DENORMALIZED_COLUMN = "DENORMALIZED_COLUMN"
BASES = (
    BASIS_FOREIGN_KEY,
    BASIS_PAYLOAD_DERIVED,
    BASIS_AUDIT_DETAIL_TEXT,
    BASIS_DENORMALIZED_COLUMN,
)

REL_QUEUE_MESSAGE = "interface_error_queue.message_id"
REL_ATTEMPT_QUEUE = "interface_recovery_attempt.queue_id"
REL_ATTEMPT_RESULT = "interface_recovery_attempt.resulting_message_id"
REL_MESSAGE_ORDER = "interface_message.order_id"
REL_ORDER_PATIENT = "lab_order.patient_id"
REL_SPECIMEN_ORDER = "specimen.order_id"
REL_RESULT_ORDER = "fish_result.order_id"
REL_REPORT_ORDER = "report.order_id"
REL_VALIDATION_ORDER = "validation_error.order_id"
REL_PAYLOAD_ACCESSION = "payload OBR-3 accession = lab_order.accession_number"
# A description of the free-text pattern, not a value: the linked message is
# the link's "to" ref.
REL_AUDIT_DETAIL = "audit detail message_id=N"
REL_AUDIT_ORDER = "audit_event.order_id"

# Message roles.
ROLE_INBOUND_DIRECT = "INBOUND_DIRECT"
ROLE_OUTBOUND_EXPORT = "OUTBOUND_EXPORT"
ROLE_QUEUE_ORIGINAL = "QUEUE_ORIGINAL"
ROLE_RECOVERY_RESULT = "RECOVERY_RESULT"

# Provenance note codes.
NOTE_FAILED_MESSAGE_NO_ORDER_FK = "FAILED_MESSAGE_NO_ORDER_FK"
NOTE_ACCESSION_NOT_MATCHED = "ACCESSION_NOT_MATCHED"
NOTE_ACCESSION_UNREADABLE = "ACCESSION_UNREADABLE"
NOTE_RESULT_SOURCE_NOT_RECORDED = "RESULT_SOURCE_NOT_RECORDED"
NOTE_AUDIT_REFERENCE_UNRESOLVED = "AUDIT_REFERENCE_UNRESOLVED"
NOTE_RAW_PAYLOAD_MISMATCH = "RAW_PAYLOAD_MISMATCH"

# The audit action the inbound filing path writes (src/interfaces/inbound_hl7.py
# ``_file_results``): detail "message_id=<id> probes=<n> (<code>, <code>)".
INBOUND_RESULT_FILED = "INBOUND_RESULT_FILED"
AUDIT_MESSAGE_ID_RE = re.compile(r"\bmessage_id=(\d+)\b")
_AUDIT_PROBE_LIST_RE = re.compile(r"\(([^()]*)\)")

# Timeline ranks: a fixed, presentational order for events in the same second.
RANK_MESSAGE = 0
RANK_QUEUED = 1
RANK_ATTEMPT = 2
RANK_AUDIT = 3
RANK_QUEUE_CLOSED = 4

_ALWAYS_LIMITATIONS = (
    "RESULTS_UPSERTED",
    "NO_RESULT_SOURCE_FK",
    "RESULT_AUDIT_ACTOR",
    "TIMESTAMP_RESOLUTION",
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _select(
    conn: sqlite3.Connection, sql: str, params: Any = ()
) -> list[dict[str, Any]]:
    """Run one read-only SELECT and return plain dicts.

    Uses a private cursor with ``sqlite3.Row`` so the caller's connection
    settings (row factory) are never changed.
    """
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]
    finally:
        cur.close()


def _ids(values: Iterable[Any]) -> str:
    """JSON array parameter for ``IN (SELECT value FROM json_each(?))``."""
    return json.dumps(sorted({v for v in values if v is not None}))


def _ascii(text: Any) -> str:
    """ASCII-only rendering of a stored value for summaries and notes."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def _is_inbound_hl7(message: dict[str, Any]) -> bool:
    return message["direction"] == "INBOUND" and message["format"] == "HL7"


def audit_message_ids(detail: str | None) -> list[int]:
    """Message ids named in an audit detail by ``message_id=N`` (sorted, unique)."""
    if not detail:
        return []
    return sorted({int(m) for m in AUDIT_MESSAGE_ID_RE.findall(detail)})


def audit_probe_codes(detail: str | None) -> list[str]:
    """Probe codes listed in an INBOUND_RESULT_FILED detail ``(A, B, ...)``."""
    if not detail:
        return []
    match = _AUDIT_PROBE_LIST_RE.search(detail)
    if match is None:
        return []
    return sorted({p.strip() for p in match.group(1).split(",") if p.strip()})


# ---------------------------------------------------------------------------
# Payload accession
# ---------------------------------------------------------------------------

def payload_accession(payload: str) -> str | None:
    """Best-effort OBR-3 accession from an inbound HL7-style payload.

    Reads the first OBR segment with the same lenient segment splitting the
    inbound parser uses and returns component 0 of field 3, stripped; an empty
    value, a missing OBR segment, or any parsing problem returns ``None``.
    Never raises.
    """
    try:
        for segment in inbound_hl7._split_segments(payload or ""):
            if segment and segment[0].strip() == "OBR":
                value = inbound_hl7._component(inbound_hl7._field(segment, 3), 0)
                return value or None
        return None
    except Exception:  # noqa: BLE001 - best effort by contract
        return None


def _accession_unreadable_reason(payload: str | None) -> str:
    """Why a payload has no readable OBR-3 accession (for a provenance note)."""
    try:
        segments = inbound_hl7._split_segments(payload or "")
    except Exception:  # noqa: BLE001
        return "the payload could not be split into segments"
    if not segments:
        return "the payload is empty (no segments)"
    if not any(s and s[0].strip() == "OBR" for s in segments):
        return "the payload has no OBR segment"
    return "the first OBR segment has an empty OBR-3"


# ---------------------------------------------------------------------------
# Anchor resolution
# ---------------------------------------------------------------------------

def resolve_anchor(
    conn: sqlite3.Connection,
    *,
    order_id: int | None = None,
    queue_id: int | None = None,
    message_id: int | None = None,
    accession: str | None = None,
) -> dict[str, Any]:
    """Resolve exactly one selector to ``{"kind", "id"}``.

    ``kind`` is ``"order"``, ``"queue"``, or ``"message"``; an accession
    resolves to its order. Zero or several selectors raise ``AnalystError``
    ``USAGE``; an unknown id or accession raises ``NOT_FOUND``.
    """
    given = [
        name
        for name, value in (
            ("order_id", order_id),
            ("queue_id", queue_id),
            ("message_id", message_id),
            ("accession", accession),
        )
        if value is not None
    ]
    if len(given) != 1:
        raise AnalystError(
            "USAGE",
            "Exactly one of order_id, queue_id, message_id, or accession must be "
            f"given (got {len(given)}: {', '.join(given) or 'none'}).",
        )
    if accession is not None:
        rows = _select(
            conn,
            "SELECT order_id FROM lab_order WHERE accession_number = ?",
            (accession,),
        )
        if not rows:
            raise AnalystError(
                "NOT_FOUND", f"No order has accession number {_ascii(accession)}."
            )
        return {"kind": "order", "id": rows[0]["order_id"]}
    if order_id is not None:
        sql, kind, value, label = (
            "SELECT 1 FROM lab_order WHERE order_id = ?", "order", order_id, "Order"
        )
    elif queue_id is not None:
        sql, kind, value, label = (
            "SELECT 1 FROM interface_error_queue WHERE queue_id = ?",
            "queue", queue_id, "Error-queue item",
        )
    else:
        sql, kind, value, label = (
            "SELECT 1 FROM interface_message WHERE message_id = ?",
            "message", message_id, "Interface message",
        )
    if not _select(conn, sql, (value,)):
        raise AnalystError("NOT_FOUND", f"{label} {value} does not exist.")
    return {"kind": kind, "id": int(value)}


# ---------------------------------------------------------------------------
# Closure
# ---------------------------------------------------------------------------

def _closure(conn: sqlite3.Connection, anchor: dict[str, Any]) -> dict[str, Any]:
    """Compute the case closure as a fixpoint over the documented edges.

    Returns the raw rows found (only rows that exist) and the set of
    payload-derived (message_id, order_id) pairs.
    """
    orders: dict[int, dict[str, Any]] = {}
    messages: dict[int, dict[str, Any]] = {}
    queues: dict[int, dict[str, Any]] = {}
    attempts: dict[int, dict[str, Any]] = {}
    payload_links: set[tuple[int, int]] = set()

    pending: dict[str, set[int]] = {"order": set(), "message": set(), "queue": set()}
    seen: dict[str, set[int]] = {"order": set(), "message": set(), "queue": set()}
    pending[anchor["kind"]].add(anchor["id"])

    def add_attempts(rows: list[dict[str, Any]]) -> None:
        for a in rows:
            attempts.setdefault(a["attempt_id"], a)
            pending["queue"].add(a["queue_id"])
            if a["resulting_message_id"] is not None:
                pending["message"].add(a["resulting_message_id"])

    while True:
        frontier = {k: pending[k] - seen[k] for k in pending}
        if not any(frontier.values()):
            break
        for k in pending:
            seen[k] |= frontier[k]
            pending[k] = set()

        q_ids = frontier["queue"]
        if q_ids:
            for q in _select(
                conn,
                "SELECT * FROM interface_error_queue "
                "WHERE queue_id IN (SELECT value FROM json_each(?))",
                (_ids(q_ids),),
            ):
                queues[q["queue_id"]] = q
                if q["message_id"] is not None:
                    pending["message"].add(q["message_id"])
            add_attempts(
                _select(
                    conn,
                    "SELECT * FROM interface_recovery_attempt "
                    "WHERE queue_id IN (SELECT value FROM json_each(?))",
                    (_ids(q_ids),),
                )
            )

        m_ids = frontier["message"]
        if m_ids:
            null_order_accessions: dict[int, str] = {}
            for m in _select(
                conn,
                "SELECT * FROM interface_message "
                "WHERE message_id IN (SELECT value FROM json_each(?))",
                (_ids(m_ids),),
            ):
                messages[m["message_id"]] = m
                if m["order_id"] is not None:
                    pending["order"].add(m["order_id"])
                elif _is_inbound_hl7(m):
                    acc = payload_accession(m["payload"])
                    if acc is not None:
                        null_order_accessions[m["message_id"]] = acc
            if null_order_accessions:
                by_accession = {
                    o["accession_number"]: o["order_id"]
                    for o in _select(
                        conn,
                        "SELECT order_id, accession_number FROM lab_order "
                        "WHERE accession_number IN (SELECT value FROM json_each(?))",
                        (json.dumps(sorted(set(null_order_accessions.values()))),),
                    )
                }
                for mid, acc in null_order_accessions.items():
                    if acc in by_accession:
                        payload_links.add((mid, by_accession[acc]))
                        pending["order"].add(by_accession[acc])
            for q in _select(
                conn,
                "SELECT queue_id FROM interface_error_queue "
                "WHERE message_id IN (SELECT value FROM json_each(?))",
                (_ids(m_ids),),
            ):
                pending["queue"].add(q["queue_id"])
            add_attempts(
                _select(
                    conn,
                    "SELECT * FROM interface_recovery_attempt "
                    "WHERE resulting_message_id IN (SELECT value FROM json_each(?))",
                    (_ids(m_ids),),
                )
            )

        o_ids = frontier["order"]
        if o_ids:
            found = _select(
                conn,
                "SELECT order_id, accession_number FROM lab_order "
                "WHERE order_id IN (SELECT value FROM json_each(?))",
                (_ids(o_ids),),
            )
            for o in found:
                orders[o["order_id"]] = o
            for m in _select(
                conn,
                "SELECT message_id FROM interface_message "
                "WHERE order_id IN (SELECT value FROM json_each(?))",
                (_ids(o_ids),),
            ):
                pending["message"].add(m["message_id"])
            if found:
                # One query per closure round (not per order): NULL-order inbound
                # HL7 messages whose payload contains any of these accessions as
                # a substring, then an exact OBR-3 comparison in Python.
                acc_to_order = {o["accession_number"]: o["order_id"] for o in found}
                for m in _select(
                    conn,
                    "SELECT im.message_id, im.payload FROM interface_message AS im "
                    "WHERE im.direction = 'INBOUND' AND im.format = 'HL7' "
                    "AND im.order_id IS NULL AND EXISTS ("
                    "  SELECT 1 FROM json_each(?) AS j "
                    "  WHERE instr(im.payload, j.value) > 0)",
                    (json.dumps(sorted(acc_to_order)),),
                ):
                    acc = payload_accession(m["payload"])
                    if acc is not None and acc in acc_to_order:
                        payload_links.add((m["message_id"], acc_to_order[acc]))
                        pending["message"].add(m["message_id"])

    return {
        "orders": orders,
        "messages": messages,
        "queues": queues,
        "attempts": attempts,
        "payload_links": payload_links,
    }


# ---------------------------------------------------------------------------
# Record shaping
# ---------------------------------------------------------------------------

def _load_orders(
    conn: sqlite3.Connection, order_ids: set[int]
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Full ORDER dicts plus the child rows used for links."""
    if not order_ids:
        return [], {"specimen": [], "fish_result": [], "report": [], "validation_error": []}
    ids = _ids(order_ids)
    order_rows = _select(
        conn,
        "SELECT o.*, pa.panel_code, "
        "  (SELECT COUNT(*) FROM probe AS pr "
        "   WHERE pr.panel_id = o.panel_id AND pr.is_required = 1) AS required_probe_count "
        "FROM lab_order AS o LEFT JOIN panel AS pa ON pa.panel_id = o.panel_id "
        "WHERE o.order_id IN (SELECT value FROM json_each(?)) ORDER BY o.order_id",
        (ids,),
    )
    patients = {
        p["patient_id"]: p
        for p in _select(
            conn,
            "SELECT patient_id, mrn, last_name, first_name, date_of_birth, sex "
            "FROM patient WHERE patient_id IN (SELECT value FROM json_each(?))",
            (_ids(o["patient_id"] for o in order_rows),),
        )
    }
    specimens = _select(
        conn,
        "SELECT specimen_id, order_id, specimen_type, external_specimen_id, "
        "collected_at, received_at, status, rejection_reason FROM specimen "
        "WHERE order_id IN (SELECT value FROM json_each(?)) ORDER BY specimen_id",
        (ids,),
    )
    results = _select(
        conn,
        "SELECT fr.result_id, fr.order_id, fr.probe_id, pr.probe_code, "
        "fr.cells_scored, fr.cells_abnormal, fr.signal_pattern, fr.interpretation, "
        "fr.entered_by, fr.entered_at FROM fish_result AS fr "
        "LEFT JOIN probe AS pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY fr.order_id, fr.probe_id",
        (ids,),
    )
    validation_errors = _select(
        conn,
        "SELECT error_id, order_id, rule_code, severity, message, created_at "
        "FROM validation_error WHERE order_id IN (SELECT value FROM json_each(?)) "
        "ORDER BY error_id",
        (ids,),
    )
    reports = _select(
        conn,
        "SELECT report_id, order_id, status, finalized_by, finalized_at, "
        "created_at, summary_text FROM report "
        "WHERE order_id IN (SELECT value FROM json_each(?)) ORDER BY report_id",
        (ids,),
    )

    shaped: list[dict[str, Any]] = []
    for o in order_rows:
        oid = o["order_id"]
        report_rows = [r for r in reports if r["order_id"] == oid]
        report = None
        if report_rows:
            r = report_rows[0]
            report = {
                "report_id": r["report_id"],
                "status": r["status"],
                "finalized_by": r["finalized_by"],
                "finalized_at": r["finalized_at"],
                "created_at": r["created_at"],
                "summary_sha256": sha256_text(r["summary_text"]),
            }
        shaped.append(
            {
                "order_id": oid,
                "accession_number": o["accession_number"],
                "status": o["status"],
                "priority": o["priority"],
                "ordering_provider": o["ordering_provider"],
                "ordered_at": o["ordered_at"],
                "collected_at": o["collected_at"],
                "finalized_at": o["finalized_at"],
                "panel_code": o["panel_code"],
                "required_probe_count": o["required_probe_count"],
                "patient": patients.get(o["patient_id"]),
                "specimens": [
                    {k: s[k] for k in s if k != "order_id"}
                    for s in specimens
                    if s["order_id"] == oid
                ],
                "current_results": [
                    {k: r[k] for k in r if k != "order_id"}
                    for r in results
                    if r["order_id"] == oid
                ],
                "stored_validation_errors": [
                    {k: v[k] for k in v if k != "order_id"}
                    for v in validation_errors
                    if v["order_id"] == oid
                ],
                "report": report,
                "_patient_id": o["patient_id"],
            }
        )
    children = {
        "specimen": specimens,
        "fish_result": results,
        "report": reports,
        "validation_error": validation_errors,
    }
    return shaped, children


def _shape_message(m: dict[str, Any], roles: list[str]) -> dict[str, Any]:
    payload = m["payload"]
    return {
        "message_id": m["message_id"],
        "direction": m["direction"],
        "format": m["format"],
        "message_type": m["message_type"],
        "status": m["status"],
        "order_id": m["order_id"],
        "control_id": m["control_id"],
        "created_at": m["created_at"],
        "payload": payload,
        "payload_sha256": sha256_text(payload),
        "payload_bytes": len(payload.encode("utf-8")),
        "payload_accession": payload_accession(payload) if _is_inbound_hl7(m) else None,
        "roles": sorted(roles),
    }


def _shape_queue(q: dict[str, Any], original: dict[str, Any] | None) -> dict[str, Any]:
    raw = q["raw_payload"]
    return {
        "queue_id": q["queue_id"],
        "message_id": q["message_id"],
        "direction": q["direction"],
        "reason": q["reason"],
        "failure_code": q["failure_code"],
        "failure_category": q["failure_category"],
        "recovery_policy": q["recovery_policy"],
        "status": q["status"],
        "created_at": q["created_at"],
        "resolved_at": q["resolved_at"],
        "terminal_at": q["terminal_at"],
        "raw_payload_sha256": None if raw is None else sha256_text(raw),
        "raw_payload_matches_original": (
            None if original is None else raw == original["payload"]
        ),
    }


_ATTEMPT_KEYS = (
    "attempt_id", "queue_id", "request_id", "action", "payload_sha256", "outcome",
    "actor", "outcome_detail", "attempted_at", "resulting_message_id",
)
_AUDIT_KEYS = (
    "event_id", "entity_type", "entity_id", "order_id", "action", "detail",
    "actor", "created_at",
)


def _link(src: str, dst: str, relation: str, basis: str) -> dict[str, str]:
    return {"from": src, "to": dst, "relation": relation, "basis": basis}


# ---------------------------------------------------------------------------
# Public case builder
# ---------------------------------------------------------------------------

def build_case(
    conn: sqlite3.Connection,
    *,
    order_id: int | None = None,
    queue_id: int | None = None,
    message_id: int | None = None,
    accession: str | None = None,
) -> dict[str, Any]:
    """Build the deterministic case view for exactly one anchor selector.

    Read-only. Returns a dict with keys ``anchor``, ``orders``, ``messages``,
    ``queue_items``, ``recovery_attempts``, ``audit_events``, ``links``,
    ``timeline``, ``provenance``, and ``limitations`` (always all present).
    Sort orders: orders by order_id, messages by message_id, queue items by
    queue_id, attempts by attempt_id, audit events by event_id, links by
    (from, to, relation), timeline by (at, rank, table, id, kind), provenance
    by (subject, code, detail), limitations by code.
    """
    anchor = resolve_anchor(
        conn,
        order_id=order_id,
        queue_id=queue_id,
        message_id=message_id,
        accession=accession,
    )
    closure = _closure(conn, anchor)
    raw_messages: dict[int, dict[str, Any]] = closure["messages"]
    raw_queues: dict[int, dict[str, Any]] = closure["queues"]
    raw_attempts: dict[int, dict[str, Any]] = closure["attempts"]
    payload_links: set[tuple[int, int]] = closure["payload_links"]

    orders, children = _load_orders(conn, set(closure["orders"]))
    order_ids = {o["order_id"] for o in orders}
    patient_ids = {o["_patient_id"] for o in orders if o["patient"] is not None}

    # --- audit events (one query) ------------------------------------------
    audit_rows = _select(
        conn,
        "SELECT * FROM audit_event "
        "WHERE order_id IN (SELECT value FROM json_each(:orders)) "
        "OR (entity_type = 'interface_error_queue' "
        "    AND entity_id IN (SELECT value FROM json_each(:queues))) "
        "OR (entity_type = 'patient' "
        "    AND entity_id IN (SELECT value FROM json_each(:patients))) "
        "ORDER BY event_id",
        {
            "orders": _ids(order_ids),
            "queues": _ids(raw_queues),
            "patients": _ids(patient_ids),
        },
    )
    audit_events = [{k: a[k] for k in _AUDIT_KEYS} for a in audit_rows]

    # Messages named by INBOUND_RESULT_FILED details (one query for any not
    # already in the case), used to resolve AUDIT_DETAIL_TEXT links.
    filed_events = [a for a in audit_events if a["action"] == INBOUND_RESULT_FILED]
    named_ids = {mid for a in filed_events for mid in audit_message_ids(a["detail"])}
    named_messages: dict[int, dict[str, Any]] = {
        mid: raw_messages[mid] for mid in named_ids if mid in raw_messages
    }
    outside = named_ids - set(raw_messages)
    if outside:
        for m in _select(
            conn,
            "SELECT message_id, direction, format, status, order_id "
            "FROM interface_message WHERE message_id IN (SELECT value FROM json_each(?))",
            (_ids(outside),),
        ):
            named_messages[m["message_id"]] = m

    # --- messages, queues, attempts ----------------------------------------
    queue_originals = {
        q["message_id"] for q in raw_queues.values() if q["message_id"] is not None
    }
    recovery_results = {
        a["resulting_message_id"]
        for a in raw_attempts.values()
        if a["resulting_message_id"] is not None
    }
    messages: list[dict[str, Any]] = []
    for mid in sorted(raw_messages):
        m = raw_messages[mid]
        roles: list[str] = []
        if m["direction"] == "OUTBOUND":
            roles.append(ROLE_OUTBOUND_EXPORT)
        if mid in queue_originals:
            roles.append(ROLE_QUEUE_ORIGINAL)
        if mid in recovery_results:
            roles.append(ROLE_RECOVERY_RESULT)
        if (
            m["direction"] == "INBOUND"
            and mid not in queue_originals
            and mid not in recovery_results
        ):
            roles.append(ROLE_INBOUND_DIRECT)
        messages.append(_shape_message(m, roles))

    queue_items = [
        _shape_queue(raw_queues[qid], raw_messages.get(raw_queues[qid]["message_id"]))
        for qid in sorted(raw_queues)
    ]
    recovery_attempts = [
        {k: raw_attempts[aid][k] for k in _ATTEMPT_KEYS} for aid in sorted(raw_attempts)
    ]

    # --- links ---------------------------------------------------------------
    links: list[dict[str, str]] = []
    for q in queue_items:
        if q["message_id"] in raw_messages:
            links.append(_link(
                ref("interface_error_queue", q["queue_id"]),
                ref("interface_message", q["message_id"]),
                REL_QUEUE_MESSAGE, BASIS_FOREIGN_KEY,
            ))
    for a in recovery_attempts:
        src = ref("interface_recovery_attempt", a["attempt_id"])
        if a["queue_id"] in raw_queues:
            links.append(_link(
                src, ref("interface_error_queue", a["queue_id"]),
                REL_ATTEMPT_QUEUE, BASIS_FOREIGN_KEY,
            ))
        if a["resulting_message_id"] in raw_messages:
            links.append(_link(
                src, ref("interface_message", a["resulting_message_id"]),
                REL_ATTEMPT_RESULT, BASIS_FOREIGN_KEY,
            ))
    for m in messages:
        if m["order_id"] is not None and m["order_id"] in order_ids:
            links.append(_link(
                ref("interface_message", m["message_id"]),
                ref("lab_order", m["order_id"]),
                REL_MESSAGE_ORDER, BASIS_FOREIGN_KEY,
            ))
    for mid, oid in payload_links:
        if mid in raw_messages and oid in order_ids:
            links.append(_link(
                ref("interface_message", mid), ref("lab_order", oid),
                REL_PAYLOAD_ACCESSION, BASIS_PAYLOAD_DERIVED,
            ))
    for o in orders:
        if o["patient"] is not None:
            links.append(_link(
                ref("lab_order", o["order_id"]), ref("patient", o["_patient_id"]),
                REL_ORDER_PATIENT, BASIS_FOREIGN_KEY,
            ))
    for table, key, relation in (
        ("specimen", "specimen_id", REL_SPECIMEN_ORDER),
        ("fish_result", "result_id", REL_RESULT_ORDER),
        ("report", "report_id", REL_REPORT_ORDER),
        ("validation_error", "error_id", REL_VALIDATION_ORDER),
    ):
        for row in children[table]:
            links.append(_link(
                ref(table, row[key]), ref("lab_order", row["order_id"]),
                relation, BASIS_FOREIGN_KEY,
            ))
    for a in audit_events:
        src = ref("audit_event", a["event_id"])
        if a["order_id"] is not None and a["order_id"] in order_ids:
            links.append(_link(
                src, ref("lab_order", a["order_id"]),
                REL_AUDIT_ORDER, BASIS_DENORMALIZED_COLUMN,
            ))
        if a["action"] == INBOUND_RESULT_FILED:
            for mid in audit_message_ids(a["detail"]):
                if mid in named_messages:
                    links.append(_link(
                        src, ref("interface_message", mid),
                        REL_AUDIT_DETAIL, BASIS_AUDIT_DETAIL_TEXT,
                    ))
    links.sort(key=lambda l: (l["from"], l["to"], l["relation"]))

    # --- provenance ----------------------------------------------------------
    provenance = _provenance(
        conn,
        orders=orders,
        messages=messages,
        queue_items=queue_items,
        audit_events=audit_events,
        payload_links=payload_links,
        named_messages=named_messages,
        raw_messages=raw_messages,
    )

    # --- timeline ------------------------------------------------------------
    timeline = _timeline(messages, queue_items, recovery_attempts, audit_events)

    codes = list(_ALWAYS_LIMITATIONS)
    if any(l["basis"] == BASIS_PAYLOAD_DERIVED for l in links) or any(
        n["code"] == NOTE_FAILED_MESSAGE_NO_ORDER_FK for n in provenance
    ):
        codes.append("FAILED_MESSAGE_ORDER_LINK")
    if any(l["basis"] == BASIS_AUDIT_DETAIL_TEXT for l in links):
        codes.append("AUDIT_DETAIL_TEXT")

    for o in orders:
        del o["_patient_id"]

    return {
        "anchor": anchor,
        "orders": orders,
        "messages": messages,
        "queue_items": queue_items,
        "recovery_attempts": recovery_attempts,
        "audit_events": audit_events,
        "links": links,
        "timeline": timeline,
        "provenance": provenance,
        "limitations": limitation_list(codes),
    }


def _provenance(
    conn: sqlite3.Connection,
    *,
    orders: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    queue_items: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
    payload_links: set[tuple[int, int]],
    named_messages: dict[int, dict[str, Any]],
    raw_messages: dict[int, dict[str, Any]],
) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []

    def note(code: str, subject: str, detail: str) -> None:
        notes.append({"code": code, "subject": subject, "detail": _ascii(detail)})

    # Which payload accessions exist as orders anywhere in the database (one query).
    inbound = [m for m in messages if m["direction"] == "INBOUND" and m["format"] == "HL7"]
    accessions = sorted({m["payload_accession"] for m in inbound if m["payload_accession"]})
    known_accessions: set[str] = set()
    if accessions:
        known_accessions = {
            r["accession_number"]
            for r in _select(
                conn,
                "SELECT accession_number FROM lab_order "
                "WHERE accession_number IN (SELECT value FROM json_each(?))",
                (json.dumps(accessions),),
            )
        }
    derived_by_message: dict[int, list[int]] = {}
    for mid, oid in payload_links:
        derived_by_message.setdefault(mid, []).append(oid)

    for m in messages:
        if m["direction"] != "INBOUND":
            continue
        mid = m["message_id"]
        subject = ref("interface_message", mid)
        acc = m["payload_accession"]
        if m["status"] != "FILED" and m["order_id"] is None:
            derived = sorted(derived_by_message.get(mid, []))
            if derived:
                targets = ", ".join(ref("lab_order", o) for o in derived)
                detail = (
                    f"interface_message {mid} is INBOUND with status {m['status']} "
                    f"and NULL order_id. Its link to {targets} is PAYLOAD_DERIVED "
                    f"(payload OBR-3 accession {acc} equals the order accession), "
                    f"not a foreign key."
                )
            else:
                detail = (
                    f"interface_message {mid} is INBOUND with status {m['status']} "
                    f"and NULL order_id. No foreign key links it to an order, and "
                    f"no order matches its payload accession "
                    f"({acc if acc else 'unreadable'})."
                )
            note(NOTE_FAILED_MESSAGE_NO_ORDER_FK, subject, detail)
        if m["format"] != "HL7":
            continue
        if acc is None:
            note(
                NOTE_ACCESSION_UNREADABLE,
                subject,
                f"interface_message {mid} has no readable OBR-3 accession: "
                f"{_accession_unreadable_reason(m['payload'])}.",
            )
        elif acc not in known_accessions:
            note(
                NOTE_ACCESSION_NOT_MATCHED,
                subject,
                f"interface_message {mid} payload OBR-3 accession {acc} does not "
                f"match any lab_order.accession_number in this database.",
            )

    for q in queue_items:
        if q["raw_payload_matches_original"] is False:
            original = raw_messages[q["message_id"]]
            raw_desc = (
                "is NULL"
                if q["raw_payload_sha256"] is None
                else f"has sha256 {q['raw_payload_sha256']}"
            )
            note(
                NOTE_RAW_PAYLOAD_MISMATCH,
                ref("interface_error_queue", q["queue_id"]),
                f"interface_error_queue {q['queue_id']} raw_payload {raw_desc}; the "
                f"original interface_message {q['message_id']} payload has sha256 "
                f"{sha256_text(original['payload'])}. The stored copies differ.",
            )

    # Audit references that do not resolve to a FILED message.
    filed_events = [a for a in audit_events if a["action"] == INBOUND_RESULT_FILED]
    for a in filed_events:
        for mid in audit_message_ids(a["detail"]):
            target = named_messages.get(mid)
            subject = ref("audit_event", a["event_id"])
            if target is None:
                note(
                    NOTE_AUDIT_REFERENCE_UNRESOLVED,
                    subject,
                    f"audit_event {a['event_id']} ({INBOUND_RESULT_FILED}) names "
                    f"message_id={mid}, which does not exist.",
                )
            elif target["status"] != "FILED":
                note(
                    NOTE_AUDIT_REFERENCE_UNRESOLVED,
                    subject,
                    f"audit_event {a['event_id']} ({INBOUND_RESULT_FILED}) names "
                    f"message_id={mid}, which has status {target['status']}, "
                    f"not FILED.",
                )

    # One RESULT_SOURCE_NOT_RECORDED note per current fish_result.
    entered_counts: dict[int, int] = {}
    for a in audit_events:
        if a["entity_type"] == "fish_result" and a["action"] == "RESULT_ENTERED":
            entered_counts[a["entity_id"]] = entered_counts.get(a["entity_id"], 0) + 1
    for o in orders:
        oid = o["order_id"]
        for r in o["current_results"]:
            code = r["probe_code"]
            candidates: set[int] = set()
            filings = 0
            for a in filed_events:
                if a["order_id"] != oid or code not in audit_probe_codes(a["detail"]):
                    continue
                for mid in audit_message_ids(a["detail"]):
                    target = named_messages.get(mid)
                    if (
                        target is not None
                        and target["direction"] == "INBOUND"
                        and target["status"] == "FILED"
                        and target["order_id"] == oid
                    ):
                        candidates.add(mid)
                        filings += 1
            entered = entered_counts.get(r["result_id"], 0)
            cand_text = (
                ", ".join(ref("interface_message", c) for c in sorted(candidates))
                or "none"
            )
            unmatched = entered - filings
            note(
                NOTE_RESULT_SOURCE_NOT_RECORDED,
                ref("fish_result", r["result_id"]),
                f"fish_result {r['result_id']} (order {oid}, probe {code}) holds only "
                f"the current value; the schema records no source message for it. "
                f"Candidate FILED inbound messages whose {INBOUND_RESULT_FILED} audit "
                f"detail names {code}: {cand_text}. RESULT_ENTERED events for this "
                f"result: {entered}; inbound filings naming this probe: {filings}; "
                f"RESULT_ENTERED events without a matching inbound filing: "
                f"{'yes (' + str(unmatched) + ')' if unmatched > 0 else 'no'}. "
                f"The current value is not attributed to any one source.",
            )

    notes.sort(key=lambda n: (n["subject"], n["code"], n["detail"]))
    return notes


def _timeline(
    messages: list[dict[str, Any]],
    queue_items: list[dict[str, Any]],
    attempts: list[dict[str, Any]],
    audit_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def event(at: str, rank: int, table: str, row_id: int, kind: str, summary: str) -> None:
        events.append({
            "at": at, "rank": rank, "table": table, "id": row_id,
            "kind": kind, "summary": _ascii(summary),
        })

    for m in messages:
        event(
            m["created_at"], RANK_MESSAGE, "interface_message", m["message_id"],
            "MESSAGE_STORED",
            f"{m['direction']} {m['format']} {m['message_type']} interface_message "
            f"{m['message_id']} stored (current status {m['status']}; order_id "
            f"{m['order_id'] if m['order_id'] is not None else 'NULL'}; control_id "
            f"{m['control_id'] if m['control_id'] is not None else 'NULL'})",
        )
    for q in queue_items:
        event(
            q["created_at"], RANK_QUEUED, "interface_error_queue", q["queue_id"],
            "QUEUED",
            f"interface_error_queue {q['queue_id']} created for interface_message "
            f"{q['message_id'] if q['message_id'] is not None else 'NULL'}: "
            f"{q['failure_code']} / {q['failure_category']} / {q['recovery_policy']} "
            f"(current status {q['status']})",
        )
        if q["resolved_at"] is not None:
            event(
                q["resolved_at"], RANK_QUEUE_CLOSED, "interface_error_queue",
                q["queue_id"], "QUEUE_RESOLVED",
                f"interface_error_queue {q['queue_id']} resolved_at recorded",
            )
        if q["terminal_at"] is not None:
            event(
                q["terminal_at"], RANK_QUEUE_CLOSED, "interface_error_queue",
                q["queue_id"], "QUEUE_TERMINAL",
                f"interface_error_queue {q['queue_id']} terminal_at recorded",
            )
    for a in attempts:
        event(
            a["attempted_at"], RANK_ATTEMPT, "interface_recovery_attempt",
            a["attempt_id"], "RECOVERY_ATTEMPT",
            f"interface_recovery_attempt {a['attempt_id']} on interface_error_queue "
            f"{a['queue_id']}: {a['action']} request_id={a['request_id']} "
            f"actor={a['actor']} outcome={a['outcome']} resulting_message_id="
            f"{a['resulting_message_id'] if a['resulting_message_id'] is not None else 'NULL'}",
        )
    for a in audit_events:
        detail = f": {a['detail']}" if a["detail"] else ""
        event(
            a["created_at"], RANK_AUDIT, "audit_event", a["event_id"], a["action"],
            f"audit_event {a['event_id']} {a['entity_type']} {a['entity_id']} "
            f"{a['action']} by {a['actor']}{detail}",
        )
    events.sort(key=lambda e: (e["at"], e["rank"], e["table"], e["id"], e["kind"]))
    return events


# ---------------------------------------------------------------------------
# Case record references (used by reconcile_case and evidence)
# ---------------------------------------------------------------------------

def case_record_refs(case: dict[str, Any]) -> set[str]:
    """Every ``table:id`` ref of a record contained in a case view.

    Covers orders and their patient, specimens, current results, stored
    validation errors and report, plus messages, queue items, recovery
    attempts, and audit events.
    """
    refs: set[str] = set()
    for o in case.get("orders", []):
        refs.add(ref("lab_order", o["order_id"]))
        if o.get("patient"):
            refs.add(ref("patient", o["patient"]["patient_id"]))
        for s in o.get("specimens", []):
            refs.add(ref("specimen", s["specimen_id"]))
        for r in o.get("current_results", []):
            refs.add(ref("fish_result", r["result_id"]))
        for v in o.get("stored_validation_errors", []):
            refs.add(ref("validation_error", v["error_id"]))
        if o.get("report"):
            refs.add(ref("report", o["report"]["report_id"]))
    for m in case.get("messages", []):
        refs.add(ref("interface_message", m["message_id"]))
    for q in case.get("queue_items", []):
        refs.add(ref("interface_error_queue", q["queue_id"]))
    for a in case.get("recovery_attempts", []):
        refs.add(ref("interface_recovery_attempt", a["attempt_id"]))
    for e in case.get("audit_events", []):
        refs.add(ref("audit_event", e["event_id"]))
    return refs
