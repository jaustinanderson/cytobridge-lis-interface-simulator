"""Tests for the analyst lab database access modes (src/analyst/store.py).

Proves the guarantees the CLI, preview, evidence, and exercise verifier rely on:
a read-only open cannot write, never creates a file, and leaves the bytes
unchanged; init never replaces an existing path; a snapshot connection sees one
consistent state; WAL-mode files are refused without being opened. All data is
synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from src import workflow
from src.analyst import store
from src.analyst.common import AnalystError


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def db_path(tmp_path):
    path = tmp_path / "lab.db"
    store.init_database(path)
    conn = store.open_read_write(path)
    try:
        workflow.create_patient(conn, "SYN-ST-01", "Synthetic", "Store", "1970-01-01", "F")
    finally:
        conn.close()
    return path


def test_read_only_open_refuses_writes_and_leaves_bytes_unchanged(db_path):
    before = _sha(db_path)
    conn = store.open_read_only(db_path)
    try:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO patient (mrn, last_name, first_name, date_of_birth, sex) "
                "VALUES ('SYN-ST-02', 'Synthetic', 'Write', '1970-01-01', 'M')"
            )
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("UPDATE patient SET last_name = 'Changed'")
        assert conn.execute("SELECT COUNT(*) FROM patient").fetchone()[0] == 1
    finally:
        conn.close()
    assert _sha(db_path) == before
    assert sorted(os.listdir(db_path.parent)) == ["lab.db"]


def test_read_only_open_rejects_writes_even_without_query_only(db_path):
    # mode=ro is itself enforced by SQLite, independent of PRAGMA query_only.
    conn = store.open_read_only(db_path)
    try:
        conn.execute("PRAGMA query_only = OFF")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("DELETE FROM patient")
    finally:
        conn.close()


@pytest.mark.parametrize("opener", [store.open_read_only, store.open_read_write])
def test_opens_never_create_a_missing_database(tmp_path, opener):
    missing = tmp_path / "missing.db"
    with pytest.raises(AnalystError) as exc:
        opener(missing)
    assert exc.value.kind == "NOT_FOUND"
    assert not missing.exists()
    assert os.listdir(tmp_path) == []


def test_init_refuses_existing_paths_without_touching_them(tmp_path, db_path):
    before = _sha(db_path)
    with pytest.raises(AnalystError) as exc:
        store.init_database(db_path)
    assert exc.value.kind == "REFUSED"
    assert _sha(db_path) == before

    empty = tmp_path / "empty.db"
    empty.write_bytes(b"")
    with pytest.raises(AnalystError) as exc:
        store.init_database(empty)
    assert exc.value.kind == "REFUSED"
    assert empty.read_bytes() == b""

    with pytest.raises(AnalystError) as exc:
        store.init_database(tmp_path)  # a directory
    assert exc.value.kind == "REFUSED"


def test_init_missing_parent_directory_is_not_found(tmp_path):
    target = tmp_path / "no-such-dir" / "lab.db"
    with pytest.raises(AnalystError) as exc:
        store.init_database(target)
    assert exc.value.kind == "NOT_FOUND"
    assert not target.parent.exists()


def test_non_cytobridge_files_are_invalid_input(tmp_path):
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database" * 50)
    other = tmp_path / "other.db"
    conn = sqlite3.connect(other)
    conn.execute("CREATE TABLE unrelated (x INTEGER)")
    conn.commit()
    conn.close()
    for path in (junk, other):
        before = _sha(path)
        with pytest.raises(AnalystError) as exc:
            store.open_read_only(path)
        assert exc.value.kind == "INVALID_INPUT"
        assert _sha(path) == before


def test_wal_mode_database_is_refused_without_side_files(tmp_path, db_path):
    conn = sqlite3.connect(db_path)
    assert conn.execute("PRAGMA journal_mode = WAL").fetchone()[0] == "wal"
    conn.close()
    for side in ("lab.db-wal", "lab.db-shm"):
        side_path = tmp_path / side
        if side_path.exists():
            side_path.unlink()
    before = _sha(db_path)
    for opener in (store.open_read_only, store.open_read_write):
        with pytest.raises(AnalystError) as exc:
            opener(db_path)
        assert exc.value.kind == "INVALID_INPUT"
        assert "WAL" in exc.value.message
    assert _sha(db_path) == before
    assert sorted(os.listdir(tmp_path)) == ["lab.db"]


def test_snapshot_connection_sees_one_consistent_state(db_path):
    reader = store.open_read_only(db_path, snapshot=True)
    writer = store.open_read_write(db_path)
    try:
        assert reader.execute("SELECT COUNT(*) FROM patient").fetchone()[0] == 1
        # In rollback-journal mode a writer cannot commit while a reader holds
        # its snapshot, so the reader's view cannot change underneath it.
        writer.execute("PRAGMA busy_timeout = 0")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            workflow.create_patient(
                writer, "SYN-ST-03", "Synthetic", "Late", "1970-01-01", "M"
            )
        writer.rollback()
        assert reader.execute("SELECT COUNT(*) FROM patient").fetchone()[0] == 1
    finally:
        reader.close()
    try:
        workflow.create_patient(writer, "SYN-ST-03", "Synthetic", "Late", "1970-01-01", "M")
    finally:
        writer.close()
    check = store.open_read_only(db_path)
    try:
        assert check.execute("SELECT COUNT(*) FROM patient").fetchone()[0] == 2
    finally:
        check.close()
