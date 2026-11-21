# Concession tracker integration

Inspected `rujiraji/concession-stand-tracker` at commit `60bcd03787145a16ee5d0073bea36aabb224a982`. Authenticated `git ls-remote --symref` confirmed it was the tip of `main`, and the local checkout matched. The findings below describe code and a disposable database from that revision. Compatibility with a live tracker database and older deployed schemas remains unverified. [Tracker commit](https://github.com/rujiraji/concession-stand-tracker/tree/60bcd03787145a16ee5d0073bea36aabb224a982)

## Declared SQLite schema

The tracker initializes 17 application tables, with 19 declared foreign keys and no views. `branches`, `categories`, and `product_types` have text `code` primary keys. The remaining tables use integer `id` primary keys. The following table groups the declared foreign keys by function. [Schema and migrations](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/database.py)

| Area | Declared references |
| --- | --- |
| Events and sales | `sale_transactions.event_id` → `events.id`. `sale_transaction_items.transaction_id` → `sale_transactions.id` and `product_id` → `products.id`. |
| Catalog and recipes | `aliases.product_id` → `products.id`. `product_inventory_links` references `products` and `inventory_items`. |
| Inventory and consumption | `inventory_batches` references `inventory_items`. `inventory_usage` references `sale_transaction_items`, `inventory_items`, and optionally `inventory_batches`. |
| Manual adjustments | `inventory_adjustments` references `inventory_items` and optionally `inventory_batches`. `inventory_adjustment_usage` references `inventory_adjustments` and `inventory_batches`. The adjustment reference cascades on delete. |
| Spending and yield | `receipt_lines` references `receipts`, `inventory_items`, and optionally `inventory_batches`. `batch_yield_targets` references `inventory_batches` and `products`, both with delete cascades. |

The application validates `products.branch`, `category`, and `product_type` against managed options. Those joins are application conventions because the schema declares no foreign keys for them. Products have a case-insensitive unique index on the concatenation of branch and official code. `inventory_type`, payment labels, and sale status use text values governed by application code. [Product option validation](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/product_options.py) · [Sales input parser](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/parser.py)

## Verified reporting behavior

- **Money and historical costs.** Sale and receipt money fields store integer cents. Inventory quantities and batch unit costs use `REAL`, and the sale service uses `Decimal` for costing. Each line's total cost rounds half up to a cent. The service distributes rounding differences across its usage allocations by largest remainder. A line keeps its transaction-time price and rounded unit cost, plus its own revenue, total cost, and profit. The product report sums those saved line totals. Receipts can allocate tax into the costs of purchased batches, including a proportional share for partially tracked receipts. [Sale costing](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/sales.py) · [Receipt allocation](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/receipts.py)
- **Timestamps and event scope.** Sale and reversal timestamps are ISO 8601 UTC text with microseconds. Event creation uses UTC text with seconds. `events.start_date` and `end_date` are nullable text. The existing reports locate the active event and use its ID for scope. [Timestamp helper](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/utils/timestamps.py) · [Event service](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/events.py) · [Report service](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/reports.py)
- **Payments, free items, and waste.** The parser supports `cash`, `square`, `free`, and `waste`. Free and waste lines record zero revenue and still consume inventory and record cost. Event and payment summaries include completed free and waste transactions. The default product report includes free units and excludes waste. Explicit payment filters can change that scope. The waste report groups completed `waste` lines by product. [Sale behavior](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/sales.py) · [Report filters and aggregates](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/reports.py)
- **Reversals and stock.** Undo changes a `completed` sale to `reversed`, records `reversed_at`, and restores the quantities from its usage records to the original batches. The sale and its usage records remain in history. Existing sales reports filter to `completed`. For stock, the tracker sums remaining batch quantities excluding inactive batches. Sale consumption prefers open batches, then available batches, in batch ID order. Manual inventory adjustments have separate audit tables, and manually emptying a bulk batch is a separate operation from recording a `waste` sale. [Undo](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/undo.py) · [Inventory](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/inventory.py) · [Bulk yield service](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/app/services/yields.py)

## Verification data

Tracker initialization seeds the managed product options. A standalone transaction generator or simulator was absent from the inspected tree. Its `tests/test_reports.py` creates temporary databases with the app's own services and checks event, product, payment, and waste totals, reversal exclusion, active-event scope, and report filters. Database, inventory adjustment, receipt, recipe, yield, and undo tests cover other useful scenarios. [Tracker report tests](https://github.com/rujiraji/concession-stand-tracker/blob/60bcd03787145a16ee5d0073bea36aabb224a982/concession_tracker/tests/test_reports.py)

QueryBridge's standard suite uses a hand-authored concession fixture with four reporting tables, plus an unrelated library fixture. The separate compatibility check runs the pinned tracker initializer and sale services against a new temporary SQLite file. It confirms all 17 tables and 19 declared foreign keys are discoverable, compares the event totals against independent arithmetic and the tracker's own summary, and verifies that stock and source bytes match expectations after inspection and querying.

```sh
python -B -m tests.check_tracker ../concession-stand-tracker
```

This check expects a checkout of the inspected commit and uses the tracker's standard-library service modules. It generates its own transactions, including free, waste, and reversed sales. Expected results are four completed transactions, 900 cents revenue, 350 cents cost, 550 cents profit, and five remaining units.

## Open reporting questions

- Which timezone and day boundaries should calendar reports use when users ask for a date range?
- Should stakeholder reports keep free units in “units sold,” or label paid and free units separately?
- Should the waste metric include manual removals and bulk batch discards in addition to `waste` sales?
- Should Square reporting use recorded gross revenue or a separately defined net amount after fees and settlement adjustments?
