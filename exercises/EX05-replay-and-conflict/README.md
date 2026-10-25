# EX05 - Request replay and request-id conflict

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation.

## Scenario

Order ACC-EX05-0001 is registered with an accessioned bone-marrow specimen. Its
instrument result message did not file. The interface team supplied a
corrected copy (`inputs/corrected_result.hl7`). During recovery, a network
hiccup makes you submit the same request twice, and a second analyst
(`analyst02`) later reuses your request id by mistake.

## Learning goal

Distinguish a matching replay from a request-id conflict, and confirm with
database records that a resolved item is never filed twice.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX05-replay-and-conflict --db lab-work/EX05-replay-and-conflict.db
DB=lab-work/EX05-replay-and-conflict.db
INPUTS=exercises/EX05-replay-and-conflict/inputs
```

## Investigate

- `python -m src.analyst worklist --db "$DB"` and
  `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`
  - which segment and field does the reason refer to?
- After each request in the task, run
  `python -m src.analyst recover history --db "$DB" --queue-id QUEUE_ID` and
  `python -m src.analyst audit --db "$DB" --order-id ORDER_ID`, and note what
  changed. Adding `--json` to the recovery commands shows whether a response
  was replayed.
- `python -m src.analyst trace --db "$DB" --queue-id QUEUE_ID` - which audit
  events belong to the queue item rather than the order?
- `python -m src.analyst reconcile --db "$DB" --queue-id QUEUE_ID`

## Your task

Submit these four recovery requests in order, all corrected re-drives of the
queue item using `$INPUTS/corrected_result.hl7`, and record the exit code and
outcome of each:

1. request id `EX05-REDRIVE-1`, actor `analyst01`;
2. the identical request again;
3. request id `EX05-REDRIVE-1` again, actor `analyst02`;
4. request id `EX05-REDRIVE-2`, actor `analyst01`.

## Questions

1. Why did the original message fail, and which segment carries the defect?
2. What did the second, identical submission return, and what did it write?
3. What made the third submission different, where is it recorded, and does
   it appear in recovery history?
4. Why was the fourth request refused even though its payload was valid?
5. How many filing events exist for the order, and why does that matter?

## Check your work

```bash
python -m src.analyst exercise verify EX05-replay-and-conflict --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX05-replay-and-conflict --db lab-work/EX05-replay-and-conflict-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
