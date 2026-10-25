"""Command-line interface for the CytoBridge analyst lab.

    python -m src.analyst [--json] <command> [options]

A thin, headless front end over the existing CytoBridge services. It adds no
product semantics: every write goes through the existing public workflow,
inbound-interface, outbound-interface, and v1.1 recovery service functions, and
every investigation command opens the database read-only
(``store.open_read_only``: SQLite ``mode=ro`` plus ``PRAGMA query_only``).

How a command may touch the database is decided by how it opens the file:

- create      ``init`` makes a NEW database file and refuses any existing path.
- read-only   worklist, show-message, trace, reconcile, recover preview,
              recover history, audit, export (without ``--store``), evidence,
              exercise list/show/verify. They never create, modify, or leave a
              journal next to the database.
- read-write  case-create, specimen-accession, result-enter, finalize, ingest,
              recover retry, recover redrive, export ``--store``. They open an
              EXISTING file (``mode=rw`` never creates one) and refuse
              documented-invalid requests with a read-only preflight before the
              first service write.

Output
------
``--json`` (accepted anywhere in argv) prints exactly one canonical JSON object
to stdout::

    {"format", "command", "ok", "exit_code", "result", "error", "synthetic"}

Text mode prints readable lines to stdout, each data output starting with the
one-line synthetic notice, and errors to stderr as ``error: <message>``. Text is
written in the stream's encoding with ``backslashreplace`` so non-ASCII content
(for example the em dash the report summary puts into outbound payloads) never
crashes a terminal with a narrow encoding.

``main(argv)`` returns the exit code and never calls ``sys.exit`` itself, so the
exercise runner can call it in-process; every connection it opens is closed.

Exit codes: 0 OK, 1 CHECK_FAILED, 2 USAGE, 3 NOT_FOUND, 4 REFUSED (including
REQUEST_ID_CONFLICT), 5 INVALID_INPUT, 6 INTERNAL.

This is an educational, synthetic simulator - not a production LIS, certified
interface engine, validated medical device, or Epic/Beaker implementation. All
data is synthetic. No PHI.
"""

from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import textwrap
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from .. import db, recovery, workflow
from ..interfaces import OutboundError, inbound_hl7, outbound_fhir, outbound_hl7
from . import preview, reconcile, store, trace
from .common import (
    EXIT_INTERNAL,
    EXIT_OK,
    FORMAT,
    SYNTHETIC_NOTICE,
    AnalystError,
    canonical_json,
    read_payload_file,
    sha256_bytes,
    sha256_text,
)

PROG = "python -m src.analyst"
JSON_FLAG = "--json"

