# EX02 - Unknown accession and unchanged retry

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation.

## Scenario

The FISH instrument interface delivered a result message for accession
ACC-EX02-0001 before anyone registered an order for it. The database contains
no patient and no order; the message did not file. Registration staff have now
asked you to register the order and get the result filed.

## Learning goal

Investigate a failed message that has no order link, register the matching
order from the evidence in the message, and use a recovery preview to check
the outcome before applying a recovery that does not change the payload.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX02-unknown-accession-retry --db lab-work/EX02-unknown-accession-retry.db
DB=lab-work/EX02-unknown-accession-retry.db
```

## Investigate

- `python -m src.analyst worklist --db "$DB"` - note the queue id, message
  id, failure code, category, and recovery policy.
- `python -m src.analyst trace --db "$DB" --queue-id QUEUE_ID` - which
  records are linked, and on what basis? Read the provenance and limitations.
- `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`
  - find the accession (OBR-3), MRN (PID-3), name (PID-5), birth date
  (PID-7), sex (PID-8), and ordering provider (OBR-16).
- `python -m src.analyst reconcile --db "$DB"`
- After you register the order, re-run `trace` for the queue item. What
  changed in the links?
- `python -m src.analyst recover history --db "$DB" --queue-id QUEUE_ID`
  before and after your preview.

## Your task

1. Register the missing order with `case-create`, using the patient and order
   details carried in the queued message (accession ACC-EX02-0001, MRN
   SYN-EX02-01) and an accessioned bone-marrow specimen.
2. Recover the queued message **without changing its payload**. Preview the
   recovery first, then apply it. Use request id `EX02-RETRY-1` and actor
   `analyst01` for both the preview and the apply.

## Questions

1. What failure code, category, and recovery policy did the queue item
   receive, and where is that mapping defined?
2. The failed message has no `order_id`. How does `trace` connect it to the
   order you registered, and why is that link labelled differently from a
   foreign key?
3. Why is an unchanged retry permitted for this item when it would be refused
   for most other failure codes?
4. Did the preview consume request id `EX02-RETRY-1` or write anything to your
   database? How can you tell?
5. After the recovery, which message is `FILED`, and how does its payload
   compare with the original message's payload?

## Check your work

```bash
python -m src.analyst exercise verify EX02-unknown-accession-retry --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX02-unknown-accession-retry --db lab-work/EX02-unknown-accession-retry-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
