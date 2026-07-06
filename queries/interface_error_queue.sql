-- interface_error_queue.sql
-- Open items in the inbound interface error queue, oldest first.
-- This is the interface analyst's worklist for messages that failed to file
-- (malformed payloads, unmatched accession numbers, etc.).

SELECT
    eq.queue_id,
    eq.created_at,
    eq.direction,
    eq.reason,
    eq.message_id,
    im.message_type,
    im.format,
    im.control_id,
    eq.status
FROM interface_error_queue AS eq
LEFT JOIN interface_message AS im ON im.message_id = eq.message_id
WHERE eq.status = 'OPEN'
ORDER BY eq.created_at ASC;
