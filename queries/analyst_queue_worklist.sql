-- analyst_queue_worklist.sql
-- Interface error-queue worklist for the analyst lab CLI, oldest first.
-- One row per queue item with its stored failure classification, the linked
-- original message's control id, and a recovery-attempt summary:
-- attempt_count, and last_outcome = the outcome of the attempt with the highest
-- attempt_id (NULL when the item has no attempts).
-- Parameterized: bind :status ('OPEN', 'RESOLVED', 'TERMINAL', or NULL for all).

SELECT
    eq.queue_id,
    eq.created_at,
    eq.status,
    eq.failure_code,
    eq.failure_category,
    eq.recovery_policy,
    eq.reason,
    eq.message_id,
    im.control_id,
    COALESCE(agg.attempt_count, 0) AS attempt_count,
    last_ra.outcome AS last_outcome,
    eq.resolved_at,
    eq.terminal_at
FROM interface_error_queue AS eq
LEFT JOIN interface_message AS im ON im.message_id = eq.message_id
LEFT JOIN (
    SELECT
        ra.queue_id,
        COUNT(*) AS attempt_count,
        MAX(ra.attempt_id) AS last_attempt_id
    FROM interface_recovery_attempt AS ra
    GROUP BY ra.queue_id
) AS agg ON agg.queue_id = eq.queue_id
LEFT JOIN interface_recovery_attempt AS last_ra
    ON last_ra.attempt_id = agg.last_attempt_id
WHERE (:status IS NULL OR eq.status = :status)
ORDER BY eq.created_at ASC, eq.queue_id ASC;
