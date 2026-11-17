"""CytoBridge Analyst Investigation and Recovery Lab.

A persistent, headless analyst toolkit layered on the existing CytoBridge
services. It adds no new product semantics: every mutation goes through the
existing public workflow, inbound-interface, outbound-interface, and v1.1
recovery service functions, and every investigation view reads the stored
records directly.

Modules
-------
    common      shared helpers: errors/exit codes, fingerprints, JSON, limitations
    store       database access modes (create-new, read-only, read-write)
    trace       chronological case view built from real primary/foreign keys
    reconcile   deterministic reconciliation findings against documented rules
    preview     non-mutating recovery preview on an isolated in-memory copy
    evidence    portable, deterministic case evidence (JSON + short report)
    exercises   executable synthetic incident exercises and their verifier
    cli         the ``python -m src.analyst`` command-line interface

The CLI is an additive extension authorized after v1.1 (issue #43). It wraps,
and does not replace, the frozen v1.1 recovery service boundary
(``retry_queue_item`` / ``redrive_queue_item`` / ``get_recovery_history``).

This is an educational, synthetic simulator - not a production LIS, certified
interface engine, validated medical device, or Epic/Beaker implementation.
All data is synthetic. No PHI.
"""
