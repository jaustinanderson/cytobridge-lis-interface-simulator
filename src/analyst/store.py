"""Database access modes for the CytoBridge analyst lab.

Three explicit ways to reach a database file, so that what a command can do is
decided by how it opens the file rather than by convention:

- ``init_database``   create a NEW database file and load ``schema.sql``. It
  refuses any path that already exists (file, directory, or symlink) and never
  replaces or truncates it.
- ``open_read_only``  open an EXISTING database with SQLite ``mode=ro`` plus
  ``PRAGMA query_only``. It never creates a missing file and cannot write.
- ``open_read_write`` open an EXISTING database with SQLite ``mode=rw``, which
  never creates a missing file. Writes are made only by the existing services.

Both open modes verify the file is an initialized CytoBridge database, refuse a
WAL-mode file (CytoBridge never enables WAL, and WAL would create side files),
and turn low-level SQLite failures into ``AnalystError`` with a useful message.
All data is synthetic. No PHI.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .. import db
from .common import AnalystError

# Every table schema.sql creates. A database missing any of them is not a
# CytoBridge database this lab can investigate.
REQUIRED_TABLES = (
    "audit_event",
    "fish_result",
    "interface_error_queue",
    "interface_message",
    "interface_recovery_attempt",
    "lab_order",
    "panel",
    "patient",
    "probe",
    "report",
    "specimen",
    "validation_error",
)


def _uri(path: Path, mode: str) -> str:
    """SQLite URI for an absolute path with the given open mode."""
    return f"{path.resolve().as_uri()}?mode={mode}"


def verify_schema(conn: sqlite3.Connection, label: str = "database") -> None:
    """Raise ``AnalystError`` unless every CytoBridge table is present."""
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    missing = sorted(set(REQUIRED_TABLES) - names)
    if missing:
        raise AnalystError(
            "INVALID_INPUT",
            f"{label} is not an initialized CytoBridge database "
            f"(missing tables: {', '.join(missing)}).",
        )


def _existing_file(path: str | Path) -> Path:
    p = Path(path)
    if not p.exists():
        raise AnalystError(
            "NOT_FOUND",
            f"Database file {p} does not exist. Create a new one with: "
            f"python -m src.analyst init --db {p}",
        )
    if not p.is_file():
        raise AnalystError("INVALID_INPUT", f"Database path {p} is not a regular file.")
    _refuse_wal(p)
    return p


def _refuse_wal(p: Path) -> None:
    """Refuse a WAL-mode database by reading its header bytes only.

    CytoBridge creates rollback-journal databases and never enables WAL. A WAL
    database would make SQLite create ``-wal``/``-shm`` side files even for a
    read-only open, and committed data in the ``-wal`` file would not be covered
    by a main-file fingerprint, so the lab refuses it before connecting. Bytes
    18 and 19 of the SQLite header are the write/read format versions; 2 means
    WAL.
    """
    try:
        with p.open("rb") as fh:
            header = fh.read(20)
    except OSError as exc:
        raise AnalystError("INVALID_INPUT", f"Cannot read database {p}: {exc}.")
    if header[:16] == b"SQLite format 3\x00" and 2 in header[18:20]:
        raise AnalystError(
            "INVALID_INPUT",
            f"Database {p} is in SQLite WAL mode. The analyst lab works only on "
            f"rollback-journal databases (the mode CytoBridge creates); it was not "
            f"opened.",
        )


def _open(path: str | Path, mode: str) -> sqlite3.Connection:
    p = _existing_file(path)
    try:
        conn = sqlite3.connect(_uri(p, mode), uri=True)
    except sqlite3.Error as exc:
        raise AnalystError("INVALID_INPUT", f"Cannot open database {p}: {exc}.")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if mode == "ro":
            conn.execute("PRAGMA query_only = ON")
        verify_schema(conn, label=f"Database {p}")
    except sqlite3.DatabaseError as exc:
        conn.close()
        raise AnalystError(
            "INVALID_INPUT", f"Database {p} is not a readable SQLite database ({exc})."
        )
    except AnalystError:
        conn.close()
        raise
    return conn


def open_read_only(path: str | Path, *, snapshot: bool = False) -> sqlite3.Connection:
    """Open an existing CytoBridge database read-only (never creates, never writes).

    With ``snapshot=True`` the connection begins a read transaction, so every
    later query sees one consistent state even if another process commits in
    the meantime; closing the connection ends it. Multi-query views (trace,
    reconcile, evidence, verification) use this so they never mix two states.
    """
    conn = _open(path, "ro")
    if snapshot:
        conn.execute("BEGIN")
    return conn


def open_read_write(path: str | Path) -> sqlite3.Connection:
    """Open an existing CytoBridge database for service writes (never creates)."""
    return _open(path, "rw")


def init_database(path: str | Path) -> Path:
    """Create a new CytoBridge database at ``path`` and return the path.

    Refuses (``AnalystError`` kind ``REFUSED``) when anything already exists at
    ``path``; the existing file is left byte-for-byte untouched. The file is
    created with ``O_EXCL`` so a concurrent creator cannot be overwritten. If
    schema loading fails, the file this call just created is removed.
    """
    p = Path(path)
    if p.exists() or p.is_symlink():
        raise AnalystError(
            "REFUSED",
            f"Refusing to initialize {p}: the path already exists. init never "
            f"replaces an existing file; choose a new path.",
        )
    parent = p.parent if str(p.parent) else Path(".")
    if not parent.is_dir():
        raise AnalystError("NOT_FOUND", f"Directory {parent} does not exist.")
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        raise AnalystError(
            "REFUSED",
            f"Refusing to initialize {p}: the path already exists.",
        )
    except OSError as exc:
        raise AnalystError("INVALID_INPUT", f"Cannot create {p}: {exc}.")
    os.close(fd)
    try:
        conn = sqlite3.connect(_uri(p, "rw"), uri=True)
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            db.init_db(conn)
        finally:
            conn.close()
    except BaseException:
        # Only ever removes the empty file this call created exclusively above.
        p.unlink(missing_ok=True)
        raise
    return p
