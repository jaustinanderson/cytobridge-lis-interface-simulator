"""Executable synthetic incident exercises and their database-querying verifier.

Each exercise lives under ``exercises/<EXID>/`` (analyst README, ``setup.json``
steps, synthetic ``inputs/*.hl7`` fixtures); its instructor answer key lives
apart, in ``exercises/answer-keys/<EXID>.json`` (``solution_steps`` and the
``expected`` persisted outcome). See ``exercises/README.md`` and
``exercises/answer-keys/README.md`` for the file formats.

Runner
------
A STEP is ``{"argv", "expect_exit", "note"}``. Steps run IN-PROCESS through the
analyst CLI: ``"{db}"`` and ``"{inputs}"`` (the absolute path of the exercise
``inputs/`` directory) are substituted as substrings of every argv element,
``--json`` is appended, and ``cli.main(argv)`` is called with stdout captured.
The JSON envelope is parsed into a STEP_RESULT ``{"argv", "exit_code",
"expect_exit", "matched", "ok", "error"}``. The first step whose exit code
differs from ``expect_exit`` stops the remaining steps. Every write a step makes
therefore goes through the CLI and, behind it, the existing public services.

``setup_exercise`` runs only the ``setup.json`` steps on a NEW database path
(an existing path is refused before anything runs). ``run_exercise`` runs the
setup, then the answer key's ``solution_steps``, then ``verify_exercise`` on
whatever was persisted. ``run_all`` does that for every exercise in its own new
database under one working directory.

Verifier
--------
``verify_exercise`` is read-only (``store.open_read_only``: SQLite ``mode=ro``
plus ``PRAGMA query_only``) and reads one snapshot (``snapshot=True``: a single
read transaction, so every check sees the same committed state). It never
looks at CLI output or exit codes: every
observed value comes from SQL queries on the persisted rows (plus the read-only
``validation.validate_order`` and ``reconcile.reconcile_database``, which only
SELECT), and every fixture fingerprint is the SHA-256 of the fixture FILE BYTES,
compared with ``sha256_text`` of the payload TEXT stored in the database (not
only with a stored fingerprint column). Each check is ``{"name", "passed",
"expected", "observed"}``; ``passed`` compares canonical JSON, so ``True`` and
``1`` are different values. Checks are emitted in a fixed order: orders,
queue_items, outbound (each in answer-key list order, per-probe and per-action
keys sorted), totals by table name, then reconcile_error_count.

An outbound ``matches_results`` check compares, per probe, what each stored
HL7/FHIR export carries (counts, percent abnormal, interpretation, the N/A/I
flag or code, signal pattern) with values recomputed from the current
``fish_result`` rows (``_expected_export_values``).

Automated exercise execution is automated testing of the simulator. It is not
manual UAT and is not evidence of any analyst's understanding or mastery. The
expected outcomes are educational exercise expectations, not clinical or
regulatory requirements.

This is an educational, synthetic simulator - not a production LIS, certified
interface engine, validated medical device, or Epic/Beaker implementation. All
data is synthetic. No PHI.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .. import validation
from ..interfaces import INTERPRETATION_FLAG, inbound_hl7, outbound_fhir
from . import reconcile, store
from .common import (
    EXIT_INTERNAL,
    REPO_ROOT,
    AnalystError,
    canonical_json,
    sha256_bytes,
    sha256_text,
)

EXERCISES_DIR: Path = REPO_ROOT / "exercises"
ANSWER_KEYS_DIR: Path = EXERCISES_DIR / "answer-keys"

SETUP_FILE = "setup.json"
INSTRUCTIONS_FILE = "README.md"
INPUTS_DIR = "inputs"

# Keys that belong only in an answer key. setup.json and the analyst-facing
# instructions must never carry them (instructions and answers stay separate).
ANSWER_KEY_ONLY_KEYS = ("expected", "question_answers", "solution_steps")

_PLACEHOLDER_RE = re.compile(r"\{db\}|\{inputs\}")

# EXPECTED vocabulary. The constants below are the authoritative definition;
# exercises/answer-keys/README.md documents them for answer-key authors. Any
# other key inside ``expected`` is reported as a failing check rather than being
# silently ignored, so an expectation is never quietly skipped.
_EXPECTED_SECTIONS = (
    "orders", "queue_items", "outbound", "totals", "reconcile_error_count",
)
_ORDER_KEYS = (
    "accession", "status", "result_count", "results", "report_status",
    "report_finalized_by", "audit_action_counts", "validation_blocking",
)
# Per-probe fish_result columns an answer key may state.
_RESULT_FIELDS = (
    "cells_scored", "cells_abnormal", "signal_pattern", "interpretation", "entered_by",
)
_QUEUE_KEYS = (
    "original_control_id", "failure_code", "failure_category", "recovery_policy",
    "status", "original_message_status", "original_payload_fixture",
    "conflict_audit_events", "attempts",
)
_QUEUE_COLUMNS = ("failure_code", "failure_category", "recovery_policy", "status")
_ATTEMPT_KEYS = (
    "request_id", "action", "outcome", "actor", "payload_fixture",
    "resulting_message_status",
)
_ATTEMPT_COLUMNS = ("request_id", "action", "outcome", "actor")
_OUTBOUND_KEYS = ("accession", "format", "count", "matches_results")
_OUTBOUND_FORMATS = ("HL7", "FHIR")

_CONFLICT_ACTION = "REQUEST_ID_CONFLICT"

# Per-probe value as outbound_hl7._obx_segments writes OBX-5 (before escaping):
#   "<abnormal>/<scored> nuclei (<percent>%) <INTERPRETATION>; signal <pattern>"
_HL7_PROBE_VALUE_RE = re.compile(
    r"(\d+)/(\d+) nuclei \(([^()]*)%\) ([A-Z]+); signal (.*)", re.S
)
# Observation.note[0].text as outbound_fhir._observation_resource writes it:
#   "<abnormal>/<scored> nuclei scored; signal pattern <pattern>"
_FHIR_NOTE_RE = re.compile(r"(\d+)/(\d+) nuclei scored; signal pattern (.*)", re.S)
# HL7 escape sequences outbound_hl7._esc produces.
_HL7_ESCAPE_RE = re.compile(r"\\([FSRTE])\\")
_HL7_UNESCAPE = {"F": "|", "S": "^", "R": "~", "T": "&", "E": "\\"}


# ---------------------------------------------------------------------------
# Loading exercise content
# ---------------------------------------------------------------------------

def _exercise_dirs() -> dict[str, Path]:
    """Every exercise directory (one holding a setup.json), keyed by its name."""
    if not EXERCISES_DIR.is_dir():
        return {}
    return {
        p.name: p
        for p in sorted(EXERCISES_DIR.iterdir())
        if p.is_dir() and p != ANSWER_KEYS_DIR and (p / SETUP_FILE).is_file()
    }


def _resolve_id(ex_id: str) -> str:
    """Resolve a full EXID, or a unique case-insensitive prefix of one.

    An unknown id raises ``AnalystError("NOT_FOUND")``; a prefix shared by more
    than one exercise raises ``AnalystError("USAGE")``.
    """
    known = sorted(_exercise_dirs())
    text = str(ex_id)
    if text in known:
        return text
    matches = [k for k in known if k.lower().startswith(text.lower())] if text else known
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise AnalystError(
            "NOT_FOUND",
            f"No exercise matches {ex_id!r}. Known exercises: {', '.join(known)}.",
        )
    raise AnalystError(
        "USAGE",
        f"Exercise id {ex_id!r} is ambiguous; it matches {', '.join(matches)}. "
        f"Give the full id or a longer prefix.",
    )


def _read_json(path: Path, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise AnalystError("NOT_FOUND", f"{label} {path} does not exist.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AnalystError("INVALID_INPUT", f"{label} {path} is not readable JSON: {exc}.")
    if not isinstance(data, dict):
        raise AnalystError("INVALID_INPUT", f"{label} {path} must hold a JSON object.")
    return data


def _check_steps(steps: Any, label: str) -> list[dict[str, Any]]:
    """Validate a STEP list's shape (argv of strings, integer expect_exit)."""
    if not isinstance(steps, list):
        raise AnalystError("INVALID_INPUT", f"{label} must be a list of steps.")
    for i, step in enumerate(steps, start=1):
        ok = (
            isinstance(step, dict)
            and isinstance(step.get("argv"), list)
            and step["argv"]
            and all(isinstance(a, str) for a in step["argv"])
            and isinstance(step.get("expect_exit"), int)
            and not isinstance(step.get("expect_exit"), bool)
        )
        if not ok:
            raise AnalystError(
                "INVALID_INPUT",
                f"{label} step {i} must be {{\"argv\": [str, ...], "
                f"\"expect_exit\": int, \"note\": str}}.",
            )
    return steps


