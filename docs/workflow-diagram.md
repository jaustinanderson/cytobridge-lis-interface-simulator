# Workflow diagram

The CytoBridge AML/MDS FISH lifecycle and its interface paths, in Mermaid. Four
views: the **order lifecycle -> finalize -> outbound**, the **inbound ingestion**
path, the **controlled error-queue recovery** flow (v1.1), and the cross-cutting
**audit trail**. GitHub renders the ```mermaid``` blocks below.

> **Synthetic learning project - no PHI.** Educational, HL7/FHIR-*style* only.
> Recovery is a **synchronous, headless Python service** - there is no UI,
> background worker, asynchronous queue, or production interface engine.

## 1. Order lifecycle -> validation -> finalization -> outbound

```mermaid
flowchart TD
    A["Create synthetic patient + order<br/>(status: ORDERED)"] --> B["Receive specimen<br/>(status: IN_PROCESS)"]
    B --> C["Accession specimen<br/>(status: ACCESSIONED)"]
    C --> D["Enter per-probe FISH results<br/>(status: PENDING_REVIEW)"]
    D --> E{"Run validation<br/>(cutoff-aware)"}
    E -->|"ERROR findings"| F["Finalize BLOCKED<br/>findings to validation_error"]
    F -->|"analyst corrects / completes"| D
    E -->|"no blocking errors"| G["Finalize order<br/>(status: FINALIZED) + report"]
    G --> H{"Outbound export<br/>(FINALIZED only)"}
    H --> I["HL7 ORU^R01-style message"]
    H --> J["FHIR DiagnosticReport Bundle"]
    I --> K[("interface_message<br/>direction = OUTBOUND")]
    J --> K

    E -.writes.-> AUD[("audit_event")]
    G -.writes.-> AUD
```

Non-finalized or data-incomplete orders cannot be exported - `collect_report_data`
raises `OutboundError` (requirements R-008, R-009).

## 2. Inbound ORU ingestion -> filing or error queue

```mermaid
flowchart TD
    IN["Inbound ORU-style message<br/>(MSH/PID/OBR/SPM/OBX)"] --> STORE[("interface_message<br/>direction = INBOUND")]
    STORE --> P{"Parse + match OBR-3 accession<br/>to a non-finalized order"}
    P -->|"missing / unmatched accession,<br/>order finalized,<br/>missing segment, no OBX"| Q["Route whole message<br/>to error queue"]
    P -->|"matched"| V{"Validate every OBX<br/>(all-or-nothing)"}
    V -->|"unknown probe, bad number,<br/>abnormal > scored,<br/>incompatible specimen"| Q
    V -->|"all OBX valid"| FILE["File per-probe results<br/>to the open order"]
    FILE --> MSGF["interface_message -> FILED"]
    FILE -.writes.-> AUD2[("audit_event<br/>INBOUND_RESULT_FILED")]
    Q --> EQ[("interface_error_queue<br/>status = OPEN + clear reason")]
    Q --> MSGE["interface_message -> ERRORED"]
```

Because filing is **all-or-nothing**, a message with any invalid OBX files
*nothing* - the order is never left half-updated (requirements R-010-R-018).

## 3. Controlled error-queue recovery (v1.1)

A compact view of one recovery request against an `OPEN` queue item, through the
headless `recovery` service. `request_id` resolution happens first; then the
stored classification and eligibility decide retry vs corrected re-drive vs
rejection. The **original failed message is immutable** and only a new message
can reach `FILED`.

```mermaid
flowchart TD
    OPEN["OPEN queue item<br/>(failure_code / category / policy)"] --> REQ{"request_id seen before?"}
    REQ -->|"same queue/action/sha/actor"| REPLAY["Return existing attempt<br/>(no new records)"]
    REQ -->|"reused, any field differs"| CONFLICT["REQUEST_ID_CONFLICT<br/>(audit event only; raise)"]
    REQ -->|"new request_id"| ELIG{"Action eligible?<br/>(status OPEN + policy)"}

    ELIG -->|"TERMINAL item, closed item,<br/>or RETRY on non-ORDER_NOT_FOUND"| REJ["REJECTED attempt<br/>(no processing message)"]
    ELIG -->|"RETRY_ORIGINAL<br/>(OPEN ORDER_NOT_FOUND)"| RETRY["Reuse immutable original payload<br/>from linked interface_message"]
    ELIG -->|"REDRIVE_CORRECTED<br/>(REDRIVE_ONLY / RETRY_OR_REDRIVE)"| REDRIVE["Process caller's corrected payload"]

    RETRY --> PROC{"Process on a NEW message<br/>(all-or-nothing filing)"}
    REDRIVE --> PROC
    PROC -->|"files OK"| SUCC["SUCCEEDED attempt<br/>new message FILED"]
    PROC -->|"handled invalid payload"| FAIL["FAILED attempt<br/>new message ERRORED"]
    PROC -->|"target order now FINALIZED/CANCELLED"| DYN["REJECTED attempt<br/>(nothing filed)"]

    SUCC --> RESOLVED["queue OPEN -> RESOLVED<br/>(resolved_at set)"]
    FAIL --> STILLOPEN["queue stays OPEN<br/>(retry later, new request_id)"]
    DYN --> TERMINAL["queue OPEN -> TERMINAL<br/>(terminal_at set)"]
    REJ --> UNCHANGED["queue unchanged"]

    ORIG[("original interface_message<br/>ERRORED - immutable")] -.lineage.-> OPEN
    SUCC -.records.-> RA[("interface_recovery_attempt<br/>+ INBOUND_RESULT_FILED audit")]
    FAIL -.records.-> RA
    DYN -.records.-> RA
    REJ -.records.-> RA
    CONFLICT -.records.-> AUD3[("audit_event<br/>REQUEST_ID_CONFLICT")]
```

Key invariants (requirements R-022-R-041): the original message and the queue
`raw_payload` are never modified; only a new recovery message may reach `FILED`;
a queue item has **at most one** `SUCCEEDED` attempt; a handled failure rolls
back all filing side effects (queue stays `OPEN`) while preserving the `ERRORED`
message and `FAILED` attempt; and recovery never creates a second error-queue
item.

## 4. Audit trail (cross-cutting)

```mermaid
flowchart LR
    subgraph SG1["Lifecycle events"]
        O["ORDERED"] --> R["RECEIVED"] --> AC["ACCESSIONED"] --> RE["RESULT_ENTERED"] --> VR["VALIDATION_RUN"] --> RF["REPORT_FINALIZED"] --> FI["FINALIZED"]
    end
    subgraph SG2["Inbound event"]
        IFE["INBOUND_RESULT_FILED<br/>(names source message_id)"]
    end
    O -.-> AE[("audit_event<br/>entity, action, actor, detail")]
    R -.-> AE
    AC -.-> AE
    RE -.-> AE
    VR -.-> AE
    RF -.-> AE
    FI -.-> AE
    IFE -.-> AE
```

Every important state change writes an `audit_event` (requirement R-007); inbound
filings add an `INBOUND_RESULT_FILED` event linking the results back to the
interface message that produced them (R-016). A successful controlled recovery
files through the same seam, so it emits the same `INBOUND_RESULT_FILED` event
(now on a new recovery message), and a mismatched `request_id` reuse adds a
`REQUEST_ID_CONFLICT` audit event (R-040, R-041). Review a single order's trail
with `queries/audit_lookup.sql`.
