# Example questions and DuckDB SQL

Each example has a question, a suggested chart type and a correct DuckDB query on the `sales` table.
They are retrieved by similarity and shown to the LLM as few-shot examples.

## Revenue by month
Question: What is the total revenue by month?
Chart: bar
```sql
SELECT month_name AS month, ROUND(SUM(order_amount), 2) AS revenue
FROM sales
GROUP BY month_num, month_name
ORDER BY month_num
```

## Month over month revenue change
Question: How much did revenue drop from April to June, month over month?
Chart: bar
```sql
WITH m AS (
  SELECT month_num, month_name, SUM(order_amount) AS revenue
  FROM sales GROUP BY month_num, month_name
)
SELECT month_name AS month, ROUND(revenue, 2) AS revenue,
       ROUND(100.0 * (revenue - LAG(revenue) OVER (ORDER BY month_num)) / LAG(revenue) OVER (ORDER BY month_num), 2) AS mom_change_pct
FROM m ORDER BY month_num
```

## Total revenue Q2
Question: What was the total revenue for the quarter?
Chart: none
```sql
SELECT ROUND(SUM(order_amount), 2) AS total_revenue,
       COUNT(DISTINCT order_id) AS orders,
       SUM(order_quantity) AS units
FROM sales
```

## Daily revenue trend
Question: Show the daily revenue trend.
Chart: line
```sql
SELECT order_date, ROUND(SUM(order_amount), 2) AS revenue
FROM sales GROUP BY order_date ORDER BY order_date
```

## Weekly orders trend
Question: How many orders per week?
Chart: line
```sql
SELECT date_trunc('week', order_date) AS week_start, COUNT(DISTINCT order_id) AS orders
FROM sales GROUP BY 1 ORDER BY 1
```

## Revenue by category with share
Question: Which product categories bring the most revenue and what is their share?
Chart: bar
```sql
SELECT product_category,
       ROUND(SUM(order_amount), 2) AS revenue,
       ROUND(100.0 * SUM(order_amount) / SUM(SUM(order_amount)) OVER (), 2) AS revenue_share_pct
FROM sales
GROUP BY product_category
ORDER BY revenue DESC
```

## Category revenue by month
Question: Compare monthly revenue of Set, kurta, Western Dress and Top.
Chart: bar
```sql
SELECT month_name AS month,
       ROUND(SUM(order_amount) FILTER (WHERE product_category = 'Set'), 2) AS "Set",
       ROUND(SUM(order_amount) FILTER (WHERE product_category = 'kurta'), 2) AS "kurta",
       ROUND(SUM(order_amount) FILTER (WHERE product_category = 'Western Dress'), 2) AS "Western Dress",
       ROUND(SUM(order_amount) FILTER (WHERE product_category = 'Top'), 2) AS "Top"
FROM sales
GROUP BY month_num, month_name
ORDER BY month_num
```

## Average price by category
Question: Which categories have the highest average price?
Chart: bar
```sql
SELECT product_category, ROUND(AVG(order_amount), 2) AS avg_amount
FROM sales GROUP BY product_category ORDER BY avg_amount DESC
```

## Average order value by month
Question: What is the average order value each month?
Chart: line
```sql
SELECT month_name AS month,
       ROUND(SUM(order_amount) / COUNT(DISTINCT order_id), 2) AS avg_order_value
FROM sales GROUP BY month_num, month_name ORDER BY month_num
```

## Sales by size
Question: Which sizes sell the most?
Chart: bar
```sql
SELECT size, SUM(order_quantity) AS units, ROUND(SUM(order_amount), 2) AS revenue
FROM sales GROUP BY size, size_order ORDER BY size_order
```

## Top states by revenue
Question: Top 10 states by revenue
Chart: bar
```sql
SELECT state, ROUND(SUM(order_amount), 2) AS revenue, COUNT(DISTINCT order_id) AS orders
FROM sales GROUP BY state ORDER BY revenue DESC LIMIT 10
```

## Revenue in one state
Question: What is the revenue from Odisha?
Chart: none
```sql
SELECT state, ROUND(SUM(order_amount), 2) AS revenue, COUNT(DISTINCT order_id) AS orders, SUM(order_quantity) AS units
FROM sales WHERE state = 'ODISHA' GROUP BY state
```

## Top cities by orders
Question: Which cities place the most orders?
Chart: bar
```sql
SELECT city, COUNT(DISTINCT order_id) AS orders, ROUND(SUM(order_amount), 2) AS revenue
FROM sales WHERE city <> 'UNKNOWN' GROUP BY city ORDER BY orders DESC LIMIT 10
```