def _load_setup(resolved: str) -> dict[str, Any]:
    path = EXERCISES_DIR / resolved / SETUP_FILE
    setup = _read_json(path, "Exercise setup file")
    if setup.get("id") != resolved:
        raise AnalystError(
            "INVALID_INPUT",
            f"{path} declares id {setup.get('id')!r}, expected {resolved!r}.",
        )
    leaked = sorted(k for k in ANSWER_KEY_ONLY_KEYS if k in setup)
    if leaked:
        raise AnalystError(
            "INVALID_INPUT",
            f"{path} carries answer-key content ({', '.join(leaked)}); solution "
            f"steps and expected outcomes belong only in the answer key.",
        )
    _check_steps(setup.get("steps"), f"{path}")
    return setup


def _load_answer_key(resolved: str) -> dict[str, Any]:
    path = ANSWER_KEYS_DIR / f"{resolved}.json"
    key = _read_json(path, "Exercise answer key")
    if key.get("id") != resolved:
        raise AnalystError(
            "INVALID_INPUT",
            f"{path} declares id {key.get('id')!r}, expected {resolved!r}.",
        )
    _check_steps(key.get("solution_steps", []), f"{path} solution_steps")
    if not isinstance(key.get("expected", {}), dict):
        raise AnalystError("INVALID_INPUT", f"{path} 'expected' must be an object.")
    return key


