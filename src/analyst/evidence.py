"""Portable, deterministic case evidence for the CytoBridge analyst lab.

``build_evidence`` packages one incident - anchored on an error-queue item or
an order - into a single JSON-serializable evidence document that an analyst
can attach to a ticket, diff against a later export, or hand to a reviewer:

- source facts: the repository revision (``git rev-parse HEAD``, ``-dirty``
  when tracked files have local changes, ``UNKNOWN`` when git is unavailable)
  and the SHA-256 of ``schema.sql``;
- the case view from ``trace.build_case`` (records, typed relationships with
  their evidential basis, timeline, provenance notes);
- payload fingerprints with explicit comparison checks (queue raw copy vs the
  original message, recovery-attempt fingerprint vs the resulting message, and
  an unchanged retry vs the original payload);
- current validation (``validation.validate_order``, SELECT only) beside the
  stored ``validation_error`` rows;
- observed outcomes, recovery history, and reconciliation findings from
  ``reconcile.reconcile_case``;
- the standard limitation statements, always including EDUCATIONAL_INVARIANTS;
- a ``content_fingerprint`` that ignores wall-clock values, so two databases
  built by the same steps at different times have the same fingerprint.

``render_report`` turns an evidence document into a short, deterministic,
plain-ASCII Markdown report. Stored non-ASCII characters are escaped with
``backslashreplace`` so the report stays ASCII.

Everything here is read-only: it reuses the trace and reconciliation queries
(plus one SELECT for queue raw-payload byte lengths) and works on a
``store.open_read_only`` connection.

This is an educational, synthetic simulator - not a production LIS, certified
interface engine, validated medical device, or Epic/Beaker implementation.
All data is synthetic. No PHI.
"""

from __future__ import annotations

import copy
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .. import db, validation
from . import reconcile, trace
from .common import (
    REPO_ROOT,
    SYNTHETIC_NOTICE,
    AnalystError,
    canonical_json,
    limitation_list,
    ref,
    sha256_file,
    sha256_text,
)

EVIDENCE_FORMAT = "cytobridge.case-evidence/1"
GENERATOR = "src.analyst.evidence"
UNKNOWN_REVISION = "UNKNOWN"
GIT_TIMEOUT_SECONDS = 5

# Keys whose values are SQLite datetime('now') stamps (or the timeline's copy of
# one). They differ between two otherwise identical builds, so the content
# fingerprint ignores them wherever they occur.
VOLATILE_KEYS: frozenset[str] = frozenset({
    "created_at",
    "attempted_at",
    "resolved_at",
    "terminal_at",
    "entered_at",
    "ordered_at",
    "finalized_at",
    "received_at",
    "at",
})

# OUTBOUND payloads embed generation/finalization timestamps (MSH-7, the
# control id, FHIR issued/received times), so their payload facts are also
# excluded from the content fingerprint.
OUTBOUND_VOLATILE_KEYS: tuple[str, ...] = (
    "payload",
    "payload_sha256",
    "payload_bytes",
    "control_id",
)

# Fingerprint roles: the four trace message roles plus the queue raw copy and
# the fingerprint a recovery attempt records.
ROLE_QUEUE_ORIGINAL = trace.ROLE_QUEUE_ORIGINAL
ROLE_QUEUE_RAW_COPY = "QUEUE_RAW_COPY"
ROLE_ATTEMPT_PAYLOAD = "ATTEMPT_PAYLOAD"
ROLE_RECOVERY_RESULT = trace.ROLE_RECOVERY_RESULT
ROLE_INBOUND_DIRECT = trace.ROLE_INBOUND_DIRECT
ROLE_OUTBOUND_EXPORT = trace.ROLE_OUTBOUND_EXPORT
FINGERPRINT_ROLES = (
    ROLE_QUEUE_ORIGINAL,
    ROLE_QUEUE_RAW_COPY,
    ROLE_ATTEMPT_PAYLOAD,
    ROLE_RECOVERY_RESULT,
    ROLE_INBOUND_DIRECT,
    ROLE_OUTBOUND_EXPORT,
)

