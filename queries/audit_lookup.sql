-- audit_lookup.sql
-- Full audit trail for one order, in chronological order.
-- Parameterized: bind :order_id.

SELECT
    ae.event_id,
    ae.created_at,
    ae.entity_type,
    ae.entity_id,
    ae.action,
    ae.actor,
    ae.detail
FROM audit_event AS ae
WHERE ae.order_id = :order_id
ORDER BY ae.event_id ASC;
