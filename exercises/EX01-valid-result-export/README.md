# EX01 - Valid inbound result to final report and exports

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation. HL7/FHIR output is
> HL7-style and FHIR-style only, not conformance-tested.

## Scenario

Synthetic patient SYN-EX01-01 has an AML/MDS FISH order, accession
ACC-EX01-0001, with an accessioned bone-marrow specimen. The FISH instrument
interface has delivered a complete result message for that accession
(`inputs/valid_result.hl7`). Nothing has been filed yet.

## Learning goal

Follow one clean result end to end: raw inbound message -> filed per-probe
results -> validation and final report -> stored outbound HL7-style and
FHIR-style messages, and confirm that every representation carries the same
values.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX01-valid-result-export --db lab-work/EX01-valid-result-export.db
DB=lab-work/EX01-valid-result-export.db
INPUTS=exercises/EX01-valid-result-export/inputs
```

## Investigate

- Look at the order before anything is filed:
  `python -m src.analyst trace --db "$DB" --accession ACC-EX01-0001`
- Read the inbound fixture itself (`$INPUTS/valid_result.hl7`): which OBX
  field carries cells scored, cells abnormal, signal pattern, and
  interpretation?
- Before finalizing, try a non-storing export and read the response:
  `python -m src.analyst export --db "$DB" --order-id ORDER_ID --format hl7`
  (replace ORDER_ID with the order id shown by `trace`).
- After each action, re-run `trace`, and use
  `python -m src.analyst audit --db "$DB" --order-id ORDER_ID` and
  `python -m src.analyst reconcile --db "$DB"`.
- Once the message is stored, view it exactly as received:
  `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`

## Your task

1. Ingest `$INPUTS/valid_result.hl7`.
2. Finalize the order as `analyst01`.
3. Store exactly one HL7-style export and exactly one FHIR-style export of the
   order. Give the HL7 export the generated-at time `2026-10-25 09:00:00` so
   its control id is reproducible.

## Questions

1. Which validation rules does finalization evaluate, and which findings, if
   any, did this order produce?
2. What happened when you tried to export before finalizing, and why?
3. Pick one probe. Where do its cells scored, cells abnormal, and
   interpretation appear in the inbound OBX, in `fish_result`, in the stored
   HL7-style OBX, and in the FHIR-style Observation?
4. Which record connects the filed results to the inbound message, and is that
   connection a foreign key?

## Check your work

```bash
python -m src.analyst exercise verify EX01-valid-result-export --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX01-valid-result-export --db lab-work/EX01-valid-result-export-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