# Fingerprint checks. Each compares SHA-256 values of exact UTF-8 payload text,
# the same comparisons reconciliation rules QUE-005 (R-022) and REC-006
# (R-026, R-028, R-040) make. A check whose counterpart record is absent from
# the case (NULL raw copy, missing message) does not pass.
CHECK_RAW_COPY_MATCHES_ORIGINAL = "QUEUE_RAW_COPY_MATCHES_ORIGINAL_PAYLOAD"
CHECK_ATTEMPT_MATCHES_RESULT = "ATTEMPT_PAYLOAD_MATCHES_RESULTING_MESSAGE"
CHECK_RETRY_MATCHES_ORIGINAL = "RETRY_PAYLOAD_MATCHES_ORIGINAL_PAYLOAD"

_RETRY_ORIGINAL = "RETRY_ORIGINAL"
_RECORD_KEYS = ("orders", "messages", "queue_items", "recovery_attempts", "audit_events")
_OUTCOMES = ("SUCCEEDED", "FAILED", "REJECTED")
_ANCHOR_TABLES = {
    "order": "lab_order",
    "queue": "interface_error_queue",
    "message": "interface_message",
}

# SQLite datetime('now') text, as interpolated into some reconciliation
# observations (for example REC-008 "audit_event N at <time>").
_SQLITE_TIMESTAMP_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\b")
_TIMESTAMP_PLACEHOLDER = "<timestamp>"
_REVISION_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


# ---------------------------------------------------------------------------
# Source revision
# ---------------------------------------------------------------------------

def _git(repo_root: str | Path, *args: str) -> str | None:
    """Run one read-only git command; return stdout, or None on any failure."""
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:  # noqa: BLE001 - missing git, timeout, OS error: all UNKNOWN
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def resolve_source_revision(repo_root: str | Path = REPO_ROOT) -> str:
    """Return the repository revision the evidence was generated from.

    ``git -C <repo_root> rev-parse HEAD``, with ``-dirty`` appended when
    ``git status --porcelain --untracked-files=no`` reports changes to tracked
    files. Returns ``"UNKNOWN"`` when git is missing, fails, times out, or
    prints something that is not a commit id. Never raises.
    """
    try:
        head = _git(repo_root, "rev-parse", "HEAD")
        if head is None:
            return UNKNOWN_REVISION
        revision = head.strip()
        if not _REVISION_RE.fullmatch(revision):
            return UNKNOWN_REVISION
        status = _git(repo_root, "status", "--porcelain", "--untracked-files=no")
        if status is None:
            return UNKNOWN_REVISION
        return f"{revision}-dirty" if status.strip() else revision
    except Exception:  # noqa: BLE001 - never raises by contract
        return UNKNOWN_REVISION


# ---------------------------------------------------------------------------
# Evidence sections
# ---------------------------------------------------------------------------

