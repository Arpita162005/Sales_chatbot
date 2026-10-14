# KPI definitions

## Revenue (gross order value)
Revenue = SUM(order_amount) over all rows, in INR (₹). This is the definition used in the EDA notebook
("net revenue" chart), so it includes the amount of lines that were later cancelled or returned when Amazon
recorded an amount for them. Use this as the default meaning of "revenue", "sales" or "sales value".
SQL: SUM(order_amount)

## Realised revenue (excluding cancelled and returned)
Realised revenue = SUM(order_amount) WHERE status_group NOT IN ('Cancelled', 'Returned').
Use it only when the user asks for net, realised, delivered or actual revenue after cancellations/returns.
SQL: SUM(order_amount) FILTER (WHERE status_group NOT IN ('Cancelled','Returned'))

## Orders and order lines
Orders = COUNT(DISTINCT order_id). Order lines = COUNT(*). Units sold = SUM(order_quantity).
An order can contain several lines (products), so orders <= order lines.

## Average order value (AOV)
AOV = SUM(order_amount) / COUNT(DISTINCT order_id). The notebook computes AOV per month this way.
Average line amount (average price per line) = AVG(order_amount); the notebook's "average price by category"
uses AVG(order_amount) per category.

## Cancellation rate
Cancellation rate = order lines with status_group = 'Cancelled' / all order lines * 100.
'Cancelled' includes ship_status 'Cancelled' and 'Shipped - Lost in Transit' (notebook definition).
SQL: 100.0 * COUNT(*) FILTER (WHERE status_group = 'Cancelled') / COUNT(*)

## Return rate
Return rate = order lines with status_group = 'Returned' / all order lines * 100.
'Returned' = Shipped - Returned to Seller, Shipped - Returning to Seller, Shipped - Rejected by Buyer,
Shipped - Damaged (notebook definition).

## Revenue share and growth
Share of revenue = revenue of a group / total revenue * 100. Use a window function:
100.0 * SUM(order_amount) / SUM(SUM(order_amount)) OVER ().
Month-over-month growth % = (this month - previous month) / previous month * 100; use LAG() over month_num.

## B2B vs B2C
customer_type = 'business' is B2B, 'customer' is B2C. Compare revenue, orders and AVG(order_amount) by customer_type.

## Promotion usage
Promotion rate = lines with has_promotion = true / all lines * 100. Compare AOV or AVG(order_amount) with and
without promotions to judge promotion impact.

## Fulfilment comparison
Compare fulfilment = 'Amazon' (FBA) vs 'Merchant' on revenue, orders, cancellation rate and return rate.
