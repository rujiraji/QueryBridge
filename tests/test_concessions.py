import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from querybridge import SQLiteConnector

from .fixtures import create_concessions


class SyntheticConcessionTests(unittest.TestCase):
    def setUp(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        database = directory / "concessions.sqlite3"
        create_concessions(database)
        self.connector = SQLiteConnector(database)

    def test_event_totals_include_free_and_waste_costs(self):
        result = self.connector.execute("""
            SELECT e.name, COUNT(*) AS transactions, SUM(t.total_revenue_cents) AS revenue,
                   SUM(t.total_cost_cents) AS cost, SUM(t.total_profit_cents) AS profit
            FROM events AS e JOIN sale_transactions AS t ON t.event_id = e.id
            WHERE e.id = ? AND t.status = 'completed'
            GROUP BY e.id
        """, (1,))
        # Two paid transactions brought in 400 + 300. The four costs are 120, 90, 60 and 180.
        self.assertEqual(result.rows, (("Fall fair", 4, 700, 450, 250),))

    def test_product_totals_exclude_waste_and_use_saved_values(self):
        result = self.connector.execute("""
            WITH included AS (
                SELECT id FROM sale_transactions
                WHERE event_id = :event AND status = 'completed' AND payment_type <> 'waste'
            )
            SELECT p.name, SUM(i.quantity), SUM(i.revenue_cents), SUM(i.cost_cents), SUM(i.profit_cents)
            FROM sale_transaction_items AS i JOIN products AS p ON p.id = i.product_id
            WHERE i.transaction_id IN (SELECT id FROM included)
            GROUP BY p.id ORDER BY p.name
        """, {"event": 1})
        # The 3 water units include one free unit and two paid at 200 each.
        self.assertEqual(result.rows, (("Popcorn", 1, 300, 90, 210), ("Water", 3, 400, 180, 220)))

    def test_payment_and_waste_totals_use_completed_sales(self):
        breakdown = self.connector.execute("""
            SELECT payment_type, COUNT(*), SUM(total_revenue_cents), SUM(total_cost_cents)
            FROM sale_transactions WHERE event_id = 1 AND status = 'completed'
            GROUP BY payment_type ORDER BY payment_type
        """)
        self.assertEqual(breakdown.rows, (
            ("cash", 1, 400, 120), ("free", 1, 0, 60), ("square", 1, 300, 90), ("waste", 1, 0, 180),
        ))
        waste = self.connector.execute("""
            SELECT p.name, SUM(i.quantity), SUM(i.cost_cents)
            FROM sale_transaction_items AS i JOIN products AS p ON p.id = i.product_id
            JOIN sale_transactions AS t ON t.id = i.transaction_id
            WHERE t.event_id = 1 AND t.status = 'completed' AND t.payment_type = 'waste'
            GROUP BY p.id
        """)
        self.assertEqual(waste.rows, (("Popcorn", 2, 180),))
