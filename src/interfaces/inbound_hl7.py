"""Inbound HL7 ORU-style ingestion for CytoBridge (Session 3).

Parses an inbound, pipe-delimited ORU-*style* result message from a synthetic
FISH instrument, matches it to an existing open AML/MDS FISH order by accession
number, and either files the per-probe results to that order or routes the whole
message to the interface **error queue** with a clear reason.

Segments understood
-------------------
    MSH   message header (control id, message type)
    PID   patient identification (synthetic MRN)
    OBR   observation request (accession = OBR-3, panel/test = OBR-4)
    SPM   specimen (specimen type = SPM-4)
    OBX   one per probe; OBX-3 = probe code, OBX-5 packs the structured value
          ``cells_scored ^ cells_abnormal ^ signal_pattern ^ interpretation``

Educational disclaimer
----------------------
This is an **HL7-style educational simulator, not a certified HL7 v2 parser.**
It accepts a small, deliberately simple ORU dialect so the mapping is legible to
an analyst; it does not implement the full HL7 grammar, MLLP framing, ACKs, or
conformance validation, and must not be pointed at a production interface.

Ingestion contract
------------------
- **Every** inbound message is stored in ``interface_message``
  (``direction = 'INBOUND'``), whether it files or fails.
- Filing is **all-or-nothing**: if any OBX is invalid the whole message goes to
  the error queue and nothing is filed, so an order never ends up with a
  partially-applied instrument message.
- A message is only fileable against an order that exists and is **not**
  ``FINALIZED`` / ``CANCELLED``.
- When results are filed, a ``lab_order`` audit event records that the results
  came from an inbound interface message.

All data is synthetic. No PHI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from .. import workflow
from ..db import execute
from . import SPECIMEN_TYPE, store_message

FIELD_SEP = "|"
COMPONENT_SEP = "^"

# Segments an ORU-style message must carry before we even try to match it.
REQUIRED_SEGMENTS = ("MSH", "PID", "OBR", "SPM")

VALID_INTERPRETATIONS = ("NORMAL", "ABNORMAL", "INDETERMINATE")


@dataclass(frozen=True)
class FailureClassification:
    """The approved category and recovery policy for one failure code."""

    category: str
    policy: str


# The single authoritative mapping from an approved failure code to its approved
# category and recovery policy (design record sec 6). Category and policy are
# ALWAYS derived from this table keyed by the code assigned at the failure site;
# they are never inferred from the human-readable reason string, and the
# (code, category, policy) triple is defined here and nowhere else. A ``TERMINAL``
# policy is what makes a queue item initialize TERMINAL rather than OPEN, so the
# two terminal order-state failures need no separately hard-coded list.
_FAILURE_CLASSIFICATION: dict[str, FailureClassification] = {
    "EMPTY_MESSAGE":            FailureClassification("MESSAGE_STRUCTURE", "REDRIVE_ONLY"),
    "MISSING_REQUIRED_SEGMENT": FailureClassification("MESSAGE_STRUCTURE", "REDRIVE_ONLY"),
    "NO_OBX":                   FailureClassification("MESSAGE_STRUCTURE", "REDRIVE_ONLY"),
    "MISSING_ACCESSION":        FailureClassification("ORDER_MATCHING", "REDRIVE_ONLY"),
    "ORDER_NOT_FOUND":          FailureClassification("ORDER_MATCHING", "RETRY_OR_REDRIVE"),
    "ORDER_FINALIZED":          FailureClassification("ORDER_STATE", "TERMINAL"),
    "ORDER_CANCELLED":          FailureClassification("ORDER_STATE", "TERMINAL"),
    "SPECIMEN_UNRECOGNIZED":    FailureClassification("SPECIMEN", "REDRIVE_ONLY"),
    "SPECIMEN_INCOMPATIBLE":    FailureClassification("SPECIMEN", "REDRIVE_ONLY"),
    "MISSING_PROBE_CODE":       FailureClassification("FISH_RESULT_CONTENT", "REDRIVE_ONLY"),
    "UNKNOWN_PROBE_CODE":       FailureClassification("FISH_RESULT_CONTENT", "REDRIVE_ONLY"),
    "INVALID_CELL_COUNT":       FailureClassification("FISH_RESULT_CONTENT", "REDRIVE_ONLY"),
    "ABNORMAL_EXCEEDS_SCORED":  FailureClassification("FISH_RESULT_CONTENT", "REDRIVE_ONLY"),
    "INVALID_INTERPRETATION":   FailureClassification("FISH_RESULT_CONTENT", "REDRIVE_ONLY"),
}

# Map an inbound SPM-4 specimen code back to the internal specimen_type. Built
# from the shared outbound SPECIMEN_TYPE table, plus a couple of common aliases
# and the internal names themselves so the parser is forgiving about senders.
_SPECIMEN_CODE_TO_TYPE: dict[str, str] = {
    code: internal for internal, (code, _display) in SPECIMEN_TYPE.items()
}
_SPECIMEN_CODE_TO_TYPE.update({
    "BM": "BONE_MARROW",
    "BONE_MARROW": "BONE_MARROW",
    "BLD": "PERIPHERAL_BLOOD",
    "WB": "PERIPHERAL_BLOOD",
    "PERIPHERAL_BLOOD": "PERIPHERAL_BLOOD",
})


class InboundError(Exception):
    """A reason an inbound message could not be filed.

    Carries the approved structured failure ``code`` (design record sec 6),
    assigned at the exact failure site, alongside the human-readable reason. The
    reason string is still used verbatim as the ``interface_error_queue.reason``
    so an analyst reading the queue sees exactly why the message did not file;
    the code drives the structured classification and is never inferred by
    parsing that reason string.
    """

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code


# ---------------------------------------------------------------------------
# Parsed message shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedObx:
    """One raw OBX result line, before validation."""

    set_id: str
    probe_code: str
    raw_value: str


@dataclass(frozen=True)
class ParsedMessage:
    """The fields extracted from an inbound ORU-style message."""

    control_id: str | None
    message_type: str
    accession_number: str | None
    mrn: str | None
    order_code: str | None
    specimen_code: str | None
    obx: list[ParsedObx] = field(default_factory=list)


@dataclass(frozen=True)
class InboundProbeResult:
    """A validated per-probe result ready to file to an order."""

    probe_code: str
    probe_id: int
    cells_scored: int
    cells_abnormal: int
    signal_pattern: str
    interpretation: str


@dataclass(frozen=True)
class IngestResult:
    """Outcome of ingesting one inbound message."""

    message_id: int
    filed: bool
    order_id: int | None = None
    reason: str | None = None
    probe_codes_filed: list[str] = field(default_factory=list)
    queue_id: int | None = None


# ---------------------------------------------------------------------------
# Low-level parsing helpers
# ---------------------------------------------------------------------------

def _split_segments(raw: str) -> list[list[str]]:
    """Split a raw message into segments, each a list of ``|``-delimited fields.

    Lenient about line endings: inbound senders vary, so ``\\r\\n``, ``\\r``, and
    ``\\n`` are all treated as segment terminators and blank lines are dropped.
    """
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    segments: list[list[str]] = []
    for line in normalized.split("\n"):
        if line.strip() == "":
            continue
        segments.append(line.split(FIELD_SEP))
    return segments


def _component(value: str | None, index: int) -> str:
    """Return the ``index``-th ``^``-component of a field, or ``""``."""
    if not value:
        return ""
    parts = value.split(COMPONENT_SEP)
    return parts[index].strip() if index < len(parts) else ""


def _field(segment: list[str], pos: int) -> str:
    """Return field ``pos`` of a segment (1-based HL7 numbering), or ``""``.

    For non-MSH segments ``segment[0]`` is the segment id, so HL7 field N is at
    list index N. MSH is handled specially by the callers that read it.
    """
    return segment[pos] if pos < len(segment) else ""


def _peek_header(raw: str) -> tuple[str | None, str]:
    """Best-effort read of MSH-10 control id and MSH-9 message type for storage.

    Runs before full validation so even a malformed message can be stored with
    whatever header it does carry. Returns ``(control_id, message_type)`` with
    ``message_type`` defaulting to ``"UNKNOWN"``.
    """
    for segment in _split_segments(raw):
        if segment and segment[0].strip() == "MSH":
            # MSH-1 is the field separator itself, so MSH-N lives at index N-1.
            control_id = segment[9].strip() if len(segment) > 9 else ""
            message_type = _component(segment[8] if len(segment) > 8 else "", 0)
            return (control_id or None, message_type or "UNKNOWN")
    return (None, "UNKNOWN")


def parse_message(raw: str) -> ParsedMessage:
    """Parse an inbound ORU-style message into a ``ParsedMessage``.

    Raises ``InboundError`` when the message is empty, a required segment is
    missing, or it carries no OBX result segments.
    """
    segments = _split_segments(raw)
    if not segments:
        raise InboundError(
            "EMPTY_MESSAGE", "Message is empty; no HL7 segments found."
        )

    present: dict[str, list[list[str]]] = {}
    for segment in segments:
        seg_id = segment[0].strip()
        present.setdefault(seg_id, []).append(segment)

    for required in REQUIRED_SEGMENTS:
        if required not in present:
            raise InboundError(
                "MISSING_REQUIRED_SEGMENT",
                f"Required segment {required} is missing.",
            )

    obx_segments = present.get("OBX", [])
    if not obx_segments:
        raise InboundError(
            "NO_OBX", "No OBX result segments present in message."
        )

    msh = present["MSH"][0]
    pid = present["PID"][0]
    obr = present["OBR"][0]
    spm = present["SPM"][0]

    control_id = (msh[9].strip() if len(msh) > 9 else "") or None
    message_type = _component(msh[8] if len(msh) > 8 else "", 0) or "UNKNOWN"

    obx: list[ParsedObx] = []
    for seg in obx_segments:
        obx.append(
            ParsedObx(
                set_id=_field(seg, 1),
                probe_code=_component(_field(seg, 3), 0),
                raw_value=_field(seg, 5),
            )
        )

    return ParsedMessage(
        control_id=control_id,
        message_type=message_type,
        accession_number=_component(_field(obr, 3), 0) or None,
        mrn=_component(_field(pid, 3), 0) or None,
        order_code=_component(_field(obr, 4), 0) or None,
        specimen_code=_component(_field(spm, 4), 0) or None,
        obx=obx,
    )


# ---------------------------------------------------------------------------
# Matching + validation
# ---------------------------------------------------------------------------

def _match_order(conn: sqlite3.Connection, parsed: ParsedMessage) -> sqlite3.Row:
    """Find the open order for the parsed accession, or raise ``InboundError``."""
    accession = parsed.accession_number
    if not accession:
        raise InboundError(
            "MISSING_ACCESSION", "Accession number (OBR-3) is missing."
        )

    order = conn.execute(
        "SELECT o.order_id, o.status, o.panel_id, "
        "       pa.panel_code, pa.specimen_type AS panel_specimen_type "
        "FROM lab_order o "
        "JOIN panel pa ON pa.panel_id = o.panel_id "
        "WHERE o.accession_number = ?",
        (accession,),
    ).fetchone()
    if order is None:
        raise InboundError(
            "ORDER_NOT_FOUND",
            f"No order matches accession number {accession}.",
        )
    if order["status"] == "FINALIZED":
        raise InboundError(
            "ORDER_FINALIZED",
            f"Order for accession {accession} is already finalized; "
            "results cannot be filed.",
        )
    if order["status"] == "CANCELLED":
        raise InboundError(
            "ORDER_CANCELLED",
            f"Order for accession {accession} is cancelled; "
            "results cannot be filed.",
        )
    return order


def _check_specimen(parsed: ParsedMessage, order: sqlite3.Row) -> None:
    """Validate the inbound specimen type against the order's panel."""
    raw_code = (parsed.specimen_code or "").upper()
    internal = _SPECIMEN_CODE_TO_TYPE.get(raw_code)
    if internal is None:
        raise InboundError(
            "SPECIMEN_UNRECOGNIZED",
            f"Specimen type '{parsed.specimen_code}' (SPM-4) is not recognized.",
        )
    expected = order["panel_specimen_type"]
    if internal != expected:
        raise InboundError(
            "SPECIMEN_INCOMPATIBLE",
            f"Specimen type '{parsed.specimen_code}' ({internal}) is "
            f"incompatible with the {order['panel_code']} panel, which "
            f"requires {expected}.",
        )


