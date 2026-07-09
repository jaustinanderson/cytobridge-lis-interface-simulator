# Workflow diagram

The CytoBridge AML/MDS FISH lifecycle and its interface paths, in Mermaid. Three
views: the **order lifecycle → finalize → outbound**, the **inbound ingestion**
path, and the cross-cutting **audit trail**. GitHub renders the ```mermaid```
blocks below.

> **Synthetic learning project — no PHI.** Educational, HL7/FHIR-*style* only.

## 1. Order lifecycle → validation → finalization → outbound

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

Non-finalized or data-incomplete orders cannot be exported — `collect_report_data`
raises `OutboundError` (requirements R-008, R-009).

## 2. Inbound ORU ingestion → filing or error queue

```mermaid
flowchart TD
    IN["Inbound ORU-style message<br/>(MSH/PID/OBR/SPM/OBX)"] --> STORE[("interface_message<br/>direction = INBOUND")]
    STORE --> P{"Parse + match OBR-3 accession<br/>to a non-finalized order"}
    P -->|"missing / unmatched accession,<br/>order finalized,<br/>missing segment, no OBX"| Q["Route whole message<br/>to error queue"]
    P -->|"matched"| V{"Validate every OBX<br/>(all-or-nothing)"}
    V -->|"unknown probe, bad number,<br/>abnormal > scored,<br/>incompatible specimen"| Q
    V -->|"all OBX valid"| FILE["File per-probe results<br/>to the open order"]
    FILE --> MSGF["interface_message → FILED"]
    FILE -.writes.-> AUD2[("audit_event<br/>INBOUND_RESULT_FILED")]
    Q --> EQ[("interface_error_queue<br/>status = OPEN + clear reason")]
    Q --> MSGE["interface_message → ERRORED"]
```

Because filing is **all-or-nothing**, a message with any invalid OBX files
*nothing* — the order is never left half-updated (requirements R-010–R-018).

## 3. Audit trail (cross-cutting)

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
interface message that produced them (R-016). Review a single order's trail with
`queries/audit_lookup.sql`.
