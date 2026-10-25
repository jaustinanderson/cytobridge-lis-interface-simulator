"""Shared helpers for the CytoBridge analyst lab.

Errors and exit codes, payload fingerprints, canonical JSON, exact payload-file
reading, and the standard limitation statements every investigation view and
evidence export repeats. All data is synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# Repository root: this file lives in src/analyst/.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Version tag carried by every machine-readable CLI envelope.
FORMAT = "cytobridge.analyst/1"

SYNTHETIC_NOTICE = (
    "SYNTHETIC EDUCATIONAL DATA ONLY. CytoBridge is a training simulator, not a "
    "production LIS, certified interface engine, validated medical device, or "
    "Epic/Beaker implementation."
)

# Process exit codes. Stable and documented in the CLI help epilog.
EXIT_OK = 0
EXIT_CHECK_FAILED = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3
EXIT_REFUSED = 4
EXIT_INVALID_INPUT = 5
EXIT_INTERNAL = 6

# Error kind -> exit code. REQUEST_ID_CONFLICT is a refusal reported distinctly.
EXIT_CODES: dict[str, int] = {
    "CHECK_FAILED": EXIT_CHECK_FAILED,
    "USAGE": EXIT_USAGE,
    "NOT_FOUND": EXIT_NOT_FOUND,
    "REFUSED": EXIT_REFUSED,
    "REQUEST_ID_CONFLICT": EXIT_REFUSED,
    "INVALID_INPUT": EXIT_INVALID_INPUT,
    "INTERNAL": EXIT_INTERNAL,
}


class AnalystError(Exception):
    """An analyst-lab request that cannot be completed as asked.

    ``kind`` is one of the ``EXIT_CODES`` keys and selects the exit code.
    ``result`` optionally carries structured detail (for example a recorded
    REJECTED recovery attempt) for the machine-readable envelope.
    """

    def __init__(
        self, kind: str, message: str, *, result: dict[str, Any] | None = None
    ) -> None:
        if kind not in EXIT_CODES:
            raise ValueError(f"Unknown AnalystError kind: {kind!r}")
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.result = result

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.kind]


# ---------------------------------------------------------------------------
# Fingerprints and canonical JSON
# ---------------------------------------------------------------------------

def sha256_text(text: str) -> str:
    """Lowercase SHA-256 of the exact UTF-8 encoding of ``text``.

    Identical to the fingerprint the recovery service stores in
    ``interface_recovery_attempt.payload_sha256``.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Lowercase SHA-256 of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | Path) -> str:
    """Lowercase SHA-256 of a file's exact bytes."""
    return sha256_bytes(Path(path).read_bytes())


def canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, two-space indent, ASCII, trailing newline."""
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def ref(table: str, row_id: int) -> str:
    """Stable textual reference to one row, e.g. ``interface_message:7``."""
    return f"{table}:{row_id}"


# ---------------------------------------------------------------------------
# Exact payload-file reading
# ---------------------------------------------------------------------------

def read_payload_file(path: str | Path) -> tuple[str, bytes]:
    """Read a message file exactly, returning ``(text, raw_bytes)``.

    The bytes are decoded as strict UTF-8 with no newline translation, so the
    text stored for the message re-encodes to exactly the file's bytes and
    ``sha256_text(text) == sha256_bytes(raw_bytes)``. A missing file, a
    directory, or bytes that are not valid UTF-8 raise ``AnalystError`` before
    anything is written anywhere.
    """
    p = Path(path)
    if not p.exists():
        raise AnalystError("NOT_FOUND", f"Input file {p} does not exist.")
    if not p.is_file():
        raise AnalystError("INVALID_INPUT", f"Input path {p} is not a regular file.")
    try:
        raw = p.read_bytes()
    except OSError as exc:
        raise AnalystError("INVALID_INPUT", f"Cannot read input file {p}: {exc}.")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AnalystError(
            "INVALID_INPUT",
            f"Input file {p} is not valid UTF-8 (byte offset {exc.start}); "
            f"CytoBridge stores message payloads as UTF-8 text. Nothing was written.",
        )
    return text, raw


# ---------------------------------------------------------------------------
# Standard limitation statements
# ---------------------------------------------------------------------------

LIMITATIONS: dict[str, str] = {
    "RESULTS_UPSERTED": (
        "fish_result holds only the current value for each order and probe; "
        "re-entry or re-filing upserts the row. Earlier per-probe values are not "
        "retained, so historical result versions cannot be reconstructed. "
        "RESULT_ENTERED audit details (probe and interpretation only) are the "
        "only textual trace of earlier entries."
    ),
    "NO_RESULT_SOURCE_FK": (
        "fish_result has no foreign key to the interface_message that wrote it. "
        "Which message (or manual entry) produced a current result value is not "
        "recorded by the schema; candidate sources are listed, not asserted."
    ),
    "TIMESTAMP_RESOLUTION": (
        "Timestamps come from SQLite datetime('now') at one-second resolution. "
        "Events that share a second are ordered by a fixed record-type rank and "
        "then row id. That ordering is presentational and does not establish "
        "causation."
    ),
    "RESULT_AUDIT_ACTOR": (
        "workflow.enter_fish_result records RESULT_ENTERED audit events with actor "
        "'system'. The person or interface that entered a current result value is "
        "fish_result.entered_by; the RESULT_ENTERED audit actor does not identify "
        "them."
    ),
    "FAILED_MESSAGE_ORDER_LINK": (
        "An inbound message that fails before filing keeps order_id NULL. Any "
        "link from such a message to an order is derived from the payload "
        "accession (OBR-3), not from a foreign key."
    ),
    "AUDIT_DETAIL_TEXT": (
        "Links from audit events to interface messages (INBOUND_RESULT_FILED "
        "'message_id=N') are free-text audit detail, not foreign keys."
    ),
    "PREVIEW_SNAPSHOT": (
        "A recovery preview simulates one snapshot in an isolated in-memory "
        "copy. It is not an authorization token or a guarantee of a later "
        "result; apply re-invokes the recovery service against the state that "
        "exists at that time."
    ),
    "EDUCATIONAL_INVARIANTS": (
        "Reconciliation rules are educational invariants derived from this "
        "repository's documented design and requirements. They are not clinical "
        "or regulatory requirements."
    ),
}


def limitation_list(codes: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
    """Return ``[{"code", "text"}]`` for the given limitation codes, sorted by code."""
    return [{"code": c, "text": LIMITATIONS[c]} for c in sorted(set(codes))]
