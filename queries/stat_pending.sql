-- stat_pending.sql
-- STAT orders that are not yet finalized, oldest first.
-- These are the highest-priority items and should surface at the top of any
-- monitoring view. Includes an elapsed-hours column to spot aging STATs.

SELECT
    o.order_id,
    o.accession_number,
    pt.mrn,
    pt.last_name || ', ' || pt.first_name AS patient_name,
    o.status,
    o.ordered_at,
    ROUND(
        (julianday('now') - julianday(o.ordered_at)) * 24.0,
        2
    ) AS hours_elapsed
FROM lab_order AS o
JOIN patient AS pt ON pt.patient_id = o.patient_id
WHERE o.priority = 'STAT'
  AND o.status NOT IN ('FINALIZED', 'CANCELLED')
ORDER BY o.ordered_at ASC;