def _raw_payload_bytes(
    conn: sqlite3.Connection, queue_ids: Iterable[int]
) -> dict[int, int | None]:
    """UTF-8 byte length of each queue item's raw_payload (one SELECT)."""
    ids = sorted({int(q) for q in queue_ids})
    if not ids:
        return {}
    cur = conn.cursor()
    try:
        rows = cur.execute(
            "SELECT queue_id, raw_payload FROM interface_error_queue "
            "WHERE queue_id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        ).fetchall()
    finally:
        cur.close()
    return {
        int(row[0]): (None if row[1] is None else len(row[1].encode("utf-8")))
        for row in rows
    }


def _check(name: str, passed: bool) -> dict[str, Any]:
    return {"check": name, "passed": bool(passed)}


def _fingerprints(
    case: dict[str, Any], raw_bytes: dict[int, int | None]
) -> list[dict[str, Any]]:
    """Payload fingerprints for every message role, queue raw copy, and attempt.

    Sorted by (subject, role); each entry's checks are sorted by check name.
    """
    messages = {m["message_id"]: m for m in case["messages"]}
    queues = {q["queue_id"]: q for q in case["queue_items"]}
    entries: list[dict[str, Any]] = []

    for m in case["messages"]:
        for role in m["roles"]:
            entries.append({
                "subject": ref("interface_message", m["message_id"]),
                "role": role,
                "sha256": m["payload_sha256"],
                "bytes": m["payload_bytes"],
                "checks": [],
            })

    for q in case["queue_items"]:
        original = messages.get(q["message_id"])
        raw_sha = q["raw_payload_sha256"]
        entries.append({
            "subject": ref("interface_error_queue", q["queue_id"]),
            "role": ROLE_QUEUE_RAW_COPY,
            "sha256": raw_sha,
            "bytes": raw_bytes.get(q["queue_id"]),
            "checks": [_check(
                CHECK_RAW_COPY_MATCHES_ORIGINAL,
                original is not None
                and raw_sha is not None
                and raw_sha == original["payload_sha256"],
            )],
        })

    for a in case["recovery_attempts"]:
        checks: list[dict[str, Any]] = []
        if a["resulting_message_id"] is not None:
            result = messages.get(a["resulting_message_id"])
            checks.append(_check(
                CHECK_ATTEMPT_MATCHES_RESULT,
                result is not None and a["payload_sha256"] == result["payload_sha256"],
            ))
        if a["action"] == _RETRY_ORIGINAL:
            queue = queues.get(a["queue_id"])
            original = messages.get(queue["message_id"]) if queue is not None else None
            checks.append(_check(
                CHECK_RETRY_MATCHES_ORIGINAL,
                original is not None
                and a["payload_sha256"] == original["payload_sha256"],
            ))
        checks.sort(key=lambda c: c["check"])
        entries.append({
            "subject": ref("interface_recovery_attempt", a["attempt_id"]),
            "role": ROLE_ATTEMPT_PAYLOAD,
            "sha256": a["payload_sha256"],
            # The attempt row stores only the fingerprint, not the payload.
            "bytes": None,
            "checks": checks,
        })

    entries.sort(key=lambda e: (e["subject"], e["role"]))
    return entries


def _validations(conn: sqlite3.Connection, case: dict[str, Any]) -> list[dict[str, Any]]:
    """Current validation (validate_order, read-only) beside stored rows.

    One entry per case order, by order_id. current_findings are sorted by
    (rule_code, severity, message); stored_findings by error_id.
    """
    out: list[dict[str, Any]] = []
    for o in case["orders"]:
        current = validation.validate_order(conn, o["order_id"])
        out.append({
            "order_id": o["order_id"],
            "current_findings": sorted(
                (
                    {"rule_code": f.rule_code, "severity": f.severity, "message": f.message}
                    for f in current
                ),
                key=lambda f: (f["rule_code"], f["severity"], f["message"]),
            ),
            "stored_findings": [
                {
                    "error_id": v["error_id"],
                    "rule_code": v["rule_code"],
                    "severity": v["severity"],
                    "message": v["message"],
                }
                for v in sorted(o["stored_validation_errors"], key=lambda v: v["error_id"])
            ],
            "blocking": validation.has_blocking_errors(current),
        })
    out.sort(key=lambda v: v["order_id"])
    return out


def _outcomes(case: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Final observed states, each list by its primary key."""
    return {
        "orders": [
            {
                "order_id": o["order_id"],
                "accession_number": o["accession_number"],
                "status": o["status"],
            }
            for o in sorted(case["orders"], key=lambda o: o["order_id"])
        ],
        "queue_items": [
            {
                "queue_id": q["queue_id"],
                "failure_code": q["failure_code"],
                "status": q["status"],
            }
            for q in sorted(case["queue_items"], key=lambda q: q["queue_id"])
        ],
        "attempts": [
            {
                "attempt_id": a["attempt_id"],
                "request_id": a["request_id"],
                "action": a["action"],
                "outcome": a["outcome"],
            }
            for a in sorted(case["recovery_attempts"], key=lambda a: a["attempt_id"])
        ],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_evidence(
    conn: sqlite3.Connection,
    *,
    queue_id: int | None = None,
    order_id: int | None = None,
    scenario_id: str | None = None,
    scenario_title: str | None = None,
    source_revision: str | None = None,
) -> dict[str, Any]:
    """Build the evidence document for exactly one queue item or order.

    Read-only. Zero or two selectors raise ``AnalystError`` ``USAGE``; an
    unknown id raises ``NOT_FOUND`` (from ``trace.build_case``).
    ``source_revision`` defaults to ``resolve_source_revision()``.

    Keys: ``format``, ``synthetic`` (always true), ``notice``, ``source``,
    ``scenario``, ``anchor``, ``relationships`` (case links), ``records``
    (case orders, messages, queue items, recovery attempts, audit events),
    ``fingerprints`` (by subject, role), ``validations`` (by order_id),
    ``outcomes``, ``recovery_history`` (by attempt_id), ``timeline``,
    ``reconciliation`` (``summary`` + ``findings``), ``provenance``,
    ``limitations`` (by code), and ``content_fingerprint``.
    """
    given = [
        name
        for name, value in (("queue_id", queue_id), ("order_id", order_id))
        if value is not None
    ]
    if len(given) != 1:
        raise AnalystError(
            "USAGE",
            "Exactly one of queue_id or order_id must be given for case evidence "
            f"(got {len(given)}: {', '.join(given) or 'none'}).",
        )

    case = trace.build_case(conn, queue_id=queue_id, order_id=order_id)
    findings = reconcile.reconcile_case(conn, case)
    raw_bytes = _raw_payload_bytes(conn, (q["queue_id"] for q in case["queue_items"]))

    evidence: dict[str, Any] = {
        "format": EVIDENCE_FORMAT,
        "synthetic": True,
        "notice": SYNTHETIC_NOTICE,
        "source": {
            "revision": (
                resolve_source_revision() if source_revision is None else source_revision
            ),
            "schema_sha256": sha256_file(db.SCHEMA_PATH),
            "generator": GENERATOR,
        },
        "scenario": {"id": scenario_id, "title": scenario_title},
        "anchor": dict(case["anchor"]),
        "relationships": copy.deepcopy(case["links"]),
        "records": {key: copy.deepcopy(case[key]) for key in _RECORD_KEYS},
        "fingerprints": _fingerprints(case, raw_bytes),
        "validations": _validations(conn, case),
        "outcomes": _outcomes(case),
        "recovery_history": copy.deepcopy(case["recovery_attempts"]),
        "timeline": copy.deepcopy(case["timeline"]),
        "reconciliation": {
            "summary": reconcile.summarize(findings),
            "findings": findings,
        },
        "provenance": copy.deepcopy(case["provenance"]),
        "limitations": limitation_list(
            [lim["code"] for lim in case["limitations"]] + ["EDUCATIONAL_INVARIANTS"]
        ),
    }
    evidence["content_fingerprint"] = content_fingerprint(evidence)
    return evidence


def _drop_volatile(value: Any) -> Any:
    """Copy of ``value`` with every VOLATILE_KEYS key removed, recursively."""
    if isinstance(value, dict):
        return {k: _drop_volatile(v) for k, v in value.items() if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_drop_volatile(v) for v in value]
    return value


def _int_or_zero(value: Any) -> int:
    return value if isinstance(value, int) else 0


def fingerprint_view(evidence: dict[str, Any]) -> dict[str, Any]:
    """The timestamp-free projection of ``evidence`` that is fingerprinted.

    Removes ``source`` and ``content_fingerprint``; every VOLATILE_KEYS key at
    any depth; ``payload``, ``payload_sha256``, ``payload_bytes`` and
    ``control_id`` of OUTBOUND messages, and the ``sha256``/``bytes`` of their
    fingerprint entries. The timeline keeps only ``(table, id, kind, rank)``
    sorted in that order, because its chronological order and its summaries
    (which restate record fields, including outbound control ids) depend on
    wall-clock time. SQLite timestamps interpolated into reconciliation
    observations are replaced by ``<timestamp>``.
    """
    view = {
        k: copy.deepcopy(v)
        for k, v in evidence.items()
        if k not in ("source", "content_fingerprint")
    }
    records = view.get("records") or {}
    outbound_refs: set[str] = set()
    for m in records.get("messages") or []:
        if m.get("direction") == "OUTBOUND":
            outbound_refs.add(ref("interface_message", m.get("message_id")))
            for key in OUTBOUND_VOLATILE_KEYS:
                m.pop(key, None)
    for f in view.get("fingerprints") or []:
        if f.get("subject") in outbound_refs:
            f.pop("sha256", None)
            f.pop("bytes", None)
    if "timeline" in view:
        view["timeline"] = sorted(
            (
                {
                    "table": e.get("table"),
                    "id": e.get("id"),
                    "kind": e.get("kind"),
                    "rank": e.get("rank"),
                }
                for e in view["timeline"] or []
            ),
            key=lambda e: (
                str(e["table"]), _int_or_zero(e["id"]), str(e["kind"]), _int_or_zero(e["rank"])
            ),
        )
    for finding in (view.get("reconciliation") or {}).get("findings") or []:
        finding["observations"] = [
            _SQLITE_TIMESTAMP_RE.sub(_TIMESTAMP_PLACEHOLDER, o) if isinstance(o, str) else o
            for o in finding.get("observations") or []
        ]
    return _drop_volatile(view)


def content_fingerprint(evidence: dict[str, Any]) -> str:
    """SHA-256 of ``canonical_json(fingerprint_view(evidence))``.

    Equal for two databases built by the same steps at different wall-clock
    times; changes when any business value (identifier, status, result value,
    inbound payload, actor, request id, finding) changes.
    """
    return sha256_text(canonical_json(fingerprint_view(evidence)))


# ---------------------------------------------------------------------------
# Markdown report
# ---------------------------------------------------------------------------

def _text(value: Any) -> str:
    """Plain-ASCII, single-line rendering of a stored value."""
    if value is None:
        return "NULL"
    text = str(value).encode("ascii", "backslashreplace").decode("ascii")
    out: list[str] = []
    for ch in text:
        if ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\x{ord(ch):02x}")
        else:
            out.append(ch)
    return "".join(out)


def _cell(value: Any) -> str:
    """A Markdown table cell: ASCII, single line, pipes escaped."""
    return _text(value).replace("|", "\\|")


def render_report(evidence: dict[str, Any]) -> str:
    """Render an evidence document as a short, deterministic ASCII Markdown report.

    Sections: header facts (notice, source revision, schema sha256, scenario,
    anchor), record counts, relationships (one line per link with its basis),
    payload fingerprints with check results, validation, observed outcomes,
    recovery history, reconciliation, provenance notes, limitations, and the
    content fingerprint. Every line is plain ASCII.
    """
    source = evidence.get("source") or {}
    scenario = evidence.get("scenario") or {}
    anchor = evidence.get("anchor") or {}
    records = evidence.get("records") or {}
    fingerprint = evidence.get("content_fingerprint")
    lines: list[str] = []
    add = lines.append

    add("# CytoBridge synthetic case evidence")
    add("")
    add(f"> {_text(evidence.get('notice', SYNTHETIC_NOTICE))}")
    add("")
    add(f"- Evidence format: {_text(evidence.get('format'))}")
    add(f"- Source revision: {_text(source.get('revision'))}")
    add(f"- Schema sha256: {_text(source.get('schema_sha256'))}")
    add(f"- Generator: {_text(source.get('generator'))}")
    title = scenario.get("title")
    add(
        f"- Scenario: {_text(scenario.get('id')) if scenario.get('id') is not None else '(none)'}"
        + (f" - {_text(title)}" if title is not None else "")
    )
    anchor_table = _ANCHOR_TABLES.get(str(anchor.get("kind")))
    add(
        f"- Anchor: {_text(anchor.get('kind'))} {_text(anchor.get('id'))}"
        + (f" ({anchor_table}:{_text(anchor.get('id'))})" if anchor_table else "")
    )
    add(f"- Content fingerprint: {_text(fingerprint)}")
    add("")

    add("## Records")
    add("")
    add(
        "- orders: {o}; interface messages: {m}; queue items: {q}; "
        "recovery attempts: {a}; audit events: {e}".format(
            o=len(records.get("orders") or []),
            m=len(records.get("messages") or []),
            q=len(records.get("queue_items") or []),
            a=len(records.get("recovery_attempts") or []),
            e=len(records.get("audit_events") or []),
        )
    )
    add("")

    add("## Relationships")
    add("")
    links = evidence.get("relationships") or []
    if not links:
        add("- None.")
    for link in links:
        add(
            f"- {_text(link.get('from'))} -> {_text(link.get('to'))}: "
            f"{_text(link.get('relation'))} [{_text(link.get('basis'))}]"
        )
    add("")

    add("## Payload fingerprints")
    add("")
    add("| Subject | Role | SHA-256 | Bytes | Checks |")
    add("|---|---|---|---|---|")
    for f in evidence.get("fingerprints") or []:
        checks = "; ".join(
            f"{_cell(c.get('check'))}={'PASS' if c.get('passed') else 'FAIL'}"
            for c in f.get("checks") or []
        ) or "-"
        size = f.get("bytes")
        add(
            f"| {_cell(f.get('subject'))} | {_cell(f.get('role'))} | "
            f"{_cell(f.get('sha256'))} | {'-' if size is None else _cell(size)} | {checks} |"
        )
    add("")

    add("## Validation (current vs stored)")
    add("")
    validations = evidence.get("validations") or []
    if not validations:
        add("- No orders in this case.")
    for v in validations:
        current = v.get("current_findings") or []
        errors = sum(1 for c in current if c.get("severity") == "ERROR")
        warnings = sum(1 for c in current if c.get("severity") == "WARNING")
        add(
            f"- lab_order:{_text(v.get('order_id'))}: current findings {len(current)} "
            f"(ERROR {errors}, WARNING {warnings}); stored validation_error rows "
            f"{len(v.get('stored_findings') or [])}; finalization blocked now: "
            f"{'yes' if v.get('blocking') else 'no'}"
        )
        for c in current:
            add(
                f"  - current {_text(c.get('severity'))} {_text(c.get('rule_code'))}: "
                f"{_text(c.get('message'))}"
            )
    add("")

    add("## Observed outcomes")
    add("")
    outcomes = evidence.get("outcomes") or {}
    for o in outcomes.get("orders") or []:
        add(
            f"- lab_order:{_text(o.get('order_id'))} "
            f"{_text(o.get('accession_number'))}: {_text(o.get('status'))}"
        )
    for q in outcomes.get("queue_items") or []:
        add(
            f"- interface_error_queue:{_text(q.get('queue_id'))} "
            f"{_text(q.get('failure_code'))}: {_text(q.get('status'))}"
        )
    attempts = outcomes.get("attempts") or []
    counts = {name: sum(1 for a in attempts if a.get("outcome") == name) for name in _OUTCOMES}
    add(
        f"- recovery attempts: {len(attempts)} (SUCCEEDED {counts['SUCCEEDED']}, "
        f"FAILED {counts['FAILED']}, REJECTED {counts['REJECTED']})"
    )
    add("")

    add("## Recovery history")
    add("")
    history = evidence.get("recovery_history") or []
    if not history:
        add("- No recovery attempts.")
    else:
        add("| Attempt | Queue item | Request id | Action | Outcome | Actor | Resulting message |")
        add("|---|---|---|---|---|---|---|")
        for a in history:
            resulting = a.get("resulting_message_id")
            add(
                f"| {_cell(a.get('attempt_id'))} | {_cell(a.get('queue_id'))} | "
                f"{_cell(a.get('request_id'))} | {_cell(a.get('action'))} | "
                f"{_cell(a.get('outcome'))} | {_cell(a.get('actor'))} | "
                f"{'none' if resulting is None else 'interface_message:' + _cell(resulting)} |"
            )
    add("")

    add("## Reconciliation")
    add("")
    reconciliation = evidence.get("reconciliation") or {}
    summary = reconciliation.get("summary") or {}
    add(
        f"- Summary: ERROR {_text(summary.get('ERROR', 0))}, "
        f"WARNING {_text(summary.get('WARNING', 0))}, INFO {_text(summary.get('INFO', 0))}"
    )
    for f in reconciliation.get("findings") or []:
        add(
            f"- {_text(f.get('rule_id'))} {_text(f.get('severity'))} "
            f"({_text(f.get('title'))}): "
            f"{', '.join(_text(s) for s in f.get('subjects') or []) or 'no subjects'}"
        )
    add("")

    add("## Provenance notes")
    add("")
    notes = evidence.get("provenance") or []
    if not notes:
        add("- None.")
    for n in notes:
        add(f"- {_text(n.get('code'))} {_text(n.get('subject'))}: {_text(n.get('detail'))}")
    add("")

    add("## Limitations")
    add("")
    for lim in evidence.get("limitations") or []:
        add(f"- {_text(lim.get('code'))}: {_text(lim.get('text'))}")
    add("")
    add(f"Content fingerprint: {_text(fingerprint)}")
    return "\n".join(lines) + "\n"