def list_exercises() -> list[dict[str, Any]]:
    """Return ``[{"id", "title", "summary"}]`` for every exercise, sorted by id."""
    items = []
    for name in sorted(_exercise_dirs()):
        setup = _load_setup(name)
        items.append({
            "id": setup["id"],
            "title": setup.get("title"),
            "summary": setup.get("summary"),
        })
    return sorted(items, key=lambda e: e["id"])


def load_exercise(ex_id: str) -> dict[str, Any]:
    """Load one exercise's setup and answer key.

    Returns ``{"id", "title", "summary", "directory", "inputs",
    "instructions_path", "answer_key_path", "setup", "answer_key"}`` (paths are
    absolute strings). Unknown id -> ``AnalystError("NOT_FOUND")``; ambiguous
    prefix -> ``AnalystError("USAGE")``. The answer key is instructor material;
    callers that show analyst-facing content must not print it.
    """
    resolved = _resolve_id(ex_id)
    setup = _load_setup(resolved)
    key = _load_answer_key(resolved)
    directory = EXERCISES_DIR / resolved
    return {
        "id": resolved,
        "title": setup.get("title"),
        "summary": setup.get("summary"),
        "directory": str(directory),
        "inputs": str(directory / INPUTS_DIR),
        "instructions_path": str(directory / INSTRUCTIONS_FILE),
        "answer_key_path": str(ANSWER_KEYS_DIR / f"{resolved}.json"),
        "setup": setup,
        "answer_key": key,
    }


def instructions_text(ex_id: str) -> str:
    """Return the exercise's analyst-facing README.md text (no answers)."""
    resolved = _resolve_id(ex_id)
    path = EXERCISES_DIR / resolved / INSTRUCTIONS_FILE
    if not path.is_file():
        raise AnalystError("NOT_FOUND", f"Exercise instructions {path} do not exist.")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Running steps in-process through the CLI
# ---------------------------------------------------------------------------

def _substitute(argv: list[str], db_path: str, inputs_dir: str) -> list[str]:
    """Replace ``{db}`` and ``{inputs}`` substrings in one pass per element."""
    values = {"{db}": db_path, "{inputs}": inputs_dir}
    return [_PLACEHOLDER_RE.sub(lambda m: values[m.group(0)], a) for a in argv]


def _run_step(step: dict[str, Any], db_path: str, inputs_dir: str) -> dict[str, Any]:
    """Run one STEP through ``cli.main`` with ``--json``; return its STEP_RESULT.

    ``matched`` requires the exit code to equal ``expect_exit`` AND the captured
    stdout to be one JSON envelope reporting that same exit code.
    """
    from . import cli  # lazy: cli imports this module lazily too

    argv = _substitute(step["argv"], db_path, inputs_dir)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            exit_code = cli.main(argv + ["--json"])
    except Exception as exc:  # cli.main should never raise; record it if it does
        exit_code = EXIT_INTERNAL
        envelope = None
        error: Any = {
            "type": "INTERNAL",
            "message": f"cli.main raised {type(exc).__name__}: {exc}",
        }
    else:
        try:
            envelope = json.loads(out.getvalue())
            if not isinstance(envelope, dict):
                raise ValueError("not a JSON object")
            error = envelope.get("error")
        except ValueError as exc:
            envelope = None
            error = {
                "type": "INTERNAL",
                "message": f"step output was not one JSON envelope ({exc}).",
            }
    expect_exit = step["expect_exit"]
    consistent = envelope is not None and envelope.get("exit_code") == exit_code
    return {
        "argv": argv,
        "exit_code": exit_code,
        "expect_exit": expect_exit,
        "matched": exit_code == expect_exit and consistent,
        "ok": bool(envelope.get("ok")) if envelope is not None else False,
        "error": error,
    }


def _run_steps(
    steps: list[dict[str, Any]], db_path: str, inputs_dir: str
) -> tuple[list[dict[str, Any]], bool]:
    """Run steps in order, stopping after the first unmatched one."""
    results: list[dict[str, Any]] = []
    for step in steps:
        result = _run_step(step, db_path, inputs_dir)
        results.append(result)
        if not result["matched"]:
            return results, False
    return results, True


def _refuse_existing_database(db_path: str | Path) -> Path:
    """Refuse an existing path (or a missing parent) before any step runs."""
    p = Path(db_path)
    if p.exists() or p.is_symlink():
        raise AnalystError(
            "REFUSED",
            f"Refusing to set up an exercise in {p}: the path already exists. "
            f"Exercises always start from a new database; choose a new path.",
        )
    parent = p.parent if str(p.parent) else Path(".")
    if not parent.is_dir():
        raise AnalystError("NOT_FOUND", f"Directory {parent} does not exist.")
    return p


def _inputs_dir(resolved: str) -> str:
    return str(EXERCISES_DIR / resolved / INPUTS_DIR)


