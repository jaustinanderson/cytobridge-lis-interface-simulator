"""Tests for analyst-lab reconciliation (``src/analyst/reconcile.py``).

A realistic CLEAN database is built through the public services only and must
produce no ERROR or WARNING findings. Then, for every rule in ``RULES``, a
negative test constructs the violating state (direct SQL is used only to build
states the services never produce; ``PRAGMA foreign_keys = OFF`` /
``PRAGMA ignore_check_constraints = ON`` only where the schema would otherwise
refuse the state) and asserts that exact rule fires with the right subjects.
All data is synthetic. No PHI.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src import recovery, workflow
from src.analyst import reconcile, trace
from src.interfaces import inbound_hl7, outbound_fhir, outbound_hl7, store_message
from tests.conftest import enter_all_results

# Rule IDs and severities transcribed from the analyst-lab contract (section 3).
EXPECTED_SEVERITY = {
    "REL-001": "ERROR", "REL-002": "ERROR",
    "MSG-001": "ERROR", "MSG-002": "WARNING", "MSG-003": "WARNING",
    "MSG-004": "WARNING", "MSG-005": "ERROR",
    "QUE-001": "ERROR", "QUE-002": "ERROR", "QUE-003": "ERROR",
    "QUE-004": "WARNING", "QUE-005": "ERROR", "QUE-006": "ERROR",
    "REC-001": "ERROR", "REC-002": "ERROR", "REC-003": "ERROR", "REC-004": "ERROR",
    "REC-005": "ERROR", "REC-006": "ERROR", "REC-007": "ERROR", "REC-008": "INFO",
    "AUD-001": "ERROR", "AUD-002": "ERROR", "AUD-003": "ERROR", "AUD-004": "ERROR",
    "AUD-005": "ERROR",
    "WF-001": "INFO", "WF-002": "ERROR", "WF-003": "INFO",
}

GOOD_CBFB = (("CBFB", "200^1^2 orange/green^NORMAL"),)
GOOD_TWO = (("CBFB", "200^1^2 orange/green^NORMAL"), ("KMT2A", "200^1^2 fusion^NORMAL"))
BAD_COUNT = (("CBFB", "abc^1^2F^NORMAL"),)
FULL_OBX = (
    ("RUNX1T1_RUNX1", "200^38^2F1R1G^ABNORMAL"),
    ("CBFB", "200^1^2 orange/green^NORMAL"),
    ("PML_RARA", "200^2^2R2G^NORMAL"),
    ("KMT2A", "200^1^2 fusion^NORMAL"),
    ("EGR1_5q", "200^3^2 orange 2 green^NORMAL"),
    ("D7S486_7q", "200^2^2 signals^NORMAL"),
    ("CEP8", "200^4^2 aqua^NORMAL"),
    ("D20S108_20q", "200^1^2 signals^NORMAL"),
    ("TP53_17p", "200^2^2R2G^NORMAL"),
)


def hl7(
    accession: str,
    control_id: str,
    obx=GOOD_CBFB,
    *,
    obr: bool = True,
    specimen: str = "BMA^Bone Marrow^L",
) -> str:
    """A small synthetic ORU-style inbound message."""
    segments = [
        "MSH|^~\\&|FISHSCAN|CYTO_INSTR|CYTOBRIDGE|CYTO_LAB|20260709101500||"
        f"ORU^R01|{control_id}|T|2.5.1",
        "PID|1||SYN-RC-PID^^^CYTO_LAB^MR||Synthetic^Reconcile||19700101|U",
    ]
    if obr:
        segments.append(f"OBR|1||{accession}|AML_MDS_FISH^AML/MDS FISH Panel^L")
    segments.append(f"SPM|1|SPM-RC||{specimen}")
    for i, (code, value) in enumerate(obx, 1):
        segments.append(f"OBX|{i}|ST|{code}^{code}^L||{value}||||||F")
    return "\r".join(segments)


def make_order(conn, suffix: str, *, accession: bool = True) -> int:
    patient_id = workflow.create_patient(
        conn, f"SYN-RC-{suffix}", "Synthetic", f"Rec{suffix}", "1970-01-01", "U"
    )
    order_id = workflow.create_order(conn, patient_id, f"ACC-RC-{suffix}", "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id)
    if accession:
        workflow.accession_specimen(conn, specimen_id)
    return order_id


def ingest(conn, payload: str):
    return inbound_hl7.ingest_message(conn, payload, actor="interface")


def finalize(conn, order_id: int) -> None:
    enter_all_results(conn, order_id)
    assert workflow.finalize_order(conn, order_id, "pathologist01").finalized


def by_rule(findings, rule_id):
    return [f for f in findings if f["rule_id"] == rule_id]


def subjects_of(findings, rule_id):
    return sorted(f["subjects"] for f in by_rule(findings, rule_id))


def one(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def m(mid):
    return f"interface_message:{mid}"


def q(qid):
    return f"interface_error_queue:{qid}"


def a(aid):
    return f"interface_recovery_attempt:{aid}"


def o(oid):
    return f"lab_order:{oid}"


def ae(eid):
    return f"audit_event:{eid}"


# ---------------------------------------------------------------------------
# Realistic clean database (public services only)
# ---------------------------------------------------------------------------

def build_clean(conn) -> SimpleNamespace:
    ns = SimpleNamespace()
    # 1. Direct filed inbound message.
    ns.filed_order = make_order(conn, "FILED")
    ns.filed = ingest(conn, hl7("ACC-RC-FILED", "RC-FILED-1", obx=GOOD_TWO))
    assert ns.filed.filed

    # 2. Queued failures across several codes (left OPEN).
    ns.open_order = make_order(conn, "OPEN")
    failures = {
        "EMPTY_MESSAGE": "",
        "MISSING_REQUIRED_SEGMENT": hl7("ACC-RC-OPEN", "RC-F-SEG", obr=False),
        "MISSING_ACCESSION": hl7("", "RC-F-ACC"),
        "SPECIMEN_UNRECOGNIZED": hl7("ACC-RC-OPEN", "RC-F-SPM", specimen="XYZ^Unknown^L"),
        "UNKNOWN_PROBE_CODE": hl7("ACC-RC-OPEN", "RC-F-PRB",
                                  obx=(("NOT_A_PROBE", "200^1^x^NORMAL"),)),
        "ABNORMAL_EXCEEDS_SCORED": hl7("ACC-RC-OPEN", "RC-F-ABN",
                                       obx=(("CBFB", "100^150^x^ABNORMAL"),)),
    }
    ns.open_items = {}
    for code, payload in failures.items():
        res = ingest(conn, payload)
        assert not res.filed
        assert one(conn, "SELECT failure_code FROM interface_error_queue WHERE queue_id = ?",
                   (res.queue_id,)) == code
        ns.open_items[code] = res.queue_id

    # 3. Corrected re-drive SUCCEEDED (NO_OBX -> corrected).
    ns.redrive_order = make_order(conn, "REDRIVE")
    no_obx = ingest(conn, hl7("ACC-RC-REDRIVE", "RC-NOOBX", obx=()))
    ns.redrive_queue = no_obx.queue_id
    ns.redrive_ok = recovery.redrive_queue_item(
        conn, no_obx.queue_id, hl7("ACC-RC-REDRIVE", "RC-NOOBX-FIX", obx=GOOD_TWO),
        request_id="RC-REQ-REDRIVE", actor="analyst01",
    )
    assert ns.redrive_ok.outcome == "SUCCEEDED"

    # 4. ORDER_NOT_FOUND retried after the order is created.
    nf = ingest(conn, hl7("ACC-RC-NF", "RC-NF"))
    ns.nf_queue = nf.queue_id
    ns.nf_order = make_order(conn, "NF")
    ns.retry_ok = recovery.retry_queue_item(
        conn, nf.queue_id, request_id="RC-REQ-RETRY", actor="analyst01"
    )
    assert ns.retry_ok.outcome == "SUCCEEDED"

    # 5. Handled FAILED re-drive, then SUCCEEDED.
    ns.fail_order = make_order(conn, "FAIL")
    bad = ingest(conn, hl7("ACC-RC-FAIL", "RC-FAIL", obx=BAD_COUNT))
    ns.fail_queue = bad.queue_id
    ns.redrive_failed = recovery.redrive_queue_item(
        conn, bad.queue_id, hl7("ACC-RC-FAIL", "RC-FAIL-2", obx=BAD_COUNT),
        request_id="RC-REQ-FAIL-1", actor="analyst01",
    )
    assert ns.redrive_failed.outcome == "FAILED"
    ns.redrive_after_fail = recovery.redrive_queue_item(
        conn, bad.queue_id, hl7("ACC-RC-FAIL", "RC-FAIL-3"),
        request_id="RC-REQ-FAIL-2", actor="analyst01",
    )
    assert ns.redrive_after_fail.outcome == "SUCCEEDED"

    # 6. REJECTED request on a RESOLVED item.
    ns.rejected_resolved = recovery.retry_queue_item(
        conn, nf.queue_id, request_id="RC-REQ-AGAIN", actor="analyst01"
    )
    assert ns.rejected_resolved.outcome == "REJECTED"

    # 7. REQUEST_ID_CONFLICT (same request_id, different actor).
    with pytest.raises(recovery.RequestIdConflictError):
        recovery.retry_queue_item(conn, nf.queue_id, request_id="RC-REQ-RETRY", actor="intruder")
    ns.conflict_event = one(
        conn, "SELECT event_id FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'"
    )

    # 8. TERMINAL ORDER_FINALIZED item, and 10. outbound exports.
    ns.final_order = make_order(conn, "FINAL")
    finalize(conn, ns.final_order)
    ns.oru = outbound_hl7.store_oru(conn, ns.final_order, generated_at="2026-07-10 12:00:00")
    ns.fhir = outbound_fhir.store_diagnostic_report(
        conn, ns.final_order, generated_at="2026-07-10 12:00:00"
    )
    late = ingest(conn, hl7("ACC-RC-FINAL", "RC-LATE"))
    ns.terminal_queue = late.queue_id
    assert one(conn, "SELECT status FROM interface_error_queue WHERE queue_id = ?",
               (late.queue_id,)) == "TERMINAL"

    # 9. Dynamic OPEN -> TERMINAL rejection.
    ns.dyn_order = make_order(conn, "DYN")
    dyn = ingest(conn, hl7("ACC-RC-DYN", "RC-DYN", obx=BAD_COUNT))
    ns.dyn_queue = dyn.queue_id
    finalize(conn, ns.dyn_order)
    ns.dyn_rejected = recovery.redrive_queue_item(
        conn, dyn.queue_id, hl7("ACC-RC-DYN", "RC-DYN-FIX"),
        request_id="RC-REQ-DYN", actor="analyst01",
    )
    assert ns.dyn_rejected.outcome == "REJECTED"
    assert one(conn, "SELECT status FROM interface_error_queue WHERE queue_id = ?",
               (dyn.queue_id,)) == "TERMINAL"

    # 11. In-progress order with missing probes (manual entry, never validated).
    ns.wip_order = make_order(conn, "WIP")
    workflow.enter_fish_result(conn, ns.wip_order, "CBFB", 200, 1, "2F", "NORMAL", "tech01")
    return ns


def test_clean_database_has_no_error_or_warning_findings(conn):
    ns = build_clean(conn)
    findings = reconcile.reconcile_database(conn)
    assert [f for f in findings if f["severity"] in ("ERROR", "WARNING")] == []
    info = sorted((f["rule_id"], f["subjects"]) for f in findings)
    incomplete = sorted([
        ns.filed_order, ns.open_order, ns.redrive_order, ns.nf_order, ns.fail_order,
        ns.wip_order,
    ])
    expected = (
        [("REC-008", sorted([ae(ns.conflict_event), q(ns.nf_queue)]))]
        + [("WF-001", [o(x)]) for x in incomplete]
        + [("WF-003", [o(x)]) for x in incomplete]
    )
    assert info == sorted(expected)
    assert reconcile.summarize(findings) == {
        "ERROR": 0, "WARNING": 0, "INFO": 1 + 2 * len(incomplete)
    }
    # Every non-FINALIZED order with outstanding prerequisites is WF-001;
    # independently: orders that are not FINALIZED and lack a required result.
    lacking = [
        r[0] for r in conn.execute(
            "SELECT o.order_id FROM lab_order AS o WHERE o.status NOT IN "
            "('FINALIZED', 'CANCELLED') AND EXISTS (SELECT 1 FROM probe AS p "
            "WHERE p.panel_id = o.panel_id AND p.is_required = 1 AND NOT EXISTS "
            "(SELECT 1 FROM fish_result AS fr WHERE fr.order_id = o.order_id "
            "AND fr.probe_id = p.probe_id)) ORDER BY o.order_id"
        )
    ]
    assert lacking == incomplete


def test_findings_shape_sort_and_determinism(conn):
    build_clean(conn)
    conn.execute("UPDATE interface_message SET status = 'RECEIVED' WHERE message_id = 1")
    first = reconcile.reconcile_database(conn)
    assert first == reconcile.reconcile_database(conn)
    json.dumps(first)
    rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    assert first == sorted(
        first, key=lambda f: (rank[f["severity"]], f["rule_id"], f["subjects"], f["observations"])
    )
    for f in first:
        assert set(f) == {
            "rule_id", "severity", "title", "documented_rule", "subjects",
            "observations", "ambiguity",
        }
        assert f["subjects"] == sorted(f["subjects"])
        assert f["severity"] == reconcile.RULES[f["rule_id"]]["severity"]
        assert all(isinstance(s, str) and s.isascii() for s in f["observations"])
    assert {f["severity"] for f in first} == {"ERROR", "WARNING", "INFO"}


def test_rules_catalogue_is_exactly_the_contract(conn):
    assert set(reconcile.RULES) == set(EXPECTED_SEVERITY)
    for rule_id, rule in reconcile.RULES.items():
        assert set(rule) == {"severity", "title", "documented_rule"}
        assert rule["severity"] == EXPECTED_SEVERITY[rule_id]
        assert rule["title"] and rule["documented_rule"]
        assert rule["documented_rule"].isascii()
        assert "clinical requirement" not in rule["documented_rule"].lower()


def test_summarize_counts_by_severity():
    findings = [{"severity": "ERROR"}, {"severity": "INFO"}, {"severity": "INFO"}]
    assert reconcile.summarize(findings) == {"ERROR": 1, "WARNING": 0, "INFO": 2}
    assert reconcile.summarize([]) == {"ERROR": 0, "WARNING": 0, "INFO": 0}


# ---------------------------------------------------------------------------
# Small base state for negative tests (public services only)
# ---------------------------------------------------------------------------

def build_base(conn) -> SimpleNamespace:
    b = SimpleNamespace()
    b.order_a = make_order(conn, "A")
    b.filed = ingest(conn, hl7("ACC-RC-A", "RC-A-1", obx=GOOD_TWO))
    nf = ingest(conn, hl7("ACC-RC-NF", "RC-NF-1"))
    b.nf_queue, b.nf_message = nf.queue_id, nf.message_id
    b.order_nf = make_order(conn, "NF")
    b.failed = recovery.redrive_queue_item(
        conn, nf.queue_id, hl7("ACC-RC-NF", "RC-NF-BAD", obx=BAD_COUNT),
        request_id="RC-B-1", actor="analyst01",
    )
    b.retried = recovery.retry_queue_item(conn, nf.queue_id, request_id="RC-B-2", actor="analyst01")
    assert (b.failed.outcome, b.retried.outcome) == ("FAILED", "SUCCEEDED")
    no_obx = ingest(conn, hl7("ACC-RC-A", "RC-A-NOOBX", obx=()))
    b.open_queue, b.open_message = no_obx.queue_id, no_obx.message_id
    b.order_fin = make_order(conn, "FIN")
    finalize(conn, b.order_fin)
    b.oru = outbound_hl7.store_oru(conn, b.order_fin, generated_at="2026-07-10 12:00:00")
    b.specimen_a = one(conn, "SELECT specimen_id FROM specimen WHERE order_id = ?", (b.order_a,))
    return b


def test_base_state_is_clean(conn):
    b = build_base(conn)
    findings = reconcile.reconcile_database(conn)
    assert [f for f in findings if f["severity"] != "INFO"] == []
    assert subjects_of(findings, "WF-001") == [[o(b.order_a)], [o(b.order_nf)]]
    assert subjects_of(findings, "WF-003") == [[o(b.order_a)], [o(b.order_nf)]]


# ---------------------------------------------------------------------------
# One violating state per rule. Each returns the exact expected subject lists.
# ---------------------------------------------------------------------------

def _rel_001(conn, b):
    conn.commit()  # PRAGMA foreign_keys is a no-op inside an open transaction.
    conn.execute("PRAGMA foreign_keys = OFF")
    sid = conn.execute(
        "INSERT INTO specimen (order_id, specimen_type) VALUES (9999, 'BONE_MARROW')"
    ).lastrowid
    conn.execute(
        "UPDATE interface_recovery_attempt SET resulting_message_id = 99999 WHERE attempt_id = ?",
        (b.failed.attempt_id,),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return [[a(b.failed.attempt_id)], [f"specimen:{sid}"]]


def _rel_002(conn, b):
    null_q = conn.execute(
        "INSERT INTO interface_error_queue (message_id, reason, raw_payload, failure_code, "
        "failure_category, recovery_policy) VALUES (NULL, 'synthetic', 'x', 'NO_OBX', "
        "'MESSAGE_STRUCTURE', 'REDRIVE_ONLY')"
    ).lastrowid
    conn.commit()  # PRAGMA foreign_keys is a no-op inside an open transaction.
    conn.execute("PRAGMA foreign_keys = OFF")
    dangling_q = conn.execute(
        "INSERT INTO interface_error_queue (message_id, reason, raw_payload, failure_code, "
        "failure_category, recovery_policy) VALUES (77777, 'synthetic', 'x', 'NO_OBX', "
        "'MESSAGE_STRUCTURE', 'REDRIVE_ONLY')"
    ).lastrowid
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return [[q(null_q)], [q(dangling_q)]]


def _msg_001(conn, b):
    conn.execute(
        "UPDATE interface_message SET order_id = NULL WHERE message_id = ?", (b.filed.message_id,)
    )
    return [[m(b.filed.message_id)]]


def _msg_002(conn, b):
    mid = conn.execute(
        "INSERT INTO interface_message (direction, message_type, format, payload, status) "
        "VALUES ('INBOUND', 'ORU', 'HL7', 'MSH|x', 'RECEIVED')"
    ).lastrowid
    return [[m(mid)]]


def _msg_003(conn, b):
    mid = conn.execute(
        "INSERT INTO interface_message (direction, message_type, format, payload, status) "
        "VALUES ('INBOUND', 'ORU', 'HL7', 'MSH|x', 'ERRORED')"
    ).lastrowid
    return [[m(mid)]]


def _msg_004(conn, b):
    dup = conn.execute(
        "INSERT INTO interface_error_queue (message_id, direction, reason, raw_payload, "
        "failure_code, failure_category, recovery_policy, status) "
        "SELECT message_id, direction, reason, raw_payload, failure_code, failure_category, "
        "recovery_policy, 'OPEN' FROM interface_error_queue WHERE queue_id = ?",
        (b.open_queue,),
    ).lastrowid
    return [sorted([m(b.open_message), q(b.open_queue), q(dup)])]


def _msg_005(conn, b):
    early = store_message(
        conn, direction="OUTBOUND", message_type="ORU", message_format="HL7",
        order_id=b.order_a, control_id="RC-EARLY", payload="MSH|synthetic",
    )
    orphan = conn.execute(
        "INSERT INTO interface_message (direction, message_type, format, payload, status) "
        "VALUES ('OUTBOUND', 'ORU', 'HL7', 'MSH|x', 'GENERATED')"
    ).lastrowid
    return [sorted([m(early), o(b.order_a)]), [m(orphan)]]


def _que_001(conn, b):
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        "UPDATE interface_error_queue SET resolved_at = '2026-01-01 00:00:00' WHERE queue_id = ?",
        (b.open_queue,),
    )
    conn.commit()
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    return [[q(b.open_queue)]]


def _que_002(conn, b):
    extra = [
        ingest(conn, hl7("ACC-RC-A", f"RC-Q2-{i}", obx=())).queue_id for i in range(2)
    ]
    conn.execute(
        "UPDATE interface_error_queue SET recovery_policy = NULL WHERE queue_id = ?",
        (b.open_queue,),
    )
    conn.execute(
        "UPDATE interface_error_queue SET failure_category = 'SPECIMEN' WHERE queue_id = ?",
        (extra[0],),
    )
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        "UPDATE interface_error_queue SET failure_code = 'NOT_A_CODE' WHERE queue_id = ?",
        (extra[1],),
    )
    conn.commit()
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    return [[q(b.open_queue)], [q(extra[0])], [q(extra[1])]]


def _que_003(conn, b):
    conn.execute(
        "UPDATE interface_error_queue SET recovery_policy = 'TERMINAL' WHERE queue_id = ?",
        (b.open_queue,),
    )
    return [[q(b.open_queue)]]


def _que_004(conn, b):
    conn.execute(
        "UPDATE interface_error_queue SET status = 'TERMINAL', terminal_at = datetime('now') "
        "WHERE queue_id = ?",
        (b.open_queue,),
    )
    return [[q(b.open_queue)]]


def _que_005(conn, b):
    conn.execute(
        "UPDATE interface_error_queue SET raw_payload = raw_payload || 'X' WHERE queue_id = ?",
        (b.open_queue,),
    )
    conn.execute(
        "UPDATE interface_error_queue SET raw_payload = NULL WHERE queue_id = ?", (b.nf_queue,)
    )
    return [sorted([q(b.open_queue), m(b.open_message)]),
            sorted([q(b.nf_queue), m(b.nf_message)])]


def _que_006(conn, b):
    conn.execute(
        "UPDATE interface_message SET status = 'FILED' WHERE message_id = ?", (b.open_message,)
    )
    return [sorted([q(b.open_queue), m(b.open_message)])]


def _rec_001(conn, b):
    conn.execute(
        "UPDATE interface_error_queue SET status = 'RESOLVED', resolved_at = datetime('now') "
        "WHERE queue_id = ?",
        (b.open_queue,),
    )
    return [[q(b.open_queue)]]


def _rec_002(conn, b):
    conn.execute(
        "UPDATE interface_error_queue SET status = 'OPEN', resolved_at = NULL WHERE queue_id = ?",
        (b.nf_queue,),
    )
    return [sorted([q(b.nf_queue), a(b.retried.attempt_id)])]


def _rec_003(conn, b):
    conn.execute(
        "UPDATE interface_message SET status = 'ERRORED' WHERE message_id = ?",
        (b.retried.resulting_message_id,),
    )
    # A second SUCCEEDED attempt (another queue item) whose message is missing.
    other = ingest(conn, hl7("ACC-RC-A", "RC-R3-NOOBX", obx=()))
    ok = recovery.redrive_queue_item(
        conn, other.queue_id, hl7("ACC-RC-A", "RC-R3-FIX"), request_id="RC-R3", actor="analyst01"
    )
    assert ok.outcome == "SUCCEEDED"
    conn.commit()
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "UPDATE interface_recovery_attempt SET resulting_message_id = 88888 WHERE attempt_id = ?",
        (ok.attempt_id,),
    )
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    return [
        sorted([a(b.retried.attempt_id), m(b.retried.resulting_message_id)]),
        [a(ok.attempt_id)],
    ]


def _rec_004(conn, b):
    conn.execute(
        "UPDATE interface_message SET status = 'RECEIVED' WHERE message_id = ?",
        (b.failed.resulting_message_id,),
    )
    return [sorted([a(b.failed.attempt_id), m(b.failed.resulting_message_id)])]


def _rec_005(conn, b):
    # FAILED attempt pointing at the queue's own original message ...
    conn.execute(
        "UPDATE interface_recovery_attempt SET resulting_message_id = ? WHERE attempt_id = ?",
        (b.nf_message, b.failed.attempt_id),
    )
    # ... and a second FAILED attempt sharing another attempt's message.
    second = recovery.redrive_queue_item(
        conn, b.open_queue, hl7("ACC-RC-A", "RC-R5-1", obx=BAD_COUNT),
        request_id="RC-R5-1", actor="analyst01",
    )
    third = recovery.redrive_queue_item(
        conn, b.open_queue, hl7("ACC-RC-A", "RC-R5-2", obx=BAD_COUNT),
        request_id="RC-R5-2", actor="analyst01",
    )
    assert (second.outcome, third.outcome) == ("FAILED", "FAILED")
    conn.execute(
        "UPDATE interface_recovery_attempt SET resulting_message_id = ? WHERE attempt_id = ?",
        (second.resulting_message_id, third.attempt_id),
    )
    return [
        sorted([a(b.failed.attempt_id), q(b.nf_queue), m(b.nf_message)]),
        sorted([m(second.resulting_message_id), a(second.attempt_id), a(third.attempt_id)]),
    ]


def _rec_006(conn, b):
    conn.execute(
        "UPDATE interface_recovery_attempt SET payload_sha256 = ? WHERE attempt_id = ?",
        ("0" * 64, b.retried.attempt_id),
    )
    conn.execute(
        "UPDATE interface_recovery_attempt SET payload_sha256 = ? WHERE attempt_id = ?",
        ("f" * 64, b.failed.attempt_id),
    )
    return [
        sorted([a(b.failed.attempt_id), m(b.failed.resulting_message_id)]),
        sorted([a(b.retried.attempt_id), m(b.retried.resulting_message_id), m(b.nf_message)]),
    ]


def _rec_007(conn, b):
    conn.execute(
        "UPDATE interface_error_queue SET failure_code = 'NO_OBX', "
        "failure_category = 'MESSAGE_STRUCTURE', recovery_policy = 'REDRIVE_ONLY' "
        "WHERE queue_id = ?",
        (b.nf_queue,),
    )
    return [sorted([a(b.retried.attempt_id), q(b.nf_queue)])]


def _rec_008(conn, b):
    with pytest.raises(recovery.RequestIdConflictError):
        recovery.redrive_queue_item(
            conn, b.open_queue, "MSH|other", request_id="RC-B-2", actor="analyst01"
        )
    event = one(conn, "SELECT event_id FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'")
    assert one(conn, "SELECT entity_id FROM audit_event WHERE event_id = ?", (event,)) == b.open_queue
    return [sorted([ae(event), q(b.open_queue)])]


def _aud_001(conn, b):
    conn.execute(
        "DELETE FROM audit_event WHERE action = 'INBOUND_RESULT_FILED' AND detail LIKE ?",
        (f"message_id={b.filed.message_id} %",),
    )
    return [[m(b.filed.message_id)]]


def _forge_filed(conn, order_id, detail):
    return conn.execute(
        "INSERT INTO audit_event (entity_type, entity_id, order_id, action, detail, actor) "
        "VALUES ('lab_order', ?, ?, 'INBOUND_RESULT_FILED', ?, 'forger')",
        (order_id, order_id, detail),
    ).lastrowid


def _aud_002(conn, b):
    missing = _forge_filed(conn, b.order_a, "message_id=99999 probes=1 (CBFB)")
    errored = _forge_filed(conn, b.order_a, f"message_id={b.open_message} probes=1 (CBFB)")
    other_order = _forge_filed(
        conn, b.order_nf, f"message_id={b.filed.message_id} probes=1 (CBFB)"
    )
    unnamed = _forge_filed(conn, b.order_a, "probes=1 (CBFB)")
    return [
        [ae(missing)],
        sorted([ae(errored), m(b.open_message)]),
        sorted([ae(other_order), m(b.filed.message_id)]),
        [ae(unnamed)],
    ]


def _aud_003(conn, b):
    rid = one(
        conn,
        "SELECT fr.result_id FROM fish_result AS fr JOIN probe AS p ON p.probe_id = fr.probe_id "
        "WHERE fr.order_id = ? AND p.probe_code = 'KMT2A'",
        (b.order_a,),
    )
    conn.execute(
        "DELETE FROM audit_event WHERE entity_type = 'fish_result' AND entity_id = ? "
        "AND action = 'RESULT_ENTERED'",
        (rid,),
    )
    return [sorted([f"fish_result:{rid}", o(b.order_a)])]


def _aud_004(conn, b):
    second = make_order(conn, "FIN2")
    finalize(conn, second)
    conn.execute("DELETE FROM report WHERE order_id = ?", (b.order_fin,))
    conn.execute(
        "DELETE FROM audit_event WHERE order_id = ? AND action IN ('REPORT_FINALIZED', 'FINALIZED')",
        (b.order_fin,),
    )
    conn.execute(
        "DELETE FROM audit_event WHERE order_id = ? AND action = 'FINALIZED'", (second,)
    )
    return [[o(b.order_fin)], [o(second)]]


def _aud_005(conn, b):
    conn.execute(
        "DELETE FROM audit_event WHERE entity_type = 'lab_order' AND entity_id = ? "
        "AND action = 'ORDERED'",
        (b.order_a,),
    )
    conn.execute(
        "DELETE FROM audit_event WHERE entity_type = 'specimen' AND entity_id = ? "
        "AND action IN ('RECEIVED', 'ACCESSIONED')",
        (b.specimen_a,),
    )
    spec = sorted([f"specimen:{b.specimen_a}", o(b.order_a)])
    return [[o(b.order_a)], spec, spec]


def _wf_001(conn, b):
    wip = make_order(conn, "WIP", accession=False)
    return [[o(b.order_a)], [o(b.order_nf)], [o(wip)]]


def _wf_002(conn, b):
    conn.execute(
        "DELETE FROM fish_result WHERE order_id = ? AND probe_id = "
        "(SELECT probe_id FROM probe WHERE probe_code = 'CBFB')",
        (b.order_fin,),
    )
    return [[o(b.order_fin)]]


def _wf_003(conn, b):
    workflow.run_validation(conn, b.order_a)
    workflow.run_validation(conn, b.order_nf)
    workflow.enter_fish_result(conn, b.order_a, "PML_RARA", 200, 2, "2R2G", "NORMAL", "tech01")
    stale = one(
        conn,
        "SELECT error_id FROM validation_error WHERE order_id = ? AND rule_code = 'MISSING_PROBE' "
        "AND message LIKE '%PML_RARA%'",
        (b.order_a,),
    )
    return [sorted([o(b.order_a), f"validation_error:{stale}"])]


NEGATIVE = {
    "REL-001": _rel_001, "REL-002": _rel_002,
    "MSG-001": _msg_001, "MSG-002": _msg_002, "MSG-003": _msg_003,
    "MSG-004": _msg_004, "MSG-005": _msg_005,
    "QUE-001": _que_001, "QUE-002": _que_002, "QUE-003": _que_003,
    "QUE-004": _que_004, "QUE-005": _que_005, "QUE-006": _que_006,
    "REC-001": _rec_001, "REC-002": _rec_002, "REC-003": _rec_003, "REC-004": _rec_004,
    "REC-005": _rec_005, "REC-006": _rec_006, "REC-007": _rec_007, "REC-008": _rec_008,
    "AUD-001": _aud_001, "AUD-002": _aud_002, "AUD-003": _aud_003, "AUD-004": _aud_004,
    "AUD-005": _aud_005,
    "WF-001": _wf_001, "WF-002": _wf_002, "WF-003": _wf_003,
}


def test_every_rule_has_a_negative_case():
    assert set(NEGATIVE) == set(reconcile.RULES) == set(EXPECTED_SEVERITY)


@pytest.mark.parametrize("rule_id", sorted(EXPECTED_SEVERITY))
def test_rule_fires_on_violating_state(conn, rule_id):
    b = build_base(conn)
    before = reconcile.reconcile_database(conn)
    if EXPECTED_SEVERITY[rule_id] != "INFO":
        assert by_rule(before, rule_id) == []
    expected = NEGATIVE[rule_id](conn, b)
    conn.commit()
    findings = reconcile.reconcile_database(conn)
    hits = by_rule(findings, rule_id)
    assert sorted(f["subjects"] for f in hits) == sorted(expected)
    for f in hits:
        assert f["severity"] == EXPECTED_SEVERITY[rule_id]
        assert f["title"] == reconcile.RULES[rule_id]["title"]
        assert f["documented_rule"] == reconcile.RULES[rule_id]["documented_rule"]
        assert f["documented_rule"].strip()
        assert f["observations"] and all(o_.strip() for o_ in f["observations"])


# ---------------------------------------------------------------------------
# Rule details beyond subjects
# ---------------------------------------------------------------------------

def test_wf_001_lists_each_blocking_rule(conn):
    b = build_base(conn)
    findings = reconcile.reconcile_database(conn)
    (hit,) = [f for f in by_rule(findings, "WF-001") if f["subjects"] == [o(b.order_a)]]
    missing = one(
        conn,
        "SELECT COUNT(*) FROM probe AS p WHERE p.is_required = 1 AND NOT EXISTS "
        "(SELECT 1 FROM fish_result AS fr WHERE fr.order_id = ? AND fr.probe_id = p.probe_id)",
        (b.order_a,),
    )
    assert missing == 7
    assert len(hit["observations"]) == missing
    assert all("MISSING_PROBE" in obs for obs in hit["observations"])


def test_wf_003_reports_stored_and_current_differences(conn):
    b = build_base(conn)
    _wf_003(conn, b)
    (hit,) = by_rule(reconcile.reconcile_database(conn), "WF-003")
    assert any(obs.startswith("stored only") and "PML_RARA" in obs for obs in hit["observations"])
    assert hit["ambiguity"]


def test_rec_006_names_both_fingerprint_checks(conn):
    b = build_base(conn)
    _rec_006(conn, b)
    hits = {tuple(f["subjects"]): f for f in by_rule(reconcile.reconcile_database(conn), "REC-006")}
    retry = hits[tuple(sorted([a(b.retried.attempt_id), m(b.retried.resulting_message_id),
                              m(b.nf_message)]))]
    assert len(retry["observations"]) == 2
    assert any("original interface_message" in obs for obs in retry["observations"])


def test_rec_006_original_payload_check_alone(conn):
    b = build_base(conn)
    conn.execute(
        "UPDATE interface_message SET payload = payload || 'X' WHERE message_id = ?",
        (b.nf_message,),
    )
    findings = reconcile.reconcile_database(conn)
    assert subjects_of(findings, "REC-006") == [
        sorted([a(b.retried.attempt_id), m(b.nf_message)])
    ]
    assert subjects_of(findings, "QUE-005") == [sorted([q(b.nf_queue), m(b.nf_message)])]


def test_audit_text_rules_declare_ambiguity(conn):
    b = build_base(conn)
    _aud_001(conn, b)
    _forge_filed(conn, b.order_a, "message_id=99999 probes=1 (CBFB)")
    findings = reconcile.reconcile_database(conn)
    for rule_id in ("AUD-001", "AUD-002"):
        hits = by_rule(findings, rule_id)
        assert hits and all("free-text audit detail" in f["ambiguity"] for f in hits)
    assert all(f["ambiguity"] is None for f in by_rule(findings, "WF-001"))


def test_wf_scope_limited_to_requested_orders(conn):
    b = build_base(conn)
    scoped = reconcile.reconcile_database(conn, order_ids={b.order_a})
    assert subjects_of(scoped, "WF-001") == [[o(b.order_a)]]
    assert subjects_of(scoped, "WF-003") == [[o(b.order_a)]]
    none = reconcile.reconcile_database(conn, order_ids=set())
    assert [f for f in none if f["rule_id"].startswith("WF-")] == []


# ---------------------------------------------------------------------------
# reconcile_case
# ---------------------------------------------------------------------------

def test_reconcile_case_only_returns_findings_touching_the_case(conn):
    b = build_base(conn)
    # Problems outside the NF case ...
    _msg_002(conn, b)
    _que_003(conn, b)
    # ... and one inside it.
    conn.execute(
        "UPDATE interface_message SET status = 'RECEIVED' WHERE message_id = ?",
        (b.failed.resulting_message_id,),
    )
    conn.commit()
    case = trace.build_case(conn, queue_id=b.nf_queue)
    refs = trace.case_record_refs(case)
    case_findings = reconcile.reconcile_case(conn, case)
    assert case_findings
    for f in case_findings:
        assert any(s in refs for s in f["subjects"]), f
    all_findings = reconcile.reconcile_database(conn)
    expected = [f for f in all_findings if any(s in refs for s in f["subjects"])
                and not (f["rule_id"].startswith("WF-") and f["subjects"][0] != o(b.order_nf))]
    assert case_findings == expected
    rules = {f["rule_id"] for f in case_findings}
    assert {"REC-004", "MSG-002"} <= rules
    assert "QUE-003" not in rules
    assert subjects_of(case_findings, "WF-001") == [[o(b.order_nf)]]
    # The outside problems exist at database level.
    assert by_rule(all_findings, "QUE-003")


# ---------------------------------------------------------------------------
# Statement count does not grow with messages / queue items / attempts
# ---------------------------------------------------------------------------

def _scaled(conn, n: int) -> None:
    open_order = make_order(conn, "SCALE")
    fin = make_order(conn, "SCALEFIN")
    finalize(conn, fin)
    for i in range(n):
        if i % 2:
            ingest(conn, hl7("ACC-RC-SCALEFIN", f"SC-F-{i}"))
        else:
            res = ingest(conn, hl7("ACC-RC-SCALE", f"SC-O-{i}", obx=BAD_COUNT))
            if i % 4 == 0:
                att = recovery.redrive_queue_item(
                    conn, res.queue_id, hl7("ACC-RC-SCALE", f"SC-R-{i}", obx=BAD_COUNT),
                    request_id=f"SC-REQ-{i}", actor="analyst01",
                )
                assert att.outcome == "FAILED"
    ingest(conn, hl7("ACC-RC-SCALE", "SC-FILED", obx=FULL_OBX[:3]))
    assert open_order and fin


def _count_statements(conn, **kwargs) -> tuple[int, list]:
    statements: list[str] = []
    conn.set_trace_callback(statements.append)
    try:
        findings = reconcile.reconcile_database(conn, **kwargs)
    finally:
        conn.set_trace_callback(None)
    return len(statements), findings


def test_statement_count_is_constant_as_rows_grow():
    from src.db import create_database

    counts = {}
    for n in (20, 200):
        c = create_database(":memory:")
        try:
            _scaled(c, n)
            messages = one(c, "SELECT COUNT(*) FROM interface_message")
            attempts = one(c, "SELECT COUNT(*) FROM interface_recovery_attempt")
            total, findings = _count_statements(c)
            non_wf, _ = _count_statements(c, order_ids=set())
            counts[n] = (total, non_wf, messages, attempts)
            assert [f for f in findings if f["severity"] != "INFO"] == []
        finally:
            c.close()
    small, large = counts[20], counts[200]
    assert large[2] > 5 * small[2] and large[3] > 5 * small[3]
    assert large[1] == small[1]
    assert large[0] == small[0]
