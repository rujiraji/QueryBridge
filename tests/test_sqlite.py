import json
import sqlite3
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType

from querybridge import QueryLimits, SQLiteConnector
from querybridge.connector import DatabaseError, InvalidQueryError, QueryRejectedError, QueryTimeoutError

from .fixtures import create_library


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(TemporaryDirectory())
        self.database = Path(temporary) / "library #100% café.sqlite3"
        create_library(self.database)
        self.connector = SQLiteConnector(self.database)

    def test_inspection_describes_declarations(self):
        schema = self.connector.inspect()
        relations = {relation.name: relation for relation in schema.relations}
        books = relations['Book "catalog"']
        self.assertEqual(books.primary_key, ("id",))
        self.assertTrue(books.strict)
        columns = {column.name: column for column in books.columns}
        self.assertEqual(columns["shelf_note"].default_sql, "'on shelf'")
        self.assertFalse(columns["shelf_note"].not_null)
        self.assertEqual(columns["copies"].declared_type, "INTEGER")
        self.assertTrue(columns["copies"].not_null)
        self.assertEqual(columns["stored_total"].kind, "stored_generated")
        self.assertEqual(columns["virtual_total"].kind, "virtual_generated")
        self.assertIn("copies * 3", books.definition_sql)
        self.assertEqual(len(books.foreign_keys), 1)
        key = books.foreign_keys[0]
        self.assertEqual(key.columns, ("district", "branch_code"))
        self.assertEqual(key.referenced_table, "Library branches")
        self.assertEqual(key.referenced_columns, (None, None))  # Implicit parent primary key.
        self.assertEqual((key.on_update, key.on_delete), ("CASCADE", "RESTRICT"))
        branches = relations["Library branches"]
        self.assertEqual(branches.primary_key, ("district", "code"))
        self.assertTrue(branches.without_rowid)
        self.assertEqual(
            {item.name for item in schema.indexes_and_triggers}, {"Book titles", "Book check"},
        )

    def test_view_metadata_and_policy(self):
        views = {relation.name: relation for relation in self.connector.inspect().relations
                 if relation.kind == "view"}
        availability = views["Book availability"]
        self.assertIsNone(availability.unsupported_reason)
        self.assertEqual([column.name for column in availability.columns],
                         ["id", "title", "copies", "stored_total"])
        self.assertIn("CREATE VIEW", availability.definition_sql)
        self.assertIsNotNone(views["Broken view"].unsupported_reason)
        self.assertEqual(self.connector.execute('SELECT count(*) FROM "book AVAILABILITY"').rows, ((3,),))
        for name in ("Catalog internals", "Random view", "Virtual copies"):
            with self.subTest(name=name):
                self.assertIsNotNone(views[name].unsupported_reason)
                with self.assertRaises(QueryRejectedError):
                    self.connector.execute(f'SELECT * FROM "{name}"')

    def test_fingerprint_depends_on_ddl_and_excludes_rows(self):
        original = self.connector.inspect().fingerprint
        self.assertRegex(original, r"^[0-9a-f]{64}$")
        equivalent = self.database.with_name("other.sqlite3")
        create_library(equivalent)
        self.assertEqual(SQLiteConnector(equivalent).inspect().fingerprint, original)
        with closing(sqlite3.connect(self.database, autocommit=True)) as writer:
            writer.execute('UPDATE "Book ""catalog""" SET copies = 3 WHERE id = 101')
            self.assertEqual(self.connector.inspect().fingerprint, original)
            writer.execute('DROP INDEX "Book titles"')
        self.assertNotEqual(self.connector.inspect().fingerprint, original)

    def test_virtual_and_shadow_tables_are_reported_and_restricted(self):
        with closing(sqlite3.connect(self.database, autocommit=True)) as writer:
            try:
                writer.execute("CREATE VIRTUAL TABLE search_index USING fts5(body)")
            except sqlite3.OperationalError as error:
                if "no such module: fts5" in str(error):
                    self.skipTest("SQLite build lacks FTS5")
                raise
        by_name = {relation.name: relation for relation in self.connector.inspect().relations}
        self.assertEqual(by_name["search_index"].kind, "virtual")
        self.assertEqual(by_name["search_index_content"].kind, "shadow")
        for name in ("search_index", "search_index_content"):
            with self.subTest(name=name):
                self.assertIsNotNone(by_name[name].unsupported_reason)
                with self.assertRaises(QueryRejectedError):
                    self.connector.execute(f"SELECT count(*) FROM {name}")

    def test_quoted_identifiers_sql_literals_comments_and_nulls(self):
        sql = '''-- ATTACH remaining text
            SELECT title, shelf_note, stored_total FROM "BOOK ""catalog"""
            WHERE title = 'SELECT; DROP TABLE'; /* UPDATE and ; are text here */'''
        result = self.connector.execute(sql)
        self.assertEqual(result.sql, sql)
        self.assertEqual(result.columns, ("title", "shelf_note", "stored_total"))
        self.assertEqual(result.rows, (("SELECT; DROP TABLE", None, 4),))
        self.assertFalse(result.truncated)

    def test_parameter_binding_and_valid_empty_result(self):
        self.assertEqual(
            self.connector.execute('SELECT copies FROM "Book ""catalog""" WHERE id = ?', (103,)).rows,
            ((5,),),
        )
        lookup = 'SELECT title FROM "Book ""catalog""" WHERE title = :title'
        self.assertEqual(self.connector.execute(lookup, MappingProxyType({"title": "Tree guide"})).rows,
                         (("Tree guide",),))
        empty = self.connector.execute(lookup, {"title": "' OR 1=1; --"})
        self.assertEqual(empty.columns, ("title",))
        self.assertEqual(empty.rows, ())
        self.assertTrue(empty.is_empty)
        self.assertFalse(empty.truncated)

    def test_join_nested_query_and_read_only_cte(self):
        result = self.connector.execute('''
            WITH stocked AS (SELECT * FROM "Book availability" WHERE copies > ?)
            SELECT b.label, SUM(s.copies),
                   (SELECT count(*) FROM "Book ""catalog""" AS all_books
                    WHERE all_books.district = b.district)
            FROM stocked AS s
            JOIN "Book ""catalog""" AS c ON c.id = s.id
            JOIN "Library branches" AS b ON (b.district, b.code) = (c.district, c.branch_code)
            GROUP BY b.district, b.code ORDER BY b.label
        ''', (1,))
        self.assertEqual(result.rows, (("North", 2, 2), ("South", 5, 2)))

    def test_finite_recursive_cte_and_values(self):
        recursive = self.connector.execute("""
            WITH RECURSIVE numbers(n) AS (VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n<5)
            SELECT sum(n) FROM numbers
        """)
        self.assertEqual(recursive.rows, ((15,),))
        self.assertEqual(self.connector.execute("VALUES (1, NULL), (2, 'value')").rows,
                         ((1, None), (2, "value")))

    def test_rejects_writes_schema_changes_and_connection_commands(self):
        for sql in (
            'DELETE FROM "Book ""catalog""" RETURNING id',
            'UPDATE "Book ""catalog""" SET copies = 0',
            'INSERT INTO "Library branches" VALUES (\'E\', \'MAIN\', \'East\')',
            'WITH ids AS (SELECT 101) DELETE FROM "Book ""catalog""" WHERE id IN ids',
            'DROP TABLE "Book ""catalog"""', 'CREATE TABLE elsewhere (x)',
            'CREATE TEMP TABLE scratch (x)', 'BEGIN', 'PRAGMA table_list', 'PRAGMA query_only = OFF',
            'EXPLAIN PRAGMA trusted_schema = ON', 'PRAGMA nonexistent_querybridge_setting',
            "ATTACH DATABASE ':memory:' AS aux", 'DETACH DATABASE main', 'VACUUM', 'ANALYZE', 'REINDEX',
        ):
            with self.subTest(sql=sql), self.assertRaises(QueryRejectedError):
                self.connector.execute(sql)

    def test_prohibited_commands_do_not_create_destination_files(self):
        destination = self.database.with_name("aux.sqlite3")
        for sql in ("ATTACH DATABASE ? AS aux", "VACUUM INTO ?"):
            with self.subTest(sql=sql):
                with self.assertRaises(QueryRejectedError):
                    self.connector.execute(sql, (str(destination),))
                self.assertFalse(destination.exists())

    def test_rejects_multiple_statements_and_policy_restricted_reads(self):
        for sql in (
            "SELECT 1; /* comment */ SELECT 2", "SELECT 1; PRAGMA query_only = OFF",
            "SELECT name FROM sqlite_schema", "SELECT count(*) FROM SQLITE_MASTER",
            "SELECT * FROM pragma_table_info('Library branches')", "SELECT RANDOM()",
            'SELECT virtual_total FROM "Book ""catalog"""',
        ):
            with self.subTest(sql=sql), self.assertRaises(QueryRejectedError):
                self.connector.execute(sql)
        # Builds compiled without extension loading can reject it at name resolution.
        with self.assertRaises((QueryRejectedError, InvalidQueryError)):
            self.connector.execute("SELECT load_extension('placeholder')")

    def test_invalid_queries_are_distinct_from_rejections(self):
        for sql in (
            "SELECT FROM missing", "SELECT 1 FROM missing", "/* comment */", "SELECT ?",
            'SELECT "misspelled column" FROM "Library branches"',
        ):
            with self.subTest(sql=sql), self.assertRaises(InvalidQueryError):
                self.connector.execute(sql)

    def test_row_limit_has_exact_truncation(self):
        limited = SQLiteConnector(self.database, QueryLimits(max_rows=2))
        sample = 'SELECT id FROM "Book ""catalog""" ORDER BY id'
        result = limited.execute(sample)
        self.assertEqual(result.rows, ((101,), (102,)))
        self.assertTrue(result.truncated)
        self.assertEqual(result.truncation_reason, "row_limit")
        self.assertFalse(limited.execute(sample + " LIMIT 2").truncated)

    def test_result_byte_limit_accounts_for_columns_rows_and_encoding(self):
        # ASCII JSON encoding represents é with six bytes and the BLOB as Base64.
        encoded_first = b'{"columns":["value","blob"],"rows":[["\\u00e9",{"blob_base64":"AAE="}]]}'
        sample = "SELECT 'é' AS value, X'0001' AS blob UNION ALL SELECT 'x', NULL"
        limited = SQLiteConnector(self.database, QueryLimits(max_result_bytes=len(encoded_first)))
        result = limited.execute(sample)
        self.assertEqual(result.rows, (("é", b"\x00\x01"),))
        self.assertEqual(result.result_bytes, len(encoded_first))
        self.assertEqual(result.truncation_reason, "byte_limit")

        header_only = SQLiteConnector(self.database, QueryLimits(max_result_bytes=33))
        omitted = header_only.execute("SELECT 'a long value' AS value")
        self.assertEqual(omitted.rows, ())
        self.assertFalse(omitted.is_empty)
        self.assertEqual(omitted.truncation_reason, "byte_limit")
        with self.assertRaises(QueryRejectedError):
            header_only.execute('SELECT 1 AS "a long result column name"')

    def test_sql_and_value_size_guards(self):
        with self.assertRaises(QueryRejectedError):
            self.connector.execute("/*" + " " * 65_536 + "*/ SELECT 1")
        with self.assertRaises(QueryRejectedError):
            self.connector.execute("SELECT ?", (b"x" * (4 * 1024 * 1024 + 1),))

    def test_execution_deadline(self):
        limited = SQLiteConnector(self.database, QueryLimits(timeout_seconds=0.05))
        with self.assertRaises(QueryTimeoutError):
            limited.execute("""
                WITH RECURSIVE numbers(n) AS (
                    VALUES(1) UNION ALL SELECT n+1 FROM numbers WHERE n<1000000000
                ) SELECT sum(n) FROM numbers
            """)
        self.assertEqual(self.connector.execute("SELECT 1").rows, ((1,),))

    def test_source_preservation(self):
        original = self.database.read_bytes()
        self.connector.inspect()
        self.assertEqual(self.database.read_bytes(), original)
        self.connector.execute('SELECT id FROM "Book ""catalog"""')
        self.assertEqual(self.database.read_bytes(), original)
        with self.assertRaises(QueryRejectedError):
            self.connector.execute('DELETE FROM "Book ""catalog"""')
        self.assertEqual(self.database.read_bytes(), original)
        self.assertEqual(set(self.database.parent.iterdir()), {self.database})

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-B", "-m", "querybridge", *args],
            capture_output=True, text=True, timeout=10, check=False,
        )

    def test_cli_inspection_and_parameterized_query(self):
        inspection = self.run_cli("inspect", str(self.database))
        self.assertEqual(inspection.returncode, 0, inspection.stderr)
        self.assertEqual(json.loads(inspection.stdout)["fingerprint"], self.connector.inspect().fingerprint)
        sql = "SELECT :name AS value, X'FF' AS bytes, 1e999 AS huge"
        query = self.run_cli("query", str(self.database), sql, "--params", '{"name":"reader"}')
        self.assertEqual(query.returncode, 0, query.stderr)
        payload = json.loads(query.stdout)
        self.assertEqual(payload["sql"], sql)
        self.assertEqual(payload["rows"], [["reader", {"blob_base64": "/w=="}, {"real": "inf"}]])
        self.assertEqual(payload["status"], "ok")

    def test_cli_empty_invalid_and_rejected_statuses(self):
        cases = (
            ("SELECT 1 WHERE 0", 0, "empty"),
            ("SELECT FROM", 1, "invalid_query"),
            ("PRAGMA query_only=OFF", 1, "rejected_query"),
        )
        for sql, code, status in cases:
            with self.subTest(status=status):
                output = self.run_cli("query", str(self.database), sql)
                self.assertEqual(output.returncode, code, output.stderr)
                self.assertEqual(json.loads(output.stderr if code else output.stdout)["status"], status)


class DatabaseFileTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(TemporaryDirectory()))

    def test_missing_file_is_never_created(self):
        missing = self.directory / "missing.db"
        connector = SQLiteConnector(missing)
        with self.assertRaises(DatabaseError):
            connector.inspect()
        with self.assertRaises(DatabaseError):
            connector.execute("SELECT 1")
        self.assertFalse(missing.exists())

    def test_invalid_file_and_directory_are_not_databases(self):
        corrupt = self.directory / "invalid.sqlite"
        corrupt.write_bytes(b"This is not a SQLite database.\n" * 32)
        for path in (corrupt, self.directory):
            with self.subTest(path=path):
                connector = SQLiteConnector(path)
                with self.assertRaises(DatabaseError):
                    connector.inspect()
                with self.assertRaises(DatabaseError):
                    connector.execute("SELECT 1")

    def test_existing_empty_sqlite_file_has_empty_schema(self):
        empty = self.directory / "empty.db"
        empty.touch()
        self.assertEqual(SQLiteConnector(empty).inspect().relations, ())
        self.assertEqual(empty.read_bytes(), b"")

    def test_wal_reads_include_committed_frames_and_preserve_data(self):
        path = self.directory / "wal.sqlite"
        with closing(sqlite3.connect(path, autocommit=True)) as writer:
            self.assertEqual(writer.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
            writer.execute("PRAGMA wal_autocheckpoint = 0")
            writer.execute("CREATE TABLE pending_frames (amount INTEGER)")
            writer.execute("INSERT INTO pending_frames VALUES (42)")
            wal = path.with_name(path.name + "-wal")
            before = (path.read_bytes(), wal.read_bytes())
            connector = SQLiteConnector(path)
            self.assertEqual(connector.inspect().relations[0].name, "pending_frames")
            self.assertEqual(connector.execute("SELECT amount FROM pending_frames").rows, ((42,),))
            self.assertEqual((path.read_bytes(), wal.read_bytes()), before)