def setup_exercise(ex_id: str, db_path: str | Path) -> dict[str, Any]:
    """Create a NEW database at ``db_path`` and run the exercise's setup steps.

    Refuses (``AnalystError("REFUSED")``) when ``db_path`` already exists; the
    existing file is not touched. Returns ``{"exercise", "database", "steps"}``.
    """
    resolved = _resolve_id(ex_id)
    setup = _load_setup(resolved)
    p = _refuse_existing_database(db_path)
    steps, _ = _run_steps(setup["steps"], str(p), _inputs_dir(resolved))
    return {"exercise": resolved, "database": str(p), "steps": steps}


def run_exercise(ex_id: str, db_path: str | Path) -> dict[str, Any]:
    """Run setup, then the answer key's solution steps, then verification.

    Solution steps run only if every setup step matched. Verification always
    runs on whatever was persisted. Refuses an existing ``db_path`` before any
    step runs. Returns ``{"exercise", "database", "steps", "verification"}``.
    """
    loaded = load_exercise(ex_id)
    resolved = loaded["id"]
    p = _refuse_existing_database(db_path)
    inputs_dir = _inputs_dir(resolved)
    steps, all_matched = _run_steps(loaded["setup"]["steps"], str(p), inputs_dir)
    if all_matched:
        more, _ = _run_steps(
            loaded["answer_key"].get("solution_steps", []), str(p), inputs_dir
        )
        steps += more
    try:
        store.open_read_only(p).close()
    except AnalystError as exc:
        # Nothing verifiable was persisted (for example init itself failed).
        # Exercise-content errors are not caught here: they propagate from
        # verify_exercise below.
        verification = {
            "exercise": resolved,
            "passed": False,
            "checks": [{
                "name": f"{_short(resolved)} database readable",
                "passed": False,
                "expected": "an initialized CytoBridge database",
                "observed": f"{exc.kind}: {exc.message}",
            }],
        }
    else:
        verification = verify_exercise(resolved, p)
    return {
        "exercise": resolved,
        "database": str(p),
        "steps": steps,
        "verification": verification,
    }


def run_all(workdir: str | Path) -> dict[str, Any]:
    """Run every exercise in its own NEW database ``<workdir>/<EXID>.db``.

    Creates ``workdir`` if it is missing (its parent must exist, else
    ``NOT_FOUND``). Refuses (``REFUSED``) before running anything when any of
    the target database files already exists. Returns ``{"passed",
    "exercises"}``; each item is a ``run_exercise`` result plus ``"passed"``
    (every step matched and verification passed), in exercise-id order.
    """
    w = Path(workdir)
    ids = [e["id"] for e in list_exercises()]
    if w.exists() or w.is_symlink():
        if not w.is_dir():
            raise AnalystError("INVALID_INPUT", f"Work directory {w} is not a directory.")
        existing = [str(w / f"{i}.db") for i in ids
                    if (w / f"{i}.db").exists() or (w / f"{i}.db").is_symlink()]
        if existing:
            raise AnalystError(
                "REFUSED",
                f"Refusing to run exercises in {w}: {len(existing)} exercise "
                f"database(s) already exist ({', '.join(existing)}). Exercises "
                f"always start from new databases; choose a new work directory.",
            )
    else:
        parent = w.parent if str(w.parent) else Path(".")
        if not parent.is_dir():
            raise AnalystError("NOT_FOUND", f"Directory {parent} does not exist.")
        w.mkdir()
    items = []
    for ex in ids:
        result = run_exercise(ex, w / f"{ex}.db")
        passed = all(s["matched"] for s in result["steps"]) and bool(
            result["verification"]["passed"]
        )
        items.append(dict(result, passed=passed))
    return {"passed": bool(items) and all(i["passed"] for i in items), "exercises": items}


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def _short(resolved: str) -> str:
    """Check-name prefix: the EXID's leading token, e.g. ``EX03``."""
    return resolved.split("-", 1)[0]


class _Checks:
    """Accumulates ``{"name", "passed", "expected", "observed"}`` checks."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, expected: Any, observed: Any) -> bool:
        observed = _jsonable(observed)
        passed = canonical_json(expected) == canonical_json(observed)
        self.items.append({
            "name": f"{self.prefix} {name}",
            "passed": passed,
            "expected": expected,
            "observed": observed,
        })
        return passed

    def unsupported(self, where: str, keys: list[str]) -> None:
        for k in sorted(keys):
            self.items.append({
                "name": f"{self.prefix} answer key {where} key {k!r} is supported",
                "passed": False,
                "expected": "a key the verifier checks",
                "observed": k,
            })


def _fixture_sha256(resolved: str, relative: str) -> str:
    """SHA-256 of a fixture FILE's exact bytes, path relative to the exercise dir."""
    base = (EXERCISES_DIR / resolved).resolve()
    path = (base / str(relative)).resolve()
    if base not in path.parents:
        raise AnalystError(
            "INVALID_INPUT",
            f"Fixture path {relative!r} for {resolved} is outside the exercise directory.",
        )
    if not path.is_file():
        raise AnalystError("NOT_FOUND", f"Fixture {path} for {resolved} does not exist.")
    return sha256_bytes(path.read_bytes())


