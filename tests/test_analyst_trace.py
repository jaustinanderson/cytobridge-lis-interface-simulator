"""Tests for the analyst-lab case view (``src/analyst/trace.py``) and the
analyst queue worklist query (``queries/analyst_queue_worklist.sql``).

Expectations are checked against SQL written independently in this file, never
by reusing the module's own queries. Broken or forged states are built with
direct SQL only where a test needs them. All data is synthetic. No PHI.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from src import recovery, workflow
from src.analyst import store, trace
from src.analyst.common import AnalystError, canonical_json
from src.analyst.reconcile import reconcile_database
from src.db import run_query
from src.interfaces import inbound_hl7, outbound_fhir, outbound_hl7
from tests.conftest import enter_all_results

# ---------------------------------------------------------------------------
# Synthetic builders
# ---------------------------------------------------------------------------

GOOD_CBFB = (("CBFB", "200^1^2 orange/green^NORMAL"),)


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
        "PID|1||SYN-TR-PID^^^CYTO_LAB^MR||Synthetic^Trace||19700101|U",
    ]
    if obr:
        segments.append(f"OBR|1||{accession}|AML_MDS_FISH^AML/MDS FISH Panel^L")
    segments.append(f"SPM|1|SPM-TR||{specimen}")
    for i, (code, value) in enumerate(obx, 1):
        segments.append(f"OBX|{i}|ST|{code}^{code}^L||{value}||||||F")
    return "\r".join(segments)


def make_order(conn, suffix: str) -> int:
    """Patient + accessioned bone-marrow order with accession ACC-TR-<suffix>."""
    patient_id = workflow.create_patient(
        conn, f"SYN-TR-{suffix}", "Synthetic", f"Trace{suffix}", "1970-01-01", "U"
    )
    order_id = workflow.create_order(conn, patient_id, f"ACC-TR-{suffix}", "Dr. Synthetic")
    specimen_id = workflow.receive_specimen(conn, order_id)
    workflow.accession_specimen(conn, specimen_id)
    return order_id


def finalize_with_exports(conn, suffix: str) -> int:
    order_id = make_order(conn, suffix)
    enter_all_results(conn, order_id)
    assert workflow.finalize_order(conn, order_id, "pathologist01").finalized
    outbound_hl7.store_oru(conn, order_id, generated_at="2026-07-10 12:00:00")
    outbound_fhir.store_diagnostic_report(conn, order_id, generated_at="2026-07-10 12:00:00")
    return order_id


def ids(rows, key):
    return [r[key] for r in rows]


def notes(case, code):
    return [n for n in case["provenance"] if n["code"] == code]


def links_of(case, basis=None):
    return [l for l in case["links"] if basis is None or l["basis"] == basis]


def without_anchor(case):
    return {k: v for k, v in case.items() if k != "anchor"}


# Primary keys and FK parent tables, transcribed from schema.sql.
PK = {
    "audit_event": "event_id",
    "fish_result": "result_id",
    "interface_error_queue": "queue_id",
    "interface_message": "message_id",
    "interface_recovery_attempt": "attempt_id",
    "lab_order": "order_id",
    "patient": "patient_id",
    "report": "report_id",
    "specimen": "specimen_id",
    "validation_error": "error_id",
}
FK_PARENT = {
    "interface_error_queue.message_id": "interface_message",
    "interface_recovery_attempt.queue_id": "interface_error_queue",
    "interface_recovery_attempt.resulting_message_id": "interface_message",
    "interface_message.order_id": "lab_order",
    "lab_order.patient_id": "patient",
    "specimen.order_id": "lab_order",
    "fish_result.order_id": "lab_order",
    "report.order_id": "lab_order",
    "validation_error.order_id": "lab_order",
}


def _split(ref_text):
    table, row_id = ref_text.split(":")
    return table, int(row_id)


def assert_fk_links_exist(conn, case):
    """Every FOREIGN_KEY link names a real FK column value and a real parent row."""
    for link in links_of(case, "FOREIGN_KEY"):
        assert link["relation"] in FK_PARENT, link
        table, column = link["relation"].split(".")
        src_table, src_id = _split(link["from"])
        dst_table, dst_id = _split(link["to"])
        assert src_table == table
        assert dst_table == FK_PARENT[link["relation"]]
        row = conn.execute(
            f"SELECT {column} FROM {table} WHERE {PK[table]} = ?", (src_id,)
        ).fetchone()
        assert row is not None and row[0] == dst_id, link
        assert conn.execute(
            f"SELECT 1 FROM {dst_table} WHERE {PK[dst_table]} = ?", (dst_id,)
        ).fetchone(), link


def expected_fk_links(conn, case):
    """FK links among the case records, computed from whole-table SQL."""
    orders = set(ids(case["orders"], "order_id"))
    messages = set(ids(case["messages"], "message_id"))
    queues = set(ids(case["queue_items"], "queue_id"))
    attempts = set(ids(case["recovery_attempts"], "attempt_id"))
    out = set()
    for qid, mid in conn.execute(
        "SELECT queue_id, message_id FROM interface_error_queue WHERE message_id IS NOT NULL"
    ):
        if qid in queues:
            out.add((f"interface_error_queue:{qid}", f"interface_message:{mid}",
                     "interface_error_queue.message_id"))
    for aid, qid, mid in conn.execute(
        "SELECT attempt_id, queue_id, resulting_message_id FROM interface_recovery_attempt"
    ):
        if aid in attempts:
            out.add((f"interface_recovery_attempt:{aid}", f"interface_error_queue:{qid}",
                     "interface_recovery_attempt.queue_id"))
            if mid is not None:
                out.add((f"interface_recovery_attempt:{aid}", f"interface_message:{mid}",
                         "interface_recovery_attempt.resulting_message_id"))
    for mid, oid in conn.execute(
        "SELECT message_id, order_id FROM interface_message WHERE order_id IS NOT NULL"
    ):
        if mid in messages:
            out.add((f"interface_message:{mid}", f"lab_order:{oid}", "interface_message.order_id"))
    for oid, pid in conn.execute("SELECT order_id, patient_id FROM lab_order"):
        if oid in orders:
            out.add((f"lab_order:{oid}", f"patient:{pid}", "lab_order.patient_id"))
    for table, key in (
        ("specimen", "specimen_id"),
        ("fish_result", "result_id"),
        ("report", "report_id"),
        ("validation_error", "error_id"),
    ):
        for row_id, oid in conn.execute(f"SELECT {key}, order_id FROM {table}"):
            if oid in orders:
                out.add((f"{table}:{row_id}", f"lab_order:{oid}", f"{table}.order_id"))
    return out


def assert_closure_complete(conn, case):
    """Independent SQL: the case is closed under every FK edge that the
    ``src/analyst/trace.py`` module docstring lists for the closure."""
    orders = set(ids(case["orders"], "order_id"))
    messages = set(ids(case["messages"], "message_id"))
    queues = set(ids(case["queue_items"], "queue_id"))
    attempts = set(ids(case["recovery_attempts"], "attempt_id"))
    for mid, oid in conn.execute("SELECT message_id, order_id FROM interface_message"):
        if oid in orders:
            assert mid in messages
        if mid in messages and oid is not None:
            assert oid in orders
    for qid, mid in conn.execute("SELECT queue_id, message_id FROM interface_error_queue"):
        if mid in messages:
            assert qid in queues
        if qid in queues and mid is not None:
            assert mid in messages
    for aid, qid, mid in conn.execute(
        "SELECT attempt_id, queue_id, resulting_message_id FROM interface_recovery_attempt"
    ):
        if qid in queues or mid in messages:
            assert aid in attempts
        if aid in attempts:
            assert qid in queues
            assert mid is None or mid in messages


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def recovery_case(conn):
    """ORDER_NOT_FOUND message, later order, FAILED re-drive, SUCCEEDED retry."""
    other_order = make_order(conn, "OTHER")
    other = inbound_hl7.ingest_message(conn, hl7("ACC-TR-OTHER", "TR-OTHER-1"))
    assert other.filed

    original = inbound_hl7.ingest_message(conn, hl7("ACC-TR-0001", "TR-ORIG"))
    assert not original.filed
    order_id = make_order(conn, "0001")
    failed = recovery.redrive_queue_item(
        conn,
        original.queue_id,
        hl7("ACC-TR-0001", "TR-BAD", obx=(("CBFB", "abc^1^2F^NORMAL"),)),
        request_id="TR-REQ-1",
        actor="analyst01",
    )
    assert failed.outcome == "FAILED"
    retried = recovery.retry_queue_item(
        conn, original.queue_id, request_id="TR-REQ-2", actor="analyst01"
    )
    assert retried.outcome == "SUCCEEDED"
    patient_id = conn.execute(
        "SELECT patient_id FROM lab_order WHERE order_id = ?", (order_id,)
    ).fetchone()[0]
    return {
        "order_id": order_id,
        "patient_id": patient_id,
        "queue_id": original.queue_id,
        "original_message_id": original.message_id,
        "failed_message_id": failed.resulting_message_id,
        "retry_message_id": retried.resulting_message_id,
        "failed_attempt_id": failed.attempt_id,
        "retry_attempt_id": retried.attempt_id,
        "other_order_id": other_order,
        "other_message_id": other.message_id,
    }


# ---------------------------------------------------------------------------
# payload_accession
# ---------------------------------------------------------------------------

def test_payload_accession_reads_first_obr_component_zero():
    assert trace.payload_accession(hl7("ACC-TR-0042", "C1")) == "ACC-TR-0042"
    two_obr = hl7("ACC-FIRST", "C1") + "\rOBR|2||ACC-SECOND|X"
    assert trace.payload_accession(two_obr) == "ACC-FIRST"
    assert trace.payload_accession("MSH|x\nOBR|1|| ACC-SP ^sub^x|y\n") == "ACC-SP"


@pytest.mark.parametrize(
    "payload",
    ["", "   \r\n", "MSH|^~\\&|A\rPID|1", "OBR|1|", "OBR|1||", "OBR|1|| ^x", None, 42],
)
def test_payload_accession_unreadable_is_none_and_never_raises(payload):
    assert trace.payload_accession(payload) is None


# ---------------------------------------------------------------------------
# Anchors
# ---------------------------------------------------------------------------

def test_all_anchors_reach_the_same_closure(conn, recovery_case):
    rc = recovery_case
    cases = [
        trace.build_case(conn, order_id=rc["order_id"]),
        trace.build_case(conn, queue_id=rc["queue_id"]),
        trace.build_case(conn, message_id=rc["original_message_id"]),
        trace.build_case(conn, message_id=rc["failed_message_id"]),
        trace.build_case(conn, message_id=rc["retry_message_id"]),
        trace.build_case(conn, accession="ACC-TR-0001"),
    ]
    assert [c["anchor"] for c in cases] == [
        {"kind": "order", "id": rc["order_id"]},
        {"kind": "queue", "id": rc["queue_id"]},
        {"kind": "message", "id": rc["original_message_id"]},
        {"kind": "message", "id": rc["failed_message_id"]},
        {"kind": "message", "id": rc["retry_message_id"]},
        {"kind": "order", "id": rc["order_id"]},
    ]
    first = without_anchor(cases[0])
    for other in cases[1:]:
        assert without_anchor(other) == first

    case = cases[0]
    assert ids(case["orders"], "order_id") == [rc["order_id"]]
    assert ids(case["messages"], "message_id") == sorted(
        [rc["original_message_id"], rc["failed_message_id"], rc["retry_message_id"]]
    )
    assert ids(case["queue_items"], "queue_id") == [rc["queue_id"]]
    assert ids(case["recovery_attempts"], "attempt_id") == sorted(
        [rc["failed_attempt_id"], rc["retry_attempt_id"]]
    )
    # Unrelated traffic stays out of the case.
    assert rc["other_order_id"] not in ids(case["orders"], "order_id")
    assert rc["other_message_id"] not in ids(case["messages"], "message_id")


def test_unknown_anchor_ids_are_not_found(conn, recovery_case):
    for kwargs in (
        {"order_id": 999},
        {"queue_id": 999},
        {"message_id": 999},
        {"accession": "ACC-TR-NOPE"},
    ):
        with pytest.raises(AnalystError) as err:
            trace.build_case(conn, **kwargs)
        assert err.value.kind == "NOT_FOUND"
        with pytest.raises(AnalystError) as err:
            trace.resolve_anchor(conn, **kwargs)
        assert err.value.kind == "NOT_FOUND"


@pytest.mark.parametrize(
    "kwargs",
    [{}, {"order_id": 1, "queue_id": 1}, {"message_id": 1, "accession": "ACC-TR-0001"}],
)
def test_zero_or_two_selectors_are_usage_errors(conn, recovery_case, kwargs):
    with pytest.raises(AnalystError) as err:
        trace.build_case(conn, **kwargs)
    assert err.value.kind == "USAGE"
    assert err.value.exit_code == 2


def test_accession_resolves_to_its_order(conn, recovery_case):
    assert trace.resolve_anchor(conn, accession="ACC-TR-0001") == {
        "kind": "order",
        "id": recovery_case["order_id"],
    }


# ---------------------------------------------------------------------------
# Foreign-key links and closure
# ---------------------------------------------------------------------------

def test_foreign_key_links_exist_and_are_complete(conn, recovery_case):
    finalize_with_exports(conn, "FINX")
    workflow.run_validation(conn, recovery_case["order_id"])
    case = trace.build_case(conn, order_id=recovery_case["order_id"])
    assert_fk_links_exist(conn, case)
    reported = {(l["from"], l["to"], l["relation"]) for l in links_of(case, "FOREIGN_KEY")}
    assert reported == expected_fk_links(conn, case)
    assert_closure_complete(conn, case)
    relations = {l["relation"] for l in links_of(case, "FOREIGN_KEY")}
    assert relations == {
        "interface_error_queue.message_id",
        "interface_recovery_attempt.queue_id",
        "interface_recovery_attempt.resulting_message_id",
        "interface_message.order_id",
        "lab_order.patient_id",
        "specimen.order_id",
        "fish_result.order_id",
        "validation_error.order_id",
    }


def test_finalized_order_case_links_report_and_outbound_exports(conn):
    order_id = finalize_with_exports(conn, "FIN")
    case = trace.build_case(conn, order_id=order_id)
    assert_fk_links_exist(conn, case)
    reported = {(l["from"], l["to"], l["relation"]) for l in links_of(case, "FOREIGN_KEY")}
    assert reported == expected_fk_links(conn, case)
    report_id = conn.execute(
        "SELECT report_id FROM report WHERE order_id = ?", (order_id,)
    ).fetchone()[0]
    assert (f"report:{report_id}", f"lab_order:{order_id}", "report.order_id") in reported
    outbound = [
        r[0]
        for r in conn.execute(
            "SELECT message_id FROM interface_message WHERE direction = 'OUTBOUND' "
            "AND order_id = ? ORDER BY message_id",
            (order_id,),
        )
    ]
    assert len(outbound) == 2
    by_id = {m["message_id"]: m for m in case["messages"]}
    for mid in outbound:
        assert by_id[mid]["roles"] == ["OUTBOUND_EXPORT"]
        assert by_id[mid]["payload_accession"] is None
    order = case["orders"][0]
    summary = conn.execute(
        "SELECT summary_text FROM report WHERE order_id = ?", (order_id,)
    ).fetchone()[0]
    assert order["report"]["status"] == "FINALIZED"
    assert order["report"]["summary_sha256"] == hashlib.sha256(
        summary.encode("utf-8")
    ).hexdigest()
    assert order["required_probe_count"] == conn.execute(
        "SELECT COUNT(*) FROM probe WHERE panel_id = 1 AND is_required = 1"
    ).fetchone()[0]
    assert len(order["current_results"]) == 9
    assert [r["probe_id"] for r in order["current_results"]] == sorted(
        r["probe_id"] for r in order["current_results"]
    )


def test_case_records_match_stored_rows(conn, recovery_case):
    rc = recovery_case
    case = trace.build_case(conn, queue_id=rc["queue_id"])
    by_id = {m["message_id"]: m for m in case["messages"]}
    for mid, payload, status, order_id in conn.execute(
        "SELECT message_id, payload, status, order_id FROM interface_message"
    ):
        if mid not in by_id:
            continue
        m = by_id[mid]
        assert m["payload"] == payload
        assert m["payload_sha256"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
        assert m["payload_bytes"] == len(payload.encode("utf-8"))
        assert m["status"] == status and m["order_id"] == order_id
        assert m["payload_accession"] == "ACC-TR-0001"
    assert by_id[rc["original_message_id"]]["roles"] == ["QUEUE_ORIGINAL"]
    assert by_id[rc["failed_message_id"]]["roles"] == ["RECOVERY_RESULT"]
    assert by_id[rc["retry_message_id"]]["roles"] == ["RECOVERY_RESULT"]

    raw, original_payload = conn.execute(
        "SELECT eq.raw_payload, im.payload FROM interface_error_queue AS eq "
        "JOIN interface_message AS im ON im.message_id = eq.message_id "
        "WHERE eq.queue_id = ?",
        (rc["queue_id"],),
    ).fetchone()
    q = case["queue_items"][0]
    assert q["raw_payload_sha256"] == hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert q["raw_payload_matches_original"] is (raw == original_payload) is True
    assert (q["failure_code"], q["failure_category"], q["recovery_policy"], q["status"]) == (
        "ORDER_NOT_FOUND", "ORDER_MATCHING", "RETRY_OR_REDRIVE", "RESOLVED"
    )
    attempts = {a["attempt_id"]: a for a in case["recovery_attempts"]}
    for row in conn.execute("SELECT * FROM interface_recovery_attempt"):
        assert attempts[row["attempt_id"]] == dict(row)

    expected_events = [
        r[0]
        for r in conn.execute(
            "SELECT event_id FROM audit_event WHERE order_id = ? "
            "OR (entity_type = 'interface_error_queue' AND entity_id = ?) "
            "OR (entity_type = 'patient' AND entity_id = ?) ORDER BY event_id",
            (rc["order_id"], rc["queue_id"], rc["patient_id"]),
        )
    ]
    assert ids(case["audit_events"], "event_id") == expected_events


def test_inbound_direct_role_and_audit_detail_link(conn):
    order_id = make_order(conn, "DIRECT")
    filed = inbound_hl7.ingest_message(conn, hl7("ACC-TR-DIRECT", "TR-DIRECT-1"))
    case = trace.build_case(conn, message_id=filed.message_id)
    assert [m["roles"] for m in case["messages"]] == [["INBOUND_DIRECT"]]
    event_id, detail = conn.execute(
        "SELECT event_id, detail FROM audit_event WHERE action = 'INBOUND_RESULT_FILED' "
        "AND order_id = ?",
        (order_id,),
    ).fetchone()
    assert detail.split()[0] == f"message_id={filed.message_id}"
    assert {
        "from": f"audit_event:{event_id}",
        "to": f"interface_message:{filed.message_id}",
        "relation": "audit detail message_id=N",
        "basis": "AUDIT_DETAIL_TEXT",
    } in case["links"]
    assert "AUDIT_DETAIL_TEXT" in [l["code"] for l in case["limitations"]]
    # The audit row's denormalized order_id is labelled as such.
    assert {
        "from": f"audit_event:{event_id}",
        "to": f"lab_order:{order_id}",
        "relation": "audit_event.order_id",
        "basis": "DENORMALIZED_COLUMN",
    } in case["links"]
    assert notes(case, "FAILED_MESSAGE_NO_ORDER_FK") == []


def test_link_bases_vocabulary():
    assert trace.BASES == (
        "FOREIGN_KEY", "PAYLOAD_DERIVED", "AUDIT_DETAIL_TEXT", "DENORMALIZED_COLUMN",
        "POLYMORPHIC_REFERENCE",
    )
    assert trace.REL_AUDIT_ENTITY == "audit_event.entity_type/entity_id"


def test_audit_events_selected_by_entity_reference_are_linked(conn, recovery_case):
    """EX05-style conflict on a queue item, and the patient CREATED event."""
    rc = recovery_case
    with pytest.raises(recovery.RequestIdConflictError):
        recovery.retry_queue_item(
            conn, rc["queue_id"], request_id="TR-REQ-2", actor="someone-else"
        )
    conflict, conflict_order = conn.execute(
        "SELECT event_id, order_id FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'"
    ).fetchone()
    created, created_order = conn.execute(
        "SELECT event_id, order_id FROM audit_event WHERE entity_type = 'patient' "
        "AND entity_id = ? AND action = 'CREATED'",
        (rc["patient_id"],),
    ).fetchone()
    assert conflict_order is None and created_order is None

    case = trace.build_case(conn, queue_id=rc["queue_id"])
    poly = links_of(case, "POLYMORPHIC_REFERENCE")
    assert {
        "from": f"audit_event:{conflict}",
        "to": f"interface_error_queue:{rc['queue_id']}",
        "relation": "audit_event.entity_type/entity_id",
        "basis": "POLYMORPHIC_REFERENCE",
    } in poly
    assert {
        "from": f"audit_event:{created}",
        "to": f"patient:{rc['patient_id']}",
        "relation": "audit_event.entity_type/entity_id",
        "basis": "POLYMORPHIC_REFERENCE",
    } in poly

    # Independent SQL: exactly the case's audit events that are not on a case
    # order but name a case queue item or patient by entity reference.
    orders = set(ids(case["orders"], "order_id"))
    queues = set(ids(case["queue_items"], "queue_id"))
    patients = {o["patient"]["patient_id"] for o in case["orders"]}
    expected = set()
    for event_id, entity_type, entity_id, order_id in conn.execute(
        "SELECT event_id, entity_type, entity_id, order_id FROM audit_event"
    ):
        if order_id in orders:
            continue
        if (entity_type == "interface_error_queue" and entity_id in queues) or (
            entity_type == "patient" and entity_id in patients
        ):
            expected.add((f"audit_event:{event_id}", f"{entity_type}:{entity_id}"))
    assert {(l["from"], l["to"]) for l in poly} == expected
    assert {l["relation"] for l in poly} == {"audit_event.entity_type/entity_id"}
    # An event linked by its order_id column gets no entity-reference link.
    denormalized = {l["from"] for l in links_of(case, "DENORMALIZED_COLUMN")}
    assert not denormalized & {l["from"] for l in poly}
    # Every audit event in the case now has at least one stated relationship.
    linked = {l["from"] for l in case["links"] if l["from"].startswith("audit_event:")}
    assert linked == {f"audit_event:{e}" for e in ids(case["audit_events"], "event_id")}
    # Each target is a record in the case.
    assert {l["to"] for l in poly} <= trace.case_record_refs(case)


# ---------------------------------------------------------------------------
# PAYLOAD_DERIVED links and message provenance
# ---------------------------------------------------------------------------

def test_order_not_found_message_later_matched_by_payload(conn, recovery_case):
    rc = recovery_case
    case = trace.build_case(conn, message_id=rc["original_message_id"])
    row = conn.execute(
        "SELECT order_id, status FROM interface_message WHERE message_id = ?",
        (rc["original_message_id"],),
    ).fetchone()
    assert tuple(row) == (None, "ERRORED")
    derived = links_of(case, "PAYLOAD_DERIVED")
    assert {(l["from"], l["to"]) for l in derived} == {
        (f"interface_message:{rc['original_message_id']}", f"lab_order:{rc['order_id']}"),
        (f"interface_message:{rc['failed_message_id']}", f"lab_order:{rc['order_id']}"),
    }
    assert {l["relation"] for l in derived} == {
        "payload OBR-3 accession = lab_order.accession_number"
    }
    fk_from_failed = [
        l for l in links_of(case, "FOREIGN_KEY")
        if l["from"] == f"interface_message:{rc['original_message_id']}"
    ]
    assert fk_from_failed == []
    no_fk = {n["subject"]: n["detail"] for n in notes(case, "FAILED_MESSAGE_NO_ORDER_FK")}
    assert set(no_fk) == {
        f"interface_message:{rc['original_message_id']}",
        f"interface_message:{rc['failed_message_id']}",
    }
    for detail in no_fk.values():
        assert "PAYLOAD_DERIVED" in detail and f"lab_order:{rc['order_id']}" in detail
    codes = [l["code"] for l in case["limitations"]]
    assert codes == sorted([
        "AUDIT_DETAIL_TEXT", "FAILED_MESSAGE_ORDER_LINK", "NO_RESULT_SOURCE_FK",
        "RESULT_AUDIT_ACTOR", "RESULTS_UPSERTED", "TIMESTAMP_RESOLUTION",
    ])
    assert notes(case, "ACCESSION_NOT_MATCHED") == []
    assert notes(case, "ACCESSION_UNREADABLE") == []


def test_finalized_order_rejection_is_linked_only_by_payload(conn):
    order_id = make_order(conn, "FIN")
    enter_all_results(conn, order_id)
    assert workflow.finalize_order(conn, order_id, "pathologist01").finalized
    rejected = inbound_hl7.ingest_message(conn, hl7("ACC-TR-FIN", "TR-FIN-LATE"))
    assert not rejected.filed
    msg = conn.execute(
        "SELECT order_id, status FROM interface_message WHERE message_id = ?",
        (rejected.message_id,),
    ).fetchone()
    assert tuple(msg) == (None, "ERRORED")
    queue = conn.execute(
        "SELECT status, failure_code FROM interface_error_queue WHERE queue_id = ?",
        (rejected.queue_id,),
    ).fetchone()
    assert tuple(queue) == ("TERMINAL", "ORDER_FINALIZED")

    by_queue = trace.build_case(conn, queue_id=rejected.queue_id)
    by_order = trace.build_case(conn, order_id=order_id)
    assert without_anchor(by_queue) == without_anchor(by_order)
    assert ids(by_queue["orders"], "order_id") == [order_id]
    message_links = [
        l for l in by_queue["links"]
        if l["from"] == f"interface_message:{rejected.message_id}"
    ]
    assert message_links == [{
        "from": f"interface_message:{rejected.message_id}",
        "to": f"lab_order:{order_id}",
        "relation": "payload OBR-3 accession = lab_order.accession_number",
        "basis": "PAYLOAD_DERIVED",
    }]
    assert [n["subject"] for n in notes(by_queue, "FAILED_MESSAGE_NO_ORDER_FK")] == [
        f"interface_message:{rejected.message_id}"
    ]
    kinds = [(e["table"], e["id"], e["kind"]) for e in by_queue["timeline"]]
    assert ("interface_error_queue", rejected.queue_id, "QUEUE_TERMINAL") in kinds
    assert ("interface_error_queue", rejected.queue_id, "QUEUED") in kinds


def test_accession_substring_does_not_create_payload_link(conn):
    order_id = make_order(conn, "1")
    near_miss = inbound_hl7.ingest_message(conn, hl7("ACC-TR-10", "TR-NEAR"))
    assert not near_miss.filed
    case = trace.build_case(conn, order_id=order_id)
    assert near_miss.message_id not in ids(case["messages"], "message_id")
    assert links_of(case, "PAYLOAD_DERIVED") == []


def test_accession_not_matched(conn):
    lost = inbound_hl7.ingest_message(conn, hl7("ACC-TR-LOST", "TR-LOST"))
    case = trace.build_case(conn, message_id=lost.message_id)
    assert case["orders"] == []
    assert conn.execute(
        "SELECT COUNT(*) FROM lab_order WHERE accession_number = 'ACC-TR-LOST'"
    ).fetchone()[0] == 0
    subject = f"interface_message:{lost.message_id}"
    not_matched = notes(case, "ACCESSION_NOT_MATCHED")
    assert [n["subject"] for n in not_matched] == [subject]
    assert "ACC-TR-LOST" in not_matched[0]["detail"]
    no_fk = notes(case, "FAILED_MESSAGE_NO_ORDER_FK")
    assert [n["subject"] for n in no_fk] == [subject]
    assert "no order matches" in no_fk[0]["detail"]
    assert links_of(case, "PAYLOAD_DERIVED") == []
    assert "FAILED_MESSAGE_ORDER_LINK" in [l["code"] for l in case["limitations"]]


def test_accession_unreadable_for_empty_missing_obr_and_empty_obr3(conn):
    empty = inbound_hl7.ingest_message(conn, "")
    no_obr = inbound_hl7.ingest_message(conn, hl7("ignored", "TR-NOOBR", obr=False))
    blank = inbound_hl7.ingest_message(conn, hl7("", "TR-BLANK"))
    codes = dict(conn.execute(
        "SELECT message_id, failure_code FROM interface_error_queue"
    ).fetchall())
    assert codes == {
        empty.message_id: "EMPTY_MESSAGE",
        no_obr.message_id: "MISSING_REQUIRED_SEGMENT",
        blank.message_id: "MISSING_ACCESSION",
    }
    expected_reason = {
        empty.message_id: "empty",
        no_obr.message_id: "no OBR segment",
        blank.message_id: "empty OBR-3",
    }
    for mid, reason in expected_reason.items():
        case = trace.build_case(conn, message_id=mid)
        assert case["messages"][0]["payload_accession"] is None
        unreadable = notes(case, "ACCESSION_UNREADABLE")
        assert [n["subject"] for n in unreadable] == [f"interface_message:{mid}"]
        assert reason in unreadable[0]["detail"]
        assert [n["subject"] for n in notes(case, "FAILED_MESSAGE_NO_ORDER_FK")] == [
            f"interface_message:{mid}"
        ]
        assert notes(case, "ACCESSION_NOT_MATCHED") == []
        assert case["orders"] == []


def test_raw_payload_mismatch_note(conn, recovery_case):
    conn.execute(
        "UPDATE interface_error_queue SET raw_payload = raw_payload || 'X' WHERE queue_id = ?",
        (recovery_case["queue_id"],),
    )
    case = trace.build_case(conn, queue_id=recovery_case["queue_id"])
    assert case["queue_items"][0]["raw_payload_matches_original"] is False
    assert [n["subject"] for n in notes(case, "RAW_PAYLOAD_MISMATCH")] == [
        f"interface_error_queue:{recovery_case['queue_id']}"
    ]


# ---------------------------------------------------------------------------
# Result provenance (no source FK) and audit references
# ---------------------------------------------------------------------------

def _result_ids(conn, order_id):
    return dict(conn.execute(
        "SELECT pr.probe_code, fr.result_id FROM fish_result AS fr "
        "JOIN probe AS pr ON pr.probe_id = fr.probe_id WHERE fr.order_id = ?",
        (order_id,),
    ).fetchall())


def test_same_probe_filed_twice_lists_both_candidates(conn):
    order_id = make_order(conn, "DUP")
    first = inbound_hl7.ingest_message(
        conn, hl7("ACC-TR-DUP", "TR-DUP-1", obx=(("CBFB", "200^1^2F^NORMAL"),))
    )
    second = inbound_hl7.ingest_message(
        conn,
        hl7("ACC-TR-DUP", "TR-DUP-2",
            obx=(("CBFB", "200^2^2F^NORMAL"), ("KMT2A", "200^1^2 fusion^NORMAL"))),
    )
    assert first.filed and second.filed
    results = _result_ids(conn, order_id)
    case = trace.build_case(conn, order_id=order_id)
    by_subject = {n["subject"]: n["detail"] for n in notes(case, "RESULT_SOURCE_NOT_RECORDED")}
    assert set(by_subject) == {f"fish_result:{rid}" for rid in results.values()}

    cbfb = by_subject[f"fish_result:{results['CBFB']}"]
    assert f"interface_message:{first.message_id}" in cbfb
    assert f"interface_message:{second.message_id}" in cbfb
    assert "RESULT_ENTERED events for this result: 2" in cbfb
    assert "inbound filings naming this probe: 2" in cbfb
    assert "without a matching inbound filing: no" in cbfb
    assert "not attributed" in cbfb

    kmt2a = by_subject[f"fish_result:{results['KMT2A']}"]
    assert f"interface_message:{second.message_id}" in kmt2a
    assert f"interface_message:{first.message_id}" not in kmt2a

    # Nothing asserts which message produced the current value.
    assert not [
        l for l in case["links"]
        if l["from"].startswith("fish_result:") and l["to"].startswith("interface_message:")
    ]
    assert {"RESULTS_UPSERTED", "NO_RESULT_SOURCE_FK"} <= {
        l["code"] for l in case["limitations"]
    }
    # Both filings are linked from their own audit rows only.
    audit_links = {(l["from"], l["to"]) for l in links_of(case, "AUDIT_DETAIL_TEXT")}
    expected = set()
    for event_id, detail in conn.execute(
        "SELECT event_id, detail FROM audit_event WHERE action = 'INBOUND_RESULT_FILED' "
        "AND order_id = ?",
        (order_id,),
    ):
        expected.add((f"audit_event:{event_id}",
                      f"interface_message:{int(detail.split()[0].split('=')[1])}"))
    assert audit_links == expected and len(expected) == 2


def test_probe_repeated_in_one_message_counts_each_occurrence(conn):
    """One message with two OBX segments for the same probe files normally."""
    order_id = make_order(conn, "DOBX")
    filed = inbound_hl7.ingest_message(
        conn,
        hl7("ACC-TR-DOBX", "TR-DOBX-1",
            obx=(("CBFB", "200^1^2F^NORMAL"), ("CBFB", "200^2^2F^NORMAL"))),
    )
    assert filed.filed
    results = _result_ids(conn, order_id)
    assert list(results) == ["CBFB"]
    rid = results["CBFB"]
    # Independent SQL: two RESULT_ENTERED events for the one upserted row, and
    # one INBOUND_RESULT_FILED event listing the probe twice.
    assert conn.execute(
        "SELECT COUNT(*) FROM audit_event WHERE entity_type = 'fish_result' "
        "AND entity_id = ? AND action = 'RESULT_ENTERED'",
        (rid,),
    ).fetchone()[0] == 2
    (detail,) = [r[0] for r in conn.execute(
        "SELECT detail FROM audit_event WHERE action = 'INBOUND_RESULT_FILED' AND order_id = ?",
        (order_id,),
    )]
    assert detail == f"message_id={filed.message_id} probes=2 (CBFB, CBFB)"
    assert trace.audit_probe_occurrences(detail) == ["CBFB", "CBFB"]
    assert trace.audit_probe_codes(detail) == ["CBFB"]

    case = trace.build_case(conn, order_id=order_id)
    (note,) = notes(case, "RESULT_SOURCE_NOT_RECORDED")
    assert note["subject"] == f"fish_result:{rid}"
    text = note["detail"]
    assert "RESULT_ENTERED events for this result: 2" in text
    assert "inbound filings naming this probe: 2" in text
    assert "without a matching inbound filing: no" in text
    # The single candidate message is listed once.
    assert text.count(f"interface_message:{filed.message_id}") == 1
    assert "names CBFB: interface_message:" in text


def test_manual_entry_without_inbound_filing_is_reported(conn):
    order_id = make_order(conn, "MAN")
    workflow.enter_fish_result(conn, order_id, "PML_RARA", 200, 2, "2R2G", "NORMAL", "tech01")
    filed = inbound_hl7.ingest_message(conn, hl7("ACC-TR-MAN", "TR-MAN-1"))
    assert filed.filed
    # Manual re-entry of the probe the message filed.
    workflow.enter_fish_result(conn, order_id, "CBFB", 200, 3, "2F", "NORMAL", "tech02")
    results = _result_ids(conn, order_id)
    case = trace.build_case(conn, order_id=order_id)
    by_subject = {n["subject"]: n["detail"] for n in notes(case, "RESULT_SOURCE_NOT_RECORDED")}

    manual = by_subject[f"fish_result:{results['PML_RARA']}"]
    assert "names PML_RARA: none" in manual
    assert "RESULT_ENTERED events for this result: 1" in manual
    assert "inbound filings naming this probe: 0" in manual
    assert "without a matching inbound filing: yes (1)" in manual

    mixed = by_subject[f"fish_result:{results['CBFB']}"]
    assert f"interface_message:{filed.message_id}" in mixed
    assert "RESULT_ENTERED events for this result: 2" in mixed
    assert "without a matching inbound filing: yes (1)" in mixed


def test_forged_audit_reference_is_unresolved(conn):
    order_id = make_order(conn, "FORGE")
    filed = inbound_hl7.ingest_message(conn, hl7("ACC-TR-FORGE", "TR-FORGE-1"))
    errored = inbound_hl7.ingest_message(
        conn, hl7("ACC-TR-FORGE", "TR-FORGE-2", obx=(("NOT_A_PROBE", "200^1^x^NORMAL"),))
    )
    assert filed.filed and not errored.filed
    cur = conn.execute(
        "INSERT INTO audit_event (entity_type, entity_id, order_id, action, detail, actor) "
        "VALUES ('lab_order', ?, ?, 'INBOUND_RESULT_FILED', "
        "'message_id=9999 probes=1 (CBFB)', 'forger')",
        (order_id, order_id),
    )
    missing_event = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO audit_event (entity_type, entity_id, order_id, action, detail, actor) "
        "VALUES ('lab_order', ?, ?, 'INBOUND_RESULT_FILED', ?, 'forger')",
        (order_id, order_id, f"message_id={errored.message_id} probes=1 (CBFB)"),
    )
    errored_event = cur.lastrowid
    conn.commit()

    case = trace.build_case(conn, order_id=order_id)
    unresolved = {n["subject"]: n["detail"] for n in notes(case, "AUDIT_REFERENCE_UNRESOLVED")}
    assert set(unresolved) == {f"audit_event:{missing_event}", f"audit_event:{errored_event}"}
    assert "message_id=9999" in unresolved[f"audit_event:{missing_event}"]
    assert "does not exist" in unresolved[f"audit_event:{missing_event}"]
    assert "ERRORED, not FILED" in unresolved[f"audit_event:{errored_event}"]
    targets = {l["to"] for l in links_of(case, "AUDIT_DETAIL_TEXT")}
    assert "interface_message:9999" not in targets
    assert f"interface_message:{errored.message_id}" in targets
    # A forged reference never becomes a result-source candidate.
    cbfb = [
        n["detail"] for n in notes(case, "RESULT_SOURCE_NOT_RECORDED")
        if "probe CBFB" in n["detail"]
    ][0]
    assert "interface_message:9999" not in cbfb
    assert f"interface_message:{errored.message_id}" not in cbfb
    assert f"interface_message:{filed.message_id}" in cbfb


# ---------------------------------------------------------------------------
# Determinism and timeline
# ---------------------------------------------------------------------------

def test_case_is_deterministic_serializable_and_sorted(conn, recovery_case):
    finalize_with_exports(conn, "SORT")
    with pytest.raises(recovery.RequestIdConflictError):
        recovery.retry_queue_item(
            conn, recovery_case["queue_id"], request_id="TR-REQ-2", actor="someone-else"
        )
    first = trace.build_case(conn, order_id=recovery_case["order_id"])
    second = trace.build_case(conn, order_id=recovery_case["order_id"])
    assert first == second
    text = canonical_json(first)
    assert text == canonical_json(second)
    assert json.loads(text) == first
    json.dumps(first)
    assert text.isascii()

    assert set(first) == {
        "anchor", "orders", "messages", "queue_items", "recovery_attempts",
        "audit_events", "links", "timeline", "provenance", "limitations",
    }
    for key, field in (
        ("orders", "order_id"),
        ("messages", "message_id"),
        ("queue_items", "queue_id"),
        ("recovery_attempts", "attempt_id"),
        ("audit_events", "event_id"),
    ):
        values = ids(first[key], field)
        assert values == sorted(values) and len(values) == len(set(values))
    assert first["links"] == sorted(
        first["links"], key=lambda l: (l["from"], l["to"], l["relation"])
    )
    assert first["timeline"] == sorted(
        first["timeline"], key=lambda e: (e["at"], e["rank"], e["table"], e["id"], e["kind"])
    )
    assert first["provenance"] == sorted(
        first["provenance"], key=lambda n: (n["subject"], n["code"], n["detail"])
    )
    codes = [l["code"] for l in first["limitations"]]
    assert codes == sorted(codes)
    assert {"RESULTS_UPSERTED", "NO_RESULT_SOURCE_FK", "RESULT_AUDIT_ACTOR",
            "TIMESTAMP_RESOLUTION"} <= set(codes)
    for m in first["messages"]:
        assert m["roles"] == sorted(m["roles"])
    for o in first["orders"]:
        assert ids(o["specimens"], "specimen_id") == sorted(ids(o["specimens"], "specimen_id"))
    assert {l["basis"] for l in first["links"]} <= set(trace.BASES)
    for e in first["timeline"]:
        assert set(e) == {"at", "rank", "table", "id", "kind", "summary"}
        assert e["summary"].isascii()

    # The conflict audit event is in the case through its queue entity.
    conflict = conn.execute(
        "SELECT event_id FROM audit_event WHERE action = 'REQUEST_ID_CONFLICT'"
    ).fetchone()[0]
    assert conflict in ids(first["audit_events"], "event_id")


def test_timeline_covers_every_record_with_documented_ranks(conn, recovery_case):
    rc = recovery_case
    case = trace.build_case(conn, queue_id=rc["queue_id"])
    events = {(e["table"], e["id"], e["kind"]): e for e in case["timeline"]}
    for m in case["messages"]:
        e = events[("interface_message", m["message_id"], "MESSAGE_STORED")]
        assert e["rank"] == 0 and e["at"] == m["created_at"]
    q = case["queue_items"][0]
    assert events[("interface_error_queue", q["queue_id"], "QUEUED")]["rank"] == 1
    resolved = events[("interface_error_queue", q["queue_id"], "QUEUE_RESOLVED")]
    assert resolved["rank"] == 4 and resolved["at"] == q["resolved_at"]
    for a in case["recovery_attempts"]:
        e = events[("interface_recovery_attempt", a["attempt_id"], "RECOVERY_ATTEMPT")]
        assert e["rank"] == 2 and e["at"] == a["attempted_at"]
    for a in case["audit_events"]:
        e = events[("audit_event", a["event_id"], a["action"])]
        assert e["rank"] == 3 and e["at"] == a["created_at"]
    assert len(case["timeline"]) == (
        len(case["messages"]) + 2 * len(case["queue_items"])
        + len(case["recovery_attempts"]) + len(case["audit_events"])
    )


# ---------------------------------------------------------------------------
# Read-only operation on a file database
# ---------------------------------------------------------------------------

def test_read_only_connection_leaves_file_unchanged(tmp_path):
    path = store.init_database(tmp_path / "lab.db")
    rw = store.open_read_write(path)
    try:
        original = inbound_hl7.ingest_message(rw, hl7("ACC-TR-RO", "TR-RO-1"))
        order_id = make_order(rw, "RO")
        retried = recovery.retry_queue_item(
            rw, original.queue_id, request_id="TR-RO-REQ", actor="analyst01"
        )
        assert retried.outcome == "SUCCEEDED"
        finalize_with_exports(rw, "RO-FIN")
    finally:
        rw.close()
    before = path.read_bytes()

    ro = store.open_read_only(path)
    try:
        by_queue = trace.build_case(ro, queue_id=original.queue_id)
        by_order = trace.build_case(ro, order_id=order_id)
        findings = reconcile_database(ro)
    finally:
        ro.close()
    assert without_anchor(by_queue) == without_anchor(by_order)
    assert not [f for f in findings if f["severity"] == "ERROR"]
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["lab.db"]


# ---------------------------------------------------------------------------
# queries/analyst_queue_worklist.sql
# ---------------------------------------------------------------------------

WORKLIST_COLUMNS = [
    "queue_id", "created_at", "status", "failure_code", "failure_category",
    "recovery_policy", "reason", "message_id", "control_id", "attempt_count",
    "last_outcome", "resolved_at", "terminal_at",
]


def _worklist_db(conn):
    """OPEN (no attempts), OPEN (REJECTED attempt), RESOLVED, and TERMINAL items."""
    make_order(conn, "WL")
    open_plain =inbound_hl7.ingest_message(conn, hl7("ACC-TR-WL", "WL-NOOBX", obx=()))
    open_rejected = inbound_hl7.ingest_message(
        conn, hl7("ACC-TR-WL", "WL-PROBE", obx=(("NOT_A_PROBE", "200^1^x^NORMAL"),))
    )
    rejected = recovery.retry_queue_item(
        conn, open_rejected.queue_id, request_id="WL-REQ-1", actor="analyst01"
    )
    assert rejected.outcome == "REJECTED"
    not_found = inbound_hl7.ingest_message(conn, hl7("ACC-TR-WLNF", "WL-NF"))
    make_order(conn, "WLNF")
    failed = recovery.redrive_queue_item(
        conn, not_found.queue_id,
        hl7("ACC-TR-WLNF", "WL-NF-BAD", obx=(("CBFB", "x^1^2F^NORMAL"),)),
        request_id="WL-REQ-2", actor="analyst01",
    )
    assert failed.outcome == "FAILED"
    retried = recovery.retry_queue_item(
        conn, not_found.queue_id, request_id="WL-REQ-3", actor="analyst01"
    )
    assert retried.outcome == "SUCCEEDED"
    fin = make_order(conn, "WLFIN")
    enter_all_results(conn, fin)
    workflow.finalize_order(conn, fin, "pathologist01")
    terminal = inbound_hl7.ingest_message(conn, hl7("ACC-TR-WLFIN", "WL-FIN"))
    return {
        "open_plain": open_plain.queue_id,
        "open_rejected": open_rejected.queue_id,
        "resolved": not_found.queue_id,
        "terminal": terminal.queue_id,
    }


def test_worklist_rows_match_independent_sql(conn):
    _worklist_db(conn)
    rows = run_query(conn, "analyst_queue_worklist", {"status": None})
    assert rows and list(rows[0].keys()) == WORKLIST_COLUMNS
    expected_order = [
        r[0] for r in conn.execute(
            "SELECT queue_id FROM interface_error_queue ORDER BY created_at, queue_id"
        )
    ]
    assert [r["queue_id"] for r in rows] == expected_order
    for r in rows:
        q = conn.execute(
            "SELECT * FROM interface_error_queue WHERE queue_id = ?", (r["queue_id"],)
        ).fetchone()
        for col in ("created_at", "status", "failure_code", "failure_category",
                    "recovery_policy", "reason", "message_id", "resolved_at", "terminal_at"):
            assert r[col] == q[col]
        assert r["control_id"] == conn.execute(
            "SELECT control_id FROM interface_message WHERE message_id = ?", (q["message_id"],)
        ).fetchone()[0]
        assert r["attempt_count"] == conn.execute(
            "SELECT COUNT(*) FROM interface_recovery_attempt WHERE queue_id = ?",
            (r["queue_id"],),
        ).fetchone()[0]
        last = conn.execute(
            "SELECT outcome FROM interface_recovery_attempt WHERE queue_id = ? "
            "ORDER BY attempt_id DESC LIMIT 1",
            (r["queue_id"],),
        ).fetchone()
        assert r["last_outcome"] == (last[0] if last else None)


def test_worklist_status_filter_and_summary_values(conn):
    q = _worklist_db(conn)
    by_status = {
        status: {r["queue_id"]: r for r in run_query(
            conn, "analyst_queue_worklist", {"status": status}
        )}
        for status in ("OPEN", "RESOLVED", "TERMINAL")
    }
    assert set(by_status["OPEN"]) == {q["open_plain"], q["open_rejected"]}
    assert set(by_status["RESOLVED"]) == {q["resolved"]}
    assert set(by_status["TERMINAL"]) == {q["terminal"]}
    plain = by_status["OPEN"][q["open_plain"]]
    assert (plain["attempt_count"], plain["last_outcome"], plain["control_id"]) == (
        0, None, "WL-NOOBX"
    )
    rejected = by_status["OPEN"][q["open_rejected"]]
    assert (rejected["attempt_count"], rejected["last_outcome"]) == (1, "REJECTED")
    resolved = by_status["RESOLVED"][q["resolved"]]
    assert (resolved["attempt_count"], resolved["last_outcome"]) == (2, "SUCCEEDED")
    assert resolved["resolved_at"] is not None and resolved["terminal_at"] is None
    terminal = by_status["TERMINAL"][q["terminal"]]
    assert (terminal["failure_code"], terminal["recovery_policy"]) == (
        "ORDER_FINALIZED", "TERMINAL"
    )
    assert (terminal["attempt_count"], terminal["last_outcome"]) == (0, None)
    assert run_query(conn, "analyst_queue_worklist", {"status": "BOGUS"}) == []


def test_worklist_orders_by_created_at_then_queue_id(conn):
    q = _worklist_db(conn)
    conn.execute(
        "UPDATE interface_error_queue SET created_at = '2001-01-01 00:00:00' WHERE queue_id = ?",
        (q["terminal"],),
    )
    conn.execute(
        "UPDATE interface_error_queue SET created_at = '2099-01-01 00:00:00' WHERE queue_id = ?",
        (q["open_plain"],),
    )
    rows = run_query(conn, "analyst_queue_worklist", {"status": None})
    order = [r["queue_id"] for r in rows]
    assert order[0] == q["terminal"] and order[-1] == q["open_plain"]
    assert order[1:-1] == sorted(order[1:-1])
