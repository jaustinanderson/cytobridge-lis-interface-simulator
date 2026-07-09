"""Outbound HL7 ORU^R01-style message generation.

Turns a finalized AML/MDS FISH order into a pipe-delimited, ORU^R01-*style*
result message with these segments:

    MSH   message header (sending/receiving app, timestamp, control id)
    PID   patient identification (synthetic MRN + name)
    OBR   observation request (accession = filler order #, panel = service id)
    SPM   specimen (type, collection/received timestamps)
    OBX   observations: a report-summary narrative, an overall impression,
          then one per-probe result observation

Educational disclaimer
----------------------
This is an **HL7-style educational simulator, not a certified HL7 v2
implementation.** Segment/field placement follows the spirit of HL7 v2.5.1 so
the mapping is legible to an analyst, but the output is not conformance-tested,
uses synthetic local codes, and should not be sent to a production interface.
The processing-ID field (MSH-11) is set to ``T`` (training) to underline that.

All data is synthetic. No PHI.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from . import OutboundReportData, collect_report_data, store_message

# HL7 v2 separators / encoding characters. MSH-1 is the field separator itself
# and MSH-2 declares the component, repetition, escape, and subcomponent chars.
FIELD_SEP = "|"
ENCODING_CHARS = r"^~\&"
COMPONENT_SEP = "^"
SEGMENT_SEP = "\r"  # HL7's canonical segment terminator is carriage return.

MESSAGE_TYPE = "ORU^R01"
HL7_VERSION = "2.5.1"
PROCESSING_ID = "T"  # T = Training (this is a simulator, not production).

SENDING_APP = "CYTOBRIDGE"
SENDING_FACILITY = "CYTO_LAB"
RECEIVING_APP = "LIS"
RECEIVING_FACILITY = "HOSP"


@dataclass(frozen=True)
class OruMessage:
    """A rendered ORU message plus its MSH-10 control id (for storage)."""

    control_id: str
    payload: str


def _esc(value: object) -> str:
    """Minimal HL7 escaping of the reserved separator characters.

    Synthetic data does not normally contain these, but escaping them keeps the
    generator honest. The backslash escape must run first.
    """
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\\", "\\E\\")
    s = s.replace("|", "\\F\\")
    s = s.replace("^", "\\S\\")
    s = s.replace("~", "\\R\\")
    s = s.replace("&", "\\T\\")
    return s


def _cx(*parts: object) -> str:
    """Build a component-delimited (``^``) field from escaped parts."""
    return COMPONENT_SEP.join(_esc(p) for p in parts)


def _ts(value: object) -> str:
    """HL7 timestamp: strip separators from an ISO date/datetime.

    ``2026-07-08 14:30:00`` -> ``20260708143000``; ``1970-05-14`` -> ``19700514``.
    Returns ``""`` for empty input.
    """
    if not value:
        return ""
    return "".join(ch for ch in str(value) if ch.isdigit())


def _segment(seg_id: str, fields_by_pos: dict[int, str]) -> str:
    """Assemble a segment from 1-based field positions, padding the gaps.

    ``fields_by_pos`` maps HL7 field number (OBR-4 -> 4) to an already-built
    value; unspecified positions are emitted empty.
    """
    size = max(fields_by_pos) if fields_by_pos else 0
    parts = [seg_id] + [""] * size
    for pos, val in fields_by_pos.items():
        parts[pos] = val
    return FIELD_SEP.join(parts)


def _control_id(data: OutboundReportData, ts: str) -> str:
    """Synthetic, deterministic MSH-10 message control id."""
    acc = "".join(ch for ch in data.accession_number if ch.isalnum())
    return f"CBORU{acc}{ts}"


def _msh(control_id: str, ts: str) -> str:
    # MSH is special: MSH-1 is the field separator, MSH-2 the encoding chars,
    # so we lay the fields out by hand rather than through _segment.
    return FIELD_SEP.join([
        "MSH",
        ENCODING_CHARS,
        SENDING_APP,
        SENDING_FACILITY,
        RECEIVING_APP,
        RECEIVING_FACILITY,
        ts,                       # MSH-7  date/time of message
        "",                       # MSH-8  security
        MESSAGE_TYPE,             # MSH-9  ORU^R01
        control_id,               # MSH-10 message control id
        PROCESSING_ID,            # MSH-11 T = training
        HL7_VERSION,              # MSH-12 2.5.1
    ])


def _pid(data: OutboundReportData) -> str:
    return _segment("PID", {
        1: "1",
        3: _cx(data.mrn, "", "", SENDING_FACILITY, "MR"),  # MRN^^^AA^MR
        5: _cx(data.last_name, data.first_name),           # family^given
        7: _ts(data.date_of_birth),                        # DOB (YYYYMMDD)
        8: _esc(data.sex),
    })


def _obr(data: OutboundReportData) -> str:
    return _segment("OBR", {
        1: "1",
        3: _esc(data.accession_number),                    # filler order number
        4: _cx(data.panel_code, data.panel_name, "L"),     # universal service id
        7: _ts(data.collected_at),                         # observation date/time
        16: _cx("", data.ordering_provider),               # ordering provider
        22: _ts(data.finalized_at),                        # status change date/time
        25: "F",                                           # result status: Final
    })


def _spm(data: OutboundReportData) -> str:
    return _segment("SPM", {
        1: "1",
        2: _esc(data.external_specimen_id or ""),          # specimen id
        4: _cx(data.specimen_code, data.specimen_display, "L"),
        17: _ts(data.collected_at),                        # collection date/time
        18: _ts(data.received_at),                         # received date/time
    })


def _obx_segments(data: OutboundReportData) -> list[str]:
    segments: list[str] = []
    seq = 1

    # OBX-1: the final report narrative. FT (formatted text); newlines become
    # the HL7 line-break escape \.br\ so the multi-line summary survives.
    summary = "\\.br\\".join(_esc(line) for line in data.summary_text.splitlines())
    segments.append(_segment("OBX", {
        1: str(seq),
        2: "FT",
        3: _cx("REPORT", "Final FISH Report Summary", "L"),
        5: summary,
        11: "F",
    }))
    seq += 1

    # OBX-2: overall coded impression.
    if data.is_abnormal:
        impression = _cx("A", "Abnormal FISH result", "L")
        flag = "A"
    else:
        impression = _cx("N", "No abnormality detected by this panel", "L")
        flag = "N"
    segments.append(_segment("OBX", {
        1: str(seq),
        2: "CE",
        3: _cx("IMPRESSION", "Overall Impression", "L"),
        5: impression,
        8: flag,
        11: "F",
    }))
    seq += 1

    # OBX-3..: one observation per probe. The value packs the structured
    # detail (counts, percent, interpretation, signal pattern); OBX-7 carries
    # the abnormal cutoff as a reference range and OBX-8 the abnormal flag.
    for probe in data.results:
        value = (
            f"{probe.cells_abnormal}/{probe.cells_scored} nuclei "
            f"({probe.percent_abnormal}%) {probe.interpretation}; "
            f"signal {probe.signal_pattern}"
        )
        segments.append(_segment("OBX", {
            1: str(seq),
            2: "ST",
            3: _cx(probe.probe_code, f"{probe.probe_name} ({probe.target})", "L"),
            5: _esc(value),
            7: _esc(f"<{probe.abnormal_cutoff_percent}% abnormal"),
            8: probe.flag,
            11: "F",
        }))
        seq += 1

    return segments


def _assemble(data: OutboundReportData, generated_at: str | None) -> OruMessage:
    ts = _ts(generated_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    control_id = _control_id(data, ts)
    segments = [
        _msh(control_id, ts),
        _pid(data),
        _obr(data),
        _spm(data),
        *_obx_segments(data),
    ]
    return OruMessage(control_id=control_id, payload=SEGMENT_SEP.join(segments))


def generate_oru(
    conn: sqlite3.Connection,
    order_id: int,
    generated_at: str | None = None,
) -> str:
    """Return the ORU^R01-style HL7 text for a finalized order.

    ``generated_at`` (``YYYY-MM-DD HH:MM:SS``) fixes the message timestamp and
    control id for deterministic samples/tests; it defaults to now.

    Raises ``OutboundError`` if the order is not finalized or data is missing.
    """
    data = collect_report_data(conn, order_id)
    return _assemble(data, generated_at).payload


def store_oru(
    conn: sqlite3.Connection,
    order_id: int,
    generated_at: str | None = None,
) -> int:
    """Generate the ORU message and persist it as an OUTBOUND HL7 row.

    Returns the new ``interface_message.message_id``.
    """
    data = collect_report_data(conn, order_id)
    message = _assemble(data, generated_at)
    return store_message(
        conn,
        direction="OUTBOUND",
        message_type="ORU",
        message_format="HL7",
        order_id=order_id,
        control_id=message.control_id,
        payload=message.payload,
    )