def _text_sha(text: Any) -> str | None:
    """sha256_text of stored payload TEXT; None for NULL; a marker for non-text."""
    if text is None:
        return None
    if not isinstance(text, str):
        return f"NON_TEXT_VALUE({type(text).__name__})"
    return sha256_text(text)


def _jsonable(value: Any) -> Any:
    """A JSON-safe form of an observed value (BLOBs from a damaged row become markers)."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"BLOB(sha256={sha256_bytes(bytes(value))})"
    return value


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _order_row(conn: sqlite3.Connection, accession: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT order_id, status, panel_id FROM lab_order WHERE accession_number = ?",
        (accession,),
    ).fetchone()


def _current_results(conn: sqlite3.Connection, order_id: int) -> dict[str, dict[str, Any]]:
    """``{probe_code: {<each _RESULT_FIELDS column>}}`` from the order's fish_result rows."""
    rows = conn.execute(
        "SELECT pr.probe_code, fr.cells_scored, fr.cells_abnormal, fr.signal_pattern, "
        "       fr.interpretation, fr.entered_by "
        "FROM fish_result fr JOIN probe pr ON pr.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? ORDER BY pr.probe_code",
        (order_id,),
    ).fetchall()
    return {r["probe_code"]: {f: r[f] for f in _RESULT_FIELDS} for r in rows}


def _percent_abnormal(scored: Any, abnormal: Any) -> float | None:
    """Percent abnormal recomputed the way ``interfaces.collect_report_data`` does.

    ``round(100.0 * abnormal / scored, 1)``, or ``0.0`` when nothing was scored.
    None when a stored count is not an integer (a damaged row), so it never matches.
    """
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (scored, abnormal)):
        return None
    return round(100.0 * abnormal / scored, 1) if scored else 0.0


def _expected_export_values(
    current: dict[str, dict[str, Any]], fmt: str
) -> dict[str, dict[str, Any]]:
    """Per-probe values the outbound generators write for the current fish_result rows.

    Counts, signal pattern and interpretation are the fish_result columns.
    ``percent_abnormal`` is recomputed (``_percent_abnormal``): HL7 OBX-5 writes
    it as text (``str`` of the rounded float, e.g. ``"1.5"``), FHIR as the number
    ``valueQuantity.value``. ``flag`` is ``interfaces.INTERPRETATION_FLAG`` of the
    interpretation (N/A/I; None for a value outside that mapping), written to
    HL7 OBX-8 and to the FHIR interpretation coding code.
    """
    out: dict[str, dict[str, Any]] = {}
    for code, r in current.items():
        pct = _percent_abnormal(r["cells_scored"], r["cells_abnormal"])
        if fmt == "HL7" and pct is not None:
            pct = str(pct)
        out[code] = {
            "cells_scored": r["cells_scored"],
            "cells_abnormal": r["cells_abnormal"],
            "percent_abnormal": pct,
            "interpretation": r["interpretation"],
            "flag": INTERPRETATION_FLAG.get(r["interpretation"]),
            "signal_pattern": r["signal_pattern"],
        }
    return out


def _verify_orders(
    conn: sqlite3.Connection, checks: _Checks, orders: list[Any]
) -> None:
    for i, exp in enumerate(orders):
        if not isinstance(exp, dict) or not isinstance(exp.get("accession"), str):
            checks.unsupported(f"orders[{i}]", ["accession (missing or not a string)"])
            continue
        checks.unsupported(f"orders[{i}]", [k for k in exp if k not in _ORDER_KEYS])
        acc = exp["accession"]
        label = f"order[{acc}]"
        order = _order_row(conn, acc)
        if not checks.add(f"{label} exists", True, order is not None):
            continue
        oid = order["order_id"]
        if "status" in exp:
            checks.add(f"{label} status", exp["status"], order["status"])
        if "result_count" in exp:
            checks.add(
                f"{label} result_count",
                exp["result_count"],
                _scalar(conn, "SELECT COUNT(*) FROM fish_result WHERE order_id = ?", (oid,)),
            )
        if "results" in exp:
            expected_results = exp["results"]
            if not isinstance(expected_results, dict):
                checks.unsupported(f"{label} results", ["results (not an object)"])
            else:
                current = _current_results(conn, oid)
                for probe in sorted(expected_results):
                    want = expected_results[probe]
                    if not isinstance(want, dict):
                        checks.unsupported(f"{label} result[{probe}]", ["(not an object)"])
                        continue
                    checks.unsupported(
                        f"{label} result[{probe}]",
                        [k for k in want if k not in _RESULT_FIELDS],
                    )
                    have = current.get(probe)
                    for field in _RESULT_FIELDS:
                        if field in want:
                            checks.add(
                                f"{label} result[{probe}] {field}",
                                want[field],
                                None if have is None else have[field],
                            )
        if "report_status" in exp:
            # null means NO report row for the order.
            checks.add(
                f"{label} report_status",
                exp["report_status"],
                _scalar(conn, "SELECT status FROM report WHERE order_id = ?", (oid,)),
            )
        if "report_finalized_by" in exp:
            # report.finalized_by (workflow.finalize_order's finalized_by, the
            # CLI's finalize --by); null when there is no report row.
            checks.add(
                f"{label} report_finalized_by",
                exp["report_finalized_by"],
                _scalar(conn, "SELECT finalized_by FROM report WHERE order_id = ?", (oid,)),
            )
        if "audit_action_counts" in exp:
            counts = exp["audit_action_counts"]
            if not isinstance(counts, dict):
                checks.unsupported(f"{label} audit_action_counts", ["(not an object)"])
            else:
                for action in sorted(counts):
                    checks.add(
                        f"{label} audit_action_counts[{action}]",
                        counts[action],
                        _scalar(
                            conn,
                            "SELECT COUNT(*) FROM audit_event "
                            "WHERE order_id = ? AND action = ?",
                            (oid, action),
                        ),
                    )
        if "validation_blocking" in exp:
            findings = validation.validate_order(conn, oid)  # SELECT only
            checks.add(
                f"{label} validation_blocking",
                exp["validation_blocking"],
                validation.has_blocking_errors(findings),
            )


