-- validation_error_rate.sql
-- Share of orders that hit at least one validation ERROR, plus raw counts.
-- A quality signal: rising error rate suggests upstream data-entry problems.

WITH order_totals AS (
    SELECT COUNT(*) AS total_orders
    FROM lab_order
),
errored AS (
    SELECT COUNT(DISTINCT ve.order_id) AS orders_with_errors
    FROM validation_error AS ve
    WHERE ve.severity = 'ERROR'
)
SELECT
    ot.total_orders,
    e.orders_with_errors,
    CASE
        WHEN ot.total_orders = 0 THEN 0.0
        ELSE ROUND(100.0 * e.orders_with_errors / ot.total_orders, 1)
    END AS error_rate_pct
FROM order_totals AS ot
CROSS JOIN errored AS e;
