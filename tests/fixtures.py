"""Build deterministic, disposable SQLite databases for tests and the CLI demo."""

import argparse
import sqlite3
from contextlib import closing
from pathlib import Path


def create_concessions(path: Path) -> None:
    _create(path, """
        CREATE TABLE events (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            start_date TEXT, end_date TEXT, notes TEXT,
            is_active INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE TABLE products (
            id INTEGER PRIMARY KEY, official_code TEXT NOT NULL, name TEXT NOT NULL,
            branch TEXT NOT NULL, category TEXT NOT NULL, product_type TEXT NOT NULL,
            current_price_cents INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE sale_transactions (
            id INTEGER PRIMARY KEY, event_id INTEGER NOT NULL REFERENCES events(id),
            payment_type TEXT NOT NULL, total_revenue_cents INTEGER NOT NULL,
            total_cost_cents INTEGER NOT NULL, total_profit_cents INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'completed', created_at TEXT NOT NULL,
            reversed_at TEXT
        );
        CREATE TABLE sale_transaction_items (
            id INTEGER PRIMARY KEY,
            transaction_id INTEGER NOT NULL REFERENCES sale_transactions(id),
            product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL,
            price_at_time_cents INTEGER NOT NULL, cost_at_time_cents INTEGER NOT NULL,
            revenue_cents INTEGER NOT NULL, cost_cents INTEGER NOT NULL, profit_cents INTEGER NOT NULL
        );
        INSERT INTO events (id, name, is_active, created_at) VALUES
            (1, 'Fall fair', 1, '2026-09-01T00:00:00+00:00'),
            (2, 'Summer fair', 0, '2026-06-01T00:00:00+00:00');
        INSERT INTO products VALUES
            (10, 'water', 'Water', 'wa', 'drinks', 'packaged_each', 250, 1, '2026-06-01T00:00:00+00:00'),
            (20, 'corn', 'Popcorn', 'fo', 'food', 'bulk_serving', 300, 1, '2026-06-01T00:00:00+00:00');
        INSERT INTO sale_transactions VALUES
            (100, 1, 'cash',   400, 120,  280, 'completed', '2026-09-01T16:00:00.000001+00:00', NULL),
            (101, 1, 'square', 300,  90,  210, 'completed', '2026-09-01T16:00:00.000002+00:00', NULL),
            (102, 1, 'free',     0,  60,  -60, 'completed', '2026-09-01T16:00:00.000003+00:00', NULL),
            (103, 1, 'waste',    0, 180, -180, 'completed', '2026-09-01T16:00:00.000004+00:00', NULL),
            (104, 1, 'cash',   200,  60,  140, 'reversed',  '2026-09-01T16:00:00.000005+00:00',
              '2026-09-01T16:00:00.000006+00:00'),
            (105, 2, 'square', 600, 180,  420, 'completed', '2026-06-01T16:00:00.000001+00:00', NULL);
        INSERT INTO sale_transaction_items VALUES
            (1, 100, 10, 2, 200, 60, 400, 120,  280),
            (2, 101, 20, 1, 300, 90, 300,  90,  210),
            (3, 102, 10, 1, 200, 60,   0,  60,  -60),
            (4, 103, 20, 2, 300, 90,   0, 180, -180),
            (5, 104, 10, 1, 200, 60, 200,  60,  140),
            (6, 105, 20, 2, 300, 90, 600, 180,  420);
    """)


def create_library(path: Path) -> None:
    _create(path, '''
        CREATE TABLE "Library branches" (
            district TEXT, code TEXT, label TEXT NOT NULL UNIQUE,
            PRIMARY KEY (district, code)
        ) WITHOUT ROWID, STRICT;
        CREATE TABLE "Book ""catalog""" (
            id INTEGER PRIMARY KEY,
            district TEXT NOT NULL,
            branch_code TEXT NOT NULL,
            title TEXT NOT NULL,
            copies INTEGER NOT NULL CHECK (copies >= 0),
            shelf_note TEXT DEFAULT 'on shelf',
            stored_total INTEGER GENERATED ALWAYS AS (copies * 2) STORED,
            virtual_total INTEGER GENERATED ALWAYS AS (copies * 3) VIRTUAL,
            FOREIGN KEY (district, branch_code) REFERENCES "Library branches"
                ON UPDATE CASCADE ON DELETE RESTRICT
        ) STRICT;
        CREATE INDEX "Book titles" ON "Book ""catalog""" (title);
        CREATE TRIGGER "Book check" AFTER UPDATE ON "Book ""catalog"""
            BEGIN SELECT 1; END;
        CREATE VIEW "Book availability" AS
            SELECT id, title, copies, stored_total FROM "Book ""catalog""" WHERE copies > 0;
        CREATE VIEW "Broken view" AS SELECT missing_column FROM "Book ""catalog""";
        CREATE VIEW "Catalog internals" AS SELECT name FROM main.sqlite_schema;
        CREATE VIEW "Random view" AS SELECT random() AS n;
        CREATE VIEW "Virtual copies" AS SELECT virtual_total FROM "Book ""catalog""";
        INSERT INTO "Library branches" VALUES ('N', 'MAIN', 'North'), ('S', 'MAIN', 'South');
        INSERT INTO "Book ""catalog""" (id, district, branch_code, title, copies, shelf_note) VALUES
            (101, 'N', 'MAIN', 'SELECT; DROP TABLE', 2, NULL),
            (103, 'S', 'MAIN', 'Café at dusk', 5, 'reserved'),
            (104, 'S', 'MAIN', 'Tree guide', 1, 'ready');
        INSERT INTO "Book ""catalog""" (id, district, branch_code, title, copies)
            VALUES (102, 'N', 'MAIN', 'Mapping downtown', 0);
    ''')


def _create(path: Path, sql: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(path)) as connection:
            connection.executescript(f"PRAGMA foreign_keys = ON; BEGIN; {sql} COMMIT;")
    except Exception:
        path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", choices=("concessions", "library"))
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    creators = {"concessions": create_concessions, "library": create_library}
    creators[args.fixture](args.path)