def _parse_count(raw: str, label: str, probe_code: str) -> int:
    """Parse a non-negative integer count, raising ``InboundError`` if malformed."""
    text = (raw or "").strip()
    try:
        value = int(text)
    except ValueError:
        raise InboundError(
            "INVALID_CELL_COUNT",
            f"Probe {probe_code}: {label} '{raw}' is not a valid integer.",
        )
    if value < 0:
        raise InboundError(
            "INVALID_CELL_COUNT",
            f"Probe {probe_code}: {label} ({value}) cannot be negative.",
        )
    return value


def _validate_obx(
    conn: sqlite3.Connection, order: sqlite3.Row, parsed: ParsedMessage
) -> list[InboundProbeResult]:
    """Validate every OBX result, returning them all or raising on the first bad one.

    Validation is all-or-nothing so a partially-valid message never files a
    partial result set to the order.
    """
    results: list[InboundProbeResult] = []
    for obx in parsed.obx:
        probe_code = obx.probe_code
        if not probe_code:
            raise InboundError(
                "MISSING_PROBE_CODE",
                "An OBX segment is missing its probe code (OBX-3).",
            )

        probe = conn.execute(
            "SELECT probe_id FROM probe WHERE panel_id = ? AND probe_code = ?",
            (order["panel_id"], probe_code),
        ).fetchone()
        if probe is None:
            raise InboundError(
                "UNKNOWN_PROBE_CODE",
                f"Probe code {probe_code} is not part of the "
                f"{order['panel_code']} panel.",
            )

        scored = _parse_count(_component(obx.raw_value, 0), "cells_scored", probe_code)
        abnormal = _parse_count(
            _component(obx.raw_value, 1), "cells_abnormal", probe_code
        )
        if abnormal > scored:
            raise InboundError(
                "ABNORMAL_EXCEEDS_SCORED",
                f"Probe {probe_code}: cells_abnormal ({abnormal}) exceeds "
                f"cells_scored ({scored}).",
            )

        signal_pattern = _component(obx.raw_value, 2)
        interpretation = _component(obx.raw_value, 3).upper()
        if interpretation not in VALID_INTERPRETATIONS:
            raise InboundError(
                "INVALID_INTERPRETATION",
                f"Probe {probe_code}: interpretation '{interpretation}' is not "
                f"one of {', '.join(VALID_INTERPRETATIONS)}.",
            )

        results.append(
            InboundProbeResult(
                probe_code=probe_code,
                probe_id=probe["probe_id"],
                cells_scored=scored,
                cells_abnormal=abnormal,
                signal_pattern=signal_pattern,
                interpretation=interpretation,
            )
        )
    return results


