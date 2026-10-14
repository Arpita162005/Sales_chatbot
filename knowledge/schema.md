# Table schema: sales

## Table sales - overview
The cleaned DuckDB table is called `sales`. One row = one order line. There is only this one table
(plus a small `dataset_info` key/value table with build metadata). Text comparisons are case-sensitive in
DuckDB, so use the exact values listed in the "Column values" document, or compare with lower()/ILIKE.

## Columns: identifiers and dates
- `row_id` BIGINT - original row index from the CSV.
- `order_id` VARCHAR - Amazon order ID. One order can contain several lines, so count orders with COUNT(DISTINCT order_id).
- `order_date` DATE - order date (2022-04-01 to 2022-06-29). Use date_trunc('week', order_date) for weekly trends.
- `month_num` INTEGER - 4, 5 or 6. Always ORDER BY month_num for month order.
- `month_name` VARCHAR - 'April', 'May', 'June'.

## Columns: order status
- `ship_status` VARCHAR - detailed Amazon status, e.g. 'Shipped', 'Shipped - Delivered to Buyer', 'Cancelled',
  'Shipped - Returned to Seller', 'Pending'.
- `status_group` VARCHAR - simplified status: 'Shipped/Delivered', 'Cancelled', 'Returned', 'Pending'.
  'Cancelled' = Cancelled + Shipped - Lost in Transit. 'Returned' = Returned to Seller, Returning to Seller,
  Rejected by Buyer, Damaged. 'Pending' = Pending, Pending - Waiting for Pick Up, Shipping.
- `courier_ship_status` VARCHAR - courier status: 'Shipped', 'Cancelled', 'Unshipped', 'unknown'.

## Columns: fulfilment
- `fulfilment` VARCHAR - 'Amazon' (fulfilled by Amazon, FBA) or 'Merchant' (seller ships itself).
- `service_level` VARCHAR - 'Expedited' or 'Standard'.

## Columns: product
- `product_category` VARCHAR - 'Set', 'kurta' (lower-case k), 'Western Dress', 'Top', 'Ethnic Dress', 'Blouse',
  'Bottom', 'Saree', 'Dupatta'.
- `style` VARCHAR - style code, e.g. 'JNE3781'. `sku` VARCHAR - SKU code. `asin` VARCHAR - Amazon product ID.
- `size` VARCHAR - 'Free','XS','S','M','L','XL','XXL','3XL','4XL','5XL','6XL'.
- `size_order` INTEGER - 1..11 in the size order above. ORDER BY size_order to list sizes correctly.

## Columns: quantity and money
- `order_quantity` INTEGER - units on the line (0 for most cancelled lines).
- `order_amount` DOUBLE - line amount in INR (₹). Missing amounts were filled with 0.
  Revenue = SUM(order_amount).

## Columns: customer and geography
- `customer_type` VARCHAR - 'business' (B2B) or 'customer' (B2C / retail).
- `state` VARCHAR - cleaned ship-to state in UPPER CASE, e.g. 'MAHARASHTRA', 'KARNATAKA', 'ODISHA', 'DELHI'.
- `state_raw` VARCHAR - state exactly as in the raw file (mixed spellings).
- `city` VARCHAR - ship-to city in UPPER CASE, e.g. 'BENGALURU', 'HYDERABAD', 'MUMBAI', 'NEW DELHI', 'CHENNAI', 'PUNE'.
- `zip` VARCHAR - postal code.

## Columns: promotions
- `promotion` VARCHAR - comma-separated promotion IDs, or 'no promotion'.
- `has_promotion` BOOLEAN - true when at least one promotion was applied.
