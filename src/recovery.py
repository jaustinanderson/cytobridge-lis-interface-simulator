"""Controlled error-queue recovery service for CytoBridge v1.1 (task P3-003).

A small, headless Python service layer that lets an analyst request a controlled
recovery of a failed inbound AML/MDS FISH message. It implements exactly the
safety boundary approved in the frozen design record
(``validation/v1.1-design-record.md``) and its requirements/test-intent files:

- an unchanged retry of the immutable original payload (``RETRY_ORIGINAL``);
- a corrected re-drive of a caller-supplied payload (``REDRIVE_CORRECTED``);
- recovery-attempt history for a queue item.

Public functions
----------------
    retry_queue_item(conn, queue_id, *, request_id, actor)
    redrive_queue_item(conn, queue_id, corrected_payload, *, request_id, actor)
    get_recovery_history(conn, queue_id)

Safety properties (all dictated by the frozen design, never invented here):

- The original failed ``interface_message`` and the queue item's ``raw_payload``
  copy are immutable; only a new recovery message may reach ``FILED``.
- ``request_id`` is a globally unique idempotency key. A replay whose queue item,
  action, payload fingerprint, and actor all match the recorded request returns
  the existing outcome and writes nothing. Any mismatch is a distinct
  ``REQUEST_ID_CONFLICT`` that fabricates no recovery attempt.
- A queue item has at most one ``SUCCEEDED`` recovery (enforced in the schema).
- Every permitted operation commits or rolls back as a unit; a handled inbound
  processing failure rolls back all filing side effects but preserves the
  attempted message as ``ERRORED`` and a ``FAILED`` attempt with the queue
  ``OPEN``; an unexpected failure rolls back the whole request and re-raises.

Classification is never re-derived here: eligibility uses the structured
``failure_code`` / ``recovery_policy`` the inbound path already stored, and
processing reuses the inbound parsing, matching, specimen/OBX validation, filing,
and audit behavior through the private seam in ``inbound_hl7``. This is an
educational, synthetic simulator. All data is synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass

from . import db, workflow
from .interfaces import inbound_hl7

# The two order-state failures whose recovery policy is TERMINAL. If processing a
# permitted request establishes that the target order is now in one of these
# states, the frozen design (sec 7) treats the request as REJECTED and moves the
# queue OPEN -> TERMINAL rather than recording a handled FAILED attempt.
_TERMINAL_INBOUND_CODES = ("ORDER_FINALIZED", "ORDER_CANCELLED")

# Recovery policies an OPEN queue item may legitimately carry (design sec 6, 8).
_OPEN_POLICIES = ("REDRIVE_ONLY", "RETRY_OR_REDRIVE")

_RETRY = "RETRY_ORIGINAL"
_REDRIVE = "REDRIVE_CORRECTED"

# Stable audit action for a request-id conflict (design sec 9; requirement R-041).
_CONFLICT_ACTION = "REQUEST_ID_CONFLICT"


class RecoveryError(Exception):
    """A recovery request cannot be honored because of contradictory state.

    Raised for a blocker the frozen design does not dictate an outcome for -- a
    missing queue item, or an OPEN item whose stored classification is null or
    contradictory. No recovery attempt, message, or FISH result is fabricated for
    these; the caller sees an explicit error instead of an invented outcome.
    """


class RequestIdConflictError(RecoveryError):
    """A reused ``request_id`` does not match its originally recorded request.

    Exposes the ``REQUEST_ID_CONFLICT`` outcome distinctly (design sec 9,
    requirement R-041): no message, recovery attempt, or FISH result is created,
    the original attempt is never overwritten, and exactly one
    ``REQUEST_ID_CONFLICT`` audit event is recorded before this is raised.
    """

    def __init__(self, request_id: str, detail: str) -> None:
        super().__init__(detail)
        self.request_id = request_id
        self.detail = detail


@dataclass(frozen=True)
class RecoveryAttempt:
    """One persisted ``interface_recovery_attempt`` row.

    The return value of ``retry_queue_item`` / ``redrive_queue_item`` (for both
    fresh requests and matching replays) and the element type of
    ``get_recovery_history``. Mirrors the stored row exactly; no additional
    state is manufactured.
    """

    attempt_id: int
    queue_id: int
    resulting_message_id: int | None
    request_id: str
    action: str
    payload_sha256: str
    outcome: str
    actor: str
    outcome_detail: str
    attempted_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RecoveryAttempt":
        return cls(
            attempt_id=row["attempt_id"],
            queue_id=row["queue_id"],
            resulting_message_id=row["resulting_message_id"],
            request_id=row["request_id"],
            action=row["action"],
            payload_sha256=row["payload_sha256"],
            outcome=row["outcome"],
            actor=row["actor"],
            outcome_detail=row["outcome_detail"],
            attempted_at=row["attempted_at"],
        )


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def _payload_sha256(payload: str) -> str:
    """Lowercase SHA-256 hex digest of the exact UTF-8 payload for the request."""
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Row access helpers
# ---------------------------------------------------------------------------

def _load_queue(conn: sqlite3.Connection, queue_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT queue_id, message_id, reason, raw_payload, failure_code, "
        "failure_category, recovery_policy, status, resolved_at, terminal_at "
        "FROM interface_error_queue WHERE queue_id = ?",
        (queue_id,),
    ).fetchone()
    if row is None:
        raise RecoveryError(f"Error-queue item {queue_id} does not exist.")
    return row


def _original_message_payload(
    conn: sqlite3.Connection, queue_row: sqlite3.Row
) -> str:
    """Return the payload of the queue item's linked original interface_message.

    RETRY_ORIGINAL reuses the exact original inbound payload from
    ``interface_message.payload`` of the message the queue item links via
    ``interface_error_queue.message_id`` -- never from
    ``interface_error_queue.raw_payload``. A null link or a missing message row is
    a blocker: ``RecoveryError`` is raised before any write, and neither stored
    copy is modified.
    """
    message_id = queue_row["message_id"]
    if message_id is None:
        raise RecoveryError(
            f"Error-queue item {queue_row['queue_id']} has no linked original "
            f"interface_message to retry."
        )
    row = conn.execute(
        "SELECT payload FROM interface_message WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        raise RecoveryError(
            f"Error-queue item {queue_row['queue_id']} links original "
            f"interface_message {message_id}, which does not exist."
        )
    return row["payload"]


def _find_attempt_by_request_id(
    conn: sqlite3.Connection, request_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM interface_recovery_attempt WHERE request_id = ?",
        (request_id,),
    ).fetchone()


def _load_attempt(conn: sqlite3.Connection, attempt_id: int) -> RecoveryAttempt:
    row = conn.execute(
        "SELECT * FROM interface_recovery_attempt WHERE attempt_id = ?",
        (attempt_id,),
    ).fetchone()
    return RecoveryAttempt.from_row(row)


# ---------------------------------------------------------------------------
# Write helpers (transaction-aware)
# ---------------------------------------------------------------------------

def _insert_attempt(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    resulting_message_id: int | None,
    request_id: str,
    action: str,
    payload_sha256: str,
    outcome: str,
    actor: str,
    outcome_detail: str,
    commit: bool,
) -> int:
    return db.execute(
        conn,
        "INSERT INTO interface_recovery_attempt "
        "(queue_id, resulting_message_id, request_id, action, payload_sha256, "
        " outcome, actor, outcome_detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            queue_id,
            resulting_message_id,
            request_id,
            action,
            payload_sha256,
            outcome,
            actor,
            outcome_detail,
        ),
        commit=commit,
    )


def _resolve_queue(conn: sqlite3.Connection, queue_id: int, *, commit: bool) -> None:
    """Transition an OPEN queue item to RESOLVED (design sec 5, 8).

    Only status and ``resolved_at`` change; the original reason, failure code,
    category, recovery policy, and raw payload are never rewritten.
    """
    db.execute(
        conn,
        "UPDATE interface_error_queue "
        "SET status = 'RESOLVED', resolved_at = datetime('now') "
        "WHERE queue_id = ? AND status = 'OPEN'",
        (queue_id,),
        commit=commit,
    )


def _terminalize_queue(
    conn: sqlite3.Connection, queue_id: int, *, commit: bool
) -> None:
    """Transition an OPEN queue item to TERMINAL (design sec 7, 8).

    Only status and ``terminal_at`` change; the original classification and raw
    payload are never rewritten.
    """
    db.execute(
        conn,
        "UPDATE interface_error_queue "
        "SET status = 'TERMINAL', terminal_at = datetime('now') "
        "WHERE queue_id = ? AND status = 'OPEN'",
        (queue_id,),
        commit=commit,
    )


# ---------------------------------------------------------------------------
# Public service boundary
# ---------------------------------------------------------------------------

def retry_queue_item(
    conn: sqlite3.Connection,
    queue_id: int,
    *,
    request_id: str,
    actor: str,
) -> RecoveryAttempt:
    """Retry a failed queue item using an exact copy of its original payload.

    Permitted only for an OPEN ``ORDER_NOT_FOUND`` item whose recovery policy is
    ``RETRY_OR_REDRIVE`` (design sec 7). The immutable original payload is read
    from the queue item's linked original ``interface_message`` (its
    ``interface_message.payload``), which is used as the source for both
    ``payload_sha256`` and the new retry message; no replacement payload is
    accepted and neither stored copy is rewritten. A missing original-message
    link or row surfaces as ``RecoveryError`` before any write. Returns the
    persisted recovery attempt and its outcome. Raises ``RequestIdConflictError``
    for a conflicting ``request_id`` and ``RecoveryError`` for contradictory
    queue state.
    """
    queue_row = _load_queue(conn, queue_id)
    payload = _original_message_payload(conn, queue_row)
    return _recover(
        conn,
        queue_row=queue_row,
        action=_RETRY,
        payload=payload,
        request_id=request_id,
        actor=actor,
    )


def redrive_queue_item(
    conn: sqlite3.Connection,
    queue_id: int,
    corrected_payload: str,
    *,
    request_id: str,
    actor: str,
) -> RecoveryAttempt:
    """Re-drive a failed queue item with a caller-supplied corrected payload.

    Permitted for an OPEN item whose recovery policy is ``REDRIVE_ONLY`` or
    ``RETRY_OR_REDRIVE`` (design sec 7). Exactly the supplied ``corrected_payload``
    is processed and stored; the original payload and queue copy are never
    overwritten. Returns the persisted recovery attempt and its outcome. Raises
    ``RequestIdConflictError`` for a conflicting ``request_id`` and
    ``RecoveryError`` for contradictory queue state.
    """
    queue_row = _load_queue(conn, queue_id)
    return _recover(
        conn,
        queue_row=queue_row,
        action=_REDRIVE,
        payload=corrected_payload,
        request_id=request_id,
        actor=actor,
    )


def get_recovery_history(
    conn: sqlite3.Connection, queue_id: int
) -> list[RecoveryAttempt]:
    """Return every recovery attempt for a queue item, in attempt_id order.

    Includes SUCCEEDED, FAILED, and REJECTED attempts. REQUEST_ID_CONFLICT events
    are intentionally excluded because a conflict creates no recovery-attempt row
    (only an audit event); they never appear here.
    """
    rows = conn.execute(
        "SELECT * FROM interface_recovery_attempt WHERE queue_id = ? "
        "ORDER BY attempt_id ASC",
        (queue_id,),
    ).fetchall()
    return [RecoveryAttempt.from_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------

def _validate_classification(queue_row: sqlite3.Row) -> None:
    """Validate the stored classification against the authoritative inbound mapping.

    Runs after request-id replay/conflict resolution and before any queue-state or
    action eligibility decision. The queue item's ``failure_code``,
    ``failure_category``, and ``recovery_policy`` must all be populated and must
    equal exactly the ``(category, policy)`` the single authoritative mapping in
    ``inbound_hl7`` (``_FAILURE_CLASSIFICATION``) assigns to that code. Null,
    contradictory, or unmappable classification raises ``RecoveryError``; because
    this runs before any write, no attempt, message, result, queue change, order
    change, or audit event is persisted. No second mapping is introduced here --
    the existing authoritative one is reused.
    """
    queue_id = queue_row["queue_id"]
    code = queue_row["failure_code"]
    category = queue_row["failure_category"]
    policy = queue_row["recovery_policy"]
    if code is None or category is None or policy is None:
        raise RecoveryError(
            f"Queue item {queue_id} has incomplete classification "
            f"(failure_code={code!r}, failure_category={category!r}, "
            f"recovery_policy={policy!r}); cannot decide recovery eligibility."
        )
    expected = inbound_hl7._FAILURE_CLASSIFICATION.get(code)
    if expected is None:
        raise RecoveryError(
            f"Queue item {queue_id} has unmappable failure_code={code!r}; it is "
            f"not in the authoritative failure classification mapping."
        )
    if category != expected.category or policy != expected.policy:
        raise RecoveryError(
            f"Queue item {queue_id} has contradictory classification: stored "
            f"(failure_code={code}, failure_category={category}, "
            f"recovery_policy={policy}) does not match the authoritative triple "
            f"(failure_code={code}, failure_category={expected.category}, "
            f"recovery_policy={expected.policy})."
        )


def _recover(
    conn: sqlite3.Connection,
    *,
    queue_row: sqlite3.Row,
    action: str,
    payload: str,
    request_id: str,
    actor: str,
) -> RecoveryAttempt:
    """Apply the frozen recovery decision order for one request.

    1. Compute the payload fingerprint for the request.
    2. Resolve ``request_id`` (replay vs conflict) BEFORE any queue-state or
       action eligibility check.
    3. Validate the exact stored classification against the authoritative mapping
       (still after request-id resolution, but before ordinary eligibility).
    4. For a fresh request, check queue-state and action eligibility.
    5. Reject, or run permitted processing under one transaction.
    """
    queue_id = queue_row["queue_id"]
    payload_sha256 = _payload_sha256(payload)

    # --- Step 2: request_id resolution (before eligibility). ---------------
    existing = _find_attempt_by_request_id(conn, request_id)
    if existing is not None:
        matches = (
            existing["queue_id"] == queue_id
            and existing["action"] == action
            and existing["payload_sha256"] == payload_sha256
            and existing["actor"] == actor
        )
        if matches:
            # Matching replay: return the recorded outcome, write nothing.
            return RecoveryAttempt.from_row(existing)
        # Conflict: record exactly one audit event and expose it distinctly.
        _record_conflict(
            conn,
            queue_id=queue_id,
            request_id=request_id,
            actor=actor,
            action=action,
            payload_sha256=payload_sha256,
            existing=existing,
        )
        raise RequestIdConflictError(
            request_id,
            f"request_id {request_id!r} was already recorded for a different "
            f"request; rejected as {_CONFLICT_ACTION}.",
        )

    # --- Step 3: classification validation (before ordinary eligibility). --
    # Null, contradictory, or unmappable classification is a blocker that
    # persists nothing; it is checked after request-id resolution but before any
    # queue-state or action eligibility decision.
    _validate_classification(queue_row)

    # --- Step 4: eligibility (fresh request only). -------------------------
    status = queue_row["status"]
    if status != "OPEN":
        # A new request against a RESOLVED or TERMINAL item is REJECTED with an
        # attempt row; the closed queue item is not reopened or changed.
        return _reject(
            conn,
            queue_id=queue_id,
            request_id=request_id,
            action=action,
            payload_sha256=payload_sha256,
            actor=actor,
            outcome_detail=(
                f"Queue item {queue_id} is {status}; recovery is not permitted "
                f"against a closed queue item."
            ),
        )

    code = queue_row["failure_code"]
    policy = queue_row["recovery_policy"]
    if action == _RETRY:
        if not (code == "ORDER_NOT_FOUND" and policy == "RETRY_OR_REDRIVE"):
            return _reject(
                conn,
                queue_id=queue_id,
                request_id=request_id,
                action=action,
                payload_sha256=payload_sha256,
                actor=actor,
                outcome_detail=(
                    f"RETRY_ORIGINAL is not permitted for queue item {queue_id} "
                    f"(failure_code={code}, recovery_policy={policy}); an unchanged "
                    f"retry is allowed only for an OPEN ORDER_NOT_FOUND / "
                    f"RETRY_OR_REDRIVE item."
                ),
            )
    else:  # _REDRIVE
        if policy not in _OPEN_POLICIES:
            # An OPEN item should never carry a TERMINAL policy; that combination
            # is contradictory, so surface it as a blocker.
            raise RecoveryError(
                f"Queue item {queue_id} is OPEN with recovery_policy={policy!r}, "
                f"which is not a valid policy for an OPEN item."
            )

    # --- Step 5: permitted processing. -------------------------------------
    return _process(
        conn,
        queue_id=queue_id,
        action=action,
        payload=payload,
        payload_sha256=payload_sha256,
        request_id=request_id,
        actor=actor,
    )


def _reject(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    request_id: str,
    action: str,
    payload_sha256: str,
    actor: str,
    outcome_detail: str,
) -> RecoveryAttempt:
    """Record a single REJECTED attempt (no message, no FISH-result side effect).

    Commits only the REJECTED attempt; the queue item is left unchanged.
    """
    attempt_id = _insert_attempt(
        conn,
        queue_id=queue_id,
        resulting_message_id=None,
        request_id=request_id,
        action=action,
        payload_sha256=payload_sha256,
        outcome="REJECTED",
        actor=actor,
        outcome_detail=outcome_detail,
        commit=True,
    )
    return _load_attempt(conn, attempt_id)


def _record_conflict(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    request_id: str,
    actor: str,
    action: str,
    payload_sha256: str,
    existing: sqlite3.Row,
) -> None:
    """Record exactly one REQUEST_ID_CONFLICT audit event; change nothing else."""
    mismatched = []
    if existing["queue_id"] != queue_id:
        mismatched.append(
            f"queue_id({existing['queue_id']}!={queue_id})"
        )
    if existing["action"] != action:
        mismatched.append(f"action({existing['action']}!={action})")
    if existing["payload_sha256"] != payload_sha256:
        mismatched.append("payload_sha256")
    if existing["actor"] != actor:
        mismatched.append(f"actor({existing['actor']!r}!={actor!r})")
    detail = (
        f"request_id={request_id} reused with mismatched "
        f"{', '.join(mismatched)}; original attempt_id={existing['attempt_id']} "
        f"(outcome={existing['outcome']}) left unchanged."
    )
    workflow.record_audit(
        conn,
        "interface_error_queue",
        queue_id,
        _CONFLICT_ACTION,
        detail=detail,
        actor=actor,
        commit=True,
    )


def _process(
    conn: sqlite3.Connection,
    *,
    queue_id: int,
    action: str,
    payload: str,
    payload_sha256: str,
    request_id: str,
    actor: str,
) -> RecoveryAttempt:
    """Run a permitted retry/re-drive as one transaction with atomic outcomes.

    Reuses the inbound parsing/matching/validation/filing seam via
    ``inbound_hl7`` under ``commit=False``, then commits exactly one of:

    - SUCCEEDED: new FILED message, FISH-result + filing audit writes, SUCCEEDED
      attempt, and queue OPEN -> RESOLVED, all together.
    - FAILED (handled inbound error): the attempted message preserved as ERRORED
      and a FAILED attempt, with every filing side effect rolled back and the
      queue left OPEN.
    - REJECTED (dynamic OPEN -> TERMINAL): no processing message, one REJECTED
      attempt, and queue OPEN -> TERMINAL, when processing establishes the target
      order is now FINALIZED or CANCELLED.

    The rollback boundary covers the entire permitted request -- recovery-message
    creation, validation and filing, the FILED update, SUCCEEDED-attempt
    insertion, queue resolution, terminalization/handled-failure bookkeeping, and
    the final commit. Any unexpected (non-``InboundError``) exception at any stage
    rolls the whole request back and re-raises, leaving ``conn.in_transaction``
    false and no partial message, result, attempt, audit, order, or queue change.
    Handled ``InboundError`` semantics are unchanged: a non-terminal inbound error
    yields a FAILED attempt and a terminal one a REJECTED attempt; generic
    exceptions and database errors are never converted into FAILED.
    """
    try:
        # Store the new INBOUND message inside a fresh transaction (the DML opens
        # it). A savepoint after the insert lets a handled failure roll filing
        # writes back while keeping the attempted message.
        message_id = inbound_hl7._store_inbound_message(conn, payload, commit=False)
        conn.execute("SAVEPOINT recovery_processing")
        try:
            _parsed, order, results = inbound_hl7._validate_inbound(conn, payload)
            filed_codes = inbound_hl7._file_results(
                conn, order, results, message_id, actor, commit=False
            )
            inbound_hl7._update_message(
                conn, message_id, status="FILED", order_id=order["order_id"],
                commit=False,
            )
        except inbound_hl7.InboundError as err:
            if err.code in _TERMINAL_INBOUND_CODES:
                # Dynamic OPEN -> TERMINAL: recovery is now prohibited. Discard the
                # attempted message entirely (no processing message) and record
                # only a REJECTED attempt plus the terminalization.
                conn.rollback()
                attempt_id = _insert_attempt(
                    conn,
                    queue_id=queue_id,
                    resulting_message_id=None,
                    request_id=request_id,
                    action=action,
                    payload_sha256=payload_sha256,
                    outcome="REJECTED",
                    actor=actor,
                    outcome_detail=(
                        f"Recovery rejected: target order is now {err.code}; "
                        f"queue item {queue_id} moved OPEN -> TERMINAL. {err}"
                    ),
                    commit=False,
                )
                _terminalize_queue(conn, queue_id, commit=False)
                conn.commit()
                return _load_attempt(conn, attempt_id)

            # Handled non-terminal failure: roll back any partial filing side
            # effects, preserve the attempted message as ERRORED, and record a
            # FAILED attempt. The queue item stays OPEN for a later new request_id.
            conn.execute("ROLLBACK TO SAVEPOINT recovery_processing")
            conn.execute("RELEASE SAVEPOINT recovery_processing")
            inbound_hl7._update_message(
                conn, message_id, status="ERRORED", commit=False
            )
            attempt_id = _insert_attempt(
                conn,
                queue_id=queue_id,
                resulting_message_id=message_id,
                request_id=request_id,
                action=action,
                payload_sha256=payload_sha256,
                outcome="FAILED",
                actor=actor,
                outcome_detail=(
                    f"Recovery processing failed ({err.code}); attempted message "
                    f"preserved as ERRORED, queue item {queue_id} left OPEN. {err}"
                ),
                commit=False,
            )
            conn.commit()
            return _load_attempt(conn, attempt_id)

        # Success: commit the new FILED message, filing side effects, the
        # SUCCEEDED attempt, and the queue resolution together.
        conn.execute("RELEASE SAVEPOINT recovery_processing")
        attempt_id = _insert_attempt(
            conn,
            queue_id=queue_id,
            resulting_message_id=message_id,
            request_id=request_id,
            action=action,
            payload_sha256=payload_sha256,
            outcome="SUCCEEDED",
            actor=actor,
            outcome_detail=(
                f"Recovery succeeded via {action}; new message {message_id} FILED "
                f"to order {order['order_id']} ({len(filed_codes)} probe "
                f"result(s)); queue item {queue_id} moved OPEN -> RESOLVED."
            ),
            commit=False,
        )
        _resolve_queue(conn, queue_id, commit=False)
        conn.commit()
        return _load_attempt(conn, attempt_id)
    except Exception:
        # Any unexpected failure anywhere in the request -- including during
        # success/terminal/handled bookkeeping or the final commit -- discards the
        # entire new request and re-raises. It is never converted into a FAILED
        # attempt. A handled InboundError returns above and never reaches here.
        conn.rollback()
        raise
