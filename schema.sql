-- CytoBridge LIS Interface Simulator — schema.sql
-- Synthetic cytogenetics/FISH LIS + interface simulator (v1).
-- SQLite / stdlib sqlite3. Raw hand-written SQL. No ORM.
--
-- This file defines the full v1 data model (order/specimen/result workflow,
-- validation, audit, and the interface message + error-queue tables). The
-- Python code for HL7/FHIR interfaces lands in a later session, but the schema
-- is provisioned now so the storage layer is stable.
--
-- All data is SYNTHETIC. No PHI. No real patient data.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Reference / lookup data: panels and probes
-- ---------------------------------------------------------------------------

-- A test panel. v1 ships exactly one panel: AML/MDS FISH.
CREATE TABLE IF NOT EXISTS panel (
    panel_id        INTEGER PRIMARY KEY,
    panel_code      TEXT    NOT NULL UNIQUE,
    panel_name      TEXT    NOT NULL,
    specimen_type   TEXT    NOT NULL
        CHECK (specimen_type IN ('BONE_MARROW', 'PERIPHERAL_BLOOD')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Probes belonging to a panel. is_required drives finalize gating.
-- abnormal_cutoff_percent is the percent-abnormal cutoff above the lab's normal
-- range (synthetic values for demonstration).
CREATE TABLE IF NOT EXISTS probe (
    probe_id                 INTEGER PRIMARY KEY,
    panel_id                 INTEGER NOT NULL REFERENCES panel(panel_id),
    probe_code               TEXT    NOT NULL,
    probe_name               TEXT    NOT NULL,
    locus                    TEXT    NOT NULL,
    target                   TEXT    NOT NULL,
    is_required              INTEGER NOT NULL DEFAULT 1 CHECK (is_required IN (0, 1)),
    abnormal_cutoff_percent  REAL    NOT NULL DEFAULT 0.0
        CHECK (abnormal_cutoff_percent >= 0.0),
    UNIQUE (panel_id, probe_code)
);

-- ---------------------------------------------------------------------------
-- Patients (synthetic)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS patient (
    patient_id      INTEGER PRIMARY KEY,
    mrn             TEXT    NOT NULL UNIQUE,
    last_name       TEXT    NOT NULL,
    first_name      TEXT    NOT NULL,
    date_of_birth   TEXT    NOT NULL,                 -- ISO 8601 date
    sex             TEXT    NOT NULL CHECK (sex IN ('M', 'F', 'U')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Orders
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS lab_order (
    order_id            INTEGER PRIMARY KEY,
    patient_id          INTEGER NOT NULL REFERENCES patient(patient_id),
    panel_id            INTEGER NOT NULL REFERENCES panel(panel_id),
    accession_number    TEXT    NOT NULL UNIQUE,
    priority            TEXT    NOT NULL DEFAULT 'ROUTINE'
        CHECK (priority IN ('ROUTINE', 'STAT')),
    status              TEXT    NOT NULL DEFAULT 'ORDERED'
        CHECK (status IN ('ORDERED', 'IN_PROCESS', 'PENDING_REVIEW',
                          'FINALIZED', 'CANCELLED')),
    ordering_provider   TEXT    NOT NULL,
    ordered_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    collected_at        TEXT,
    finalized_at        TEXT
);

CREATE INDEX IF NOT EXISTS idx_lab_order_status   ON lab_order(status);
CREATE INDEX IF NOT EXISTS idx_lab_order_patient  ON lab_order(patient_id);

-- ---------------------------------------------------------------------------
-- Specimens
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS specimen (
    specimen_id             INTEGER PRIMARY KEY,
    order_id                INTEGER NOT NULL REFERENCES lab_order(order_id),
    specimen_type           TEXT    NOT NULL
        CHECK (specimen_type IN ('BONE_MARROW', 'PERIPHERAL_BLOOD')),
    external_specimen_id    TEXT,
    collected_at            TEXT,
    received_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    status                  TEXT    NOT NULL DEFAULT 'RECEIVED'
        CHECK (status IN ('RECEIVED', 'ACCESSIONED', 'REJECTED')),
    rejection_reason        TEXT,
    -- A rejected specimen must carry a reason; a non-rejected one must not.
    CHECK ((status = 'REJECTED' AND rejection_reason IS NOT NULL)
        OR (status <> 'REJECTED' AND rejection_reason IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_specimen_order ON specimen(order_id);

-- ---------------------------------------------------------------------------
-- Structured per-probe FISH results
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS fish_result (
    result_id       INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL REFERENCES lab_order(order_id),
    probe_id        INTEGER NOT NULL REFERENCES probe(probe_id),
    cells_scored    INTEGER NOT NULL CHECK (cells_scored >= 0),
    cells_abnormal  INTEGER NOT NULL CHECK (cells_abnormal >= 0),
    signal_pattern  TEXT    NOT NULL,
    interpretation  TEXT    NOT NULL
        CHECK (interpretation IN ('NORMAL', 'ABNORMAL', 'INDETERMINATE')),
    entered_by      TEXT    NOT NULL,
    entered_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    -- One result per probe per order.
    UNIQUE (order_id, probe_id),
    -- Abnormal cells can never exceed the number scored.
    CHECK (cells_abnormal <= cells_scored)
);

CREATE INDEX IF NOT EXISTS idx_fish_result_order ON fish_result(order_id);

-- ---------------------------------------------------------------------------
-- Validation errors raised against an order
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS validation_error (
    error_id    INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES lab_order(order_id),
    rule_code   TEXT    NOT NULL,
    severity    TEXT    NOT NULL DEFAULT 'ERROR'
        CHECK (severity IN ('ERROR', 'WARNING')),
    message     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_validation_error_order ON validation_error(order_id);

-- ---------------------------------------------------------------------------
-- Finalized report (one per order)
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS report (
    report_id       INTEGER PRIMARY KEY,
    order_id        INTEGER NOT NULL UNIQUE REFERENCES lab_order(order_id),
    summary_text    TEXT    NOT NULL,
    iscn_text       TEXT,                            -- seam for future ISCN parser
    status          TEXT    NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'FINALIZED')),
    finalized_by    TEXT,
    finalized_at    TEXT,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Audit trail
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS audit_event (
    event_id    INTEGER PRIMARY KEY,
    entity_type TEXT    NOT NULL,                    -- e.g. 'lab_order', 'specimen'
    entity_id   INTEGER NOT NULL,
    order_id    INTEGER,                             -- denormalized for fast lookup
    action      TEXT    NOT NULL,                    -- e.g. 'CREATED', 'FINALIZED'
    detail      TEXT,
    actor       TEXT    NOT NULL DEFAULT 'system',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_audit_event_order ON audit_event(order_id);
CREATE INDEX IF NOT EXISTS idx_audit_event_entity ON audit_event(entity_type, entity_id);

-- ---------------------------------------------------------------------------
-- Interface messages (HL7 ORU / FHIR DiagnosticReport) — provisioned for a
-- later session. Kept here so the schema is stable now.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS interface_message (
    message_id  INTEGER PRIMARY KEY,
    direction   TEXT    NOT NULL CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    message_type TEXT   NOT NULL,                    -- e.g. 'ORU', 'DiagnosticReport'
    format      TEXT    NOT NULL CHECK (format IN ('HL7', 'FHIR')),
    order_id    INTEGER REFERENCES lab_order(order_id),
    control_id  TEXT,                                -- MSH-10 / resource id
    payload     TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'GENERATED'
        CHECK (status IN ('GENERATED', 'SENT', 'RECEIVED', 'FILED', 'ERRORED')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interface_message_order ON interface_message(order_id);

-- Inbound messages that could not be filed land here with a clear reason.
CREATE TABLE IF NOT EXISTS interface_error_queue (
    queue_id    INTEGER PRIMARY KEY,
    message_id  INTEGER REFERENCES interface_message(message_id),
    direction   TEXT    NOT NULL DEFAULT 'INBOUND'
        CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    reason      TEXT    NOT NULL,
    raw_payload TEXT,
    status      TEXT    NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN', 'RESOLVED')),
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_error_queue_status ON interface_error_queue(status);

-- ---------------------------------------------------------------------------
-- Reference seed data: the AML/MDS FISH panel and its probes.
-- Synthetic, illustrative probe set and cutoffs — not a clinical reference.
-- ---------------------------------------------------------------------------

INSERT OR IGNORE INTO panel (panel_id, panel_code, panel_name, specimen_type)
VALUES (1, 'AML_MDS_FISH', 'AML/MDS FISH Panel', 'BONE_MARROW');

INSERT OR IGNORE INTO probe
    (panel_id, probe_code, probe_name, locus, target, is_required,
     abnormal_cutoff_percent)
VALUES
    (1, 'RUNX1T1_RUNX1', 'RUNX1T1/RUNX1',       '8q22/21q22', 't(8;21)',              1, 2.0),
    (1, 'CBFB',          'CBFB break-apart',    '16q22',      'inv(16)/t(16;16)',     1, 2.5),
    (1, 'PML_RARA',      'PML/RARA',            '15q24/17q21','t(15;17)',             1, 2.0),
    (1, 'KMT2A',         'KMT2A (MLL) break-apart', '11q23', '11q23 rearrangement',  1, 2.5),
    (1, 'EGR1_5q',       'EGR1 (5q31)',         '5q31',       '-5/del(5q)',           1, 3.0),
    (1, 'D7S486_7q',     'D7S486 (7q31)',       '7q31',       '-7/del(7q)',           1, 3.0),
    (1, 'CEP8',          'CEP8 (centromere 8)', '8cen',       '+8 (trisomy 8)',       1, 2.5),
    (1, 'D20S108_20q',   'D20S108 (20q12)',     '20q12',      'del(20q)',             1, 3.0),
    (1, 'TP53_17p',      'TP53 (17p13)',        '17p13',      'del(17p)/TP53 loss',   1, 2.5);
