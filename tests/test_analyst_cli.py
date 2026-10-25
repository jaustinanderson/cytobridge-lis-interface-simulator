"""Tests for the analyst-lab command-line interface (src.analyst.cli).

Every acceptance gate is proven with DATABASE CONTENTS read through an
independent ``sqlite3`` connection (or the database file's SHA-256), not by
matching output strings:

- persistence across separate ``python -m src.analyst`` processes, including an
  explicit process-restart check from a brand-new interpreter;
- the ``--json`` envelope (stable keys, exactly one object, exit codes);
- error paths return the contract's exit code and leave state correct
  (nothing created, nothing changed, or exactly the rows the service writes);
- read-only commands leave the database file byte-identical with no journal;
- inbound payload bytes (CR and CRLF segment terminators, multibyte UTF-8) are
  stored and shown back exactly;
- original-message immutability and handled-failure rollback through the CLI
  (the fault injection is a test-only monkeypatch, never in src/);
- text output is readable, starts with the synthetic notice, and survives a
  terminal encoding that cannot represent the report's em dash.

All data is synthetic. No PHI.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from src import workflow
from src.analyst import cli, preview, store, trace
from src.analyst.common import EXIT_CODES, FORMAT, SYNTHETIC_NOTICE
from src.interfaces import inbound_hl7
from tests.conftest import FULL_RESULTS

REPO_ROOT = Path(__file__).resolve().parent.parent
TABLES = store.REQUIRED_TABLES
ENVELOPE_KEYS = {"format", "command", "ok", "exit_code", "result", "error", "synthetic"}

# Synthetic identifiers used only by this module.
ACC1 = "ACC-CLI-0001"
ACC2 = "ACC-CLI-0002"
MRN1 = "SYN-CLI-01"
MRN2 = "SYN-CLI-02"
BAD_COUNT = {"PML_RARA": "2OO^2^2R2G^NORMAL"}  # fails INVALID_CELL_COUNT
RICH_REQUEST = "REQ-CLI-RICH-1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def oru(
    control_id: str,
    accession: str,
    *,
    override: dict[str, str] | None = None,
    terminator: str = "\r",
    last_name: str = "Synthetic",
) -> str:
    """A synthetic inbound ORU-style FISH message carrying all nine probes."""
    lines = [
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20261024101500||"
        f"ORU^R01|{control_id}|T|2.5.1",
        f"PID|1||SYN-CLI-01^^^CYTO_LAB^MR||{last_name}^Cli||19700101|F",
        f"OBR|1||{accession}|AML_MDS_FISH^AML/MDS FISH Panel^L|||20261024090000"
        "|||||||||^Dr. Synthetic||||||20261024100000|||F",
        "SPM|1|BM-CLI-01||BMA^Bone Marrow^L|||||||||||||20261024090000|20261024093000",
    ]
    for set_id, (probe, scored, abnormal, pattern, interp) in enumerate(
        FULL_RESULTS, start=1
    ):
        value = (override or {}).get(probe, f"{scored}^{abnormal}^{pattern}^{interp}")
        lines.append(f"OBX|{set_id}|ST|{probe}^{probe}^L||{value}||||||F")
    return terminator.join(lines) + terminator


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ro_connect(db: Path) -> sqlite3.Connection:
    """Independent read-only connection (never creates, never writes)."""
    conn = sqlite3.connect(f"{db.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_counts(db: Path) -> dict[str, int]:
    with contextlib.closing(ro_connect(db)) as conn:
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in TABLES}


def query(db: Path, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with contextlib.closing(ro_connect(db)) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def scalar(db: Path, sql: str, params: tuple = ()) -> Any:
    with contextlib.closing(ro_connect(db)) as conn:
        return conn.execute(sql, params).fetchone()[0]


def snapshot(db: Path) -> dict:
    with contextlib.closing(ro_connect(db)) as conn:
        return preview.snapshot_tables(conn)


def sidecars(db: Path) -> list[str]:
    """Journal/WAL/SHM files next to the database (there must be none)."""
    return sorted(
        p.name for p in db.parent.iterdir() if p.name.startswith(db.name) and p != db
    )


def check_envelope(env: dict[str, Any], code: int) -> None:
    assert set(env) == ENVELOPE_KEYS
    assert env["format"] == FORMAT
    assert env["synthetic"] is True
    assert env["exit_code"] == code
    assert env["ok"] is (code == 0)
    assert isinstance(env["command"], str)
    if code == 0:
        assert env["error"] is None
        assert isinstance(env["result"], dict)
    else:
        assert set(env["error"]) == {"type", "message"}
        assert env["error"]["type"] in EXIT_CODES
        assert EXIT_CODES[env["error"]["type"]] == code
        assert env["error"]["message"]


def parse_single_json(text: str) -> dict[str, Any]:
    """stdout must hold exactly one JSON object (json.loads rejects extra data)."""
    assert text.lstrip().startswith("{") and text.endswith("}\n")
    return json.loads(text)


class Lab:
    """Drives ``cli.main`` in-process against one file database."""

    def __init__(self, root: Path, capsys: Any = None) -> None:
        self.root = root
        self.db = root / "lab.db"
        self.inputs = root / "inputs"
        self.inputs.mkdir(exist_ok=True)
        self.capsys = capsys

    @property
    def dbargs(self) -> list[str]:
        return ["--db", str(self.db)]

    def write(self, name: str, content: str | bytes) -> Path:
        path = self.inputs / name
        path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
        return path

    def _call(self, argv: list[str]) -> tuple[int, str, str]:
        if self.capsys is not None:
            self.capsys.readouterr()
            code = cli.main(argv)
            captured = self.capsys.readouterr()
            return code, captured.out, captured.err
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def run(self, *args: Any, expect: int | None = None) -> dict[str, Any]:
        """JSON mode; checks the envelope and returns it."""
        code, out, err = self._call([str(a) for a in args] + ["--json"])
        env = parse_single_json(out)
        check_envelope(env, code)
        assert err == ""
        if expect is not None:
            assert code == expect, env
        return env

    def text(self, *args: Any) -> tuple[int, str, str]:
        return self._call([str(a) for a in args])

    def init(self) -> None:
        self.run("init", *self.dbargs, expect=0)

    def case(self, accession: str, mrn: str, *extra: str, expect: int = 0) -> dict:
        return self.run(*case_args(self.dbargs, accession, mrn), *extra, expect=expect)

    def ingest(self, name: str, payload: str | bytes, expect: int) -> dict:
        path = self.write(name, payload)
        return self.run("ingest", *self.dbargs, "--file", path, expect=expect)


def case_args(dbargs: list[str], accession: str, mrn: str) -> list[str]:
    return [
        "case-create", *dbargs, "--mrn", mrn, "--last-name", "Synthetic",
        "--first-name", "Cli", "--dob", "1970-01-01", "--sex", "F",
        "--accession", accession, "--provider", "Dr. Synthetic",
        "--external-specimen-id", f"BM-{mrn}",
    ]


def run_proc(*args: Any, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "src.analyst", *[str(a) for a in args]],
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=120,
        env=env,
    )


def proc_json(*args: Any, expect: int) -> dict[str, Any]:
    proc = run_proc(*args, "--json")
    env = parse_single_json(proc.stdout.decode("ascii"))
    check_envelope(env, proc.returncode)
    assert proc.returncode == expect, (proc.stdout, proc.stderr)
    return env


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def rich_template(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """A database built only through the CLI (in-process, StringIO capture).

    patient 1 / order 1 ACC1 FINALIZED; message 1 ERRORED -> queue 1 RESOLVED by
    attempt 1 (REDRIVE_CORRECTED, RICH_REQUEST, analyst01) -> message 3 FILED;
    message 2 ERRORED -> queue 2 OPEN ORDER_NOT_FOUND (its order ACC2 created
    afterwards, so a retry would now file); message 4 OUTBOUND HL7; message 5
    ERRORED -> queue 3 TERMINAL ORDER_FINALIZED; patient 2 / order 2 ACC2
    IN_PROCESS with an accessioned specimen and no results.
    """
    root = tmp_path_factory.mktemp("cli_rich")
    lab = Lab(root)
    lab.init()
    lab.case(ACC1, MRN1)
    lab.ingest("bad.hl7", oru("CLI-BAD", ACC1, override=BAD_COUNT), expect=4)
    lab.ingest("unknown.hl7", oru("CLI-UNK", ACC2), expect=4)
    good = lab.write("good.hl7", oru("CLI-GOOD", ACC1))
    lab.run(
        "recover", "redrive", *lab.dbargs, "--queue-id", 1, "--file", good,
        "--request-id", RICH_REQUEST, "--actor", "analyst01", expect=0,
    )
    lab.run("finalize", *lab.dbargs, "--order-id", 1, "--by", "analyst01", expect=0)
    lab.run(
        "export", *lab.dbargs, "--order-id", 1, "--format", "hl7", "--store",
        "--generated-at", "2026-10-25 09:00:00", expect=0,
    )
    lab.ingest("late.hl7", oru("CLI-LATE", ACC1), expect=4)
    lab.case(ACC2, MRN2)
    return {"db": lab.db, "good": good, "inputs": lab.inputs}


@pytest.fixture()
def rich(tmp_path: Path, rich_template: dict[str, Path], capsys: Any) -> Lab:
    lab = Lab(tmp_path, capsys)
    shutil.copyfile(rich_template["db"], lab.db)
    shutil.copyfile(rich_template["good"], lab.inputs / "good.hl7")
    return lab


@pytest.fixture()
def open_lab(tmp_path: Path, capsys: Any) -> Lab:
    """order 1 ACC1 IN_PROCESS; message 1 ERRORED -> queue 1 OPEN REDRIVE_ONLY."""
    lab = Lab(tmp_path, capsys)
    lab.init()
    lab.case(ACC1, MRN1)
    lab.ingest("bad.hl7", oru("CLI-BAD", ACC1, override=BAD_COUNT), expect=4)
    return lab


def test_rich_template_is_reconciled(rich: Lab) -> None:
    """Baseline sanity: the CLI-built template has the documented shape."""
    queues = query(rich.db, "SELECT queue_id, status, failure_code FROM "
                            "interface_error_queue ORDER BY queue_id")
    assert [(q["status"], q["failure_code"]) for q in queues] == [
        ("RESOLVED", "INVALID_CELL_COUNT"),
        ("OPEN", "ORDER_NOT_FOUND"),
        ("TERMINAL", "ORDER_FINALIZED"),
    ]
    env = rich.run("reconcile", *rich.dbargs, expect=0)
    assert env["result"]["summary"]["ERROR"] == 0


# ---------------------------------------------------------------------------
# Persistence across processes
# ---------------------------------------------------------------------------

def test_each_command_in_separate_process_persists(tmp_path: Path) -> None:
    db = tmp_path / "persist.db"
    files = tmp_path / "files"
    files.mkdir()
    unknown = files / "unknown.hl7"
    unknown.write_bytes(oru("SUB-UNK", "ACC-SUB-0002").encode())
    bad = files / "bad.hl7"
    bad.write_bytes(oru("SUB-BAD", "ACC-SUB-0001", override=BAD_COUNT).encode())
    good = files / "good.hl7"
    good.write_bytes(oru("SUB-GOOD", "ACC-SUB-0001").encode())
    dbargs = ["--db", db]

    proc_json("init", *dbargs, expect=0)
    proc_json(*case_args(["--db", str(db)], "ACC-SUB-0001", "SYN-SUB-01"), expect=0)
    first = proc_json("ingest", *dbargs, "--file", unknown, expect=4)
    assert first["result"]["failure_code"] == "ORDER_NOT_FOUND"
    proc_json(*case_args(["--db", str(db)], "ACC-SUB-0002", "SYN-SUB-02"), expect=0)
    retry = proc_json(
        "recover", "retry", *dbargs, "--queue-id", 1, "--request-id", "SUB-RETRY-1",
        "--actor", "analyst01", expect=0,
    )
    assert retry["command"] == "recover retry"
    proc_json("ingest", *dbargs, "--file", bad, expect=4)
    redrive = proc_json(
        "recover", "redrive", *dbargs, "--queue-id", 2, "--file", good,
        "--request-id", "SUB-REDRIVE-1", "--actor", "analyst01", expect=0,
    )
    proc_json("finalize", *dbargs, "--order-id", 1, "--by", "analyst01", expect=0)
    export = proc_json(
        "export", *dbargs, "--order-id", 1, "--format", "hl7", "--store",
        "--generated-at", "2026-10-25 09:00:00", expect=0,
    )

    # A fresh process reads everything back.
    case = proc_json("trace", *dbargs, "--queue-id", 2, expect=0)["result"]
    assert [q["status"] for q in case["queue_items"]] == ["RESOLVED"]
    assert [a["outcome"] for a in case["recovery_attempts"]] == ["SUCCEEDED"]
    history = proc_json("recover", "history", *dbargs, "--queue-id", 1, expect=0)
    assert [a["request_id"] for a in history["result"]["attempts"]] == ["SUB-RETRY-1"]
    worklist = proc_json("worklist", *dbargs, "--status", "ALL", expect=0)["result"]
    assert [(i["queue_id"], i["status"], i["last_outcome"]) for i in worklist["items"]] == [
        (1, "RESOLVED", "SUCCEEDED"), (2, "RESOLVED", "SUCCEEDED"),
    ]

    # An independent sqlite3 connection confirms the rows themselves.
    assert query(db, "SELECT order_id, accession_number, status FROM lab_order "
                     "ORDER BY order_id") == [
        {"order_id": 1, "accession_number": "ACC-SUB-0001", "status": "FINALIZED"},
        {"order_id": 2, "accession_number": "ACC-SUB-0002", "status": "PENDING_REVIEW"},
    ]
    assert query(db, "SELECT order_id, COUNT(*) AS n FROM fish_result "
                     "GROUP BY order_id ORDER BY order_id") == [
        {"order_id": 1, "n": 9}, {"order_id": 2, "n": 9},
    ]
    attempts = query(db, "SELECT attempt_id, queue_id, action, outcome, "
                         "resulting_message_id FROM interface_recovery_attempt "
                         "ORDER BY attempt_id")
    assert attempts == [
        {"attempt_id": 1, "queue_id": 1, "action": "RETRY_ORIGINAL",
         "outcome": "SUCCEEDED", "resulting_message_id": 2},
        {"attempt_id": 2, "queue_id": 2, "action": "REDRIVE_CORRECTED",
         "outcome": "SUCCEEDED",
         "resulting_message_id": redrive["result"]["attempt"]["resulting_message_id"]},
    ]
    messages = query(db, "SELECT message_id, direction, status, order_id FROM "
                         "interface_message ORDER BY message_id")
    assert [(m["direction"], m["status"], m["order_id"]) for m in messages] == [
        ("INBOUND", "ERRORED", None), ("INBOUND", "FILED", 2),
        ("INBOUND", "ERRORED", None), ("INBOUND", "FILED", 1),
        ("OUTBOUND", "GENERATED", 1),
    ]
    assert scalar(db, "SELECT status FROM report WHERE order_id = 1") == "FINALIZED"
    stored = scalar(db, "SELECT payload FROM interface_message WHERE message_id = ?",
                    (export["result"]["stored_message_id"],))
    assert hashlib.sha256(stored.encode("utf-8")).hexdigest() == (
        export["result"]["payload_sha256"]
    )
    assert sidecars(db) == []


def test_process_restart_data_visible_from_new_interpreter(tmp_path: Path) -> None:
    db = tmp_path / "restart.db"
    writer = run_proc("init", "--db", db)
    assert writer.returncode == 0
    writer = run_proc(*case_args(["--db", str(db)], "ACC-RST-0001", "SYN-RST-01"),
                      "--json")
    assert writer.returncode == 0  # the writing process has exited
    order_id = json.loads(writer.stdout)["result"]["order_id"]

    # A brand-new interpreter with only the stdlib reads the committed rows.
    reader = subprocess.run(
        [sys.executable, "-I", "-c",
         "import json, sqlite3, sys\n"
         "c = sqlite3.connect('file:' + sys.argv[1] + '?mode=ro', uri=True)\n"
         "print(json.dumps(c.execute('SELECT o.order_id, o.accession_number, p.mrn "
         "FROM lab_order o JOIN patient p ON p.patient_id = o.patient_id').fetchall()))\n",
         str(db)],
        capture_output=True, timeout=60,
    )
    assert reader.returncode == 0, reader.stderr
    assert json.loads(reader.stdout) == [[order_id, "ACC-RST-0001", "SYN-RST-01"]]

    # And a new CLI process resolves the same order by accession.
    case = proc_json("trace", "--db", db, "--accession", "ACC-RST-0001", expect=0)
    assert case["result"]["anchor"] == {"kind": "order", "id": order_id}
    assert case["result"]["orders"][0]["patient"]["mrn"] == "SYN-RST-01"


def test_subprocess_usage_error_and_help() -> None:
    proc = run_proc("--json", "frobnicate")
    assert proc.returncode == 2
    env = parse_single_json(proc.stdout.decode("ascii"))
    check_envelope(env, 2)
    assert env["error"]["type"] == "USAGE"
    assert proc.stderr == b""

    proc = run_proc("--help")
    assert proc.returncode == 0
    assert b"exit codes:" in proc.stdout and b"read-only commands" in proc.stdout


# ---------------------------------------------------------------------------
# JSON envelope
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_json_flag_accepted_anywhere(rich: Lab, position: str) -> None:
    args = ["worklist", "--db", str(rich.db), "--status", "ALL"]
    if position == "first":
        argv = ["--json"] + args
    elif position == "middle":
        argv = args[:3] + ["--json"] + args[3:]
    else:
        argv = args + ["--json"]
    rich.capsys.readouterr()
    code = cli.main(argv)
    env = parse_single_json(rich.capsys.readouterr().out)
    check_envelope(env, code)
    assert code == 0
    assert env["command"] == "worklist"
    assert [i["queue_id"] for i in env["result"]["items"]] == [1, 2, 3]


def test_envelope_command_words_and_results(rich: Lab) -> None:
    env = rich.run("recover", "history", *rich.dbargs, "--queue-id", 1, expect=0)
    assert env["command"] == "recover history"
    assert set(env["result"]) >= {"queue_id", "queue_status", "failure_code",
                                  "attempts", "conflict_audit_events"}
    env = rich.run("show-message", *rich.dbargs, "--message-id", 3, expect=0)
    assert env["command"] == "show-message"
    row = query(rich.db, "SELECT * FROM interface_message WHERE message_id = 3")[0]
    for key in ("message_id", "direction", "format", "message_type", "status",
                "order_id", "control_id", "created_at", "payload"):
        assert env["result"][key] == row[key]
    env = rich.run("audit", *rich.dbargs, "--order-id", 1, expect=0)
    ids = [e["event_id"] for e in env["result"]["events"]]
    assert ids == [r["event_id"] for r in query(
        rich.db, "SELECT event_id FROM audit_event WHERE order_id = 1 ORDER BY event_id")]


# ---------------------------------------------------------------------------
# Usage errors -> 2
# ---------------------------------------------------------------------------

USAGE_CASES = [
    [],
    ["frobnicate"],
    ["worklist"],
    ["worklist", "--db", "{db}", "--status", "BOGUS"],
    ["trace", "--db", "{db}"],
    ["trace", "--db", "{db}", "--order-id", "1", "--queue-id", "1"],
    ["recover"],
    ["recover", "bogus"],
    ["recover", "retry", "--db", "{db}", "--queue-id", "1"],
    ["result-enter", "--db", "{db}", "--order-id", "abc", "--probe", "CEP8",
     "--scored", "200", "--abnormal", "1", "--pattern", "2", "--interpretation",
     "NORMAL", "--entered-by", "t"],
    ["result-enter", "--db", "{db}", "--order-id", "1", "--probe", "CEP8",
     "--scored", "200", "--abnormal", "1", "--pattern", "2", "--interpretation",
     "MAYBE", "--entered-by", "t"],
    ["export", "--db", "{db}", "--order-id", "1", "--format", "pdf"],
    ["export", "--db", "{db}", "--order-id", "1", "--format", "hl7",
     "--generated-at", "yesterday"],
    ["exercise"],
    ["init", "--db", "{db}", "--unknown-flag"],
    ["case-create", "--db", "{db}", "--mrn", "SYN-U-1", "--last-name", "S",
     "--first-name", "U", "--dob", "01/02/1970", "--sex", "F", "--accession",
     "ACC-U-1", "--provider", "Dr. S"],
    ["case-create", "--db", "{db}", "--mrn", "SYN-U-1", "--last-name", "S",
     "--first-name", "U", "--dob", "1970-02-30", "--sex", "F", "--accession",
     "ACC-U-1", "--provider", "Dr. S"],
]


@pytest.mark.parametrize("argv", USAGE_CASES, ids=lambda a: " ".join(a[:2]) or "empty")
def test_usage_errors_exit_2(tmp_path: Path, capsys: Any, argv: list[str]) -> None:
    db = tmp_path / "never.db"
    argv = [a.replace("{db}", str(db)) for a in argv]
    lab = Lab(tmp_path, capsys)
    env = lab.run(*argv, expect=2)
    assert env["error"]["type"] == "USAGE"
    assert env["result"] is None
    code, out, err = lab.text(*argv)
    assert code == 2 and out == ""
    assert err.startswith("error: ") and "Traceback" not in err
    assert not db.exists()


def test_preview_file_rules_are_usage_errors(rich: Lab) -> None:
    before = sha256_path(rich.db)
    env = rich.run("recover", "preview", *rich.dbargs, "--queue-id", 2, "--action",
                   "redrive", "--request-id", "P-1", "--actor", "a", expect=2)
    assert env["error"]["type"] == "USAGE" and "--file" in env["error"]["message"]
    env = rich.run("recover", "preview", *rich.dbargs, "--queue-id", 2, "--action",
                   "retry", "--file", rich.inputs / "good.hl7", "--request-id", "P-1",
                   "--actor", "a", expect=2)
    assert env["error"]["type"] == "USAGE"
    assert sha256_path(rich.db) == before


# ---------------------------------------------------------------------------
# Missing / invalid databases and inputs
# ---------------------------------------------------------------------------

MISSING_DB_COMMANDS = [
    ["worklist"],
    ["show-message", "--message-id", "1"],
    ["trace", "--order-id", "1"],
    ["reconcile"],
    ["recover", "history", "--queue-id", "1"],
    ["recover", "preview", "--queue-id", "1", "--action", "retry",
     "--request-id", "R", "--actor", "a"],
    ["audit", "--order-id", "1"],
    ["export", "--order-id", "1", "--format", "hl7"],
    ["export", "--order-id", "1", "--format", "fhir", "--store"],
    ["case-create", "--mrn", "SYN-M-1", "--last-name", "S", "--first-name", "M",
     "--dob", "1970-01-01", "--sex", "U", "--accession", "ACC-M-1", "--provider", "P"],
    ["specimen-accession", "--specimen-id", "1"],
    ["result-enter", "--order-id", "1", "--probe", "CEP8", "--scored", "200",
     "--abnormal", "1", "--pattern", "2", "--interpretation", "NORMAL",
     "--entered-by", "t"],
    ["finalize", "--order-id", "1", "--by", "a"],
    ["ingest", "--file", "{good}"],
    ["recover", "retry", "--queue-id", "1", "--request-id", "R", "--actor", "a"],
    ["recover", "redrive", "--queue-id", "1", "--file", "{good}", "--request-id",
     "R", "--actor", "a"],
    ["evidence", "--order-id", "1"],
]


def _with_db(argv: list[str], db: Path, good: Path) -> list[str]:
    words = 2 if argv[0] == "recover" else 1
    rest = [a.replace("{good}", str(good)) for a in argv[words:]]
    return argv[:words] + ["--db", str(db)] + rest


@pytest.mark.parametrize("argv", MISSING_DB_COMMANDS, ids=lambda a: " ".join(a[:2]))
def test_missing_database_is_not_created(tmp_path: Path, capsys: Any, argv: list[str]) -> None:
    lab = Lab(tmp_path, capsys)
    good = lab.write("good.hl7", oru("M-GOOD", ACC1))
    db = tmp_path / "missing.db"
    env = lab.run(*_with_db(argv, db, good), expect=3)
    assert env["error"]["type"] == "NOT_FOUND"
    assert not db.exists() and sidecars(db) == []


def test_missing_input_file_exit_3(open_lab: Lab) -> None:
    before = sha256_path(open_lab.db)
    missing = open_lab.inputs / "nope.hl7"
    for argv in (
        ["ingest", *open_lab.dbargs, "--file", missing],
        ["recover", "redrive", *open_lab.dbargs, "--queue-id", 1, "--file", missing,
         "--request-id", "R-MISS", "--actor", "a"],
        ["recover", "preview", *open_lab.dbargs, "--queue-id", 1, "--action",
         "redrive", "--file", missing, "--request-id", "R-MISS", "--actor", "a"],
    ):
        env = open_lab.run(*argv, expect=3)
        assert env["error"]["type"] == "NOT_FOUND"
    assert sha256_path(open_lab.db) == before
    assert not missing.exists()


def test_non_utf8_input_exit_5_database_unchanged(open_lab: Lab) -> None:
    before = sha256_path(open_lab.db)
    latin1 = oru("CLI-LATIN1", ACC1).replace("Synthetic^Cli", "Synth\u00e9tic^Cli")
    bad_bytes = open_lab.write("latin1.hl7", latin1.encode("latin-1"))
    for argv in (
        ["ingest", *open_lab.dbargs, "--file", bad_bytes],
        ["recover", "redrive", *open_lab.dbargs, "--queue-id", 1, "--file", bad_bytes,
         "--request-id", "R-L1", "--actor", "a"],
        ["recover", "preview", *open_lab.dbargs, "--queue-id", 1, "--action",
         "redrive", "--file", bad_bytes, "--request-id", "R-L1", "--actor", "a"],
    ):
        env = open_lab.run(*argv, expect=5)
        assert env["error"]["type"] == "INVALID_INPUT"
        assert "UTF-8" in env["error"]["message"]
    assert sha256_path(open_lab.db) == before


@pytest.mark.parametrize("kind", ["text", "other_sqlite"])
def test_non_cytobridge_database_exit_5(tmp_path: Path, capsys: Any, kind: str) -> None:
    lab = Lab(tmp_path, capsys)
    fake = tmp_path / "fake.db"
    if kind == "text":
        fake.write_bytes(b"not a database, just synthetic text\n")
    else:
        with contextlib.closing(sqlite3.connect(fake)) as conn:
            conn.execute("CREATE TABLE something_else (x INTEGER)")
            conn.commit()
    before = fake.read_bytes()
    good = lab.write("good.hl7", oru("F-GOOD", ACC1))
    for argv in (
        ["worklist", "--db", fake],
        ["trace", "--db", fake, "--order-id", 1],
        ["ingest", "--db", fake, "--file", good],
        case_args(["--db", str(fake)], ACC1, MRN1),
    ):
        env = lab.run(*argv, expect=5)
        assert env["error"]["type"] == "INVALID_INPUT"
    assert fake.read_bytes() == before
    assert sidecars(fake) == []


def test_init_refuses_existing_path(rich: Lab) -> None:
    before = rich.db.read_bytes()
    env = rich.run("init", *rich.dbargs, expect=4)
    assert env["error"]["type"] == "REFUSED"
    assert rich.db.read_bytes() == before
    other = rich.root / "existing.txt"
    other.write_bytes(b"synthetic placeholder")
    rich.run("init", "--db", other, expect=4)
    assert other.read_bytes() == b"synthetic placeholder"
    env = rich.run("init", "--db", rich.root / "no-such-dir" / "x.db", expect=3)
    assert not (rich.root / "no-such-dir").exists()


# ---------------------------------------------------------------------------
# Unknown ids -> 3, nothing changed
# ---------------------------------------------------------------------------

UNKNOWN_ID_COMMANDS = [
    ["trace", "--order-id", "99"],
    ["trace", "--queue-id", "99"],
    ["trace", "--message-id", "99"],
    ["trace", "--accession", "ACC-NOPE"],
    ["show-message", "--message-id", "99"],
    ["reconcile", "--order-id", "99"],
    ["reconcile", "--queue-id", "99"],
    ["recover", "history", "--queue-id", "99"],
    ["recover", "preview", "--queue-id", "99", "--action", "retry",
     "--request-id", "R-99", "--actor", "a"],
    ["recover", "retry", "--queue-id", "99", "--request-id", "R-99", "--actor", "a"],
    ["recover", "redrive", "--queue-id", "99", "--file", "{good}",
     "--request-id", "R-99", "--actor", "a"],
    ["audit", "--order-id", "99"],
    ["export", "--order-id", "99", "--format", "hl7"],
    ["export", "--order-id", "99", "--format", "fhir", "--store"],
    ["result-enter", "--order-id", "99", "--probe", "CEP8", "--scored", "200",
     "--abnormal", "1", "--pattern", "2", "--interpretation", "NORMAL",
     "--entered-by", "t"],
    ["finalize", "--order-id", "99", "--by", "a"],
    ["specimen-accession", "--specimen-id", "99"],
    ["evidence", "--order-id", "99"],
]


@pytest.mark.parametrize("argv", UNKNOWN_ID_COMMANDS, ids=lambda a: " ".join(a[:3]))
def test_unknown_ids_exit_3_nothing_changed(rich: Lab, argv: list[str]) -> None:
    before_sha, before_counts = sha256_path(rich.db), table_counts(rich.db)
    env = rich.run(*_with_db(argv, rich.db, rich.inputs / "good.hl7"), expect=3)
    assert env["error"]["type"] == "NOT_FOUND"
    assert table_counts(rich.db) == before_counts
    assert sha256_path(rich.db) == before_sha


# ---------------------------------------------------------------------------
# Refusals -> 4 with correct state
# ---------------------------------------------------------------------------

def test_case_create_duplicates_refused_without_rows(rich: Lab) -> None:
    before = table_counts(rich.db)
    env = rich.run(*case_args(rich.dbargs, "ACC-CLI-0099", MRN1), expect=4)
    assert env["error"]["type"] == "REFUSED" and MRN1 in env["error"]["message"]
    env = rich.run(*case_args(rich.dbargs, ACC1, "SYN-CLI-99"), expect=4)
    assert env["error"]["type"] == "REFUSED" and ACC1 in env["error"]["message"]
    assert table_counts(rich.db) == before


def test_case_create_and_specimen_accession(rich: Lab) -> None:
    env = rich.run(*case_args(rich.dbargs, "ACC-CLI-0003", "SYN-CLI-03"),
                   "--no-accession", "--priority", "STAT", expect=0)
    result = env["result"]
    assert result["specimen_status"] == "RECEIVED"
    row = query(rich.db, "SELECT priority, status FROM lab_order WHERE order_id = ?",
                (result["order_id"],))[0]
    assert row == {"priority": "STAT", "status": "IN_PROCESS"}
    env = rich.run("specimen-accession", *rich.dbargs, "--specimen-id",
                   result["specimen_id"], expect=0)
    assert scalar(rich.db, "SELECT status FROM specimen WHERE specimen_id = ?",
                  (result["specimen_id"],)) == "ACCESSIONED"
    before = table_counts(rich.db)
    env = rich.run("specimen-accession", *rich.dbargs, "--specimen-id",
                   result["specimen_id"], expect=4)
    assert env["error"]["type"] == "REFUSED"
    assert table_counts(rich.db) == before


def test_result_enter_refusals_and_invalid_values(rich: Lab) -> None:
    def enter(order_id: int, probe: str, scored: int, abnormal: int, expect: int) -> dict:
        return rich.run(
            "result-enter", *rich.dbargs, "--order-id", order_id, "--probe", probe,
            "--scored", scored, "--abnormal", abnormal, "--pattern", "2 aqua",
            "--interpretation", "NORMAL", "--entered-by", "tech01", expect=expect,
        )

    before = table_counts(rich.db)
    assert enter(1, "CEP8", 200, 1, 4)["error"]["type"] == "REFUSED"  # FINALIZED
    assert enter(2, "NOT_A_PROBE", 200, 1, 5)["error"]["type"] == "INVALID_INPUT"
    assert enter(2, "CEP8", 10, 11, 5)["error"]["type"] == "INVALID_INPUT"
    assert enter(2, "CEP8", -1, 0, 5)["error"]["type"] == "INVALID_INPUT"
    assert table_counts(rich.db) == before

    env = enter(2, "CEP8", 200, 4, 0)
    assert env["result"]["order_status"] == "PENDING_REVIEW"
    rows = query(rich.db, "SELECT fr.result_id, fr.cells_scored, fr.cells_abnormal, "
                          "fr.entered_by FROM fish_result fr JOIN probe p "
                          "ON p.probe_id = fr.probe_id WHERE fr.order_id = 2 "
                          "AND p.probe_code = 'CEP8'")
    assert rows == [{"result_id": env["result"]["result_id"], "cells_scored": 200,
                     "cells_abnormal": 4, "entered_by": "tech01"}]


def test_finalize_blocked_and_already_finalized(rich: Lab) -> None:
    def audit_count(action: str, order_id: int) -> int:
        return scalar(rich.db, "SELECT COUNT(*) FROM audit_event WHERE action = ? "
                               "AND order_id = ?", (action, order_id))

    assert audit_count("FINALIZE_BLOCKED", 2) == 0
    env = rich.run("finalize", *rich.dbargs, "--order-id", 2, "--by", "analyst01",
                   expect=4)
    assert env["error"]["type"] == "REFUSED"
    assert env["result"]["finalized"] is False and env["result"]["findings"]
    assert {f["rule_code"] for f in env["result"]["findings"]} == {"MISSING_PROBE"}
    assert scalar(rich.db, "SELECT status FROM lab_order WHERE order_id = 2") == "IN_PROCESS"
    assert audit_count("FINALIZE_BLOCKED", 2) == 1
    assert scalar(rich.db, "SELECT COUNT(*) FROM report WHERE order_id = 2") == 0

    before = table_counts(rich.db)
    env = rich.run("finalize", *rich.dbargs, "--order-id", 1, "--by", "analyst01",
                   expect=4)
    assert env["error"]["type"] == "REFUSED" and env["result"] is None
    assert table_counts(rich.db) == before


def test_retry_on_redrive_only_item_rejected(open_lab: Lab) -> None:
    before = table_counts(open_lab.db)
    env = open_lab.run("recover", "retry", *open_lab.dbargs, "--queue-id", 1,
                       "--request-id", "R-RO-1", "--actor", "analyst01", expect=4)
    assert env["error"]["type"] == "REFUSED"
    assert env["result"]["attempt"]["outcome"] == "REJECTED"
    after = table_counts(open_lab.db)
    assert after["interface_recovery_attempt"] == before["interface_recovery_attempt"] + 1
    assert {t: n for t, n in after.items() if t != "interface_recovery_attempt"} == {
        t: n for t, n in before.items() if t != "interface_recovery_attempt"}
    assert query(open_lab.db, "SELECT outcome, resulting_message_id, action FROM "
                              "interface_recovery_attempt") == [
        {"outcome": "REJECTED", "resulting_message_id": None, "action": "RETRY_ORIGINAL"}]
    assert scalar(open_lab.db, "SELECT status FROM interface_error_queue") == "OPEN"


@pytest.mark.parametrize("action", ["retry", "redrive"])
def test_terminal_item_rejected(rich: Lab, action: str) -> None:
    before = table_counts(rich.db)
    argv = ["recover", action, *rich.dbargs, "--queue-id", 3,
            "--request-id", f"R-TERM-{action}", "--actor", "analyst01"]
    if action == "redrive":
        argv += ["--file", rich.inputs / "good.hl7"]
    env = rich.run(*argv, expect=4)
    assert env["result"]["attempt"]["outcome"] == "REJECTED"
    assert env["result"]["queue_status"] == "TERMINAL"
    after = table_counts(rich.db)
    assert after["interface_recovery_attempt"] == before["interface_recovery_attempt"] + 1
    assert after["interface_message"] == before["interface_message"]
    assert after["fish_result"] == before["fish_result"]


def test_request_id_conflict_and_matching_replay(rich: Lab) -> None:
    good = rich.inputs / "good.hl7"
    before_snapshot, before_counts = snapshot(rich.db), table_counts(rich.db)
    replay = rich.run("recover", "redrive", *rich.dbargs, "--queue-id", 1, "--file",
                      good, "--request-id", RICH_REQUEST, "--actor", "analyst01",
                      expect=0)
    assert replay["result"]["replayed"] is True
    assert replay["result"]["attempt"]["attempt_id"] == 1
    assert table_counts(rich.db) == before_counts
    assert snapshot(rich.db) == before_snapshot  # every row of every table

    def conflicts() -> int:
        return scalar(rich.db, "SELECT COUNT(*) FROM audit_event WHERE action = "
                               "'REQUEST_ID_CONFLICT' AND entity_type = "
                               "'interface_error_queue' AND entity_id = 1")

    before = table_counts(rich.db)
    assert conflicts() == 0
    env = rich.run("recover", "redrive", *rich.dbargs, "--queue-id", 1, "--file", good,
                   "--request-id", RICH_REQUEST, "--actor", "analyst02", expect=4)
    assert env["error"]["type"] == "REQUEST_ID_CONFLICT"
    assert env["result"]["attempt"] is None and env["result"]["replayed"] is False
    after = table_counts(rich.db)
    assert after["audit_event"] == before["audit_event"] + 1
    assert {t: n for t, n in after.items() if t != "audit_event"} == {
        t: n for t, n in before.items() if t != "audit_event"}
    assert conflicts() == 1
    history = rich.run("recover", "history", *rich.dbargs, "--queue-id", 1, expect=0)
    assert len(history["result"]["conflict_audit_events"]) == 1
    assert [a["request_id"] for a in history["result"]["attempts"]] == [RICH_REQUEST]


def test_export_refusals_and_out_file(rich: Lab) -> None:
    before = table_counts(rich.db)
    for extra in ([], ["--store"]):
        env = rich.run("export", *rich.dbargs, "--order-id", 2, "--format", "hl7",
                       *extra, expect=4)
        assert env["error"]["type"] == "REFUSED"
    existing = rich.root / "existing.hl7"
    existing.write_bytes(b"synthetic placeholder")
    env = rich.run("export", *rich.dbargs, "--order-id", 1, "--format", "hl7",
                   "--store", "--out", existing, expect=4)
    assert env["error"]["type"] == "REFUSED"
    assert existing.read_bytes() == b"synthetic placeholder"
    assert table_counts(rich.db) == before

    out = rich.root / "export.hl7"
    env = rich.run("export", *rich.dbargs, "--order-id", 1, "--format", "hl7",
                   "--store", "--out", out, expect=0)
    stored = scalar(rich.db, "SELECT payload FROM interface_message WHERE "
                             "message_id = ?", (env["result"]["stored_message_id"],))
    assert out.read_bytes() == stored.encode("utf-8")
    assert env["result"]["payload_sha256"] == sha256_path(out)
    assert "\n" not in stored and "\r" in stored  # HL7 CR segment terminators


def test_reconcile_error_exit_1(rich: Lab) -> None:
    # Direct SQL constructs a state the services never produce (test-only).
    with contextlib.closing(sqlite3.connect(rich.db)) as conn:
        conn.execute("UPDATE interface_message SET order_id = NULL WHERE message_id = 3")
        conn.commit()
    env = rich.run("reconcile", *rich.dbargs, expect=1)
    assert env["error"]["type"] == "CHECK_FAILED"
    assert env["result"]["summary"]["ERROR"] >= 1
    assert "MSG-001" in {f["rule_id"] for f in env["result"]["findings"]}


def test_unexpected_exception_exit_6(rich: Lab, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("test-only failure")

    monkeypatch.setattr(trace, "build_case", boom)
    env = rich.run("trace", *rich.dbargs, "--order-id", 1, expect=6)
    assert env["error"]["type"] == "INTERNAL"
    assert "RuntimeError" in env["error"]["message"]
    code, out, err = rich.text("trace", *rich.dbargs, "--order-id", 1)
    assert code == 6 and err.startswith("error: ") and "Traceback" not in err


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

READ_ONLY_COMMANDS: list[tuple[list[str], int]] = [
    (["worklist"], 0),
    (["worklist", "--status", "RESOLVED"], 0),
    (["worklist", "--status", "TERMINAL"], 0),
    (["worklist", "--status", "ALL"], 0),
    (["show-message", "--message-id", "1"], 0),
    (["show-message", "--message-id", "4", "--raw"], 0),
    (["trace", "--order-id", "1"], 0),
    (["trace", "--queue-id", "1"], 0),
    (["trace", "--message-id", "5"], 0),
    (["trace", "--accession", ACC2], 0),
    (["reconcile"], 0),
    (["reconcile", "--order-id", "1"], 0),
    (["reconcile", "--queue-id", "2"], 0),
    (["recover", "preview", "--queue-id", "2", "--action", "retry",
      "--request-id", "RO-PREVIEW-1", "--actor", "analyst01"], 0),
    (["recover", "preview", "--queue-id", "3", "--action", "retry",
      "--request-id", "RO-PREVIEW-2", "--actor", "analyst01"], 4),
    (["recover", "preview", "--queue-id", "1", "--action", "redrive", "--file",
      "{good}", "--request-id", RICH_REQUEST, "--actor", "analyst01"], 0),
    (["recover", "history", "--queue-id", "1"], 0),
    (["audit", "--order-id", "1"], 0),
    (["export", "--order-id", "1", "--format", "hl7"], 0),
    (["export", "--order-id", "1", "--format", "fhir",
      "--generated-at", "2026-10-25 09:00:00"], 0),
]


def test_read_only_commands_leave_database_identical(rich: Lab) -> None:
    before_sha = sha256_path(rich.db)
    good = rich.inputs / "good.hl7"
    for argv, expect in READ_ONLY_COMMANDS:
        full = _with_db(argv, rich.db, good)
        env = rich.run(*full, expect=expect)
        if argv[:2] == ["recover", "preview"]:
            assert env["result"]["snapshot"]["source_unchanged"] is True
        code, out, _err = rich.text(*full)
        assert code == expect
        assert out
        assert sha256_path(rich.db) == before_sha, argv
        assert sidecars(rich.db) == [], argv
    # The previews consumed no request ids and wrote no attempts.
    assert scalar(rich.db, "SELECT COUNT(*) FROM interface_recovery_attempt "
                           "WHERE request_id LIKE 'RO-PREVIEW-%'") == 0


def test_preview_outcomes_match_service_semantics(rich: Lab) -> None:
    ok = rich.run("recover", "preview", *rich.dbargs, "--queue-id", 2, "--action",
                  "retry", "--request-id", "PV-1", "--actor", "analyst01", expect=0)
    assert ok["result"]["simulated"]["status"] == "SUCCEEDED"
    assert ok["result"]["request"]["action"] == "RETRY_ORIGINAL"
    rejected = rich.run("recover", "preview", *rich.dbargs, "--queue-id", 3,
                        "--action", "retry", "--request-id", "PV-2", "--actor",
                        "analyst01", expect=4)
    assert rejected["error"]["type"] == "REFUSED"
    assert rejected["result"]["simulated"]["status"] == "REJECTED"
    replayed = rich.run("recover", "preview", *rich.dbargs, "--queue-id", 1, "--action",
                        "redrive", "--file", rich.inputs / "good.hl7", "--request-id",
                        RICH_REQUEST, "--actor", "analyst01", expect=0)
    assert replayed["result"]["simulated"]["status"] == "REPLAYED"
    assert replayed["result"]["request"]["action"] == "REDRIVE_CORRECTED"


def test_evidence_read_only_and_output_files(rich: Lab) -> None:
    before_sha = sha256_path(rich.db)
    out, report = rich.root / "ev.json", rich.root / "ev.md"
    env = rich.run("evidence", *rich.dbargs, "--queue-id", 1, "--scenario", "CLI-T",
                   "--title", "CLI test", "--source-revision", "test-revision",
                   "--out", out, "--report", report, expect=0)
    written = json.loads(out.read_text(encoding="ascii"))
    assert written["content_fingerprint"] == env["result"]["content_fingerprint"]
    assert env["result"]["evidence"]["content_fingerprint"] == written["content_fingerprint"]
    assert written["source"]["revision"] == "test-revision"
    assert report.read_bytes()
    code, text, _ = rich.text("evidence", *rich.dbargs, "--order-id", 1,
                              "--source-revision", "test-revision")
    assert code == 0 and text.startswith(SYNTHETIC_NOTICE + "\n")
    assert sha256_path(rich.db) == before_sha and sidecars(rich.db) == []

    # Existing outputs are refused before anything is written.
    existing = rich.root / "existing.json"
    existing.write_bytes(b"synthetic placeholder")
    new_report = rich.root / "new.md"
    env = rich.run("evidence", *rich.dbargs, "--queue-id", 1, "--out", existing,
                   "--report", new_report, expect=4)
    assert env["error"]["type"] == "REFUSED"
    assert existing.read_bytes() == b"synthetic placeholder"
    assert not new_report.exists()


def test_exercise_list_and_show_read_only(rich: Lab) -> None:
    before_sha = sha256_path(rich.db)
    listed = rich.run("exercise", "list", expect=0)
    ids = [e["id"] for e in listed["result"]["exercises"]]
    assert ids == sorted(ids) and "EX01-valid-result-export" in ids
    shown = rich.run("exercise", "show", "EX01-valid-result-export", expect=0)
    assert shown["result"]["instructions"]
    assert "solution_steps" not in json.dumps(shown["result"])
    for argv in (["exercise", "list"], ["exercise", "show", "EX01-valid-result-export"]):
        code, out, _ = rich.text(*argv)
        assert code == 0 and out.startswith(SYNTHETIC_NOTICE + "\n")
    rich.run("exercise", "show", "EX99-nope", expect=3)
    assert sha256_path(rich.db) == before_sha and sidecars(rich.db) == []


def test_exercise_setup_run_verify_and_run_all(tmp_path: Path, capsys: Any) -> None:
    lab = Lab(tmp_path, capsys)
    db = tmp_path / "ex01.db"
    run = lab.run("exercise", "run", "EX01-valid-result-export", "--db", db, expect=0)
    assert run["command"] == "exercise run"
    assert run["result"]["verification"]["passed"] is True
    assert all(step["matched"] for step in run["result"]["steps"])
    assert scalar(db, "SELECT status FROM lab_order WHERE accession_number = "
                      "'ACC-EX01-0001'") == "FINALIZED"
    before_sha = sha256_path(db)
    verify = lab.run("exercise", "verify", "EX01-valid-result-export", "--db", db,
                     expect=0)
    assert verify["result"]["passed"] is True
    assert sha256_path(db) == before_sha and sidecars(db) == []

    # A setup-only database does not yet satisfy the expected outcome: exit 1.
    setup_db = tmp_path / "ex01-setup.db"
    setup = lab.run("exercise", "setup", "EX01-valid-result-export", "--db", setup_db,
                    expect=0)
    assert all(step["matched"] for step in setup["result"]["steps"])
    failed = lab.run("exercise", "verify", "EX01-valid-result-export", "--db",
                     setup_db, expect=1)
    assert failed["error"]["type"] == "CHECK_FAILED"
    assert failed["result"]["passed"] is False
    code, out, err = lab.text("exercise", "verify", "EX01-valid-result-export",
                              "--db", setup_db)
    assert code == 1 and out.startswith(SYNTHETIC_NOTICE + "\n")
    assert err.startswith("error: ")

    # Exercises never reuse a database: an existing path is refused untouched.
    before_bytes = db.read_bytes()
    for action in ("setup", "run"):
        env = lab.run("exercise", action, "EX01-valid-result-export", "--db", db,
                      expect=4)
        assert env["error"]["type"] == "REFUSED"
    assert db.read_bytes() == before_bytes

    workdir = tmp_path / "all"
    every = lab.run("exercise", "run-all", "--workdir", workdir, expect=0)
    assert every["result"]["passed"] is True
    assert sorted(p.name for p in workdir.glob("*.db")) == [
        f"{e['id']}.db" for e in lab.run("exercise", "list", expect=0)["result"]["exercises"]
    ]


# ---------------------------------------------------------------------------
# Connections are always closed
# ---------------------------------------------------------------------------

def test_no_connection_left_unclosed(rich: Lab) -> None:
    """Python 3.13+ warns when a sqlite3 connection is collected unclosed."""
    import gc
    import warnings

    good = rich.inputs / "good.hl7"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        for argv, _expect in READ_ONLY_COMMANDS + TEXT_COMMANDS:
            rich.text(*_with_db(argv, rich.db, good))
        for argv in UNKNOWN_ID_COMMANDS:
            rich.text(*_with_db(argv, rich.db, good))
        rich.text("finalize", *rich.dbargs, "--order-id", 1, "--by", "a")
        gc.collect()
    leaks = [w for w in caught if issubclass(w.category, ResourceWarning)
             and "sqlite3" in str(w.message).lower() + str(w.filename).lower()]
    assert leaks == []


# ---------------------------------------------------------------------------
# Exact payload preservation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("terminator", ["\r", "\r\n"], ids=["CR", "CRLF"])
def test_ingest_preserves_exact_payload_bytes(open_lab: Lab, terminator: str) -> None:
    text = oru(f"CLI-BYTES-{len(terminator)}", ACC1, terminator=terminator,
               last_name="Synth\u00e9tic")  # multibyte UTF-8 on purpose
    raw = text.encode("utf-8")
    path = open_lab.write("bytes.hl7", raw)
    env = open_lab.run("ingest", *open_lab.dbargs, "--file", path, expect=0)
    message_id = env["result"]["message_id"]
    stored = scalar(open_lab.db, "SELECT payload FROM interface_message WHERE "
                                 "message_id = ?", (message_id,))
    file_sha = hashlib.sha256(raw).hexdigest()
    assert hashlib.sha256(stored.encode("utf-8")).hexdigest() == file_sha
    assert env["result"]["payload_sha256"] == file_sha
    assert env["result"]["payload_bytes"] == len(raw)
    segments = 4 + len(FULL_RESULTS)
    if terminator == "\r":
        assert "\n" not in stored and stored.count("\r") == segments
    else:
        assert stored.count("\r\n") == segments
    shown = open_lab.run("show-message", *open_lab.dbargs, "--message-id", message_id,
                         expect=0)
    assert shown["result"]["payload_sha256"] == file_sha
    assert shown["result"]["payload"] == text

    proc = run_proc("show-message", *open_lab.dbargs, "--message-id", message_id, "--raw")
    assert proc.returncode == 0 and proc.stderr == b""
    assert proc.stdout == raw


# ---------------------------------------------------------------------------
# Immutability and rollback through the CLI
# ---------------------------------------------------------------------------

def _original_state(db: Path) -> dict[str, Any]:
    return {
        "message": query(db, "SELECT payload, control_id, status, created_at, order_id "
                             "FROM interface_message WHERE message_id = 1"),
        "queue_raw": scalar(db, "SELECT raw_payload FROM interface_error_queue "
                                "WHERE queue_id = 1"),
    }


def test_corrected_redrive_keeps_original_immutable(open_lab: Lab) -> None:
    before = _original_state(open_lab.db)
    corrected = oru("CLI-CORR", ACC1, terminator="\r\n").encode("utf-8")
    path = open_lab.write("corrected.hl7", corrected)
    env = open_lab.run("recover", "redrive", *open_lab.dbargs, "--queue-id", 1,
                       "--file", path, "--request-id", "R-IMM-1", "--actor",
                       "analyst01", expect=0)
    attempt = env["result"]["attempt"]
    assert attempt["outcome"] == "SUCCEEDED" and env["result"]["replayed"] is False
    assert env["result"]["resulting_message_status"] == "FILED"
    assert env["result"]["queue_status"] == "RESOLVED"
    assert _original_state(open_lab.db) == before
    assert before["message"][0]["status"] == "ERRORED"
    resulting = scalar(open_lab.db, "SELECT payload FROM interface_message WHERE "
                                    "message_id = ?", (attempt["resulting_message_id"],))
    assert resulting.encode("utf-8") == corrected
    assert attempt["payload_sha256"] == hashlib.sha256(corrected).hexdigest()


def test_failed_redrive_rolls_back_then_later_success(
    open_lab: Lab, monkeypatch: pytest.MonkeyPatch
) -> None:
    corrected = open_lab.write("corrected.hl7", oru("CLI-CORR", ACC1))
    real = workflow.enter_fish_result
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> int:
        # Test-only injection: the second probe write raises a handled inbound error.
        calls["n"] += 1
        if calls["n"] == 2:
            raise inbound_hl7.InboundError(
                "INVALID_CELL_COUNT", "test-only injected failure on the second probe"
            )
        return real(*args, **kwargs)

    before_state = _original_state(open_lab.db)
    before = table_counts(open_lab.db)
    monkeypatch.setattr(workflow, "enter_fish_result", flaky)
    env = open_lab.run("recover", "redrive", *open_lab.dbargs, "--queue-id", 1,
                       "--file", corrected, "--request-id", "R-FAIL-1", "--actor",
                       "analyst01", expect=4)
    monkeypatch.undo()
    assert calls["n"] == 2
    assert env["error"]["type"] == "REFUSED"
    attempt = env["result"]["attempt"]
    assert attempt["outcome"] == "FAILED"
    assert env["result"]["resulting_message_status"] == "ERRORED"

    db = open_lab.db
    assert scalar(db, "SELECT status FROM interface_message WHERE message_id = ?",
                  (attempt["resulting_message_id"],)) == "ERRORED"
    assert scalar(db, "SELECT COUNT(*) FROM fish_result WHERE order_id = 1") == 0
    assert scalar(db, "SELECT COUNT(*) FROM audit_event WHERE order_id = 1 AND action "
                      "IN ('RESULT_ENTERED', 'INBOUND_RESULT_FILED')") == 0
    assert scalar(db, "SELECT status FROM interface_error_queue WHERE queue_id = 1") == "OPEN"
    assert scalar(db, "SELECT status FROM lab_order WHERE order_id = 1") == "IN_PROCESS"
    after = table_counts(db)
    assert after["interface_message"] == before["interface_message"] + 1
    assert after["interface_recovery_attempt"] == before["interface_recovery_attempt"] + 1
    assert {t: n for t, n in after.items()
            if t not in ("interface_message", "interface_recovery_attempt")} == {
        t: n for t, n in before.items()
        if t not in ("interface_message", "interface_recovery_attempt")}
    assert _original_state(db) == before_state

    env = open_lab.run("recover", "redrive", *open_lab.dbargs, "--queue-id", 1,
                       "--file", corrected, "--request-id", "R-FAIL-2", "--actor",
                       "analyst01", expect=0)
    assert env["result"]["attempt"]["outcome"] == "SUCCEEDED"
    assert scalar(db, "SELECT COUNT(*) FROM fish_result WHERE order_id = 1") == 9
    assert scalar(db, "SELECT status FROM interface_error_queue WHERE queue_id = 1") == "RESOLVED"
    assert [r["outcome"] for r in query(
        db, "SELECT outcome FROM interface_recovery_attempt ORDER BY attempt_id")] == [
        "FAILED", "SUCCEEDED"]


# ---------------------------------------------------------------------------
# Text mode
# ---------------------------------------------------------------------------

TEXT_COMMANDS: list[tuple[list[str], int]] = [
    (["worklist", "--status", "ALL"], 0),
    (["show-message", "--message-id", "4"], 0),
    (["trace", "--order-id", "1"], 0),
    (["trace", "--queue-id", "2"], 0),
    (["trace", "--message-id", "1"], 0),
    (["trace", "--accession", ACC1], 0),
    (["reconcile"], 0),
    (["recover", "preview", "--queue-id", "2", "--action", "retry",
      "--request-id", "TX-P-1", "--actor", "analyst01"], 0),
    (["recover", "preview", "--queue-id", "3", "--action", "retry",
      "--request-id", "TX-P-2", "--actor", "analyst01"], 4),
    (["recover", "history", "--queue-id", "1"], 0),
    (["audit", "--order-id", "1"], 0),
    (["export", "--order-id", "1", "--format", "hl7"], 0),
    (["export", "--order-id", "1", "--format", "fhir"], 0),
    (["result-enter", "--order-id", "2", "--probe", "CEP8", "--scored", "200",
      "--abnormal", "4", "--pattern", "2 aqua", "--interpretation", "NORMAL",
      "--entered-by", "tech01"], 0),
    (["finalize", "--order-id", "2", "--by", "analyst01"], 4),
    (["ingest", "--file", "{good}"], 4),
    (["recover", "retry", "--queue-id", "2", "--request-id", "TX-R-1",
      "--actor", "analyst01"], 0),
    (["recover", "retry", "--queue-id", "2", "--request-id", "TX-R-1",
      "--actor", "analyst01"], 0),
    (["recover", "retry", "--queue-id", "2", "--request-id", "TX-R-1",
      "--actor", "analyst02"], 4),
    (["recover", "redrive", "--queue-id", "3", "--file", "{good}",
      "--request-id", "TX-R-2", "--actor", "analyst01"], 4),
    (["case-create", "--mrn", "SYN-CLI-04", "--last-name", "Synthetic",
      "--first-name", "Text", "--dob", "1970-01-04", "--sex", "U", "--accession",
      "ACC-CLI-0004", "--provider", "Dr. Synthetic", "--no-accession"], 0),
    (["specimen-accession", "--specimen-id", "3"], 0),
]


def test_text_mode_output_for_every_command(rich: Lab) -> None:
    good = rich.inputs / "good.hl7"
    for argv, expect in TEXT_COMMANDS:
        code, out, err = rich.text(*_with_db(argv, rich.db, good))
        assert code == expect, (argv, err)
        assert out.startswith(SYNTHETIC_NOTICE + "\n"), argv
        assert len(out.splitlines()) >= 2, argv
        if expect == 0:
            assert err == "", argv
        else:
            assert err.startswith("error: ") and "Traceback" not in err, argv
    code, out, err = rich.text("init", "--db", rich.root / "fresh.db")
    assert code == 0 and out.startswith(SYNTHETIC_NOTICE + "\n")
    code, out, err = rich.text("trace", "--db", rich.root / "nope.db", "--order-id", 1)
    assert code == 3 and out == "" and err.startswith("error: ")


def test_text_trace_has_all_sections(rich: Lab) -> None:
    code, out, _ = rich.text("trace", *rich.dbargs, "--queue-id", 1)
    assert code == 0
    for heading in ("case anchor: queue 1", "== orders", "== messages", "== queue items",
                    "== recovery attempts", "== links", "== timeline",
                    "== provenance notes", "== limitations"):
        assert heading in out
    assert "QUEUE_ORIGINAL" in out and "RECOVERY_RESULT" in out
    assert "FOREIGN_KEY" in out and "PAYLOAD_DERIVED" in out


def test_text_recovery_outcome_line(open_lab: Lab) -> None:
    corrected = open_lab.write("corrected.hl7", oru("CLI-CORR", ACC1))
    code, out, err = open_lab.text("recover", "redrive", *open_lab.dbargs, "--queue-id",
                                   1, "--file", corrected, "--request-id", "TX-OUT-1",
                                   "--actor", "analyst01")
    assert code == 0 and err == ""
    assert out.splitlines()[1] == (
        "recovery attempt 1: SUCCEEDED (REDRIVE_CORRECTED) -> message 2 FILED; "
        "queue 1 RESOLVED"
    )


def test_text_mode_survives_ascii_terminal(rich: Lab) -> None:
    # The report summary puts an em dash into the outbound HL7 payload.
    payload = scalar(rich.db, "SELECT payload FROM interface_message WHERE message_id = 4")
    assert "\u2014" in payload
    for argv in (
        ["export", *rich.dbargs, "--order-id", 1, "--format", "hl7"],
        ["show-message", *rich.dbargs, "--message-id", 4],
    ):
        proc = run_proc(*argv, env_extra={"PYTHONIOENCODING": "ascii"})
        assert proc.returncode == 0, proc.stderr
        text = proc.stdout.decode("ascii")  # every byte is ASCII
        assert text.startswith(SYNTHETIC_NOTICE + "\n")
        assert "\\u2014" in text


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

HELP_COMMANDS = [
    [], ["init"], ["case-create"], ["specimen-accession"], ["result-enter"],
    ["finalize"], ["ingest"], ["worklist"], ["show-message"], ["trace"],
    ["reconcile"], ["recover"], ["recover", "preview"], ["recover", "retry"],
    ["recover", "redrive"], ["recover", "history"], ["export"], ["audit"],
    ["evidence"], ["exercise"], ["exercise", "list"], ["exercise", "show"],
    ["exercise", "setup"], ["exercise", "run"], ["exercise", "verify"],
    ["exercise", "run-all"],
]


@pytest.mark.parametrize("words", HELP_COMMANDS, ids=lambda w: " ".join(w) or "top")
def test_help_exits_0(capsys: Any, words: list[str]) -> None:
    capsys.readouterr()
    assert cli.main(words + ["--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: python -m src.analyst")
    assert "exit codes:" in out
    for code, kind in ((0, "OK"), (1, "CHECK_FAILED"), (2, "USAGE"), (3, "NOT_FOUND"),
                       (4, "REFUSED"), (5, "INVALID_INPUT"), (6, "INTERNAL")):
        assert f"  {code}  {kind}" in out
    assert "read-only commands" in out and "write commands" in out
    capsys.readouterr()
    assert cli.main(["--json"] + words + ["--help"]) == 0
