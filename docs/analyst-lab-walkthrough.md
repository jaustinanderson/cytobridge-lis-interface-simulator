# Analyst lab walkthrough

A clean-checkout walkthrough of the CytoBridge analyst investigation and recovery
lab. It takes one synthetic interface incident from raw input to verified
recovery and exported evidence, using only commands - no Python editing.

> **Synthetic educational data only - no PHI.** CytoBridge is a training
> simulator, not a production LIS, certified interface engine, validated medical
> device, or Epic/Beaker implementation. Running these commands is an automated
> or self-guided exercise. It is not a manual UAT pass and does not demonstrate
> anyone's mastery by itself.

## What you need

- Python 3.11 or later. The lab uses only the standard library
  (`sqlite3`, `argparse`); `pytest` is needed only to run the test suite.
- A clone of `jaustinanderson/cytobridge-lis-interface-simulator`. Run every
  command from the repository root.

```bash
git clone https://github.com/jaustinanderson/cytobridge-lis-interface-simulator.git
cd cytobridge-lis-interface-simulator
mkdir lab-work            # git ignores this folder; databases go here
python -m src.analyst --help
```

`--help` lists every command, says which ones are read-only, and prints the exit
codes: `0` ok, `1` check failed, `2` usage, `3` not found, `4` refused or a
negative recorded outcome, `5` invalid input, `6` internal error. Add `--json`
to any command for one machine-readable envelope object instead of text.

## The incident (exercise EX03)

This walkthrough reveals the solution to exercise EX03. To practise first, read
[`exercises/EX03-invalid-result-redrive/README.md`](../exercises/EX03-invalid-result-redrive/README.md)
and stop after step 1.

### 1. Set up the incident

```bash
python -m src.analyst exercise setup EX03 --db lab-work/ex03.db
```

This creates a **new** database (it refuses an existing path), registers a
synthetic order `ACC-EX03-0001` with an accessioned bone-marrow specimen, and
ingests the instrument message `exercises/EX03-invalid-result-redrive/inputs/original_result.hl7`.
The ingest step is expected to exit `4`: the message did not file.

### 2. Find the failure

```bash
python -m src.analyst worklist --db lab-work/ex03.db
```

Queue item `1` is `OPEN` with `INVALID_CELL_COUNT / REDRIVE_ONLY`, and its
reason names the defect. Read commands such as this one open the database
read-only, never create a missing file, and leave the file byte-for-byte
unchanged.

### 3. Inspect the evidence

```bash
python -m src.analyst trace --db lab-work/ex03.db --queue-id 1
python -m src.analyst show-message --db lab-work/ex03.db --message-id 1 --raw
python -m src.analyst reconcile --db lab-work/ex03.db
```

- `trace` shows the order, specimen, current results, the stored message and
  its SHA-256 fingerprint, the queue item, every link with its basis, a
  timeline, provenance notes, and limitations. The failed message has
  `order_id` NULL, so its link to the order is labeled `PAYLOAD_DERIVED`
  (from the OBR-3 accession), not a foreign key.
- `show-message --raw` prints the stored payload exactly as received. Look at
  OBX set id 3 to find the malformed cell count.
- `reconcile` checks documented rules (relationships, message/queue/recovery
  state, audit evidence, workflow prerequisites) and should report no ERROR
  findings. Its rules are educational invariants, not clinical requirements.

### 4. Preview the correction

```bash
python -m src.analyst recover preview --db lab-work/ex03.db --queue-id 1 \
  --action redrive --file exercises/EX03-invalid-result-redrive/inputs/corrected_result.hl7 \
  --request-id EX03-REDRIVE-1 --actor analyst01
```

The preview copies the database into memory, runs the **unchanged** recovery
service on the copy, and prints the simulated outcome (`SUCCEEDED`) and the
row changes it would make. It reports `source database unchanged: yes`. It
does not use up the request id and writes nothing. It describes one snapshot
only: it is not an approval, and it does not guarantee the later result.

### 5. Apply the corrected re-drive

```bash
python -m src.analyst recover redrive --db lab-work/ex03.db --queue-id 1 \
  --file exercises/EX03-invalid-result-redrive/inputs/corrected_result.hl7 \
  --request-id EX03-REDRIVE-1 --actor analyst01
python -m src.analyst recover history --db lab-work/ex03.db --queue-id 1
```

The apply calls the recovery service again against current state. The
corrected payload files on a **new** message `2`, the original message `1`
stays `ERRORED` and unchanged, and queue item `1` moves `OPEN -> RESOLVED`.
Running the same command again is a matching replay: it exits `0`, says
`replayed`, and writes nothing. Reusing `EX03-REDRIVE-1` with a different actor
is a `REQUEST_ID_CONFLICT` (exit `4`). A conflict creates no attempt and
records one audit event, which would make step 7's verification fail. That is
intended, because the verifier checks the expected end state.

### 6. Export portable evidence

```bash
python -m src.analyst evidence --db lab-work/ex03.db --queue-id 1 \
  --scenario EX03-invalid-result-redrive \
  --out lab-work/ex03-evidence.json --report lab-work/ex03-report.md
```

The JSON is deterministic. It records the source revision, the scenario, every
record relationship, the original and corrected payload fingerprints with
their consistency checks, validations, outcomes, recovery history,
reconciliation findings, and limitations. Each fixture's fingerprint can be
checked independently. For example, `sha256sum exercises/EX03-invalid-result-redrive/inputs/*.hl7`
should match the message fingerprints in the report. The `content_fingerprint`
ignores timestamps, so rerunning the same steps in a new database gives the
same value. `evidence` refuses to overwrite an existing output file.

### 7. Verify the persisted outcome

```bash
python -m src.analyst exercise verify EX03 --db lab-work/ex03.db
```

The verifier opens the database read-only and checks the stored rows and
fixture-file hashes against the answer key in
[`exercises/answer-keys/`](../exercises/answer-keys/). It does not rely on any
command's success message. It exits `0` when every check passes and `1`
otherwise, listing each failed check.

## Run every exercise automatically

```bash
python -m src.analyst exercise run-all --workdir lab-work/all
```

This runs all seven exercises (valid result to export, unknown accession and
retry, invalid result and re-drive, finalized-order terminal rejection, replay
and request-id conflict, blocked finalization and correction, failed recovery
then success). Each runs in its own new database under `lab-work/all/`, followed
by its reference solution and verification. A second run into the same folder
is refused, because exercise databases are never reused.

## Limits to keep in mind

- `fish_result` holds only the **current** value per probe (re-entry upserts),
  and no column records which message wrote it. The lab says so and lists
  candidate sources instead of asserting one.
- Timestamps have one-second resolution. Timeline order within a second is
  presentational and does not establish causation.
- There is no public order-cancellation service, so the lab cannot cancel an
  order. Exercise EX04 shows terminal rejection with a FINALIZED order.

See [`validation/known-issues.md`](../validation/known-issues.md) for the full
list and [`docs/interface-troubleshooting.md`](interface-troubleshooting.md)
for the recovery rules the lab applies.
