import json
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO

from .evaluate import CASES, EVENT_TOTALS, run


class EvaluationRegressionTests(unittest.TestCase):
    def test_known_wrong_decisions_fail_the_evaluator(self):
        cases = {case.id: case for case in CASES}
        bounds = ["2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z"]
        faults = (
            ("utc-boundary-revenue", "recorded Qwen 2.5 date SQL",
             "SELECT SUM(total_revenue_cents) FROM sale_transactions WHERE status = 'completed' "
             "AND created_at BETWEEN '2026-06-01T00:00:00Z' AND '2026-06-02T00:00:00Z'", []),
            ("utc-boundary-revenue", "recorded Qwen 3.5 date SQL",
             "SELECT SUM(st.total_revenue_cents) FROM sale_transactions st JOIN events e ON st.event_id = e.id "
             "WHERE st.status = 'completed' AND st.created_at >= '2026-06-01T00:00:00Z' "
             "AND st.created_at < '2026-06-02T00:00:00Z'", []),
            ("utc-boundary-revenue", "inclusive end after normalization",
             "SELECT sum(total_revenue_cents) FROM sale_transactions WHERE status = 'completed' "
             "AND qb_utc_us(created_at) BETWEEN qb_utc_us(?) AND qb_utc_us(?)", bounds),
            ("microsecond-window", "Julian-day precision loss",
             "SELECT id FROM sale_transactions WHERE event_id = 1 AND status = 'completed' "
             "AND julianday(created_at) >= julianday(?) AND julianday(created_at) < julianday(?) ORDER BY id",
             ["2026-09-01T16:00:00.000002Z", "2026-09-01T16:00:00.000004Z"]),
            ("water-revenue", "missing product filter",
             "SELECT sum(total_revenue_cents) FROM sale_transactions WHERE event_id = ? AND status = 'completed'", [1]),
            ("water-revenue", "product used as event name",
             "SELECT sum(t.total_revenue_cents) FROM sale_transactions t JOIN events e ON e.id = t.event_id "
             "WHERE e.name = ? AND t.status = 'completed'", ["Water"]),
            ("paid-units", "line count instead of quantities",
             "SELECT count(*) FROM sale_transaction_items i JOIN sale_transactions t ON t.id = i.transaction_id "
             "WHERE t.event_id = 1 AND t.status = 'completed' AND t.payment_type IN ('cash','square')", []),
            ("paid-units", "reversed paid units included",
             "SELECT sum(i.quantity) FROM sale_transaction_items i JOIN sale_transactions t ON t.id = i.transaction_id "
             "WHERE t.event_id = 1 AND t.payment_type IN ('cash','square')", []),
            ("inactive-event-totals", "active event substituted", EVENT_TOTALS, [1]),
            ("inactive-paid-units", "active flag substituted",
             "SELECT sum(i.quantity) FROM sale_transaction_items i JOIN sale_transactions t ON t.id = i.transaction_id "
             "JOIN events e ON e.id = t.event_id WHERE e.is_active = 1 AND t.status = 'completed' "
             "AND t.payment_type IN ('cash','square')", []),
            ("branch-copies", "embedded quotes removed",
             'SELECT b.label, sum(c.copies) FROM "Book catalog" c JOIN "Library branches" b '
             'ON c.district = b.district AND c.branch_code = b.code GROUP BY b.label ORDER BY b.label', []),
        )
        for identity, label, sql, parameters in faults:
            with self.subTest(fault=label):
                mutant = replace(cases[identity], offline_decision={"kind": "query", "sql": sql, "parameters": parameters})
                output = StringIO()
                with redirect_stdout(output):
                    correct = run(False, None, None, None, cases=(mutant,))
                self.assertFalse(correct, output.getvalue())
                records = [json.loads(line) for line in output.getvalue().splitlines() if line.startswith("{")]
                self.assertEqual(len(records), 1)
                self.assertFalse(records[0]["passed"])
                self.assertTrue(records[0]["source_preserved"])
