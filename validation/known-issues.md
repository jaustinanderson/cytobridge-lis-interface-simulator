# Known issues and limitations

Honest, documented boundaries of the CytoBridge LIS Interface Simulator. Listing
these is itself part of the analyst-first intent: a good validation package says
plainly what a system does **not** do.

> **Synthetic learning project — no PHI.** Nothing here is a clinical or
> production system. This is **Beaker-adjacent learning, not Epic build
> experience.**

## Known issues (tracked)

| ID | Area | Description | Impact | Disposition |
|---|---|---|---|---|
| **KI-01** | Test coverage | Five of six `queries/*.sql` views (`pending_review`, `stat_pending`, `turnaround_time`, `validation_error_rate`, `audit_lookup`) lack a dedicated result-asserting `pytest`; they are exercised for runnability via `demo_run.py`. Only `interface_error_queue.sql` is directly asserted. | Low — queries run, but regressions in their output shape would not be caught automatically. | Accepted for now; candidate for a future `test_queries.py`. Traceability R-019 marked 🟡. |
| **KI-02** | Inbound parser | The inbound HL7 parser accepts a small, fixed ORU dialect and packs the per-probe result into `OBX-5` as `scored^abnormal^signal^interp`. It is **not** a general HL7 v2 parser (no repetitions, escaping edge cases, Z-segments, or field-length rules). | Low — by design for an educational simulator. | Documented in `docs/interface-mapping.md`; won't fix (out of scope). |
| **KI-03** | Error-queue lifecycle | Error-queue entries are created `OPEN`; the schema supports `RESOLVED`/`resolved_at`, but there is no code path or workflow function to resolve/re-drive a queued message (an analyst would run the `UPDATE` by hand, as shown in `docs/interface-troubleshooting.md`). | Low | Deferred — a "resolve + resend" workflow is a candidate future session. |

## Design limitations (by scope, not defects)

| Area | Limitation |
|---|---|
| **Standards conformance** | HL7/FHIR outputs and the inbound parser are *educational, style-only*. They use synthetic local code systems (`urn:cytobridge:*`), set HL7 `MSH-11 = T` (training), and are **not** conformance-validated. No MLLP framing, ACK/NACK, or FHIR profile validation. Must not be pointed at a production interface. |
| **Panel breadth** | Exactly one panel is modeled: **AML/MDS FISH** with 9 synthetic required probes and illustrative cutoffs. Cutoffs are not clinical reference values. |
| **Persistence** | The demo and tests use an **in-memory** SQLite database seeded from `schema.sql`. There is no migration tooling, connection pooling, or multi-user concurrency model. |
| **Security / identity** | No authentication, authorization, role model, or PHI-handling controls. `actor` fields are free-text strings (`tech01`, `analyst01`, `interface`), not authenticated identities. |
| **ISCN** | `reports.py` defines an ISCN parser **seam** (`parse_iscn`) but does not parse ISCN nomenclature; it returns the raw string with `is_valid=False`. |
| **Clinical logic** | Interpretation consistency is a simple cutoff comparison. There is no clonal-evolution, mosaicism, or multi-probe-pattern reasoning. |
| **Interface transport** | Messages are passed as in-process strings/files. There is no listener, queue broker, retry/backoff, or network transport. |
| **UI** | Headless by design — no Streamlit/web UI. Analyst interaction is via Python calls and raw SQL. |

## Explicitly out of scope (won't add in this project)

Per the project's guardrails, the following are intentionally **not** added:
a UI, Docker, CI pipelines, an ORM, additional panels, new database platforms,
new/real HL7/FHIR dependencies, and any real or Epic-derived content.

## Non-goals restated

This project does **not** claim: certified HL7/FHIR compliance, clinical
validity, production readiness, Epic/Beaker build experience, or handling of any
real patient data. See [`risk-assessment.md`](risk-assessment.md) for how these
boundaries are managed and [`docs/portfolio-review.md`](../docs/portfolio-review.md)
for what the project *does* demonstrate.
