-- turnaround_time.sql
-- Turnaround time (TAT) for finalized orders, from order placement to finalize.
-- Reported in hours. Useful for TAT dashboards and STAT vs ROUTINE comparison.

SELECT
    o.order_id,
    o.accession_number,
    o.priority,
    p.panel_code,
    o.ordered_at,
    o.finalized_at,
    ROUND(
        (julianday(o.finalized_at) - julianday(o.ordered_at)) * 24.0,
        2
    ) AS tat_hours
FROM lab_order AS o
JOIN panel AS p ON p.panel_id = o.panel_id
WHERE o.status = 'FINALIZED'
  AND o.finalized_at IS NOT NULL
ORDER BY tat_hours DESC;
