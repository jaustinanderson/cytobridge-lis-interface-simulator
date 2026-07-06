-- pending_review.sql
-- Orders that have results entered and are awaiting review/finalization.
-- This is the analyst's daily worklist for the review queue.

SELECT
    o.order_id,
    o.accession_number,
    o.priority,
    pt.mrn,
    pt.last_name || ', ' || pt.first_name AS patient_name,
    o.ordered_at,
    COUNT(fr.result_id) AS results_entered,
    (SELECT COUNT(*) FROM probe pr
      WHERE pr.panel_id = o.panel_id AND pr.is_required = 1) AS required_probes
FROM lab_order AS o
JOIN patient AS pt      ON pt.patient_id = o.patient_id
LEFT JOIN fish_result AS fr ON fr.order_id = o.order_id
WHERE o.status = 'PENDING_REVIEW'
GROUP BY o.order_id
ORDER BY
    CASE o.priority WHEN 'STAT' THEN 0 ELSE 1 END,
    o.ordered_at ASC;