# ---------------------------------------------------------------------------
# Storage + filing side effects
# ---------------------------------------------------------------------------

def _update_message(
    conn: sqlite3.Connection,
    message_id: int,
    *,
    status: str,
    order_id: int | None = None,
) -> None:
    """Update the stored inbound message's status (and order link once matched)."""
    execute(
        conn,
        "UPDATE interface_message SET status = ?, order_id = COALESCE(?, order_id) "
        "WHERE message_id = ?",
        (status, order_id, message_id),
    )


def _route_to_error_queue(
    conn: sqlite3.Connection,
    message_id: int,
    *,
    code: str,
    reason: str,
    raw_payload: str,
) -> int:
    """Record a classified inbound error-queue entry and return its queue_id.

    The failure ``code`` is assigned at the failure site; its category and
    recovery policy come from the single authoritative classification mapping,
    never from the human-readable ``reason``. A ``TERMINAL`` policy
    (``ORDER_FINALIZED`` / ``ORDER_CANCELLED``) initializes the item directly as
    TERMINAL with a populated ``terminal_at`` and null ``resolved_at``; every
    other approved failure initializes OPEN with both timestamps null. A code
    absent from the mapping is left to raise as a blocker rather than being
    given any fallback classification.
    """
    classification = _FAILURE_CLASSIFICATION[code]
    if classification.policy == "TERMINAL":
        return execute(
            conn,
            "INSERT INTO interface_error_queue "
            "(message_id, direction, reason, raw_payload, "
            " failure_code, failure_category, recovery_policy, "
            " status, terminal_at) "
            "VALUES (?, 'INBOUND', ?, ?, ?, ?, ?, 'TERMINAL', datetime('now'))",
            (
                message_id,
                reason,
                raw_payload,
                code,
                classification.category,
                classification.policy,
            ),
        )
    return execute(
        conn,
        "INSERT INTO interface_error_queue "
        "(message_id, direction, reason, raw_payload, "
        " failure_code, failure_category, recovery_policy, status) "
        "VALUES (?, 'INBOUND', ?, ?, ?, ?, ?, 'OPEN')",
        (
            message_id,
            reason,
            raw_payload,
            code,
            classification.category,
            classification.policy,
        ),
    )


