# Normalizing an offer

Compare every offer as **INR, excluding GST, per the category's normalized unit**.
Keep full precision until the comparison.

| Currency | INR per unit |
|---|---|
| USD | 84.5 |
| EUR | 91 |
| GBP | 106 |

Any other currency is missing information. A supplier contract rate (see `suppliers/`)
replaces this table for that supplier and currency.

- **GST:** a GST-inclusive price is divided by (1 + the category's GST rate).
- **Billing period:** monthly ↔ yearly is ×/÷ 12. A subscription price with no period is missing information.
- **Totals:** "₹X in total for all N units" → X ÷ N.
- **List-price discounts:** "D% off our list price of ₹X" → X × (1 − D/100).
- **Tiered prices:** use the tier that matches the thread's quantity.
- **Volume discounts:** apply only if the thread's quantity meets the threshold.
- **Packs and sub-units:** see the category page. A dozen is 12 units.
- **Notation:** ₹1.2 lakh = ₹1,20,000; ₹18.5k = ₹18,500; amounts may be written in words.
- **Threads:** only the supplier's latest offer counts. A correction replaces the price it
  corrects; "D% off our previous quote" applies to the supplier's own previous quote.
