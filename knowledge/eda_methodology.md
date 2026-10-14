# EDA notebook methodology

## Objective of the analysis
The notebook "Forecast E-Commerce Sales Data Analysis" analyses the sales of Amazon India during Q2 2022
(April to June). It looks at revenue trend, product categories, average order value, sizes, cancellations and
returns, state-wise popularity, and B2B versus retail customers.

## Data cleaning steps applied
1. Dropped columns not needed: index, Unnamed: 22, fulfilled-by, ship-country, currency, Sales Channel.
2. Removed duplicate lines on (Order ID, ASIN), keeping the first occurrence (7 duplicates).
3. Filled missing Courier Status with 'unknown', missing promotion-ids with 'no promotion',
   missing Amount with 0, missing ship-city / ship-state / ship-postal-code with 'unknown'.
4. Renamed columns to snake_case (order_ID, ship_status, product_category, order_amount, state, ...).
5. Converted B2B True/False to customer_type 'business' / 'customer'.
6. Parsed dates and dropped March 2022 because it had only one day of data.
7. Added a month column (april, may, june) and an ordered size category (Free, XS ... 6XL).
The chatbot's DuckDB table applies exactly these steps, so its numbers match the notebook.

## Additional cleaning done by the chatbot
State names were standardised to UPPER CASE and common misspellings merged (Orissa -> ODISHA,
Rajshthan/Rajsthan/RJ -> RAJASTHAN, Pondicherry -> PUDUCHERRY, New Delhi -> DELHI, PB -> PUNJAB,
NL -> NAGALAND, AR -> ARUNACHAL PRADESH). The original spelling is kept in state_raw.
Cities were upper-cased. A status_group column groups the detailed statuses.

## Why missing amounts are zero
Most rows with a missing Amount are cancelled orders, so the notebook set them to 0 rather than dropping them.
This keeps cancelled lines in order counts and cancellation rates without adding revenue.

## Note on the combined cancel + return percentage
The notebook printed "17.53% of all orders" for cancelled + returned lines, but that figure divides by the total
units (sum of order_quantity) instead of the number of lines. Measured against order lines, cancelled + returned
lines are 15.86% (cancelled 14.22% + returned 1.64%). The chatbot uses the order-line basis.

## Charts in the notebook
Monthly revenue bar chart with the Q2 average line; average monthly order value bar + line;
top category revenue by month for Set, kurta, Western Dress and Top; sales by product size
(S, M and L highlighted as the best-selling sizes).