def _file_results(
    conn: sqlite3.Connection,
    order: sqlite3.Row,
    results: list[InboundProbeResult],
    message_id: int,
    actor: str,
) -> list[str]:
    """File validated probe results to the order and audit the inbound filing."""
    order_id = order["order_id"]
    filed_codes: list[str] = []
    for r in results:
        workflow.enter_fish_result(
            conn,
            order_id,
            r.probe_code,
            r.cells_scored,
            r.cells_abnormal,
            r.signal_pattern,
            r.interpretation,
            entered_by=actor,
        )
        filed_codes.append(r.probe_code)

    workflow.record_audit(
        conn,
        "lab_order",
        order_id,
        "INBOUND_RESULT_FILED",
        order_id=order_id,
        detail=(
            f"message_id={message_id} probes={len(filed_codes)} "
            f"({', '.join(filed_codes)})"
        ),
        actor=actor,
    )
    return filed_codes


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def ingest_message(
    conn: sqlite3.Connection,
    raw_payload: str,
    *,
    actor: str = "interface",
) -> IngestResult:
    """Ingest one inbound ORU-style message.

    The raw message is **always** stored in ``interface_message`` first. If it
    parses, matches an open order, and every OBX validates, the probe results are
    filed to that order (with an audit event) and the message is marked
    ``FILED``. Otherwise the whole message is routed to ``interface_error_queue``
    with a clear reason and the message is marked ``ERRORED``.
    """
    control_id, message_type = _peek_header(raw_payload)
    message_id = store_message(
        conn,
        direction="INBOUND",
        message_type=message_type,
        message_format="HL7",
        order_id=None,
        control_id=control_id,
        payload=raw_payload,
        status="RECEIVED",
    )

    try:
        parsed = parse_message(raw_payload)
        order = _match_order(conn, parsed)
        _check_specimen(parsed, order)
        results = _validate_obx(conn, order, parsed)
    except InboundError as err:
        reason = str(err)
        queue_id = _route_to_error_queue(
            conn,
            message_id,
            code=err.code,
            reason=reason,
            raw_payload=raw_payload,
        )
        _update_message(conn, message_id, status="ERRORED")
        return IngestResult(
            message_id=message_id,
            filed=False,
            reason=reason,
            queue_id=queue_id,
        )

    filed_codes = _file_results(conn, order, results, message_id, actor)
    _update_message(conn, message_id, status="FILED", order_id=order["order_id"])
    return IngestResult(
        message_id=message_id,
        filed=True,
        order_id=order["order_id"],
        probe_codes_filed=filed_codes,
    )


def ingest_file(
    conn: sqlite3.Connection,
    path: str,
    *,
    actor: str = "interface",
) -> IngestResult:
    """Read an inbound message file and ingest it (convenience for demos/tests)."""
    from pathlib import Path

    raw = Path(path).read_text(encoding="utf-8")
    return ingest_message(conn, raw, actor=actor)
