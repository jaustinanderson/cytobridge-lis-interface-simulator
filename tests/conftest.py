"""Shared pytest fixtures for CytoBridge tests."""

from __future__ import annotations

import sqlite3

import pytest

from src import workflow
from src.db import create_database

# Full set of required probes with below-cutoff (NORMAL) synthetic results,
# except RUNX1T1_RUNX1 which is clearly ABNORMAL. Mirrors demo_run.
FULL_RESULTS = [
    ("RUNX1T1_RUNX1", 200, 38, "2F1R1G", "ABNORMAL"),
    ("CBFB", 200, 1, "2 orange/green", "NORMAL"),
    ("PML_RARA", 200, 2, "2R2G", "NORMAL"),
    ("KMT2A", 200, 1, "2 fusion", "NORMAL"),
    ("EGR1_5q", 200, 3, "2 orange 2 green", "NORMAL"),
    ("D7S486_7q", 200, 2, "2 signals", "NORMAL"),
    ("CEP8", 200, 4, "2 aqua", "NORMAL"),
    ("D20S108_20q", 200, 1, "2 signals", "NORMAL"),
    ("TP53_17p", 200, 2, "2R2G", "NORMAL"),
]


@pytest.fixture()
def conn() -> sqlite3.Connection:
    """A fresh in-memory database with schema + reference seed loaded."""
    connection = create_database(":memory:")
    yield connection
    connection.close()


@pytest.fixture()
def accessioned_order(conn: sqlite3.Connection) -> int:
    """A patient + order with an accessioned bone-marrow specimen (no results)."""
    patient_id = workflow.create_patient(
        conn, "SYN-9001", "Test", "Pat", "1980-01-01", "F"
    )
    order_id = workflow.create_order(
        conn, patient_id, "ACC-TEST-0001", "Dr. Test"
    )
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def enter_all_results(conn: sqlite3.Connection, order_id: int, skip=()) -> None:
    """Enter FULL_RESULTS for an order, optionally skipping some probe codes."""
    for probe_code, scored, abn, pattern, interp in FULL_RESULTS:
        if probe_code in skip:
            continue
        workflow.enter_fish_result(
            conn, order_id, probe_code, scored, abn, pattern, interp, "tech01"
        )
