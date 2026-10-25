# EX07 - Failed recovery, then success

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation.

## Scenario

Order ACC-EX07-0001 is registered with an accessioned bone-marrow specimen. Its
instrument result message did not file. Under time pressure, the interface
team sent a quick fix (`inputs/correction_v1.hl7`) and asked you to re-drive
it immediately. An hour later they sent a follow-up
(`inputs/correction_v2.hl7`).

## Learning goal

See what a handled recovery failure preserves and what it rolls back, why one
reported failure can hide another, and how a still-open item is recovered
later with a new request.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX07-failed-recovery-then-success --db lab-work/EX07-failed-recovery-then-success.db
DB=lab-work/EX07-failed-recovery-then-success.db
INPUTS=exercises/EX07-failed-recovery-then-success/inputs
```

## Investigate

- `python -m src.analyst worklist --db "$DB"` - read the reason.
- `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`
  - read the whole OBX segment the reason refers to, not only the field it
  names.
- After the first re-drive: `python -m src.analyst recover history --db "$DB" --queue-id QUEUE_ID`,
  `python -m src.analyst trace --db "$DB" --queue-id QUEUE_ID`, and
  `python -m src.analyst worklist --db "$DB"`.
- Use `python -m src.analyst recover preview --db "$DB" ...` before any
  further request.
- `python -m src.analyst reconcile --db "$DB" --queue-id QUEUE_ID` and
  `python -m src.analyst evidence --db "$DB" --queue-id QUEUE_ID --scenario EX07-failed-recovery-then-success --out lab-work/EX07-evidence.json --report lab-work/EX07-report.md`
  once you are done.

## Your task

1. Re-drive the queue item with `$INPUTS/correction_v1.hl7` as delivered,
   without previewing it (request id `EX07-REDRIVE-1`, actor `analyst01`).
2. Investigate the result. Then recover the item with
   `$INPUTS/correction_v2.hl7` under request id `EX07-REDRIVE-2`, actor
   `analyst01`.

## Questions

1. The queue reason names one defect. Is there a second defect in the same OBX
   segment of the original payload, and why did ingestion not report it?
2. After the first re-drive, what is the attempt outcome, what happened to the
   message that attempt created, and what is the queue status?
3. What did the failed attempt roll back, and what did it preserve?
4. Why could you recover the item again, and why did the second request need a
   new request id?
5. How many interface messages exist for this incident, and what role does
   each one play?

## Check your work

```bash
python -m src.analyst exercise verify EX07-failed-recovery-then-success --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX07-failed-recovery-then-success --db lab-work/EX07-failed-recovery-then-success-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
