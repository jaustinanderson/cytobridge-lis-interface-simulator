# Outbound interface mapping (Session 2)

How CytoBridge turns a **finalized** AML/MDS FISH order into outbound
interface messages, and exactly which stored fields land where.

> **Educational simulator — not certified.** The messages below are *shaped
> like* HL7 v2 ORU^R01 and FHIR R4 `DiagnosticReport` so the mapping is legible
> to an analyst. They are **not** conformance-tested against an HL7/FHIR
> validator, they use synthetic local code systems, and they must not be sent to
> a production interface. All data is **synthetic — no PHI**.

## Scope

- **Direction:** outbound only. Inbound instrument ingestion/routing remains
  deferred to a later session.
- **Precondition:** a message is generated **only for a `FINALIZED` order** that
  has a finalized `report`, a `specimen`, and per-probe `fish_result` rows.
  Any missing piece raises `interfaces.OutboundError` — the generator never
  emits a silently incomplete report.
- **Source of truth:** `interfaces.collect_report_data()` reads the order,
  patient, panel, specimen, report, and results once, and both generators render
  from that single snapshot so the HL7 and FHIR outputs always agree.

Generated messages can be persisted to the existing `interface_message` table
(`direction = 'OUTBOUND'`, `format = 'HL7' | 'FHIR'`) with no schema change.

## Shared source fields

| Concept | Source (table.column) |
|---|---|
| Accession number | `lab_order.accession_number` |
| Synthetic MRN | `patient.mrn` |
| Patient name | `patient.last_name`, `patient.first_name` |
| Date of birth / sex | `patient.date_of_birth`, `patient.sex` |
| Panel (test) code / name | `panel.panel_code`, `panel.panel_name` |
| Specimen type | `specimen.specimen_type` → `SPECIMEN_TYPE` code/display |
| Specimen id / times | `specimen.external_specimen_id`, `collected_at`, `received_at` |
| Ordering provider | `lab_order.ordering_provider` |
| Report status | `report.status` (`FINALIZED` → `final` / `F`) |
| Report summary | `report.summary_text` |
| Per-probe result | `fish_result.*` joined to `probe.*` |

Percent-abnormal is derived as `100 * cells_abnormal / cells_scored` (1 decimal).
The abnormal flag / interpretation code is `A` (ABNORMAL), `N` (NORMAL), or `I`
(INDETERMINATE).

## HL7 ORU^R01-style mapping

Pipe-delimited, segments terminated by carriage return (`\r`). Message type
`ORU^R01`, version `2.5.1`, processing id `T` (**T = training**, since this is a
simulator, not production).

| Segment | Purpose | Key fields (source) |
|---|---|---|
| `MSH` | Message header | MSH-9 `ORU^R01`; MSH-10 synthetic control id; MSH-7 message time; MSH-11 `T` |
| `PID` | Patient | PID-3 MRN (`patient.mrn`) `^^^CYTO_LAB^MR`; PID-5 name; PID-7 DOB; PID-8 sex |
| `OBR` | Observation request | OBR-3 filler = `accession_number`; OBR-4 `panel_code^panel_name^L`; OBR-7 collected; OBR-16 provider; OBR-22 finalized; OBR-25 `F` (final) |
| `SPM` | Specimen | SPM-2 specimen id; SPM-4 `code^display^L`; SPM-17 collected; SPM-18 received |
| `OBX` #1 | Report summary | `FT` value = `report.summary_text` (newlines → `\.br\`) |
| `OBX` #2 | Overall impression | `CE` value `A`/`N`; OBX-8 abnormal flag |
| `OBX` #3..n | Per-probe result | `ST` value = `abn/scored nuclei (pct%) INTERP; signal ...`; OBX-3 `probe_code^probe_name (target)^L`; OBX-7 `<cutoff% abnormal`; OBX-8 flag; OBX-11 `F` |

Timestamps are HL7-style digit strings (`YYYYMMDDHHMMSS`; DOB `YYYYMMDD`). The
reserved separator characters `| ^ ~ \ &` are escaped in field values.

> **Note on HL7 segment separators.** HL7 v2 terminates each segment with a
> **carriage return** (`\r`, `0x0D`), not a line feed — so the
> `aml_mds_oru.hl7` sample contains bare CR characters by design. GitHub may
> flag such a file as containing "hidden or bidirectional Unicode text" because
> a lone CR is an unusual control character; this is expected. The canonical CR
> segment terminator is kept intact rather than rewritten to `\n`. There are no
> bidirectional or invisible Unicode characters in this project.

## FHIR R4-style mapping

A `Bundle` (`type = collection`) whose entries are, in order: `Patient`,
`Specimen`, one `Observation` per probe, then the `DiagnosticReport`.

| Resource | Element | Source |
|---|---|---|
| `Patient` | `identifier` (MR), `name`, `gender`, `birthDate` | `patient.*` (sex → `male`/`female`/`unknown`) |
| `Specimen` | `accessionIdentifier`, `type`, `receivedTime`, `collection` | `lab_order.accession_number`, `specimen.*` |
| `Observation` | `code` (probe), `valueQuantity` (% abnormal), `interpretation`, `referenceRange.high` (cutoff), `note` (counts + signal) | `probe.*`, `fish_result.*` |
| `DiagnosticReport` | `status` `final`, `category` GE, `code` (panel), `identifier` (accession), `subject`, `specimen`, `result[]` → Observations, `performer` (synthetic lab), `conclusion` = summary, `issued` = finalized | `lab_order.*`, `panel.*`, `report.*` |

Local code systems are namespaced `urn:cytobridge:*` to make clear they are
synthetic, not real value sets. FHIR `dateTime` values carry a `Z` (UTC) offset
when a time component is present; `birthDate` is a bare date.

### Ordering provider (HL7 vs FHIR)

The **ordering provider** (`lab_order.ordering_provider`) is represented in the
HL7 ORU-style mapping at **OBR-16**. It is intentionally **not** placed on
`DiagnosticReport.performer`: in FHIR, `performer` names who *produced* the
result, while the ordering provider is the *requester* and belongs on an order
resource (`ServiceRequest.requester`). Session 2 does not model a
`ServiceRequest`/order-resource layer, so `DiagnosticReport.performer` carries
only a clearly synthetic performing-lab display
(`CytoBridge Synthetic Cytogenetics Laboratory`), and **FHIR ordering-provider
modeling is deferred** to that future layer.

## Sample output

- HL7: [`sample_messages/outbound/aml_mds_oru.hl7`](../sample_messages/outbound/aml_mds_oru.hl7)
- FHIR: [`sample_messages/outbound/aml_mds_diagnostic_report.json`](../sample_messages/outbound/aml_mds_diagnostic_report.json)

Both samples are for the same synthetic finalized order (accession
`ACC-2026-0001`, MRN `SYN-1001`), regenerable from the workflow via
`interfaces.outbound_hl7` / `interfaces.outbound_fhir`.
