import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from querybridge import SQLiteConnector
from querybridge.connector import InvalidQueryError, QueryRejectedError

from .fixtures import create_utc_boundaries


class TimestampTests(unittest.TestCase):
    def setUp(self):
        self.database = Path(self.enterContext(TemporaryDirectory())) / "boundaries.sqlite3"
        create_utc_boundaries(self.database)
        self.connector = SQLiteConnector(self.database)

    def test_offset_forms_and_microseconds_use_exact_integers(self):
        for timestamp, expected in (
            ("1970-01-01T00:00:00Z", 0),
            ("1970-01-01 00:00+00:00", 0),
            ("1970-01-01T05:30:00+05:30", 0),
            ("1969-12-31T19:00:00-05:00", 0),
            ("1969-12-31T23:59:59.999999Z", -1),
            ("1970-01-01t00:00:00.123456z", 123456),
            ("1970-01-01T00:01Z", 60_000_000),
            ("0001-01-01T00:00:00Z", -62_135_596_800_000_000),
            ("9999-12-31T23:59:59.999999Z", 253_402_300_799_999_999),
        ):
            with self.subTest(timestamp=timestamp):
                result = self.connector.execute("SELECT qb_utc_us(?)", (timestamp,))
                self.assertEqual(result.rows, ((expected,),))

    def test_unsupported_values_return_null_without_guessing_timezone(self):
        for timestamp in (
            None, 0, b"1970-01-01T00:00:00Z", "now", "2026-06-01", "2026-06-01T00:00:00",
            "2026-02-30T00:00:00Z", "2026-06-01T24:00:00Z", "2026-06-01T00:00:60Z",
            "2026-06-01T00:00:00+24:00", "2026-06-01T00:00:00+00:60",
            "2026-06-01T00:00:00.1234567Z", "2026-06-01T00:00:00Z trailing",
        ):
            with self.subTest(timestamp=timestamp):
                self.assertEqual(self.connector.execute("SELECT qb_utc_us(?)", (timestamp,)).rows, ((None,),))

    def test_half_open_interval_keeps_start_and_last_microsecond(self):
        before = self.database.read_bytes()
        bounds = ("2026-06-01T00:00:00Z", "2026-06-02T00:00:00Z")
        clause = "status = 'completed' AND qb_utc_us(created_at) >= qb_utc_us(?) AND qb_utc_us(created_at) < qb_utc_us(?)"
        self.assertEqual(self.connector.execute(f"SELECT id FROM sale_transactions WHERE {clause} ORDER BY id", bounds).rows,
                         ((105,), (106,), (110,)))
        self.assertEqual(self.connector.execute(f"SELECT sum(total_revenue_cents) FROM sale_transactions WHERE {clause}", bounds).rows,
                         ((1100,),))
        self.assertEqual(self.database.read_bytes(), before)

    def test_adjacent_microseconds_do_not_collapse(self):
        bounds = ("2026-09-01T16:00:00.000002Z", "2026-09-01T16:00:00.000004Z")
        result = self.connector.execute(
            "SELECT id FROM sale_transactions WHERE event_id = 1 AND status = 'completed' "
            "AND qb_utc_us(created_at) >= qb_utc_us(?) AND qb_utc_us(created_at) < qb_utc_us(?) ORDER BY id", bounds,
        )
        self.assertEqual(result.rows, ((101,), (102,)))

    def test_helper_does_not_enable_functions_inside_untrusted_views(self):
        with closing(sqlite3.connect(self.database)) as writer:
            writer.execute("CREATE VIEW source_callback AS SELECT qb_utc_us(created_at) AS stamp FROM sale_transactions")
            writer.commit()
        view = next(relation for relation in self.connector.inspect().relations if relation.name == "source_callback")
        self.assertIsNotNone(view.unsupported_reason)
        with self.assertRaises((InvalidQueryError, QueryRejectedError)):
            self.connector.execute("SELECT * FROM source_callback")
