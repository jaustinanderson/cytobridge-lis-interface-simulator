"""Outbound FHIR R4-style DiagnosticReport generation.

Turns a finalized AML/MDS FISH order into a FHIR R4-*style* ``Bundle`` (type
``collection``) containing:

    Patient            synthetic MRN identifier, name, birthDate, gender
    Specimen           accession identifier, specimen type, received time
    Observation (n)    one per probe: percent-abnormal value, interpretation,
                       reference range (cutoff), and a note with raw counts +
                       signal pattern
    DiagnosticReport   status, panel code, accession identifier, subject +
                       specimen references, ``result`` links to the per-probe
                       Observations, and the report summary as ``conclusion``

Educational disclaimer
----------------------
This is a **FHIR-style educational simulator, not a formally validated FHIR
implementation.** The resource shapes follow FHIR R4 closely enough to read the
mapping, but they are not profile-validated, use synthetic local code systems
(``urn:cytobridge:*``), and should not be posted to a real FHIR server as-is.

All data is synthetic. No PHI.
"""

from __future__ import annotations

import json
import re
import sqlite3

from . import OutboundReportData, collect_report_data, store_message

# Synthetic, clearly non-clinical code systems for this simulator.
SYS_MRN = "urn:cytobridge:mrn"
SYS_ACCESSION = "urn:cytobridge:accession"
SYS_PANEL = "urn:cytobridge:panel"
SYS_PROBE = "urn:cytobridge:fish-probe"
SYS_SPECIMEN = "urn:cytobridge:specimen-type"
SYS_INTERPRETATION = (
    "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
)
SYS_OBS_CATEGORY = (
    "http://terminology.hl7.org/CodeSystem/observation-category"
)
UCUM = "http://unitsofmeasure.org"

_SEX_TO_GENDER = {"M": "male", "F": "female", "U": "unknown"}
_INTERP_DISPLAY = {"N": "Normal", "A": "Abnormal", "I": "Indeterminate"}


def _slug(value: str) -> str:
    """Make a FHIR-id-safe token (letters, digits, hyphen) from a value."""
    return re.sub(r"[^A-Za-z0-9-]", "-", str(value)).strip("-")


def _dt(value: object) -> str | None:
    """Coerce a stored ``YYYY-MM-DD HH:MM:SS`` value to a FHIR dateTime.

    Adds the ``T`` separator and a ``Z`` (UTC) offset when a time is present, as
    FHIR requires a timezone whenever a time component is given. A bare date is
    returned unchanged. Returns ``None`` for empty input.
    """
    if not value:
        return None
    s = str(value).strip().replace(" ", "T")
    if "T" in s and not (s.endswith("Z") or "+" in s):
        s += "Z"
    return s


def _patient_resource(data: OutboundReportData, patient_id: str) -> dict:
    return {
        "resourceType": "Patient",
        "id": patient_id,
        "identifier": [{
            "type": {"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/v2-0203",
                "code": "MR",
                "display": "Medical record number",
            }]},
            "system": SYS_MRN,
            "value": data.mrn,
        }],
        "name": [{
            "use": "official",
            "family": data.last_name,
            "given": [data.first_name],
        }],
        "gender": _SEX_TO_GENDER.get(data.sex, "unknown"),
        "birthDate": data.date_of_birth,
    }


def _specimen_resource(
    data: OutboundReportData, specimen_id: str, patient_ref: str
) -> dict:
    resource: dict = {
        "resourceType": "Specimen",
        "id": specimen_id,
        "accessionIdentifier": {
            "system": SYS_ACCESSION,
            "value": data.accession_number,
        },
        "type": {
            "coding": [{
                "system": SYS_SPECIMEN,
                "code": data.specimen_code,
                "display": data.specimen_display,
            }],
            "text": data.specimen_display,
        },
        "subject": {"reference": patient_ref},
    }
    received = _dt(data.received_at)
    if received:
        resource["receivedTime"] = received
    collected = _dt(data.collected_at)
    if collected:
        resource["collection"] = {"collectedDateTime": collected}
    if data.external_specimen_id:
        resource["identifier"] = [{
            "system": "urn:cytobridge:external-specimen-id",
            "value": data.external_specimen_id,
        }]
    return resource