# CLI spelling of the two frozen recovery actions (design record sec 7).
ACTION_CHOICES: dict[str, str] = {
    "retry": preview.RETRY_ORIGINAL,
    "redrive": preview.REDRIVE_CORRECTED,
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

# Order states in which workflow.enter_fish_result / finalize_order refuse work.
_CLOSED_ORDER_STATES = ("FINALIZED", "CANCELLED")

_TOP_COMMANDS = (
    "init", "case-create", "specimen-accession", "result-enter", "finalize",
    "ingest", "worklist", "show-message", "trace", "reconcile", "recover",
    "export", "audit", "evidence", "exercise",
)
_NESTED_COMMANDS: dict[str, tuple[str, ...]] = {
    "recover": ("preview", "retry", "redrive", "history"),
    "exercise": ("list", "show", "setup", "run", "verify", "run-all"),
}

EPILOG = """\
exit codes:
  0  OK             command completed (a recovery request SUCCEEDED, fresh or replayed)
  1  CHECK_FAILED   reconcile found ERROR findings; exercise verification failed
  2  USAGE          invalid arguments, unknown command, missing required option
  3  NOT_FOUND      database, input file, order, queue item, message, specimen,
                    or exercise does not exist (a missing database is never created)
  4  REFUSED        request refused or not applied: duplicate MRN/accession,
                    closed order, finalize blocked, message routed to the error
                    queue, recovery outcome not SUCCEEDED, REQUEST_ID_CONFLICT,
                    existing output file, init on an existing path
  5  INVALID_INPUT  input file not UTF-8, not a CytoBridge database, invalid
                    result values
  6  INTERNAL       unexpected error (the message names the exception type)

read-only commands (SQLite mode=ro + query_only; never create or change the db):
  worklist, show-message, trace, reconcile, recover preview, recover history,
  audit, export (without --store), evidence, exercise list/show/verify

write commands (open an EXISTING db; every write goes through the existing
CytoBridge services after a read-only preflight):
  case-create, specimen-accession, result-enter, finalize, ingest,
  recover retry, recover redrive, export --store

create commands (new files only; an existing path is refused):
  init, exercise setup/run/run-all

--json may appear anywhere and prints exactly one JSON object:
  {"format", "command", "ok", "exit_code", "result", "error", "synthetic"}

Synthetic educational data only. Not a production LIS, certified interface
engine, validated medical device, or Epic/Beaker implementation. No PHI.
"""


# ---------------------------------------------------------------------------
# argparse without process exits
# ---------------------------------------------------------------------------

class _ParserExit(Exception):
    """argparse asked to exit (``--help``); carries the status to return."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose errors become ``AnalystError("USAGE")``.

    Usage errors therefore render as a JSON envelope under ``--json`` and exit
    2 through ``main``'s single return path instead of ``sys.exit``.
    """

    def error(self, message: str) -> None:  # type: ignore[override]
        raise AnalystError("USAGE", f"{message} (see: {self.prog} --help)")

    def exit(self, status: int = 0, message: str | None = None) -> None:  # type: ignore[override]
        if message:
            _emit_text(sys.stderr, message)
        raise _ParserExit(status)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _emit_text(stream: Any, text: str) -> None:
    """Write ``text`` without ever failing on characters the stream cannot encode.

    A binary-backed stream gets the text encoded in its own encoding with
    ``errors="backslashreplace"``; a pure text stream (``io.StringIO`` used by
    an in-process caller) receives the text unchanged.
    """
    if not text:
        return
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(text)
        stream.flush()
        return
    encoding = getattr(stream, "encoding", None) or "utf-8"
    try:
        data = text.encode(encoding, errors="backslashreplace")
    except LookupError:
        data = text.encode("utf-8", errors="backslashreplace")
    stream.flush()
    buffer.write(data)
    buffer.flush()


def _emit_bytes(stream: Any, data: bytes) -> None:
    """Write exact bytes (``show-message --raw``); decode only for a text-only stream."""
    buffer = getattr(stream, "buffer", None)
    if buffer is None:
        stream.write(data.decode("utf-8"))
        stream.flush()
        return
    stream.flush()
    buffer.write(data)
    buffer.flush()


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _table(
    headers: Sequence[str], rows: Sequence[Sequence[Any]], indent: str = "  "
) -> list[str]:
    """Left-aligned columns; the last column is never padded or truncated."""
    cells = [[_cell(v) for v in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in cells:
        for i, c in enumerate(row):
            widths[i] = max(widths[i], len(c))
    last = len(headers) - 1

    def fmt(values: Sequence[str]) -> str:
        parts = [v if i == last else v.ljust(widths[i]) for i, v in enumerate(values)]
        return (indent + "  ".join(parts)).rstrip()

    rule = ["-" * (w if i < last else max(len(headers[i]), min(w, 40)))
            for i, w in enumerate(widths)]
    return [fmt(list(headers)), fmt(rule)] + [fmt(r) for r in cells]


def _section(title: str, count: int | None = None) -> list[str]:
    suffix = "" if count is None else f" ({count})"
    return ["", f"== {title}{suffix} =="]


def _wrap(text: Any, indent: str = "    ", hang: str = "") -> list[str]:
    return textwrap.wrap(
        _cell(text), width=100, initial_indent=indent,
        subsequent_indent=indent + hang,
    ) or [indent + "-"]


def _payload_lines(payload: str, indent: str = "  ") -> list[str]:
    """Payload shown one segment/line per row (segment terminators -> line breaks)."""
    normalized = payload.replace("\r\n", "\n").replace("\r", "\n")
    return [indent + line for line in normalized.split("\n")]


def _status(value: Any) -> str:
    return "NULL" if value is None else str(value)


# ---------------------------------------------------------------------------
# Database access (always closed)
# ---------------------------------------------------------------------------

@contextmanager
def _read_only(path: str) -> Iterator[sqlite3.Connection]:
    conn = store.open_read_only(path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def _read_write(path: str) -> Iterator[sqlite3.Connection]:
    conn = store.open_read_write(path)
    try:
        yield conn
    finally:
        conn.close()


def _order_row(conn: sqlite3.Connection, order_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT o.order_id, o.accession_number, o.status, o.panel_id, pa.panel_code "
        "FROM lab_order o JOIN panel pa ON pa.panel_id = o.panel_id "
        "WHERE o.order_id = ?",
        (order_id,),
    ).fetchone()
    if row is None:
        raise AnalystError("NOT_FOUND", f"Order {order_id} does not exist.")
    return row


def _order_status(conn: sqlite3.Connection, order_id: int) -> str | None:
    row = conn.execute(
        "SELECT status FROM lab_order WHERE order_id = ?", (order_id,)
    ).fetchone()
    return None if row is None else row["status"]


def _queue_row(conn: sqlite3.Connection, queue_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT queue_id, message_id, status, failure_code, failure_category, "
        "recovery_policy FROM interface_error_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise AnalystError("NOT_FOUND", f"Error-queue item {queue_id} does not exist.")
    return row


def _message_status(conn: sqlite3.Connection, message_id: int | None) -> str | None:
    if message_id is None:
        return None
    row = conn.execute(
        "SELECT status FROM interface_message WHERE message_id = ?", (message_id,)
    ).fetchone()
    return None if row is None else row["status"]


def _check_date(value: str, option: str) -> None:
    ok = bool(_DATE_RE.match(value))
    if ok:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            ok = False
    if not ok:
        raise AnalystError(
            "USAGE", f"{option} {value!r} is not a valid date in YYYY-MM-DD format."
        )


def _check_datetime(value: str, option: str) -> None:
    ok = bool(_DATETIME_RE.match(value))
    if ok:
        try:
            datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            ok = False
    if not ok:
        raise AnalystError(
            "USAGE",
            f"{option} {value!r} is not a valid timestamp in "
            f"'YYYY-MM-DD HH:MM:SS' format.",
        )


def _check_new_output(path: Path, option: str) -> None:
    """Refuse an existing output path before anything is written."""
    if path.exists() or path.is_symlink():
        raise AnalystError(
            "REFUSED",
            f"Refusing to overwrite existing file {path} ({option}); choose a new "
            f"path. Nothing was written.",
        )
    parent = path.parent if str(path.parent) else Path(".")
    if not parent.is_dir():
        raise AnalystError(
            "NOT_FOUND", f"Output directory {parent} ({option}) does not exist."
        )


def _write_new_file(path: Path, data: bytes, option: str) -> None:
    """Create ``path`` exclusively and write exact bytes."""
    try:
        with open(path, "xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise AnalystError(
            "REFUSED", f"Refusing to overwrite existing file {path} ({option})."
        )
    except OSError as exc:
        raise AnalystError("INVALID_INPUT", f"Cannot write {path} ({option}): {exc}.")


def _findings(findings: Sequence[Any]) -> list[dict[str, str]]:
    return [
        {"rule_code": f.rule_code, "severity": f.severity, "message": f.message}
        for f in findings
    ]


# ---------------------------------------------------------------------------
# Command handlers: each returns a result dict or raises AnalystError
# ---------------------------------------------------------------------------

def _cmd_init(ns: argparse.Namespace) -> dict[str, Any]:
    path = store.init_database(ns.db)
    return {"database": str(path), "created": True}


def _cmd_case_create(ns: argparse.Namespace) -> dict[str, Any]:
    _check_date(ns.dob, "--dob")
    with _read_write(ns.db) as conn:
        # Read-only preflight: patient.mrn and lab_order.accession_number are
        # UNIQUE in schema.sql; refuse before the first service write.
        if conn.execute("SELECT 1 FROM patient WHERE mrn = ?", (ns.mrn,)).fetchone():
            raise AnalystError(
                "REFUSED",
                f"A patient with MRN {ns.mrn} already exists (patient.mrn is "
                f"UNIQUE); nothing was created.",
            )
        if conn.execute(
            "SELECT 1 FROM lab_order WHERE accession_number = ?", (ns.accession,)
        ).fetchone():
            raise AnalystError(
                "REFUSED",
                f"An order with accession {ns.accession} already exists "
                f"(lab_order.accession_number is UNIQUE); nothing was created.",
            )
        patient_id = workflow.create_patient(
            conn, ns.mrn, ns.last_name, ns.first_name, ns.dob, ns.sex
        )
        order_id = workflow.create_order(
            conn, patient_id, ns.accession, ns.provider, priority=ns.priority
        )
        specimen_id = workflow.receive_specimen(
            conn,
            order_id,
            specimen_type=ns.specimen_type,
            external_specimen_id=ns.external_specimen_id,
            collected_at=ns.collected_at,
        )
        if not ns.no_accession:
            workflow.accession_specimen(conn, specimen_id)
        specimen_status = conn.execute(
            "SELECT status FROM specimen WHERE specimen_id = ?", (specimen_id,)
        ).fetchone()["status"]
        order_status = _order_status(conn, order_id)
    return {
        "patient_id": patient_id,
        "order_id": order_id,
        "specimen_id": specimen_id,
        "accession_number": ns.accession,
        "order_status": order_status,
        "specimen_status": specimen_status,
    }


def _cmd_specimen_accession(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_write(ns.db) as conn:
        try:
            workflow.accession_specimen(conn, ns.specimen_id)
        except workflow.WorkflowError as exc:
            kind = "NOT_FOUND" if "does not exist" in str(exc) else "REFUSED"
            raise AnalystError(kind, f"{exc} Nothing was written.")
        row = conn.execute(
            "SELECT s.specimen_id, s.order_id, s.status, o.status AS order_status "
            "FROM specimen s JOIN lab_order o ON o.order_id = s.order_id "
            "WHERE s.specimen_id = ?",
            (ns.specimen_id,),
        ).fetchone()
    return {
        "specimen_id": row["specimen_id"],
        "order_id": row["order_id"],
        "specimen_status": row["status"],
        "order_status": row["order_status"],
    }


def _cmd_result_enter(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_write(ns.db) as conn:
        order = _order_row(conn, ns.order_id)
        if order["status"] in _CLOSED_ORDER_STATES:
            raise AnalystError(
                "REFUSED",
                f"Order {ns.order_id} is {order['status']}; "
                f"workflow.enter_fish_result does not accept results for a "
                f"FINALIZED or CANCELLED order. Nothing was written.",
            )
        probe = conn.execute(
            "SELECT probe_id FROM probe WHERE panel_id = ? AND probe_code = ?",
            (order["panel_id"], ns.probe),
        ).fetchone()
        if probe is None:
            raise AnalystError(
                "INVALID_INPUT",
                f"Probe code {ns.probe!r} is not part of the {order['panel_code']} "
                f"panel. Nothing was written.",
            )
        if ns.scored < 0 or ns.abnormal < 0:
            raise AnalystError(
                "INVALID_INPUT",
                f"Cell counts cannot be negative (--scored {ns.scored}, --abnormal "
                f"{ns.abnormal}; schema.sql CHECK cells_scored >= 0 and "
                f"cells_abnormal >= 0). Nothing was written.",
            )
        if ns.abnormal > ns.scored:
            raise AnalystError(
                "INVALID_INPUT",
                f"--abnormal {ns.abnormal} exceeds --scored {ns.scored} (schema.sql "
                f"CHECK cells_abnormal <= cells_scored). Nothing was written.",
            )
        try:
            result_id = workflow.enter_fish_result(
                conn,
                ns.order_id,
                ns.probe,
                ns.scored,
                ns.abnormal,
                ns.pattern,
                ns.interpretation,
                ns.entered_by,
            )
        except workflow.WorkflowError as exc:
            raise AnalystError("REFUSED", str(exc))
        order_status = _order_status(conn, ns.order_id)
    return {
        "result_id": result_id,
        "order_id": ns.order_id,
        "probe_code": ns.probe,
        "order_status": order_status,
    }


def _cmd_finalize(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_write(ns.db) as conn:
        order = _order_row(conn, ns.order_id)
        if order["status"] in _CLOSED_ORDER_STATES:
            raise AnalystError(
                "REFUSED",
                f"Order {ns.order_id} is already {order['status']}; finalize was "
                f"not attempted and nothing was written.",
            )
        outcome = workflow.finalize_order(conn, ns.order_id, ns.by)
        order_status = _order_status(conn, ns.order_id)
    findings = _findings(outcome.findings)
    if not outcome.finalized:
        errors = sum(1 for f in findings if f["severity"] == "ERROR")
        raise AnalystError(
            "REFUSED",
            f"Finalization of order {ns.order_id} was blocked by {errors} ERROR "
            f"validation finding(s); the order stays {order_status}. "
            f"workflow.finalize_order recorded the findings and a FINALIZE_BLOCKED "
            f"audit event.",
            result={
                "order_id": ns.order_id,
                "finalized": False,
                "findings": findings,
                "order_status": order_status,
            },
        )
    return {
        "order_id": ns.order_id,
        "finalized": True,
        "report_id": outcome.report_id,
        "findings": findings,
        "order_status": order_status,
    }


def _cmd_ingest(ns: argparse.Namespace) -> dict[str, Any]:
    # Read (and UTF-8 check) the exact file bytes before the database is opened
    # for write: a missing or undecodable file changes nothing.
    text, raw = read_payload_file(ns.file)
    with _read_write(ns.db) as conn:
        outcome = inbound_hl7.ingest_message(conn, text, actor=ns.actor)
        queue = None
        if outcome.queue_id is not None:
            queue = conn.execute(
                "SELECT failure_code, failure_category, recovery_policy, status "
                "FROM interface_error_queue WHERE queue_id = ?",
                (outcome.queue_id,),
            ).fetchone()
    result = {
        "message_id": outcome.message_id,
        "filed": outcome.filed,
        "order_id": outcome.order_id,
        "queue_id": outcome.queue_id,
        "reason": outcome.reason,
        "probe_codes_filed": list(outcome.probe_codes_filed),
        "payload_sha256": sha256_bytes(raw),
        "payload_bytes": len(raw),
        "failure_code": None if queue is None else queue["failure_code"],
        "failure_category": None if queue is None else queue["failure_category"],
        "recovery_policy": None if queue is None else queue["recovery_policy"],
        "queue_status": None if queue is None else queue["status"],
    }
    if not outcome.filed:
        raise AnalystError(
            "REFUSED",
            f"Message {outcome.message_id} was not filed: routed to error queue "
            f"item {outcome.queue_id} ({result['failure_code']}, policy "
            f"{result['recovery_policy']}, status {result['queue_status']}). "
            f"Reason: {outcome.reason}",
            result=result,
        )
    return result


def _cmd_worklist(ns: argparse.Namespace) -> dict[str, Any]:
    status = None if ns.status == "ALL" else ns.status
    with _read_only(ns.db) as conn:
        rows = db.rows_to_dicts(
            db.run_query(conn, "analyst_queue_worklist", {"status": status})
        )
    return {"status_filter": ns.status, "count": len(rows), "items": rows}


def _cmd_show_message(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_only(ns.db) as conn:
        row = conn.execute(
            "SELECT message_id, direction, format, message_type, status, order_id, "
            "control_id, created_at, payload FROM interface_message "
            "WHERE message_id = ?",
            (ns.message_id,),
        ).fetchone()
    if row is None:
        raise AnalystError(
            "NOT_FOUND", f"Interface message {ns.message_id} does not exist."
        )
    result = dict(row)
    result["payload_sha256"] = sha256_text(result["payload"])
    result["payload_bytes"] = len(result["payload"].encode("utf-8"))
    return result


def _cmd_trace(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_only(ns.db) as conn:
        return trace.build_case(
            conn,
            order_id=ns.order_id,
            queue_id=ns.queue_id,
            message_id=ns.message_id,
            accession=ns.accession,
        )


def _cmd_reconcile(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_only(ns.db) as conn:
        if ns.order_id is None and ns.queue_id is None:
            scope: dict[str, Any] = {"kind": "database", "id": None}
            findings = reconcile.reconcile_database(conn)
        else:
            if ns.order_id is not None:
                scope = {"kind": "order", "id": ns.order_id}
                case = trace.build_case(conn, order_id=ns.order_id)
            else:
                scope = {"kind": "queue", "id": ns.queue_id}
                case = trace.build_case(conn, queue_id=ns.queue_id)
            findings = reconcile.reconcile_case(conn, case)
    summary = reconcile.summarize(findings)
    result = {"scope": scope, "summary": summary, "findings": findings}
    if summary.get("ERROR", 0):
        raise AnalystError(
            "CHECK_FAILED",
            f"Reconciliation found {summary['ERROR']} ERROR finding(s) "
            f"({summary.get('WARNING', 0)} WARNING, {summary.get('INFO', 0)} INFO).",
            result=result,
        )
    return result


def _recovery_payload(ns: argparse.Namespace, action: str, command: str) -> str | None:
    """The corrected payload for a re-drive, read exactly; None for a retry."""
    if action == preview.REDRIVE_CORRECTED:
        if ns.file is None:
            raise AnalystError(
                "USAGE",
                f"{command} with a corrected re-drive requires --file PATH (the "
                f"corrected payload).",
            )
        text, _raw = read_payload_file(ns.file)
        return text
    if getattr(ns, "file", None) is not None:
        raise AnalystError(
            "USAGE",
            f"{command} with an unchanged retry reuses the stored original payload "
            f"exactly; --file is not accepted.",
        )
    return None


def _cmd_recover_preview(ns: argparse.Namespace) -> dict[str, Any]:
    action = ACTION_CHOICES[ns.action]
    corrected = _recovery_payload(ns, action, "recover preview")
    result = preview.preview_recovery(
        ns.db,
        ns.queue_id,
        action=action,
        request_id=ns.request_id,
        actor=ns.actor,
        corrected_payload=corrected,
    )
    sim = result["simulated"]
    status = sim["status"]
    if status == "SUCCEEDED" or (
        status == "REPLAYED" and sim["recorded_outcome"] == "SUCCEEDED"
    ):
        return result
    detail = f": {sim['error']}" if sim.get("error") else ""
    if status == "REPLAYED":
        detail = f" (recorded outcome {sim['recorded_outcome']})"
    raise AnalystError(
        "REFUSED",
        f"Preview only, nothing was written: applying this request now would "
        f"yield {status}{detail}.",
        result=result,
    )


def _cmd_recover_apply(ns: argparse.Namespace) -> dict[str, Any]:
    action = preview.RETRY_ORIGINAL if ns._words == "recover retry" else (
        preview.REDRIVE_CORRECTED
    )
    corrected = _recovery_payload(ns, action, ns._words)
    failure: tuple[str, str] | None = None
    attempt: recovery.RecoveryAttempt | None = None
    with _read_write(ns.db) as conn:
        _queue_row(conn, ns.queue_id)
        prior = conn.execute(
            "SELECT attempt_id FROM interface_recovery_attempt WHERE request_id = ?",
            (ns.request_id,),
        ).fetchone()
        prior_id = None if prior is None else prior["attempt_id"]
        try:
            if action == preview.RETRY_ORIGINAL:
                attempt = recovery.retry_queue_item(
                    conn, ns.queue_id, request_id=ns.request_id, actor=ns.actor
                )
            else:
                attempt = recovery.redrive_queue_item(
                    conn,
                    ns.queue_id,
                    corrected,  # type: ignore[arg-type]
                    request_id=ns.request_id,
                    actor=ns.actor,
                )
        except recovery.RequestIdConflictError as exc:
            failure = (
                "REQUEST_ID_CONFLICT",
                f"{exc} No message, recovery attempt, or result was created; the "
                f"recovery service recorded one REQUEST_ID_CONFLICT audit event "
                f"(see: recover history --queue-id {ns.queue_id}).",
            )
        except recovery.RecoveryError as exc:
            failure = ("REFUSED", f"{exc} No recovery attempt was recorded.")
        queue_status = _queue_row(conn, ns.queue_id)["status"]
        resulting_status = _message_status(
            conn, None if attempt is None else attempt.resulting_message_id
        )
    result = {
        "queue_id": ns.queue_id,
        "action": action,
        "request_id": ns.request_id,
        "attempt": None if attempt is None else preview.attempt_dict(attempt),
        "replayed": attempt is not None
        and prior_id is not None
        and attempt.attempt_id == prior_id,
        "queue_status": queue_status,
        "resulting_message_status": resulting_status,
    }
    if failure is not None:
        ns._failure_kind = failure[0]
        raise AnalystError(failure[0], failure[1], result=result)
    assert attempt is not None
    if attempt.outcome != "SUCCEEDED":
        raise AnalystError(
            "REFUSED",
            f"recovery attempt {attempt.attempt_id}: {attempt.outcome} "
            f"({attempt.action}) - {attempt.outcome_detail}",
            result=result,
        )
    return result


def _cmd_recover_history(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_only(ns.db) as conn:
        queue = _queue_row(conn, ns.queue_id)
        attempts = [
            preview.attempt_dict(a)
            for a in recovery.get_recovery_history(conn, ns.queue_id)
        ]
        conflicts = db.rows_to_dicts(conn.execute(
            "SELECT event_id, entity_type, entity_id, order_id, action, detail, "
            "actor, created_at FROM audit_event "
            "WHERE action = 'REQUEST_ID_CONFLICT' "
            "AND entity_type = 'interface_error_queue' AND entity_id = ? "
            "ORDER BY event_id",
            (ns.queue_id,),
        ).fetchall())
    return {
        "queue_id": ns.queue_id,
        "queue_status": queue["status"],
        "failure_code": queue["failure_code"],
        "attempts": attempts,
        "conflict_audit_events": conflicts,
    }


def _cmd_export(ns: argparse.Namespace) -> dict[str, Any]:
    fmt = ns.format.upper()
    if ns.generated_at is not None:
        _check_datetime(ns.generated_at, "--generated-at")
    out = None if ns.out is None else Path(ns.out)
    if out is not None:
        _check_new_output(out, "--out")
    opener = _read_write if ns.store else _read_only
    stored_id: int | None = None
    with opener(ns.db) as conn:
        _order_row(conn, ns.order_id)
        try:
            if ns.store:
                if fmt == "HL7":
                    stored_id = outbound_hl7.store_oru(
                        conn, ns.order_id, ns.generated_at
                    )
                else:
                    stored_id = outbound_fhir.store_diagnostic_report(
                        conn, ns.order_id, ns.generated_at
                    )
                payload = conn.execute(
                    "SELECT payload FROM interface_message WHERE message_id = ?",
                    (stored_id,),
                ).fetchone()["payload"]
            elif fmt == "HL7":
                payload = outbound_hl7.generate_oru(conn, ns.order_id, ns.generated_at)
            else:
                payload = outbound_fhir.generate_diagnostic_report_json(
                    conn, ns.order_id, ns.generated_at
                )
        except OutboundError as exc:
            raise AnalystError("REFUSED", f"{exc} Nothing was stored or written.")
    data = payload.encode("utf-8")
    if out is not None:
        _write_new_file(out, data, "--out")
    return {
        "order_id": ns.order_id,
        "format": fmt,
        "payload_sha256": sha256_bytes(data),
        "payload_bytes": len(data),
        "stored_message_id": stored_id,
        "out": None if out is None else str(out),
        "payload": payload,
    }


def _cmd_audit(ns: argparse.Namespace) -> dict[str, Any]:
    with _read_only(ns.db) as conn:
        order = _order_row(conn, ns.order_id)
        events = db.rows_to_dicts(
            db.run_query(conn, "audit_lookup", {"order_id": ns.order_id})
        )
    return {
        "order_id": ns.order_id,
        "accession_number": order["accession_number"],
        "count": len(events),
        "events": events,
    }


def _cmd_evidence(ns: argparse.Namespace) -> dict[str, Any]:
    from . import evidence  # lazy: optional module of the analyst lab

    out = None if ns.out is None else Path(ns.out)
    report = None if ns.report is None else Path(ns.report)
    if out is not None and report is not None and (
        out.resolve() == report.resolve()
    ):
        raise AnalystError("USAGE", "--out and --report must name different files.")
    if out is not None:
        _check_new_output(out, "--out")
    if report is not None:
        _check_new_output(report, "--report")
    revision = (
        ns.source_revision
        if ns.source_revision is not None
        else evidence.resolve_source_revision()
    )
    with _read_only(ns.db) as conn:
        ev = evidence.build_evidence(
            conn,
            queue_id=ns.queue_id,
            order_id=ns.order_id,
            scenario_id=ns.scenario,
            scenario_title=ns.title,
            source_revision=revision,
        )
    evidence_text = canonical_json(ev)
    report_text = evidence.render_report(ev)
    if out is not None:
        _write_new_file(out, evidence_text.encode("utf-8"), "--out")
    if report is not None:
        _write_new_file(report, report_text.encode("utf-8"), "--report")
    records = ev.get("records", {})
    counts = {name: len(records.get(name, [])) for name in (
        "orders", "messages", "queue_items", "recovery_attempts", "audit_events"
    )}
    counts["relationships"] = len(ev.get("relationships", []))
    counts["fingerprints"] = len(ev.get("fingerprints", []))
    counts["findings"] = len(ev.get("reconciliation", {}).get("findings", []))
    result: dict[str, Any] = {
        "content_fingerprint": ev.get("content_fingerprint"),
        "out": None if out is None else str(out),
        "report": None if report is None else str(report),
        "counts": counts,
        "anchor": ev.get("anchor"),
        "reconciliation_summary": ev.get("reconciliation", {}).get("summary"),
    }
    if ns.json_mode:
        result["evidence"] = ev
    else:
        ns._report_text = report_text
    return result


def _cmd_exercise_list(ns: argparse.Namespace) -> dict[str, Any]:
    from . import exercises  # lazy: optional module of the analyst lab

    items = exercises.list_exercises()
    return {"count": len(items), "exercises": items}


def _exercise_identity(exercises: Any, ex_id: str) -> dict[str, Any]:
    loaded = exercises.load_exercise(ex_id)
    setup = loaded.get("setup") if isinstance(loaded.get("setup"), dict) else loaded
    resolved = loaded.get("id") or setup.get("id") or ex_id
    return {
        "id": resolved,
        "title": loaded.get("title") or setup.get("title"),
        "summary": loaded.get("summary") or setup.get("summary"),
    }


def _cmd_exercise_show(ns: argparse.Namespace) -> dict[str, Any]:
    from . import exercises  # lazy

    identity = _exercise_identity(exercises, ns.exercise_id)
    identity["instructions"] = exercises.instructions_text(ns.exercise_id)
    return identity


def _steps_failed(steps: Any) -> list[Any]:
    return [s for s in (steps or []) if isinstance(s, dict) and not s.get("matched", True)]


def _refuse_existing_database(path: str) -> None:
    """Exercises always start from a NEW database, exactly as ``init`` requires."""
    p = Path(path)
    if p.exists() or p.is_symlink():
        raise AnalystError(
            "REFUSED",
            f"Refusing to use {p}: the path already exists. Exercise setup always "
            f"starts with init on a new database; choose a new path.",
        )


def _cmd_exercise_setup(ns: argparse.Namespace) -> dict[str, Any]:
    from . import exercises  # lazy

    _refuse_existing_database(ns.db)
    result = exercises.setup_exercise(ns.exercise_id, ns.db)
    failed = _steps_failed(result.get("steps"))
    if failed:
        raise AnalystError(
            "CHECK_FAILED",
            f"Exercise setup step exited {failed[0].get('exit_code')} where "
            f"{failed[0].get('expect_exit')} was expected; setup stopped.",
            result=result,
        )
    return result


def _cmd_exercise_run(ns: argparse.Namespace) -> dict[str, Any]:
    from . import exercises  # lazy

    _refuse_existing_database(ns.db)
    result = exercises.run_exercise(ns.exercise_id, ns.db)
    verification = result.get("verification") or {}
    if _steps_failed(result.get("steps")) or not verification.get("passed", False):
        raise AnalystError(
            "CHECK_FAILED",
            f"Exercise {result.get('exercise', ns.exercise_id)} did not pass: a step "
            f"exit code differed from its expectation or verification failed.",
            result=result,
        )
    return result


def _cmd_exercise_verify(ns: argparse.Namespace) -> dict[str, Any]:
    from . import exercises  # lazy

    result = exercises.verify_exercise(ns.exercise_id, ns.db)
    if not result.get("passed", False):
        failed = [c.get("name") for c in result.get("checks", []) if not c.get("passed")]
        raise AnalystError(
            "CHECK_FAILED",
            f"Exercise {result.get('exercise', ns.exercise_id)} verification failed "
            f"({len(failed)} check(s)).",
            result=result,
        )
    return result


def _cmd_exercise_run_all(ns: argparse.Namespace) -> dict[str, Any]:
    from . import exercises  # lazy

    result = exercises.run_all(ns.workdir)
    if not result.get("passed", False):
        raise AnalystError(
            "CHECK_FAILED", "At least one exercise did not pass.", result=result
        )
    return result


# ---------------------------------------------------------------------------
# Text renderers: each returns lines shown after the synthetic notice
# ---------------------------------------------------------------------------

def _render_init(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [f"created new CytoBridge database: {r['database']} (schema.sql loaded)"]


def _render_case_create(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [
        f"case created: patient {r['patient_id']}, order {r['order_id']} "
        f"(accession {r['accession_number']}, status {r['order_status']}), "
        f"specimen {r['specimen_id']} ({r['specimen_status']})"
    ]


def _render_specimen_accession(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [
        f"specimen {r['specimen_id']}: {r['specimen_status']} "
        f"(order {r['order_id']} {r['order_status']})"
    ]


def _render_result_enter(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [
        f"result {r['result_id']} entered: order {r['order_id']} probe "
        f"{r['probe_code']} (order status {r['order_status']})",
        "note: fish_result is upserted; re-entering a probe replaces its current value.",
    ]


def _findings_table(findings: Sequence[dict[str, Any]]) -> list[str]:
    if not findings:
        return ["  none"]
    return _table(
        ("severity", "rule_code", "message"),
        [(f["severity"], f["rule_code"], f["message"]) for f in findings],
    )


def _render_finalize(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    if r.get("finalized"):
        head = (
            f"order {r['order_id']}: FINALIZED (report {r['report_id']}); "
            f"status {r['order_status']}"
        )
    else:
        head = f"order {r['order_id']}: finalize BLOCKED; status {r['order_status']}"
    return [head, "validation findings:"] + _findings_table(r.get("findings", []))


def _render_ingest(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    if r["filed"]:
        lines = [
            f"message {r['message_id']}: FILED -> order {r['order_id']} "
            f"({len(r['probe_codes_filed'])} probe result(s): "
            f"{', '.join(r['probe_codes_filed']) or '-'})"
        ]
    else:
        lines = [
            f"message {r['message_id']}: ERRORED -> error queue item "
            f"{r['queue_id']} ({r['failure_code']} / {r['failure_category']} / "
            f"{r['recovery_policy']}, status {r['queue_status']})",
            f"reason: {r['reason']}",
        ]
    lines.append(
        f"payload sha256 {r['payload_sha256']} ({r['payload_bytes']} bytes, "
        f"stored exactly as read)"
    )
    return lines


def _render_worklist(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    lines = [
        f"error-queue worklist: status {r['status_filter']}, {r['count']} item(s) "
        f"(oldest first: created_at, queue_id)"
    ]
    if not r["items"]:
        return lines + ["  no items"]
    return lines + _table(
        ("queue", "created_at", "status", "failure_code", "policy", "message",
         "control_id", "attempts", "last_outcome", "reason"),
        [
            (i["queue_id"], i["created_at"], i["status"], i["failure_code"],
             i["recovery_policy"], i["message_id"], i["control_id"],
             i["attempt_count"], i["last_outcome"], i["reason"])
            for i in r["items"]
        ],
    )


def _render_show_message(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [
        f"message {r['message_id']}: {r['direction']} {r['format']} "
        f"{r['message_type']}  status {r['status']}  order {_status(r['order_id'])}",
        f"control_id {r['control_id'] or '-'}  created {r['created_at']}",
        f"payload sha256 {r['payload_sha256']} ({r['payload_bytes']} bytes)",
        "payload (segment terminators shown as line breaks; use --raw for exact bytes):",
    ] + _payload_lines(r["payload"])


def _render_trace(case: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    anchor = case["anchor"]
    lines = [
        f"case anchor: {anchor['kind']} {anchor['id']}",
        f"records: {len(case['orders'])} order(s), {len(case['messages'])} "
        f"message(s), {len(case['queue_items'])} queue item(s), "
        f"{len(case['recovery_attempts'])} recovery attempt(s), "
        f"{len(case['audit_events'])} audit event(s)",
    ]

    lines += _section("orders", len(case["orders"]))
    for o in case["orders"]:
        p = o["patient"]
        lines.append(
            f"order {o['order_id']}  accession {o['accession_number']}  status "
            f"{o['status']}  priority {o['priority']}  panel {o['panel_code']}"
        )
        lines.append(
            f"  patient {p['patient_id']}  mrn {p['mrn']}  {p['last_name']}, "
            f"{p['first_name']}  dob {p['date_of_birth']}  sex {p['sex']}"
        )
        lines.append(
            f"  provider {o['ordering_provider']}  ordered {o['ordered_at']}  "
            f"collected {o['collected_at'] or '-'}  finalized {o['finalized_at'] or '-'}"
        )
        lines.append(f"  specimens ({len(o['specimens'])}):")
        if o["specimens"]:
            lines += _table(
                ("specimen", "type", "external_id", "status", "received_at",
                 "rejection_reason"),
                [(s["specimen_id"], s["specimen_type"], s["external_specimen_id"],
                  s["status"], s["received_at"], s["rejection_reason"])
                 for s in o["specimens"]],
                indent="    ",
            )
        lines.append(
            f"  current results ({len(o['current_results'])} of "
            f"{o['required_probe_count']} required probes; current values only):"
        )
        if o["current_results"]:
            lines += _table(
                ("result", "probe", "scored", "abnormal", "interpretation",
                 "entered_by", "entered_at", "signal_pattern"),
                [(x["result_id"], x["probe_code"], x["cells_scored"],
                  x["cells_abnormal"], x["interpretation"], x["entered_by"],
                  x["entered_at"], x["signal_pattern"])
                 for x in o["current_results"]],
                indent="    ",
            )
        stored = o["stored_validation_errors"]
        lines.append(f"  stored validation findings ({len(stored)}):")
        if stored:
            lines += _table(
                ("error", "severity", "rule_code", "created_at", "message"),
                [(v["error_id"], v["severity"], v["rule_code"], v["created_at"],
                  v["message"]) for v in stored],
                indent="    ",
            )
        rep = o["report"]
        if rep is None:
            lines.append("  report: none")
        else:
            lines.append(
                f"  report {rep['report_id']}: {rep['status']} by "
                f"{rep['finalized_by'] or '-'} at {rep['finalized_at'] or '-'} "
                f"(summary sha256 {rep['summary_sha256']})"
            )

    lines += _section("messages", len(case["messages"]))
    for m in case["messages"]:
        lines.append(
            f"message {m['message_id']}  {m['direction']} {m['format']} "
            f"{m['message_type']}  status {m['status']}  order "
            f"{_status(m['order_id'])}  control_id {m['control_id'] or '-'}  "
            f"created {m['created_at']}"
        )
        lines.append(f"  roles: {', '.join(m['roles']) or '-'}")
        accession = m.get("payload_accession")
        lines.append(
            f"  sha256 {m['payload_sha256']}  bytes {m['payload_bytes']}"
            + (f"  payload accession {accession}" if accession else "")
        )

    lines += _section("queue items", len(case["queue_items"]))
    for q in case["queue_items"]:
        match = q["raw_payload_matches_original"]
        lines.append(
            f"queue {q['queue_id']}  message {_status(q['message_id'])}  status "
            f"{q['status']}  {q['failure_code']} / {q['failure_category']} / "
            f"{q['recovery_policy']}"
        )
        lines.append(
            f"  created {q['created_at']}  resolved {q['resolved_at'] or '-'}  "
            f"terminal {q['terminal_at'] or '-'}"
        )
        lines.append(
            f"  raw_payload sha256 {_status(q['raw_payload_sha256'])}  matches "
            f"original: {'unknown' if match is None else ('yes' if match else 'NO')}"
        )
        lines.append(f"  reason: {q['reason']}")

    attempts = case["recovery_attempts"]
    lines += _section("recovery attempts", len(attempts))
    if attempts:
        lines += _table(
            ("attempt", "queue", "request_id", "action", "outcome", "actor",
             "result_msg", "attempted_at", "payload_sha256"),
            [(a["attempt_id"], a["queue_id"], a["request_id"], a["action"],
              a["outcome"], a["actor"], a["resulting_message_id"],
              a["attempted_at"], a["payload_sha256"]) for a in attempts],
        )
        for a in attempts:
            lines.append(f"  attempt {a['attempt_id']}: {a['outcome_detail']}")

    lines += _section("links", len(case["links"]))
    if case["links"]:
        lines += _table(
            ("from", "to", "basis", "relation"),
            [(k["from"], k["to"], k["basis"], k["relation"]) for k in case["links"]],
        )

    lines += _section("timeline", len(case["timeline"]))
    lines.append("  (order: timestamp, record-type rank, table, id; presentational, not causal)")
    if case["timeline"]:
        lines += _table(
            ("at", "record", "kind", "summary"),
            [(e["at"], f"{e['table']}:{e['id']}", e["kind"], e["summary"])
             for e in case["timeline"]],
        )

    lines += _section("provenance notes", len(case["provenance"]))
    for n in case["provenance"]:
        lines.append(f"  [{n['code']}] {n['subject']}")
        lines += _wrap(n["detail"])

    lines += _section("limitations", len(case["limitations"]))
    for lim in case["limitations"]:
        lines.append(f"  [{lim['code']}]")
        lines += _wrap(lim["text"])
    return lines


def _render_findings(findings: Sequence[dict[str, Any]]) -> list[str]:
    if not findings:
        return ["  no findings"]
    lines = _table(
        ("severity", "rule_id", "subjects", "title"),
        [(f["severity"], f["rule_id"], ", ".join(f["subjects"]), f["title"])
         for f in findings],
    )
    lines.append("")
    lines.append("observations:")
    for f in findings:
        lines.append(f"  {f['rule_id']} [{', '.join(f['subjects'])}]")
        lines += _wrap(f"rule: {f['documented_rule']}", hang="  ")
        for obs in f.get("observations", []):
            lines += _wrap(f"- {obs}", hang="  ")
        if f.get("ambiguity"):
            lines += _wrap(f"ambiguity: {f['ambiguity']}", hang="  ")
    return lines


def _render_reconcile(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    scope = r["scope"]
    where = "whole database" if scope["kind"] == "database" else (
        f"case of {scope['kind']} {scope['id']}"
    )
    s = r["summary"]
    return [
        f"reconciliation ({where}): ERROR {s.get('ERROR', 0)}, WARNING "
        f"{s.get('WARNING', 0)}, INFO {s.get('INFO', 0)}",
        "rules are educational invariants from this repository's design and "
        "requirements, not clinical requirements",
    ] + _render_findings(r["findings"])


def _change_lines(counts: dict[str, dict[str, int]]) -> list[str]:
    if not counts:
        return ["  no table changes"]
    return _table(
        ("table", "inserted", "updated", "deleted"),
        [(t, c["inserted"], c["updated"], c["deleted"])
         for t, c in sorted(counts.items())],
    )


def _render_recover_preview(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    req, sim, snap = r["request"], r["simulated"], r["snapshot"]
    lines = [
        f"PREVIEW (nothing written): {req['action']} on queue {req['queue_id']} "
        f"request {req['request_id']} by {req['actor']} -> simulated {sim['status']}"
        + (f" (recorded outcome {sim['recorded_outcome']})"
           if sim["status"] == "REPLAYED" else ""),
        f"queue status would go {sim['queue_status_before']} -> "
        f"{sim['queue_status_after']}",
    ]
    a = sim.get("attempt")
    if a is not None:
        lines.append(
            f"simulated attempt {a['attempt_id']}: {a['outcome']} ({a['action']}) "
            f"-> message {_status(a['resulting_message_id'])}"
        )
        lines += _wrap(a["outcome_detail"], indent="  ")
    if sim.get("error"):
        lines.append(f"service said: {sim['error']}")
    if req.get("corrected_payload_sha256"):
        lines.append(f"corrected payload sha256 {req['corrected_payload_sha256']}")
    lines.append("simulated row changes:")
    lines += _change_lines(r["change_counts"])
    lines.append(
        f"source database unchanged: {'yes' if snap['source_unchanged'] else 'NO'} "
        f"(sha256 {snap['source_sha256_before']})"
    )
    lines += _wrap(r["notice"], indent="")
    return lines


def _render_recover_apply(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    a = r.get("attempt")
    if a is None:
        kind = getattr(ns, "_failure_kind", None)
        label = "REQUEST_ID_CONFLICT" if kind == "REQUEST_ID_CONFLICT" else "NOT APPLIED"
        return [
            f"request {r['request_id']} ({r['action']}) on queue {r['queue_id']}: "
            f"{label} -> no recovery attempt or message recorded; queue "
            f"{r['queue_id']} {r['queue_status']}"
        ]
    if a["resulting_message_id"] is None:
        target = "no message"
    else:
        target = (
            f"message {a['resulting_message_id']} "
            f"{_status(r['resulting_message_status'])}"
        )
    line = (
        f"recovery attempt {a['attempt_id']}: {a['outcome']} ({a['action']}) -> "
        f"{target}; queue {r['queue_id']} {r['queue_status']}"
    )
    lines = [line]
    if r["replayed"]:
        lines.append(
            "replayed: request_id was already recorded for this exact request; the "
            "recorded attempt was returned and nothing was written"
        )
    lines += _wrap(a["outcome_detail"], indent="  ")
    lines.append(f"payload sha256 {a['payload_sha256']}  request {a['request_id']}  actor {a['actor']}")
    return lines


def _render_recover_history(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    lines = [
        f"queue {r['queue_id']}: status {r['queue_status']}  failure_code "
        f"{r['failure_code']}  attempts {len(r['attempts'])}  request-id "
        f"conflicts {len(r['conflict_audit_events'])}",
    ]
    lines += _section("recovery attempts, attempt_id order", len(r["attempts"]))
    if r["attempts"]:
        lines += _table(
            ("attempt", "request_id", "action", "outcome", "actor", "result_msg",
             "attempted_at"),
            [(a["attempt_id"], a["request_id"], a["action"], a["outcome"],
              a["actor"], a["resulting_message_id"], a["attempted_at"])
             for a in r["attempts"]],
        )
        for a in r["attempts"]:
            lines.append(f"  attempt {a['attempt_id']}: {a['outcome_detail']}")
    else:
        lines.append("  none")
    lines += _section(
        "REQUEST_ID_CONFLICT audit events, no attempt rows",
        len(r["conflict_audit_events"]),
    )
    if r["conflict_audit_events"]:
        lines += _table(
            ("event", "created_at", "actor", "detail"),
            [(e["event_id"], e["created_at"], e["actor"], e["detail"])
             for e in r["conflict_audit_events"]],
        )
    else:
        lines.append("  none")
    return lines


def _render_export(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    lines = [
        f"{r['format']} export for order {r['order_id']}: sha256 "
        f"{r['payload_sha256']} ({r['payload_bytes']} bytes)",
        "stored as interface_message "
        + (str(r["stored_message_id"]) if r["stored_message_id"] is not None
           else "- (not stored; use --store)"),
    ]
    if r["out"] is not None:
        lines.append(f"written to {r['out']} (exact bytes)")
    else:
        lines.append(
            "payload (segment terminators shown as line breaks; use --out FILE for "
            "exact bytes):"
        )
        lines += _payload_lines(r["payload"])
    return lines


def _render_audit(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    lines = [
        f"audit trail for order {r['order_id']} ({r['accession_number']}): "
        f"{r['count']} event(s), event_id order"
    ]
    if r["events"]:
        lines += _table(
            ("event", "created_at", "entity", "action", "actor", "detail"),
            [(e["event_id"], e["created_at"], f"{e['entity_type']}:{e['entity_id']}",
              e["action"], e["actor"], e["detail"]) for e in r["events"]],
        )
    return lines


def _render_evidence(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    anchor = r.get("anchor") or {}
    lines = [
        f"case evidence for {anchor.get('kind', '-')} {anchor.get('id', '-')}: "
        f"content fingerprint {r['content_fingerprint']}",
        "counts: " + ", ".join(f"{k} {v}" for k, v in sorted(r["counts"].items())),
    ]
    summary = r.get("reconciliation_summary")
    if isinstance(summary, dict):
        lines.append(
            f"reconciliation: ERROR {summary.get('ERROR', 0)}, WARNING "
            f"{summary.get('WARNING', 0)}, INFO {summary.get('INFO', 0)}"
        )
    lines.append(f"evidence JSON: {r['out'] or '- (use --out FILE.json)'}")
    lines.append(f"report: {r['report'] or '- (use --report FILE.md)'}")
    report_text = getattr(ns, "_report_text", None)
    if r["report"] is None and report_text:
        lines.append("")
        lines += report_text.rstrip("\n").split("\n")
    return lines


def _render_exercise_list(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    lines = [f"{r['count']} synthetic incident exercise(s):"]
    return lines + _table(
        ("id", "title"),
        [(e.get("id"), e.get("title")) for e in r["exercises"]],
    )


def _render_exercise_show(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    lines = [f"exercise {r['id']}: {r.get('title') or '-'}"]
    if r.get("summary"):
        lines += _wrap(r["summary"], indent="  ")
    lines.append("")
    return lines + str(r.get("instructions") or "").rstrip("\n").split("\n")


def _step_lines(steps: Any) -> list[str]:
    steps = [s for s in (steps or []) if isinstance(s, dict)]
    if not steps:
        return ["  no steps"]
    return _table(
        ("step", "exit", "expected", "matched", "command"),
        [(i, s.get("exit_code"), s.get("expect_exit"), s.get("matched"),
          " ".join(str(a) for a in s.get("argv", [])))
         for i, s in enumerate(steps, start=1)],
    )


def _check_lines(verification: Any) -> list[str]:
    if not isinstance(verification, dict):
        return ["  not run"]
    checks = [c for c in verification.get("checks", []) if isinstance(c, dict)]
    lines = [f"  passed: {'yes' if verification.get('passed') else 'NO'} "
             f"({len(checks)} check(s))"]
    failed = [c for c in checks if not c.get("passed")]
    if checks:
        lines += _table(
            ("passed", "check"),
            [(c.get("passed"), c.get("name")) for c in checks],
        )
    for c in failed:
        lines.append(f"  FAILED {c.get('name')}: expected {c.get('expected')!r}, "
                     f"observed {c.get('observed')!r}")
    return lines


def _render_exercise_setup(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [
        f"exercise {r.get('exercise', ns.exercise_id)} set up in "
        f"{r.get('database', ns.db)}"
    ] + _step_lines(r.get("steps"))


def _render_exercise_run(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return (
        [f"exercise {r.get('exercise', ns.exercise_id)} run in "
         f"{r.get('database', ns.db)}", "steps:"]
        + _step_lines(r.get("steps"))
        + ["verification:"]
        + _check_lines(r.get("verification"))
    )


def _render_exercise_verify(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    return [f"exercise {r.get('exercise', ns.exercise_id)} verification "
            f"(read-only):"] + _check_lines(r)


def _render_exercise_run_all(r: dict[str, Any], ns: argparse.Namespace) -> list[str]:
    rows = []
    for e in r.get("exercises", []):
        if not isinstance(e, dict):
            continue
        verification = e.get("verification")
        passed = e.get("passed")
        if passed is None and isinstance(verification, dict):
            passed = verification.get("passed")
        rows.append((e.get("exercise") or e.get("id"), passed, e.get("database")))
    return [f"all exercises passed: {'yes' if r.get('passed') else 'NO'}"] + _table(
        ("exercise", "passed", "database"), rows
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

Handler = Callable[[argparse.Namespace], dict[str, Any]]
Renderer = Callable[[dict[str, Any], argparse.Namespace], list[str]]


def _describe(text: str) -> str:
    """Pre-wrapped help description followed by the synthetic notice."""
    return textwrap.fill(text, width=79) + "\n\n" + textwrap.fill(
        SYNTHETIC_NOTICE, width=79
    )


def _leaf(
    sub: Any,
    name: str,
    words: str,
    handler: Handler,
    renderer: Renderer,
    help_text: str,
    mode: str,
) -> argparse.ArgumentParser:
    parser = sub.add_parser(
        name,
        help=f"{help_text} [{mode}]",
        description=_describe(f"{help_text} Database access: {mode}."),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.set_defaults(_handler=handler, _renderer=renderer, _words=words)
    return parser


def _db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, metavar="PATH",
                        help="CytoBridge SQLite database file")


def build_parser() -> argparse.ArgumentParser:
    """Build the ``python -m src.analyst`` argument parser."""
    parser = _ArgumentParser(
        prog=PROG,
        description=_describe(
            "CytoBridge analyst investigation and recovery lab: a headless CLI "
            "over the existing CytoBridge services."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    parser.add_argument(
        JSON_FLAG, action="store_true",
        help="print exactly one JSON envelope (accepted anywhere in argv)",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>", required=True)

    p = _leaf(sub, "init", "init", _cmd_init, _render_init,
              "Create a NEW database file and load schema.sql.", "create")
    _db(p)

    p = _leaf(sub, "case-create", "case-create", _cmd_case_create, _render_case_create,
              "Register a synthetic patient, order, and received (and by default "
              "accessioned) specimen.", "read-write")
    _db(p)
    p.add_argument("--mrn", required=True)
    p.add_argument("--last-name", required=True)
    p.add_argument("--first-name", required=True)
    p.add_argument("--dob", required=True, metavar="YYYY-MM-DD")
    p.add_argument("--sex", required=True, choices=("M", "F", "U"))
    p.add_argument("--accession", required=True)
    p.add_argument("--provider", required=True)
    p.add_argument("--priority", choices=("ROUTINE", "STAT"), default="ROUTINE")
    p.add_argument("--specimen-type", choices=("BONE_MARROW", "PERIPHERAL_BLOOD"),
                   default="BONE_MARROW")
    p.add_argument("--external-specimen-id", default=None)
    p.add_argument("--collected-at", default=None)
    p.add_argument("--no-accession", action="store_true",
                   help="leave the specimen RECEIVED (not accessioned)")

    p = _leaf(sub, "specimen-accession", "specimen-accession", _cmd_specimen_accession,
              _render_specimen_accession, "Accession a RECEIVED specimen.", "read-write")
    _db(p)
    p.add_argument("--specimen-id", required=True, type=int, metavar="N")

    p = _leaf(sub, "result-enter", "result-enter", _cmd_result_enter,
              _render_result_enter,
              "Enter (upsert) one structured per-probe FISH result.", "read-write")
    _db(p)
    p.add_argument("--order-id", required=True, type=int, metavar="N")
    p.add_argument("--probe", required=True, metavar="CODE")
    p.add_argument("--scored", required=True, type=int, metavar="N")
    p.add_argument("--abnormal", required=True, type=int, metavar="N")
    p.add_argument("--pattern", required=True, metavar="TEXT")
    p.add_argument("--interpretation", required=True,
                   choices=("NORMAL", "ABNORMAL", "INDETERMINATE"))
    p.add_argument("--entered-by", required=True, metavar="WHO")

    p = _leaf(sub, "finalize", "finalize", _cmd_finalize, _render_finalize,
              "Validate and finalize an order (blocked by ERROR findings).",
              "read-write")
    _db(p)
    p.add_argument("--order-id", required=True, type=int, metavar="N")
    p.add_argument("--by", required=True, metavar="WHO")

    p = _leaf(sub, "ingest", "ingest", _cmd_ingest, _render_ingest,
              "Ingest one inbound HL7-style message file exactly as its bytes "
              "(strict UTF-8, no newline translation).", "read-write")
    _db(p)
    p.add_argument("--file", required=True, metavar="PATH")
    p.add_argument("--actor", default="interface", metavar="WHO")

    p = _leaf(sub, "worklist", "worklist", _cmd_worklist, _render_worklist,
              "List error-queue items, oldest first.", "read-only")
    _db(p)
    p.add_argument("--status", choices=("OPEN", "RESOLVED", "TERMINAL", "ALL"),
                   default="OPEN")

    p = _leaf(sub, "show-message", "show-message", _cmd_show_message,
              _render_show_message, "Show one stored interface message.", "read-only")
    _db(p)
    p.add_argument("--message-id", required=True, type=int, metavar="N")
    p.add_argument("--raw", action="store_true",
                   help="text mode: write only the exact stored payload bytes")

    p = _leaf(sub, "trace", "trace", _cmd_trace, _render_trace,
              "Build the case view for an order, queue item, message, or accession.",
              "read-only")
    _db(p)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--order-id", type=int, metavar="N")
    group.add_argument("--queue-id", type=int, metavar="N")
    group.add_argument("--message-id", type=int, metavar="N")
    group.add_argument("--accession", metavar="ACC")

    p = _leaf(sub, "reconcile", "reconcile", _cmd_reconcile, _render_reconcile,
              "Run reconciliation checks (exit 1 when any ERROR finding exists).",
              "read-only")
    _db(p)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--order-id", type=int, metavar="N")
    group.add_argument("--queue-id", type=int, metavar="N")

    recover = sub.add_parser(
        "recover",
        help="Recovery requests: preview [read-only], retry/redrive [read-write], "
             "history [read-only].",
        description=_describe(
            "Recovery requests through the unchanged v1.1 recovery service."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    rsub = recover.add_subparsers(dest="recover_command", metavar="<action>",
                                  required=True)

    p = _leaf(rsub, "preview", "recover preview", _cmd_recover_preview,
              _render_recover_preview,
              "Simulate a retry/redrive on an isolated in-memory copy (exit 0 only "
              "when it would SUCCEED).", "read-only source")
    _db(p)
    p.add_argument("--queue-id", required=True, type=int, metavar="N")
    p.add_argument("--action", required=True, choices=tuple(ACTION_CHOICES))
    p.add_argument("--file", metavar="PATH",
                   help="corrected payload (required for redrive, refused for retry)")
    p.add_argument("--request-id", required=True, metavar="ID")
    p.add_argument("--actor", required=True, metavar="WHO")

    p = _leaf(rsub, "retry", "recover retry", _cmd_recover_apply,
              _render_recover_apply,
              "RETRY_ORIGINAL: re-process the stored original payload unchanged.",
              "read-write")
    _db(p)
    p.add_argument("--queue-id", required=True, type=int, metavar="N")
    p.add_argument("--request-id", required=True, metavar="ID")
    p.add_argument("--actor", required=True, metavar="WHO")

    p = _leaf(rsub, "redrive", "recover redrive", _cmd_recover_apply,
              _render_recover_apply,
              "REDRIVE_CORRECTED: process a corrected payload file exactly as its "
              "bytes.", "read-write")
    _db(p)
    p.add_argument("--queue-id", required=True, type=int, metavar="N")
    p.add_argument("--file", required=True, metavar="PATH")
    p.add_argument("--request-id", required=True, metavar="ID")
    p.add_argument("--actor", required=True, metavar="WHO")

    p = _leaf(rsub, "history", "recover history", _cmd_recover_history,
              _render_recover_history,
              "Show recovery attempts and REQUEST_ID_CONFLICT audit events for a "
              "queue item.", "read-only")
    _db(p)
    p.add_argument("--queue-id", required=True, type=int, metavar="N")

    p = _leaf(sub, "export", "export", _cmd_export, _render_export,
              "Generate the outbound HL7- or FHIR-style message for a FINALIZED "
              "order.", "read-only, or read-write with --store")
    _db(p)
    p.add_argument("--order-id", required=True, type=int, metavar="N")
    p.add_argument("--format", required=True, type=str.lower, choices=("hl7", "fhir"))
    p.add_argument("--store", action="store_true",
                   help="persist as an OUTBOUND interface_message (read-write)")
    p.add_argument("--generated-at", metavar='"YYYY-MM-DD HH:MM:SS"')
    p.add_argument("--out", metavar="FILE", help="write exact payload bytes to a NEW file")

    p = _leaf(sub, "audit", "audit", _cmd_audit, _render_audit,
              "Show the audit trail for one order (queries/audit_lookup.sql).",
              "read-only")
    _db(p)
    p.add_argument("--order-id", required=True, type=int, metavar="N")

    p = _leaf(sub, "evidence", "evidence", _cmd_evidence, _render_evidence,
              "Build portable, deterministic case evidence (JSON and a short "
              "report).", "read-only")
    _db(p)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--queue-id", type=int, metavar="N")
    group.add_argument("--order-id", type=int, metavar="N")
    p.add_argument("--scenario", metavar="ID")
    p.add_argument("--title", metavar="T")
    p.add_argument("--source-revision", metavar="REV")
    p.add_argument("--out", metavar="FILE.json", help="write evidence JSON to a NEW file")
    p.add_argument("--report", metavar="FILE.md", help="write the report to a NEW file")

    exercise = sub.add_parser(
        "exercise",
        help="Synthetic incident exercises: list/show [no db], setup/run [create], "
             "verify [read-only], run-all [create].",
        description=_describe("Executable synthetic incident exercises."),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        allow_abbrev=False,
    )
    esub = exercise.add_subparsers(dest="exercise_command", metavar="<action>",
                                   required=True)
    _leaf(esub, "list", "exercise list", _cmd_exercise_list, _render_exercise_list,
          "List the exercises.", "none")
    p = _leaf(esub, "show", "exercise show", _cmd_exercise_show, _render_exercise_show,
              "Show one exercise's analyst instructions.", "none")
    p.add_argument("exercise_id", metavar="ID")
    p = _leaf(esub, "setup", "exercise setup", _cmd_exercise_setup,
              _render_exercise_setup,
              "Create a NEW database and run the exercise setup steps.", "create")
    p.add_argument("exercise_id", metavar="ID")
    _db(p)
    p = _leaf(esub, "run", "exercise run", _cmd_exercise_run, _render_exercise_run,
              "Set up, run the solution steps, and verify (exit 1 on failure).",
              "create")
    p.add_argument("exercise_id", metavar="ID")
    _db(p)
    p = _leaf(esub, "verify", "exercise verify", _cmd_exercise_verify,
              _render_exercise_verify,
              "Verify a database against the exercise's expected outcome (exit 1 "
              "on failure).", "read-only")
    p.add_argument("exercise_id", metavar="ID")
    _db(p)
    p = _leaf(esub, "run-all", "exercise run-all", _cmd_exercise_run_all,
              _render_exercise_run_all,
              "Run every exercise in NEW databases under --workdir.", "create")
    p.add_argument("--workdir", required=True, metavar="DIR")
    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _guess_words(args: Sequence[str]) -> str:
    """Best-effort command words for an envelope when parsing fails."""
    positional = [a for a in args if not a.startswith("-")]
    if not positional or positional[0] not in _TOP_COMMANDS:
        return ""
    first = positional[0]
    nested = _NESTED_COMMANDS.get(first)
    if nested and len(positional) > 1 and positional[1] in nested:
        return f"{first} {positional[1]}"
    return first


def _envelope(
    words: str, exit_code: int, result: Any, error: dict[str, str] | None
) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "command": words,
        "ok": exit_code == EXIT_OK,
        "exit_code": exit_code,
        "result": result,
        "error": error,
        "synthetic": True,
    }


def _render_text(
    ns: argparse.Namespace | None, result: Any, error: dict[str, str] | None
) -> None:
    if result is not None and ns is not None:
        if getattr(ns, "_words", "") == "show-message" and getattr(ns, "raw", False):
            _emit_bytes(sys.stdout, result["payload"].encode("utf-8"))
        else:
            lines = [SYNTHETIC_NOTICE] + ns._renderer(result, ns)
            _emit_text(sys.stdout, "\n".join(lines) + "\n")
    if error is not None:
        _emit_text(sys.stderr, f"error: {error['message']}\n")


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and return its exit code (never calls ``sys.exit``)."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    json_mode = JSON_FLAG in raw_args
    args = [a for a in raw_args if a != JSON_FLAG]
    words = _guess_words(args)
    ns: argparse.Namespace | None = None
    result: Any = None
    error: dict[str, str] | None = None
    exit_code = EXIT_OK
    try:
        ns = build_parser().parse_args(args)
        words = ns._words
        ns.json_mode = json_mode
        result = ns._handler(ns)
    except _ParserExit as exc:
        return exc.status
    except AnalystError as exc:
        exit_code = exc.exit_code
        error = {"type": exc.kind, "message": exc.message}
        result = exc.result
    except Exception as exc:  # unexpected: report it, never a bare traceback
        exit_code = EXIT_INTERNAL
        error = {"type": "INTERNAL", "message": f"{type(exc).__name__}: {exc}"}
        result = None

    try:
        if json_mode:
            text = canonical_json(_envelope(words, exit_code, result, error))
            _emit_text(sys.stdout, text)
        else:
            _render_text(ns, result, error)
    except Exception as exc:  # rendering must not turn into a traceback either
        exit_code = EXIT_INTERNAL
        error = {
            "type": "INTERNAL",
            "message": f"output rendering failed: {type(exc).__name__}: {exc}",
        }
        if json_mode:
            _emit_text(sys.stdout, canonical_json(_envelope(words, exit_code, None, error)))
        else:
            _emit_text(sys.stderr, f"error: {error['message']}\n")
    return exit_code
