"""Non-mutating recovery preview for the CytoBridge analyst lab.

``preview_recovery`` answers "what would this retry / re-drive request record
if it were applied right now?" without changing the database an analyst is
investigating:

1. The source file is opened read-only (``store.open_read_only``: SQLite
   ``mode=ro`` plus ``PRAGMA query_only``) and copied in one consistent step
   into a private ``:memory:`` database with the ``sqlite3`` backup API.
2. The UNCHANGED public v1.1 recovery service (``recovery.retry_queue_item`` /
   ``recovery.redrive_queue_item``) runs against that in-memory copy. Request-id
   replay/conflict resolution, classification validation, eligibility, the
   single transaction, handled-failure rollback, and dynamic OPEN -> TERMINAL
   behavior are therefore exactly the service's own; nothing is re-implemented
   or re-interpreted here.
3. Every CytoBridge table in the copy is snapshotted before and after the call
   and the row-level differences are reported.

The source is never written: its ``request_id`` is not consumed, and no
attempt, message, result, queue change, or audit row (including a
REQUEST_ID_CONFLICT audit event) reaches it. The source file's SHA-256 is
recorded before and after as evidence. A preview simulates one snapshot; it is
not an authorization token or a guarantee. Applying a request later re-invokes
the recovery service against whatever state exists at that time (see
``LIMITATIONS["PREVIEW_SNAPSHOT"]``).

Educational, synthetic simulator only. All data is synthetic. No PHI.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Any

from .. import recovery
from . import store
from .common import LIMITATIONS, AnalystError, sha256_file, sha256_text

# Every table the preview snapshots and diffs: all CytoBridge tables.
PREVIEW_TABLES: tuple[str, ...] = tuple(store.REQUIRED_TABLES)

# The two recovery actions of the frozen v1.1 service boundary (design sec 7).
RETRY_ORIGINAL = "RETRY_ORIGINAL"
REDRIVE_CORRECTED = "REDRIVE_CORRECTED"
ACTIONS: tuple[str, ...] = (RETRY_ORIGINAL, REDRIVE_CORRECTED)

SNAPSHOT_METHOD = "sqlite3 backup API into an isolated in-memory database"

# Columns whose values are SQLite datetime('now') stamps (or caller-supplied
# times) and therefore differ between two otherwise identical runs. Replaced by
# ``TIMESTAMP_PLACEHOLDER`` in ``normalize_changes``.
TIMESTAMP_COLUMNS: frozenset[str] = frozenset({
    "created_at",
    "attempted_at",
    "resolved_at",
    "terminal_at",
    "entered_at",
    "ordered_at",
    "finalized_at",
    "received_at",
})
TIMESTAMP_PLACEHOLDER = "<timestamp>"

# Row dict: column name -> stored value.
Row = dict[str, Any]
# Snapshot: table -> {row key -> row dict}.
Snapshot = dict[str, dict[int, Row]]


# ---------------------------------------------------------------------------
# Snapshots and diffs
# ---------------------------------------------------------------------------

def _key_column(conn: sqlite3.Connection, table: str) -> str:
    """Return the table's INTEGER PRIMARY KEY column, or ``rowid`` if it has none.

    Every CytoBridge table declares an ``INTEGER PRIMARY KEY`` (an alias of the
    SQLite rowid), so the key is that column.
    """
    columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    pk = [c for c in columns if c[5]]
    if len(pk) == 1 and str(pk[0][2]).upper() == "INTEGER":
        return str(pk[0][1])
    return "rowid"


def snapshot_tables(
    conn: sqlite3.Connection, tables: tuple[str, ...] = PREVIEW_TABLES
) -> Snapshot:
    """Read every row of ``tables`` into ``{table: {key: row dict}}``.

    Rows are keyed by the integer primary key (rowid alias) and hold every
    column. Works with any row factory. Read-only.
    """
    snapshot: Snapshot = {}
    for table in tables:
        key = _key_column(conn, table)
        cursor = conn.execute(
            f'SELECT "{key}", * FROM "{table}" ORDER BY "{key}"'
            if key != "rowid"
            else f'SELECT rowid, * FROM "{table}" ORDER BY rowid'
        )
        names = [d[0] for d in cursor.description][1:]
        rows: dict[int, Row] = {}
        for raw in cursor.fetchall():
            values = tuple(raw)
            rows[int(values[0])] = dict(zip(names, values[1:]))
        snapshot[table] = rows
    return snapshot


def diff_snapshots(before: Snapshot, after: Snapshot) -> dict[str, dict[str, list]]:
    """Return the row-level changes from ``before`` to ``after``.

    Shape: ``{table: {"inserted": [row], "updated": [{"key", "before",
    "after"}], "deleted": [row]}}``. Only tables with at least one change
    appear. ``updated`` entries carry only the columns whose values changed.
    Every list is sorted by row key; tables iterate in name order.
    """
    changes: dict[str, dict[str, list]] = {}
    for table in sorted(set(before) | set(after)):
        old = before.get(table, {})
        new = after.get(table, {})
        inserted = [dict(new[k]) for k in sorted(set(new) - set(old))]
        deleted = [dict(old[k]) for k in sorted(set(old) - set(new))]
        updated = []
        for k in sorted(set(old) & set(new)):
            o, n = old[k], new[k]
            changed = sorted(c for c in set(o) | set(n) if o.get(c) != n.get(c))
            if changed:
                updated.append({
                    "key": k,
                    "before": {c: o.get(c) for c in changed},
                    "after": {c: n.get(c) for c in changed},
                })
        if inserted or updated or deleted:
            changes[table] = {
                "inserted": inserted,
                "updated": updated,
                "deleted": deleted,
            }
    return changes


def _normalize_row(row: Row) -> Row:
    return {
        col: (
            TIMESTAMP_PLACEHOLDER
            if col in TIMESTAMP_COLUMNS and value is not None
            else value
        )
        for col, value in row.items()
    }


def normalize_changes(changes: dict[str, dict[str, list]]) -> dict[str, dict[str, list]]:
    """Return a copy of ``changes`` with timestamp values masked.

    Every non-null value in a column named in ``TIMESTAMP_COLUMNS`` (created_at,
    attempted_at, resolved_at, terminal_at, entered_at, ordered_at,
    finalized_at, received_at) becomes ``"<timestamp>"``; NULLs stay ``None``
    so a NULL -> stamped transition remains visible. All other columns, and the
    ``key`` of updated rows, are untouched. The input is not modified. Used to
    compare a preview's change set with a real apply from the same baseline.
    """
    out: dict[str, dict[str, list]] = {}
    for table, change in changes.items():
        out[table] = {
            "inserted": [_normalize_row(r) for r in change.get("inserted", [])],
            "updated": [
                {
                    "key": u["key"],
                    "before": _normalize_row(u["before"]),
                    "after": _normalize_row(u["after"]),
                }
                for u in change.get("updated", [])
            ],
            "deleted": [_normalize_row(r) for r in change.get("deleted", [])],
        }
    return out


def change_counts(changes: dict[str, dict[str, list]]) -> dict[str, dict[str, int]]:
    """Per-table counts of inserted / updated / deleted rows (changed tables only)."""
    return {
        table: {
            "inserted": len(change["inserted"]),
            "updated": len(change["updated"]),
            "deleted": len(change["deleted"]),
        }
        for table, change in changes.items()
    }


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------

def attempt_dict(attempt: recovery.RecoveryAttempt) -> dict[str, Any]:
    """Plain dict of one recovery attempt (the persisted row's columns)."""
    return dataclasses.asdict(attempt)


def _check_request(action: str, corrected_payload: str | None) -> None:
    if action not in ACTIONS:
        raise AnalystError(
            "USAGE",
            f"Unknown recovery action {action!r}; expected one of "
            f"{', '.join(ACTIONS)}.",
        )
    if action == RETRY_ORIGINAL and corrected_payload is not None:
        raise AnalystError(
            "USAGE",
            "RETRY_ORIGINAL reuses the stored original payload exactly and takes "
            "no corrected payload.",
        )
    if action == REDRIVE_CORRECTED and corrected_payload is None:
        raise AnalystError(
            "USAGE", "REDRIVE_CORRECTED requires a corrected payload."
        )


def _queue_status(conn: sqlite3.Connection, queue_id: int) -> str | None:
    row = conn.execute(
        "SELECT status FROM interface_error_queue WHERE queue_id = ?", (queue_id,)
    ).fetchone()
    return None if row is None else row[0]


def _attempt_id_for_request(conn: sqlite3.Connection, request_id: str) -> int | None:
    row = conn.execute(
        "SELECT attempt_id FROM interface_recovery_attempt WHERE request_id = ?",
        (request_id,),
    ).fetchone()
    return None if row is None else int(row[0])


def _isolated_copy(source_path: Path) -> tuple[sqlite3.Connection, str]:
    """Copy the source into a private in-memory database in one backup step.

    Returns ``(memory_connection, source_sha256)``. The source is opened
    read-only (``store.open_read_only``: ``mode=ro`` + ``query_only``, never
    creates a missing file) and a read transaction is held while the file is
    fingerprinted and copied, so in SQLite's default rollback-journal mode no
    writer can commit in between and the fingerprint describes exactly the
    bytes that were copied. The source connection is closed before this
    returns; the copy gets the CytoBridge connection settings (row access by
    name, foreign keys on).
    """
    src = store.open_read_only(source_path)
    try:
        src.execute("BEGIN")
        src.execute("SELECT count(*) FROM sqlite_master").fetchone()
        source_sha256 = sha256_file(source_path)
        mem = sqlite3.connect(":memory:")
        try:
            src.backup(mem)
        except BaseException:
            mem.close()
            raise
    finally:
        src.close()
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys = ON")
    return mem, source_sha256


def preview_recovery(
    source_path: str | Path,
    queue_id: int,
    *,
    action: str,
    request_id: str,
    actor: str,
    corrected_payload: str | None = None,
) -> dict[str, Any]:
    """Simulate one recovery request on an isolated in-memory copy.

    ``action`` is ``"RETRY_ORIGINAL"`` (``corrected_payload`` must be ``None``)
    or ``"REDRIVE_CORRECTED"`` (``corrected_payload`` required; an empty string
    is a payload). Argument errors raise ``AnalystError("USAGE")`` before the
    database is touched; a missing database or queue item raises
    ``AnalystError("NOT_FOUND")``; an unreadable or non-CytoBridge file raises
    ``AnalystError("INVALID_INPUT")``.

    Simulated status:

    - ``REQUEST_ID_CONFLICT``: the service raised ``RequestIdConflictError``
      (its single conflict audit row appears in ``changes`` only).
    - ``BLOCKED``: the service raised another ``RecoveryError`` (for example
      null or contradictory stored classification); nothing was recorded.
    - ``REPLAYED``: ``request_id`` was already recorded and the service
      returned that same attempt (``recorded_outcome`` is its outcome).
    - otherwise the new attempt's outcome: ``SUCCEEDED``, ``FAILED``, or
      ``REJECTED``.

    Any other exception from the service propagates unchanged (the service has
    already rolled back the in-memory copy); the copy is always discarded and
    the source is never written.
    """
    _check_request(action, corrected_payload)
    path = Path(source_path)

    # The store's read-only open raises NOT_FOUND / INVALID_INPUT and never
    # creates a missing file.
    mem, before_sha = _isolated_copy(path)
    try:
        status_before = _queue_status(mem, queue_id)
        if status_before is None:
            raise AnalystError(
                "NOT_FOUND", f"Error-queue item {queue_id} does not exist."
            )

        existing_attempt_id = _attempt_id_for_request(mem, request_id)
        before = snapshot_tables(mem)

        attempt: recovery.RecoveryAttempt | None = None
        error: str | None = None
        try:
            if action == RETRY_ORIGINAL:
                attempt = recovery.retry_queue_item(
                    mem, queue_id, request_id=request_id, actor=actor
                )
            else:
                attempt = recovery.redrive_queue_item(
                    mem,
                    queue_id,
                    corrected_payload,  # type: ignore[arg-type]
                    request_id=request_id,
                    actor=actor,
                )
        except recovery.RequestIdConflictError as exc:
            status, error = "REQUEST_ID_CONFLICT", str(exc)
        except recovery.RecoveryError as exc:
            status, error = "BLOCKED", str(exc)
        else:
            if (
                existing_attempt_id is not None
                and attempt.attempt_id == existing_attempt_id
            ):
                status = "REPLAYED"
            else:
                status = attempt.outcome

        after = snapshot_tables(mem)
        status_after = _queue_status(mem, queue_id)
    finally:
        mem.close()

    after_sha = sha256_file(path)
    changes = diff_snapshots(before, after)
    return {
        "mode": "PREVIEW",
        "request": {
            "queue_id": queue_id,
            "action": action,
            "request_id": request_id,
            "actor": actor,
            "corrected_payload_sha256": (
                None if corrected_payload is None else sha256_text(corrected_payload)
            ),
        },
        "snapshot": {
            "method": SNAPSHOT_METHOD,
            "source_sha256_before": before_sha,
            "source_sha256_after": after_sha,
            "source_unchanged": before_sha == after_sha,
        },
        "simulated": {
            "status": status,
            "recorded_outcome": None if attempt is None else attempt.outcome,
            "attempt": None if attempt is None else attempt_dict(attempt),
            "error": error,
            "queue_status_before": status_before,
            "queue_status_after": status_after,
        },
        "changes": changes,
        "change_counts": change_counts(changes),
        "notice": LIMITATIONS["PREVIEW_SNAPSHOT"],
    }
