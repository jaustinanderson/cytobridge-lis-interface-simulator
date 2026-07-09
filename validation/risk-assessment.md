# Risk assessment

A focused risk assessment for the CytoBridge LIS Interface Simulator, framed
around the risks that matter for a **synthetic LIS / interface workflow**: data
integrity, interface misbehavior, and — importantly for a portfolio artifact —
the risk of *misrepresentation* (implying this is more than an educational
simulator).

> **Synthetic learning project — no PHI.** This is a demonstration of risk
> *thinking*, not a formal FMEA or a safety case for a clinical system. Scoring
> is qualitative. This is **Beaker-adjacent learning, not Epic build
> experience.**

## Method

Each risk is rated **Likelihood** (Low/Med/High) × **Impact** (Low/Med/High) →
**Severity**, in the project's context (a headless, synthetic simulator run by
its author and shown to reviewers). "Impact" is impact *within that context* —
e.g. a misleading claim harms credibility, not patients, because no real patient
data is ever involved.

## Risk register

### A. Data-integrity risks (workflow / validation)

| ID | Risk | Likelihood | Impact | Severity | Mitigation (in place) | Residual |
|---|---|---|---|---|---|---|
| RA-01 | Abnormal cells exceed scored cells, corrupting percent-abnormal | Low | Med | **Low** | Schema `CHECK (cells_abnormal <= cells_scored)` **and** validation `ABN_EXCEEDS_SCORED`; inbound also rejects it (R-014). Tests: `test_schema_rejects_abnormal_exceeding_scored`. | Low |
| RA-02 | A missed abnormal — high percent called `NORMAL` — finalized silently | Med | High | **Med** | Cutoff-aware `INTERP_CONSISTENCY` makes at/above-cutoff-called-NORMAL a **blocking** error (R-005); finalize is blocked (R-006). Tests cover both directions. | Low |
| RA-03 | Report finalized while a required probe is missing | Med | High | **Med** | `MISSING_PROBE` blocks finalize (R-003, R-006); findings persisted to `validation_error`. | Low |
| RA-04 | State change happens with no audit record | Low | Med | **Low** | `record_audit` on every workflow step (R-007) and on inbound filing (R-016). Test: `test_audit_trail_records_state_changes`. | Low |

### B. Interface risks (outbound / inbound)

| ID | Risk | Likelihood | Impact | Severity | Mitigation (in place) | Residual |
|---|---|---|---|---|---|---|
| RA-05 | Incomplete/non-final report exported to an interface | Med | High | **Med** | Outbound generation is **finalized-only**; `collect_report_data` raises `OutboundError` on non-finalized or data-incomplete orders (R-008, R-009). Tests cover the block. | Low |
| RA-06 | Inbound message filed to the wrong order | Low | High | **Med** | Match strictly by unique `accession_number` to a **non-finalized** order; unmatched → error queue (R-012); finalized → rejected (R-015). | Low |
| RA-07 | A malformed/partial inbound message half-updates an order | Med | High | **Med** | **All-or-nothing** filing — every OBX validated before any is filed; any failure routes the whole message to the error queue (R-014, R-017). Test proves the valid OBX in a bad message is not filed. | Low |
| RA-08 | An inbound failure is silently dropped (no trace) | Med | Med | **Med** | Every inbound message stored in `interface_message` regardless of outcome (R-010); failures open an `interface_error_queue` row with a clear reason + timestamp (R-018). Analyst worklist: `interface_error_queue.sql`. | Low |
| RA-09 | Incompatible specimen type accepted (e.g. peripheral blood for a marrow panel) | Low | Med | **Low** | Inbound specimen type checked against the order's panel; incompatible → error queue. | Low |

### C. Representation / portfolio risks

| ID | Risk | Likelihood | Impact | Severity | Mitigation (in place) | Residual |
|---|---|---|---|---|---|---|
| RA-10 | Reader assumes certified HL7/FHIR conformance | Med | Med | **Med** | Explicit "educational / style-only, not certified" disclaimers in README, `docs/interface-mapping.md`, code docstrings; HL7 `MSH-11 = T` (training); synthetic `urn:cytobridge:*` code systems. | Low |
| RA-11 | Reader assumes this is Epic/Beaker build experience | Med | High | **Med** | Prominent **"Not affiliated with Epic; Beaker-adjacent learning, not Epic build experience"** boundary in README and [`docs/portfolio-review.md`](../docs/portfolio-review.md). | Low |
| RA-12 | Reader assumes real patient data / clinical validity | Low | High | **Med** | "Synthetic — no PHI" notice repeated across README, schema, docs; illustrative (non-clinical) cutoffs; single demonstration panel. | Low |
| RA-13 | Synthetic data drifts toward realistic PHI over time | Low | Med | **Low** | Fixed synthetic identifiers (`SYN-*`, `ACC-*`); guardrails in every session prohibit real/Epic content. | Low |

### D. Engineering / process risks

| ID | Risk | Likelihood | Impact | Severity | Mitigation (in place) | Residual |
|---|---|---|---|---|---|---|
| RA-14 | Regression in an analyst query goes unnoticed | Med | Low | **Low** | Queries run in `demo_run.py`; error-queue query asserted by a test. Gap tracked as **KI-01**. | Med (accepted) |
| RA-15 | Behavior changes without documentation keeping up | Med | Med | **Med** | One-session-one-PR change control; docs updated each session; this validation package + traceability matrix make drift visible. | Low |
| RA-16 | Demo not reproducible on a reviewer's machine | Low | Med | **Low** | Stdlib + SQLite only; `pytest` sole dev dependency; in-memory DB seeded from `schema.sql`; deterministic sample messages. | Low |

## Highest-attention risks

The residual-risk profile is low across the board; the ones worth stating out
loud to a reviewer are:

1. **RA-11 / RA-10 / RA-12 (representation).** The single most important control
   in a *portfolio* context is not overclaiming. The Epic/Beaker boundary and the
   synthetic-data + educational-standard disclaimers are deliberately repeated
   and are the reason [`docs/portfolio-review.md`](../docs/portfolio-review.md)
   exists.
2. **RA-07 (all-or-nothing inbound).** The design choice that a partial/invalid
   instrument message never half-updates an order is the key interface-integrity
   control and is directly tested.
3. **RA-14 (KI-01).** The one accepted engineering gap — add `test_queries.py` in
   a future session to close it.

## Risk-acceptance statement

For the project's stated purpose — a synthetic, analyst-first demonstration of
LIS/interface workflow thinking — residual risks are **acceptable**. No risk in
this register involves real patients or PHI, because none is ever present. The
governing control is honest scoping: the project repeatedly and explicitly states
what it is (educational simulator) and what it is not (certified, clinical, or
Epic build work).