def _verify_queue_items(
    conn: sqlite3.Connection, checks: _Checks, resolved: str, items: list[Any]
) -> None:
    for i, exp in enumerate(items):
        if not isinstance(exp, dict) or not isinstance(exp.get("original_control_id"), str):
            checks.unsupported(
                f"queue_items[{i}]", ["original_control_id (missing or not a string)"]
            )
            continue
        checks.unsupported(f"queue_items[{i}]", [k for k in exp if k not in _QUEUE_KEYS])
        cid = exp["original_control_id"]
        label = f"queue[{cid}]"
        # The original message is the queue item's message_id (FK); control ids
        # can repeat (an unchanged retry copies MSH-10), so match through the
        # queue -> original message join only.
        rows = conn.execute(
            "SELECT q.queue_id, q.failure_code, q.failure_category, q.recovery_policy, "
            "       q.status, q.raw_payload, m.message_id, m.status AS message_status, "
            "       m.payload "
            "FROM interface_error_queue q "
            "JOIN interface_message m ON m.message_id = q.message_id "
            "WHERE m.control_id = ? ORDER BY q.queue_id",
            (cid,),
        ).fetchall()
        if not checks.add(f"{label} matching queue items", 1, len(rows)):
            continue
        q = rows[0]
        qid = q["queue_id"]
        for column in _QUEUE_COLUMNS:
            if column in exp:
                checks.add(f"{label} {column}", exp[column], q[column])
        if "original_message_status" in exp:
            checks.add(
                f"{label} original_message_status",
                exp["original_message_status"],
                q["message_status"],
            )
        if "original_payload_fixture" in exp:
            fixture = exp["original_payload_fixture"]
            want = None if fixture is None else _fixture_sha256(resolved, fixture)
            checks.add(
                f"{label} original message payload sha256", want, _text_sha(q["payload"])
            )
            checks.add(f"{label} raw_payload sha256", want, _text_sha(q["raw_payload"]))
        if "conflict_audit_events" in exp:
            checks.add(
                f"{label} conflict_audit_events",
                exp["conflict_audit_events"],
                _scalar(
                    conn,
                    "SELECT COUNT(*) FROM audit_event WHERE action = ? "
                    "AND entity_type = 'interface_error_queue' AND entity_id = ?",
                    (_CONFLICT_ACTION, qid),
                ),
            )
        if "attempts" in exp:
            _verify_attempts(conn, checks, resolved, label, qid, exp["attempts"])


def _verify_attempts(
    conn: sqlite3.Connection,
    checks: _Checks,
    resolved: str,
    label: str,
    queue_id: int,
    expected: Any,
) -> None:
    if not isinstance(expected, list):
        checks.unsupported(f"{label} attempts", ["(not a list)"])
        return
    rows = conn.execute(
        "SELECT a.attempt_id, a.request_id, a.action, a.outcome, a.actor, "
        "       a.payload_sha256, a.resulting_message_id, "
        "       m.message_id AS result_row_id, m.status AS result_status, "
        "       m.payload AS result_payload "
        "FROM interface_recovery_attempt a "
        "LEFT JOIN interface_message m ON m.message_id = a.resulting_message_id "
        "WHERE a.queue_id = ? ORDER BY a.attempt_id",
        (queue_id,),
    ).fetchall()
    checks.add(f"{label} attempt count", len(expected), len(rows))
    for n, (exp, row) in enumerate(zip(expected, rows), start=1):
        alabel = f"{label} attempt[{n}]"
        if not isinstance(exp, dict):
            checks.unsupported(alabel, ["(not an object)"])
            continue
        checks.unsupported(alabel, [k for k in exp if k not in _ATTEMPT_KEYS])
        for column in _ATTEMPT_COLUMNS:
            if column in exp:
                checks.add(f"{alabel} {column}", exp[column], row[column])
        has_result_id = row["resulting_message_id"] is not None
        if "payload_fixture" in exp:
            fixture = exp["payload_fixture"]
            want = None if fixture is None else _fixture_sha256(resolved, fixture)
            checks.add(f"{alabel} payload_fixture sha256", want, row["payload_sha256"])
            if has_result_id:
                # Hash the stored resulting payload TEXT, not a stored fingerprint.
                checks.add(
                    f"{alabel} resulting message payload sha256",
                    want,
                    _text_sha(row["result_payload"]),
                )
        if "resulting_message_status" in exp:
            if not has_result_id:
                observed = None
            elif row["result_row_id"] is None:
                observed = f"MISSING(message_id={row['resulting_message_id']})"
            else:
                observed = row["result_status"]
            checks.add(
                f"{alabel} resulting_message_status",
                exp["resulting_message_status"],
                observed,
            )


