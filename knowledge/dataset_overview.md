# Dataset overview

## What the data is
The dataset is the Amazon India "Amazon Sale Report" from Kaggle ("Unlock profits with e-commerce sales data").
It holds order lines of an apparel seller on Amazon.in for Q2 of financial year 2022 (April, May and June 2022).
Every row is one order line: one product (ASIN / SKU) inside one Amazon order. One order ID can have several lines.
All amounts are in Indian Rupees (INR, ₹). The sales channel is Amazon.in and the ship country is India for all rows.

## Business questions this assistant answers
Revenue and its trend by month or day, revenue and units by product category, size, style or SKU,
geography (state, city), fulfilment method (Amazon vs Merchant), service level (Expedited vs Standard),
customer type (business B2B vs customer B2C), promotions, average order value (AOV),
cancellations, returns and order status mix.

## Time period covered
Order dates run from 1 April 2022 to 29 June 2022. Only one day of March 2022 existed in the raw file,
so March rows were removed (same as the EDA notebook). Months available: April, May, June 2022.

## Products sold
Women's ethnic and western apparel. Categories: Set, kurta, Western Dress, Top, Ethnic Dress, Blouse,
Bottom, Saree, Dupatta. Sizes from Free and XS up to 6XL.

## Data source and licence note
Source: Kaggle dataset "thedevastator/unlock-profits-with-e-commerce-sales-data". The raw file name is
Amazon_Sale_Report_1.csv. The analysis notebook is "Forecast E-Commerce Sales Data Analysis".
