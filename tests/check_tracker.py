"""Opt-in compatibility check using a disposable database built by the inspected tracker commit."""

import argparse
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from querybridge import SQLiteConnector

TRACKER_COMMIT = "60bcd03787145a16ee5d0073bea36aabb224a982"


def check_tracker(checkout: Path) -> None:
    git = ["git", "--no-optional-locks", "-C", str(checkout)]
    head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    if head != TRACKER_COMMIT:
        raise SystemExit(f"Expected tracker commit {TRACKER_COMMIT}. Found {head}.")
    changes = subprocess.check_output(
        [*git, "status", "--porcelain", "--untracked-files=all", "--", "concession_tracker/app"],
        text=True,
    )
    if changes.strip():
        raise SystemExit("Use an unmodified checkout of the tracker application for this check.")

    sys.dont_write_bytecode = True
    sys.path.insert(0, str(checkout / "concession_tracker"))
    from app.database import init_db
    from app.parser import parse_sale
    from app.services.events import create_event, use_event
    from app.services.inventory import create_batch, create_inventory_item, create_product_link
    from app.services.products import create_product
    from app.services.reports import get_event_summary
    from app.services.sales import create_sale
    from app.services.undo import undo_last_transaction

    verify = TestCase()
    with TemporaryDirectory() as directory:
        database = Path(directory) / "tracker.sqlite3"
        init_db(database)
        event = create_event("Compatibility check", database)
        use_event(event.name, database)
        create_product("demo", "Demo water", "wa", "drinks", "packaged_each", 300, database)
        create_inventory_item("Demo bottles", "each", "packaged_each", database)
        create_batch("Demo bottles", "Lot", Decimal(10), "each", 700, database)
        create_product_link("wademo", "Demo bottles", Decimal(1), "each", database)
        for sale in ("wademo 2 /c", "wademo /s", "wademo /f", "wademo /w", "wademo /c"):
            create_sale(parse_sale(sale, database), database)
        undo_last_transaction(database)

        before = database.read_bytes()
        connector = SQLiteConnector(database)
        schema = connector.inspect()
        verify.assertEqual(len(schema.relations), 17)
        verify.assertEqual(sum(len(relation.foreign_keys) for relation in schema.relations), 19)
        verify.assertTrue(all(relation.kind == "table" and relation.unsupported_reason is None
                              for relation in schema.relations))
        totals = connector.execute("""
            SELECT count(*), sum(total_revenue_cents), sum(total_cost_cents), sum(total_profit_cents),
                   sum(CASE WHEN payment_type = 'cash' THEN total_revenue_cents ELSE 0 END),
                   sum(CASE WHEN payment_type = 'square' THEN total_revenue_cents ELSE 0 END),
                   sum(CASE WHEN payment_type = 'free' THEN total_cost_cents ELSE 0 END),
                   sum(CASE WHEN payment_type = 'waste' THEN total_cost_cents ELSE 0 END)
            FROM sale_transactions WHERE event_id = ? AND status = 'completed'
        """, (event.id,))
        # Ten units cost 70 cents each. Five units (three paid) remain consumed after undo.
        expected = (4, 900, 350, 550, 600, 300, 70, 70)
        summary = get_event_summary(database)
        verify.assertEqual((
            summary.transaction_count, summary.total_revenue_cents, summary.total_cost_cents,
            summary.total_profit_cents, summary.cash_revenue_cents, summary.square_revenue_cents,
            summary.free_item_cost_cents, summary.waste_item_cost_cents,
        ), expected)
        verify.assertEqual(totals.rows, (expected,))
        stock = connector.execute("SELECT remaining_quantity FROM inventory_batches")
        verify.assertEqual(stock.rows, ((5.0,),))
        verify.assertEqual(database.read_bytes(), before)

    print(f"Tracker {head} matched 17 tables, 19 declared foreign keys, {expected}, and 5 units left.")
    print("Disposable database bytes were identical after inspection and queries.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    args = parser.parse_args()
    check_tracker(args.checkout.resolve(strict=True))