## Most popular category per state
Question: What is the most popular category in each state?
Chart: none
```sql
SELECT state, product_category, units
FROM (
  SELECT state, product_category, SUM(order_quantity) AS units,
         ROW_NUMBER() OVER (PARTITION BY state ORDER BY SUM(order_quantity) DESC) AS rn
  FROM sales GROUP BY state, product_category
) t
WHERE rn = 1
ORDER BY units DESC
```

## Order status breakdown
Question: What is the breakdown of order status?
Chart: doughnut
```sql
SELECT status_group, COUNT(*) AS order_lines,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM sales GROUP BY status_group ORDER BY order_lines DESC
```

## Cancellation and return rate
Question: What are the cancellation and return rates?
Chart: none
```sql
SELECT COUNT(*) FILTER (WHERE status_group = 'Cancelled') AS cancelled_lines,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status_group = 'Cancelled') / COUNT(*), 2) AS cancellation_rate_pct,
       COUNT(*) FILTER (WHERE status_group = 'Returned') AS returned_lines,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status_group = 'Returned') / COUNT(*), 2) AS return_rate_pct
FROM sales
```

## Cancellation rate by category
Question: Which categories have the highest cancellation rate?
Chart: bar
```sql
SELECT product_category,
       COUNT(*) AS order_lines,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status_group = 'Cancelled') / COUNT(*), 2) AS cancellation_rate_pct
FROM sales GROUP BY product_category HAVING COUNT(*) >= 50
ORDER BY cancellation_rate_pct DESC
```

## Amazon vs Merchant fulfilment
Question: Compare Amazon fulfilment with Merchant fulfilment.
Chart: bar
```sql
SELECT fulfilment,
       COUNT(DISTINCT order_id) AS orders,
       ROUND(SUM(order_amount), 2) AS revenue,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status_group = 'Cancelled') / COUNT(*), 2) AS cancellation_rate_pct
FROM sales GROUP BY fulfilment ORDER BY revenue DESC
```

## B2B vs B2C
Question: How do business customers compare with retail customers?
Chart: bar
```sql
SELECT customer_type, COUNT(DISTINCT order_id) AS orders,
       ROUND(SUM(order_amount), 2) AS revenue,
       ROUND(AVG(order_amount), 2) AS avg_amount
FROM sales GROUP BY customer_type ORDER BY revenue DESC
```

## Promotion impact
Question: Do orders with promotions have a higher value?
Chart: bar
```sql
SELECT CASE WHEN has_promotion THEN 'With promotion' ELSE 'No promotion' END AS promotion_status,
       COUNT(*) AS order_lines,
       ROUND(SUM(order_amount), 2) AS revenue,
       ROUND(AVG(order_amount), 2) AS avg_amount
FROM sales GROUP BY 1 ORDER BY revenue DESC
```

## Top SKUs
Question: What are the top 10 best selling SKUs by units?
Chart: bar
```sql
SELECT sku, product_category, SUM(order_quantity) AS units, ROUND(SUM(order_amount), 2) AS revenue
FROM sales GROUP BY sku, product_category ORDER BY units DESC LIMIT 10
```

## Top styles in a category
Question: Top 5 kurta styles by revenue
Chart: bar
```sql
SELECT style, ROUND(SUM(order_amount), 2) AS revenue, SUM(order_quantity) AS units
FROM sales WHERE product_category = 'kurta'
GROUP BY style ORDER BY revenue DESC LIMIT 5
```

## Expedited vs Standard
Question: Revenue by service level
Chart: doughnut
```sql
SELECT service_level, ROUND(SUM(order_amount), 2) AS revenue, COUNT(DISTINCT order_id) AS orders
FROM sales GROUP BY service_level ORDER BY revenue DESC
```

## Best day of week
Question: Which day of the week has the highest sales?
Chart: bar
```sql
SELECT dayname(order_date) AS weekday, ROUND(SUM(order_amount), 2) AS revenue
FROM sales GROUP BY dayname(order_date), isodow(order_date) ORDER BY isodow(order_date)
```

## Category revenue in one month
Question: Revenue by category in May
Chart: bar
```sql
SELECT product_category, ROUND(SUM(order_amount), 2) AS revenue
FROM sales WHERE month_num = 5
GROUP BY product_category ORDER BY revenue DESC
```

## State and category filter
Question: How many Set units were sold in Karnataka in June?
Chart: none
```sql
SELECT SUM(order_quantity) AS units, ROUND(SUM(order_amount), 2) AS revenue
FROM sales
WHERE state = 'KARNATAKA' AND product_category = 'Set' AND month_num = 6
```