def _hl7_unescape(value: str) -> str:
    return _HL7_ESCAPE_RE.sub(lambda m: _HL7_UNESCAPE[m.group(1)], value)


def _hl7_probe_values(payload: str, panel_codes: set[str]) -> dict[str, Any]:
    """Per-probe values from a stored outbound HL7 payload.

    Reads every OBX whose OBX-3 first component is a panel probe code and parses
    OBX-5 as ``outbound_hl7._obx_segments`` writes it (counts, the percent text,
    interpretation, signal pattern), plus the OBX-8 abnormal flag. An
    unparseable value or a repeated probe code is reported as such (and
    therefore never matches).
    """
    observed: dict[str, Any] = {}
    for seg in inbound_hl7._split_segments(payload):
        if not seg or seg[0].strip() != "OBX":
            continue
        code = _hl7_unescape(inbound_hl7._component(inbound_hl7._field(seg, 3), 0))
        if code not in panel_codes:
            continue
        raw = _hl7_unescape(inbound_hl7._field(seg, 5))
        match = _HL7_PROBE_VALUE_RE.fullmatch(raw)
        if code in observed:
            observed[code] = "DUPLICATE_OBX"
        elif match is None:
            observed[code] = {"unparsed_obx_5": raw}
        else:
            observed[code] = {
                "cells_scored": int(match.group(2)),
                "cells_abnormal": int(match.group(1)),
                "percent_abnormal": match.group(3),
                "interpretation": match.group(4),
                "flag": _hl7_unescape(inbound_hl7._field(seg, 8)),
                "signal_pattern": match.group(5),
            }
    return {k: observed[k] for k in sorted(observed)}


def _first_text(items: Any) -> str | None:
    """``items[0]["text"]`` when ``items`` is a non-empty list of objects, else None."""
    if isinstance(items, list) and items and isinstance(items[0], dict):
        text = items[0].get("text")
        return text if isinstance(text, str) else None
    return None


def _fhir_interpretation_code(items: Any) -> Any:
    """The single ``interpretation[0].coding`` code whose system is SYS_INTERPRETATION.

    None when there is no such coding or more than one, so it never matches.
    """
    if not (isinstance(items, list) and items and isinstance(items[0], dict)):
        return None
    codings = items[0].get("coding")
    codes = [c.get("code") for c in (codings if isinstance(codings, list) else [])
             if isinstance(c, dict) and c.get("system") == outbound_fhir.SYS_INTERPRETATION]
    return codes[0] if len(codes) == 1 else None


def _fhir_probe_values(payload: str) -> dict[str, Any]:
    """Per-probe values from a stored outbound FHIR Bundle payload.

    Reads every Observation entry: the probe code is the ``code.coding`` entry
    whose system is ``outbound_fhir.SYS_PROBE``; counts and signal pattern come
    from ``note[0].text``, percent abnormal from ``valueQuantity.value``, the
    interpretation from ``interpretation[0].text`` and the flag from its
    ``SYS_INTERPRETATION`` coding code, as ``outbound_fhir._observation_resource``
    writes them.
    """
    try:
        bundle = json.loads(payload)
    except ValueError as exc:
        return {"<payload>": f"UNPARSEABLE_JSON: {exc}"}
    observed: dict[str, Any] = {}
    entries = bundle.get("entry") if isinstance(bundle, dict) else None
    for n, entry in enumerate(entries if isinstance(entries, list) else []):
        res = entry.get("resource") if isinstance(entry, dict) else None
        if not isinstance(res, dict) or res.get("resourceType") != "Observation":
            continue
        code_obj = res.get("code")
        codings = code_obj.get("coding") if isinstance(code_obj, dict) else None
        codes = [c.get("code") for c in (codings if isinstance(codings, list) else [])
                 if isinstance(c, dict) and c.get("system") == outbound_fhir.SYS_PROBE]
        if len(codes) != 1 or not isinstance(codes[0], str):
            observed[f"<entry {n}>"] = "NO_SINGLE_PROBE_CODING"
            continue
        code = codes[0]
        note = _first_text(res.get("note"))
        interp = _first_text(res.get("interpretation"))
        quantity = res.get("valueQuantity")
        match = _FHIR_NOTE_RE.fullmatch(note) if note is not None else None
        if code in observed:
            observed[code] = "DUPLICATE_OBSERVATION"
        elif match is None:
            observed[code] = {"unparsed_note": note}
        else:
            observed[code] = {
                "cells_scored": int(match.group(2)),
                "cells_abnormal": int(match.group(1)),
                "percent_abnormal": (
                    quantity.get("value") if isinstance(quantity, dict) else None
                ),
                "interpretation": interp,
                "flag": _fhir_interpretation_code(res.get("interpretation")),
                "signal_pattern": match.group(3),
            }
    return {k: observed[k] for k in sorted(observed)}