def _observation_resource(
    data: OutboundReportData,
    probe,
    obs_id: str,
    patient_ref: str,
    specimen_ref: str,
) -> dict:
    return {
        "resourceType": "Observation",
        "id": obs_id,
        "status": "final",
        "category": [{"coding": [{
            "system": SYS_OBS_CATEGORY,
            "code": "laboratory",
            "display": "Laboratory",
        }]}],
        "code": {
            "coding": [{
                "system": SYS_PROBE,
                "code": probe.probe_code,
                "display": probe.probe_name,
            }],
            "text": f"{probe.probe_name} ({probe.target}) [{probe.locus}]",
        },
        "subject": {"reference": patient_ref},
        "specimen": {"reference": specimen_ref},
        "valueQuantity": {
            "value": probe.percent_abnormal,
            "unit": "%",
            "system": UCUM,
            "code": "%",
        },
        "interpretation": [{"coding": [{
            "system": SYS_INTERPRETATION,
            "code": probe.flag,
            "display": _INTERP_DISPLAY.get(probe.flag, "Normal"),
        }], "text": probe.interpretation}],
        "referenceRange": [{
            "high": {"value": probe.abnormal_cutoff_percent, "unit": "%"},
            "text": f"<{probe.abnormal_cutoff_percent}% abnormal nuclei",
        }],
        "note": [{
            "text": (
                f"{probe.cells_abnormal}/{probe.cells_scored} nuclei scored; "
                f"signal pattern {probe.signal_pattern}"
            )
        }],
    }


def _diagnostic_report_resource(
    data: OutboundReportData,
    report_id: str,
    patient_ref: str,
    specimen_ref: str,
    result_refs: list[str],
) -> dict:
    resource: dict = {
        "resourceType": "DiagnosticReport",
        "id": report_id,
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v2-0074",
            "code": "GE",
            "display": "Genetics",
        }]}],
        "code": {
            "coding": [{
                "system": SYS_PANEL,
                "code": data.panel_code,
                "display": data.panel_name,
            }],
            "text": data.panel_name,
        },
        "identifier": [{
            "system": SYS_ACCESSION,
            "value": data.accession_number,
        }],
        "subject": {"reference": patient_ref},
        "specimen": [{"reference": specimen_ref}],
        "performer": [{"display": data.ordering_provider}],
        "result": [{"reference": ref} for ref in result_refs],
        "conclusion": data.summary_text,
    }
    effective = _dt(data.collected_at)
    if effective:
        resource["effectiveDateTime"] = effective
    issued = _dt(data.finalized_at)
    if issued:
        resource["issued"] = issued
    return resource


def build_diagnostic_report(
    conn: sqlite3.Connection,
    order_id: int,
    generated_at: str | None = None,  # accepted for a uniform generator API
) -> dict:
    """Return a FHIR R4-style ``Bundle`` dict for a finalized order.

    Raises ``OutboundError`` if the order is not finalized or data is missing.
    ``generated_at`` is accepted for signature parity with the HL7 generator; the
    resource ids are derived from the (synthetic) accession number instead.
    """
    data = collect_report_data(conn, order_id)

    acc = _slug(data.accession_number)
    patient_id = f"patient-{_slug(data.mrn)}"
    specimen_id = f"specimen-{acc}"
    report_id = f"report-{acc}"
    patient_ref = f"Patient/{patient_id}"
    specimen_ref = f"Specimen/{specimen_id}"

    observations: list[dict] = []
    result_refs: list[str] = []
    for probe in data.results:
        obs_id = f"obs-{acc}-{_slug(probe.probe_code)}"
        observations.append(
            _observation_resource(
                data, probe, obs_id, patient_ref, specimen_ref
            )
        )
        result_refs.append(f"Observation/{obs_id}")

    entries = [
        _patient_resource(data, patient_id),
        _specimen_resource(data, specimen_id, patient_ref),
        *observations,
        _diagnostic_report_resource(
            data, report_id, patient_ref, specimen_ref, result_refs
        ),
    ]

    return {
        "resourceType": "Bundle",
        "id": f"cytobridge-{acc}",
        "type": "collection",
        "entry": [
            {"fullUrl": f"{r['resourceType']}/{r['id']}", "resource": r}
            for r in entries
        ],
    }


def generate_diagnostic_report_json(
    conn: sqlite3.Connection,
    order_id: int,
    generated_at: str | None = None,
    indent: int = 2,
) -> str:
    """Return the FHIR Bundle serialized as a JSON string."""
    bundle = build_diagnostic_report(conn, order_id, generated_at)
    return json.dumps(bundle, indent=indent)


def store_diagnostic_report(
    conn: sqlite3.Connection,
    order_id: int,
    generated_at: str | None = None,
) -> int:
    """Generate the FHIR Bundle and persist it as an OUTBOUND FHIR row.

    Returns the new ``interface_message.message_id``.
    """
    bundle = build_diagnostic_report(conn, order_id, generated_at)
    return store_message(
        conn,
        direction="OUTBOUND",
        message_type="DiagnosticReport",
        message_format="FHIR",
        order_id=order_id,
        control_id=bundle["id"],
        payload=json.dumps(bundle, indent=2),
    )
