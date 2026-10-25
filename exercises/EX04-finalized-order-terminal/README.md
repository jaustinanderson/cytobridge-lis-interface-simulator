# EX04 - Late result for a finalized order

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation.

## Scenario

Order ACC-EX04-0001 received a complete instrument result, and an analyst
finalized it. Later the same afternoon the instrument interface sent a second
result message for the same accession with different counts
(`inputs/late_result.hl7`). That message did not file. A colleague asks
whether you can "push the late result through".

## Learning goal

Recognize a failure the recovery design treats as terminal, see what the
system records when recovery is attempted anyway, and confirm that the
finalized order and its results are untouched.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX04-finalized-order-terminal --db lab-work/EX04-finalized-order-terminal.db
DB=lab-work/EX04-finalized-order-terminal.db
INPUTS=exercises/EX04-finalized-order-terminal/inputs
```

## Investigate

- `python -m src.analyst worklist --db "$DB"` - why is the default worklist
  empty? Try `--status ALL`.
- `python -m src.analyst trace --db "$DB" --accession ACC-EX04-0001` - how
  many inbound messages are associated with this order, and on what basis is
  each one linked?
- `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`
  for each inbound message. Compare their counts with the order's current
  results.
- `python -m src.analyst audit --db "$DB" --order-id ORDER_ID` - which
  message does the filing event name?
- `python -m src.analyst recover history --db "$DB" --queue-id QUEUE_ID`
  before and after your recovery requests.

## Your task

Determine whether the late result can be recovered, and leave a record of the
attempts:

1. Preview a corrected re-drive of the queue item using
   `$INPUTS/late_result.hl7` (request id `EX04-REDRIVE-1`, actor
   `analyst01`).
2. Submit that re-drive (same request id and actor).
3. Submit an unchanged retry of the queue item (request id `EX04-RETRY-1`,
   actor `analyst01`).

## Questions

1. Why did this queue item start in a closed state instead of `OPEN`?
2. What did the preview report, and why is a preview not a guarantee of what a
   later apply will do?
3. What records did each of your recovery requests create, and what did they
   not create?
4. Which values are current on the order: the first message's or the late
   message's? What evidence supports your answer, and what can the schema not
   tell you in general about earlier result values?
5. What does CytoBridge permit you to do with a finalized order, and what
   would acting on the late values require?

## Check your work

```bash
python -m src.analyst exercise verify EX04-finalized-order-terminal --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX04-finalized-order-terminal --db lab-work/EX04-finalized-order-terminal-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