def _verify_outbound(
    conn: sqlite3.Connection, checks: _Checks, outbound: list[Any]
) -> None:
    for i, exp in enumerate(outbound):
        if (
            not isinstance(exp, dict)
            or not isinstance(exp.get("accession"), str)
            or exp.get("format") not in _OUTBOUND_FORMATS
        ):
            checks.unsupported(
                f"outbound[{i}]", ["accession/format (missing or not HL7/FHIR)"]
            )
            continue
        checks.unsupported(f"outbound[{i}]", [k for k in exp if k not in _OUTBOUND_KEYS])
        acc, fmt = exp["accession"], exp["format"]
        label = f"outbound[{acc} {fmt}]"
        order = _order_row(conn, acc)
        if not checks.add(f"{label} order exists", True, order is not None):
            continue
        oid = order["order_id"]
        messages = conn.execute(
            "SELECT message_id, payload FROM interface_message "
            "WHERE direction = 'OUTBOUND' AND format = ? AND order_id = ? "
            "ORDER BY message_id",
            (fmt, oid),
        ).fetchall()
        if "count" in exp:
            checks.add(f"{label} count", exp["count"], len(messages))
        if "matches_results" in exp:
            want = _jsonable(_expected_export_values(_current_results(conn, oid), fmt))
            panel_codes = {
                r[0] for r in conn.execute(
                    "SELECT probe_code FROM probe WHERE panel_id = ?",
                    (order["panel_id"],),
                ).fetchall()
            }
            parsed = [
                (
                    m["message_id"],
                    {"<payload>": _text_sha(m["payload"])}
                    if not isinstance(m["payload"], str)
                    else _hl7_probe_values(m["payload"], panel_codes)
                    if fmt == "HL7"
                    else _fhir_probe_values(m["payload"]),
                )
                for m in messages
            ]
            # Every stored message must cover exactly the fish_result probe set
            # with equal values (compared as canonical JSON, like every check);
            # with no stored message there is nothing that matches, so the
            # observation is false.
            matches = bool(parsed) and all(
                canonical_json(_jsonable(values)) == canonical_json(want)
                for _mid, values in parsed
            )
            checks.add(f"{label} matches_results", exp["matches_results"], matches)
            if exp["matches_results"] is True:
                for mid, values in parsed:
                    checks.add(f"{label} message[{mid}] per-probe values", want, values)


def _verify_totals(conn: sqlite3.Connection, checks: _Checks, totals: Any) -> None:
    if not isinstance(totals, dict):
        checks.unsupported("totals", ["(not an object)"])
        return
    unknown = [t for t in totals if t not in store.REQUIRED_TABLES]
    checks.unsupported("totals", [f"table {t}" for t in unknown])
    for table in sorted(t for t in totals if t in store.REQUIRED_TABLES):
        checks.add(
            f"totals[{table}]",
            totals[table],
            _scalar(conn, f'SELECT COUNT(*) FROM "{table}"'),
        )


def verify_exercise(ex_id: str, db_path: str | Path) -> dict[str, Any]:
    """Verify a database against the exercise answer key's ``expected`` outcome.

    Read-only: the database is opened with ``store.open_read_only(...,
    snapshot=True)`` and only SELECT statements run, all inside one read
    transaction (a missing file raises ``NOT_FOUND`` and is never created).
    Every observed value is derived from the persisted rows or from
    fixture file bytes, never from CLI output. Returns ``{"exercise",
    "passed", "checks"}``; ``passed`` is true only when at least one check ran
    and every check passed.
    """
    loaded = load_exercise(ex_id)
    resolved = loaded["id"]
    expected = loaded["answer_key"].get("expected", {})
    checks = _Checks(_short(resolved))
    # snapshot=True: one read transaction, so every check sees one state.
    conn = store.open_read_only(db_path, snapshot=True)
    try:
        checks.unsupported("expected", [k for k in expected if k not in _EXPECTED_SECTIONS])
        for section in ("orders", "queue_items", "outbound"):
            value = expected.get(section)
            if value is not None and not isinstance(value, list):
                checks.unsupported(section, ["(not a list)"])
                expected = dict(expected, **{section: []})
        _verify_orders(conn, checks, expected.get("orders") or [])
        _verify_queue_items(conn, checks, resolved, expected.get("queue_items") or [])
        _verify_outbound(conn, checks, expected.get("outbound") or [])
        if "totals" in expected:
            _verify_totals(conn, checks, expected["totals"])
        if "reconcile_error_count" in expected:
            findings = reconcile.reconcile_database(conn)
            checks.add(
                "reconcile_error_count",
                expected["reconcile_error_count"],
                sum(1 for f in findings if f["severity"] == "ERROR"),
            )
    finally:
        conn.close()
    items = checks.items
    return {
        "exercise": resolved,
        "passed": bool(items) and all(c["passed"] for c in items),
        "checks": items,
    }
