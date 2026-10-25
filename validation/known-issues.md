# Known issues and limitations

Honest, documented boundaries of the CytoBridge LIS Interface Simulator. Listing
these is itself part of the analyst-first intent: a good validation package says
plainly what a system does **not** do.

> **Synthetic learning project - no PHI.** Nothing here is a clinical or
> production system. This is **Beaker-adjacent learning, not Epic build
> experience.**

## Known issues (tracked)

| ID | Area | Description | Impact | Disposition |
|---|---|---|---|---|
| **KI-02** | Inbound parser | The inbound HL7 parser accepts a small, fixed ORU dialect and packs the per-probe result into `OBX-5` as `scored^abnormal^signal^interp`. It is **not** a general HL7 v2 parser (no repetitions, escaping edge cases, Z-segments, or field-length rules). | Low - by design for an educational simulator. | Documented in `docs/interface-mapping.md`; won't fix (out of scope). |
| **KI-04** | Inbound filing | An `OBX` cell count that parses as an integer but exceeds SQLite's 64-bit range (for example `99999999999999999999`) raises `OverflowError` while `ingest_message` files results. Each result write commits on its own, so the probes before it stay filed, the message stays `RECEIVED`, and no error-queue item is created. Found while building the analyst lab; existing inbound behavior, unchanged. | Low - synthetic input only, but it breaks all-or-nothing filing (RA-07) for that input. The analyst lab surfaces it: `reconcile` reports the stuck message as `MSG-002`. | Returned to Austin for a decision. The design record does not dictate a classification for out-of-range counts or atomic ingestion, so this is new hardening semantics. |

## Resolved items

| ID | Resolution |
|---|---|
| **KI-01** | Closed by `tests/test_queries.py`: all five previously uncovered analyst views now have deterministic result assertions; the existing inbound suite covers `interface_error_queue.sql`. R-019 is PASS. |
| **KI-03** | Closed by the v1.1 **controlled error-queue recovery service** (`src/recovery.py`, phases P3-001 through P3-003). An analyst now recovers a failed message headlessly through `retry_queue_item` / `redrive_queue_item` / `get_recovery_history`, which resolve or terminalize the queue item atomically after exactly one successful recovery (or a proven-prohibited action). The controlled service **replaces the former manual `UPDATE ... SET status = 'RESOLVED'`** step; `docs/interface-troubleshooting.md` no longer instructs a manual queue update (raw SQL is now read-only, for investigation). Behavior is proven by `tests/test_recovery_service.py`, `tests/test_recovery_schema.py`, and `tests/test_failure_classification.py`, and traced under R-020 - R-041. |

## Design limitations (by scope, not defects)

| Area | Limitation |
|---|---|
| **Standards conformance** | HL7/FHIR outputs and the inbound parser are *educational, style-only*. They use synthetic local code systems (`urn:cytobridge:*`), set HL7 `MSH-11 = T` (training), and are **not** conformance-validated. No MLLP framing, ACK/NACK, or FHIR profile validation. Must not be pointed at a production interface. |
| **Panel breadth** | Exactly one panel is modeled: **AML/MDS FISH** with 9 synthetic required probes and illustrative cutoffs. Cutoffs are not clinical reference values. |
| **Persistence** | The demo and most tests use an **in-memory** SQLite database seeded from `schema.sql`; targeted recovery tests (`tests/test_recovery_service.py`) additionally verify **file-backed durability** after a success and after a handled failure by reopening an on-disk database, and the analyst lab works on file databases across separate processes. Even so, there is no migration tooling, connection pooling, or multi-user concurrency model. |
| **Security / identity** | No authentication, authorization, role model, or PHI-handling controls. `actor` fields are free-text strings (`tech01`, `analyst01`, `interface`), not authenticated identities. |
| **ISCN** | `reports.py` defines an ISCN parser **seam** (`parse_iscn`) but does not parse ISCN nomenclature; it returns the raw string with `is_valid=False`. |
| **Clinical logic** | Interpretation consistency is a simple cutoff comparison. There is no clonal-evolution, mosaicism, or multi-probe-pattern reasoning. |
| **Interface transport** | Messages are passed as in-process strings/files. There is no listener, queue broker, retry/backoff, or network transport. |
| **Recovery workflow** | The v1.1 controlled recovery is **synchronous, headless, and single-process** (`src/recovery.py`). There is no scheduler, background worker, asynchronous `QUEUED`/`PROCESSING` state, automatic retry/backoff, UI, or API - an analyst calls the service functions directly or through the analyst lab CLI, which wraps the same functions. It is not a production interface-engine recovery subsystem. |
| **Analyst lab CLI** | `python -m src.analyst` is a single-user, synchronous, standard-library command-line wrapper over the existing services, working on a local SQLite file. It has no server, GUI, scheduler, network transport, authentication, or concurrency control, and `--actor` is free text. `case-create` calls four workflow services that each commit; its read-only preflight refuses duplicate MRNs and accessions before writing, but an unexpected failure partway through could leave a partial case. It cannot cancel orders because no public cancellation service exists. Its exercises therefore show terminal rejection with a FINALIZED order; the `ORDER_CANCELLED` terminal path stays covered by the existing recovery service tests. |
| **Result history** | `fish_result` keeps only the current value per order and probe; re-entry and re-filing upsert the row. Earlier counts and interpretations are not retained, and no column records which message or manual entry wrote the current value. `RESULT_ENTERED` audit details record only the probe and interpretation, with actor `system`. Trace and evidence views state this and list candidate sources without asserting one. |
| **Case provenance** | An inbound message that fails before filing keeps `order_id` NULL; the lab links it to an order only through its payload accession (OBR-3) and labels that link payload-derived. Links from `INBOUND_RESULT_FILED` audit events to messages are parsed from free-text detail. Timestamps have one-second resolution, so timeline order within a second is presentational and does not establish causation. |
| **Recovery preview** | A preview simulates one snapshot on an in-memory copy. It is not an authorization token or a guarantee of a later result; apply re-invokes the recovery service against the state that exists then. The lab refuses WAL-mode databases (CytoBridge never enables WAL), so a fingerprint of the main database file covers all committed data. |
| **SQLite features** | The analyst lab uses SQLite JSON functions (`json_each`), built into SQLite 3.38 and later and present in the supported Python builds. |
| **UI** | Headless by design - no Streamlit/web UI. Analyst interaction is via Python calls, raw SQL, and the headless analyst lab CLI. |

## Explicitly out of scope (won't add in this project)

Per the project's guardrails, the following are intentionally **not** added:
a UI, Docker packaging or deployment infrastructure, an ORM, additional panels,
new database platforms, new/real HL7/FHIR dependencies, and any real or
Epic-derived content. CI is present solely to run the test suite and demo.

## Non-goals restated

This project does **not** claim: certified HL7/FHIR compliance, clinical
validity, production readiness, Epic/Beaker build experience, or handling of any
real patient data. See [`risk-assessment.md`](risk-assessment.md) for how these
boundaries are managed and [`docs/portfolio-review.md`](../docs/portfolio-review.md)
for what the project *does* demonstrate.
