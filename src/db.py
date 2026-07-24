"""SQLite helpers for CytoBridge.

Thin wrappers over the stdlib ``sqlite3`` module. No ORM. All statements that
take user/data values use parameterized queries. Foreign keys are enforced on
every connection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# Repo layout: this file lives in src/, schema.sql and queries/ are at the root.
_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = _ROOT / "schema.sql"
QUERIES_DIR = _ROOT / "queries"


def connect(database: str = ":memory:") -> sqlite3.Connection:
    """Open a connection with foreign keys on and row access by column name."""
    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: Path | str = SCHEMA_PATH) -> None:
    """Create tables and load reference seed data from schema.sql."""
    sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def create_database(database: str = ":memory:") -> sqlite3.Connection:
    """Convenience: connect + init in one call."""
    conn = connect(database)
    init_db(conn)
    return conn


def execute(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
    *,
    commit: bool = True,
) -> int:
    """Run a write statement (INSERT/UPDATE/DELETE) and return lastrowid.

    By default commits so callers get durable, observable state. Uses bound
    parameters. Pass ``commit=False`` to leave the write inside the caller's
    open transaction/savepoint so a multi-step operation can commit or roll back
    as a unit; the default ``commit=True`` preserves every existing caller's
    behavior. Callers that pass ``commit=False`` are responsible for committing
    or rolling back the surrounding transaction.
    """
    cur = conn.execute(sql, params)
    if commit:
        conn.commit()
    return cur.lastrowid


def query_all(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
) -> list[sqlite3.Row]:
    """Run a SELECT and return all rows."""
    return conn.execute(sql, params).fetchall()


def query_one(
    conn: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
) -> sqlite3.Row | None:
    """Run a SELECT and return the first row (or None)."""
    return conn.execute(sql, params).fetchone()


def load_query(name: str) -> str:
    """Read a named .sql file from the queries/ directory.

    ``name`` may be given with or without the ``.sql`` extension.
    """
    filename = name if name.endswith(".sql") else f"{name}.sql"
    path = QUERIES_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"No query file: {path}")
    return path.read_text(encoding="utf-8")


def run_query(
    conn: sqlite3.Connection,
    name: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
) -> list[sqlite3.Row]:
    """Load a query file by name and execute it with bound parameters."""
    return query_all(conn, load_query(name), params)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    """Convert sqlite3.Row objects to plain dicts (handy for JSON/printing)."""
    return [dict(r) for r in rows]
