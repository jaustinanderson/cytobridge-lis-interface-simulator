# EX06 - Blocked finalization and result correction

> **Synthetic educational exercise.** All data is synthetic; no PHI. CytoBridge
> is a training simulator, not a production LIS, certified interface engine,
> validated medical device, or Epic/Beaker implementation. The probe cutoffs
> are synthetic seed values in `schema.sql`, not a clinical reference.

## Scenario

Order ACC-EX06-0001 received an instrument result message that filed without
error. An analyst then tried to finalize the order, and finalization did not
complete. The bench technologist (`tech01`) has re-reviewed the slides and
supplied this worksheet:

| Probe | Cells scored | Cells abnormal | Signal pattern | Interpretation |
|---|---|---|---|---|
| TP53_17p | 200 | 2 | 2R2G | NORMAL |
| CEP8 | 200 | 12 | 3 aqua | ABNORMAL |

## Learning goal

Read validation findings back to the rules that produced them, correct results
through the normal result-entry service, and understand what the database
keeps, and does not keep, about a corrected value.

## Set up

```bash
mkdir -p lab-work
python -m src.analyst exercise setup EX06-finalize-blocked-correction --db lab-work/EX06-finalize-blocked-correction.db
DB=lab-work/EX06-finalize-blocked-correction.db
```

## Investigate

- `python -m src.analyst trace --db "$DB" --accession ACC-EX06-0001` - read
  the current results and the stored validation errors.
- `python -m src.analyst reconcile --db "$DB" --order-id ORDER_ID` - which
  finalization prerequisites are outstanding?
- `python -m src.analyst audit --db "$DB" --order-id ORDER_ID` - which events
  record the blocked attempt?
- `python -m src.analyst show-message --db "$DB" --message-id MESSAGE_ID --raw`
  - count the OBX segments and read the CEP8 line.
- Compare each result with its cutoff in `schema.sql` and with the rules in
  `src/validation.py`.
- After each correction, re-run `trace` and `audit`.

## Your task

Enter the two worksheet results above for the order (entered by `tech01`),
then finalize the order as `analyst01`.

## Questions

1. Which validation findings blocked finalization, and which rule in
   `src/validation.py` produced each one?
2. The CEP8 value was filed by the interface without error. Why did ingestion
   accept it while finalization did not?
3. After you re-enter CEP8, how many CEP8 result rows exist, and what evidence
   remains of the earlier value?
4. What happened to the stored validation findings from the blocked attempt
   when the order finalized?
5. Which audit events show that a blocked attempt happened before the
   successful one?

## Check your work

```bash
python -m src.analyst exercise verify EX06-finalize-blocked-correction --db "$DB"
```

To see the automated reference run (this executes the answer key on a new
database and reveals the solution), use a different path:

```bash
python -m src.analyst exercise run EX06-finalize-blocked-correction --db lab-work/EX06-finalize-blocked-correction-reference.db
```

A reference run is automated testing, not manual UAT, and not evidence of
mastery.
