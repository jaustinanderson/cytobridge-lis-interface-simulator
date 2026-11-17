# EX03 - Invalid result value and corrected re-drive

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation.

## Scenario

Order ACC-EX03-0001 is registered with an accessioned bone-marrow specimen.
The instrument interface sent a result message for it, but the message did not
file and no results reached the order. The interface team has since sent a
corrected copy of the message (`inputs/corrected_result.hl7`).

## Learning goal

Locate a single malformed value in a raw payload, understand why the whole
message was held back, and apply a corrected re-drive while proving the
original message and its queue copy are preserved.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX03-invalid-result-redrive --db lab-work/EX03-invalid-result-redrive.db
DB=lab-work/EX03-invalid-result-redrive.db
INPUTS=exercises/EX03-invalid-result-redrive/inputs
```

## Investigate

- `python -m src.analyst worklist --db "$DB"` - read the reason and the
  stored classification.
- `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`
  - find the exact OBX segment and component the reason refers to. Look
  closely at each character.
- `python -m src.analyst trace --db "$DB" --queue-id QUEUE_ID` - note the
  original message's payload fingerprint and the queue raw-copy check.
- Compare the original payload with `$INPUTS/corrected_result.hl7`. What
  differs besides the corrected value?
- `python -m src.analyst recover preview --db "$DB" ...` simulates a
  recovery on an isolated copy. Add `--json` to see the full preview result,
  including its `snapshot` and `changes` sections.
- After applying, re-run `trace`, `recover history`, and
  `python -m src.analyst reconcile --db "$DB" --queue-id QUEUE_ID`, then
  export the case:
  `python -m src.analyst evidence --db "$DB" --queue-id QUEUE_ID --scenario EX03-invalid-result-redrive --out lab-work/EX03-evidence.json --report lab-work/EX03-report.md`
  and compare its fingerprints with `sha256sum $INPUTS/*.hl7`.

## Your task

Preview a corrected re-drive of the queue item using
`$INPUTS/corrected_result.hl7`, then apply it. Use request id
`EX03-REDRIVE-1` and actor `analyst01` for both.

## Questions

1. What exactly is wrong in the original payload, and which OBX segment and
   component carries it?
2. Eight of the nine OBX segments were valid. Why was none of them filed?
3. Why must this item be recovered with a corrected re-drive rather than an
   unchanged retry?
4. After the re-drive, show that the original message and the queue's
   `raw_payload` copy are unchanged. Which values did you compare?
5. How does the recovery attempt's `payload_sha256` relate to the message the
   attempt created?

## Check your work

```bash
python -m src.analyst exercise verify EX03-invalid-result-redrive --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX03-invalid-result-redrive --db lab-work/EX03-invalid-result-redrive-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
