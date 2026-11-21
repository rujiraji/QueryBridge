# QueryBridge

QueryBridge, formerly Usense, is a project for asking natural-language questions about relational data. Phase 1 implements the SQLite foundation and is awaiting independent review. Natural-language queries, the React interface, and report generation are planned.

## Run locally

Use Python 3.12 or newer with SQLite 3.37 or newer. The connector and CLI use the Python standard library. From the repository root, run `python -m querybridge --help`. Installing the project with `python -m pip install .` also provides the `querybridge` command.

## Verify Phase 1

Run the synthetic test suite with `python -B -m unittest discover -v`.

Create a disposable concession fixture at a fresh path and inspect it. The inspection lists the four fixture tables and their declared keys, with a schema fingerprint.

```sh
python -B -m tests.fixtures concessions .querybridge-data/demo.sqlite3
python -B -m querybridge inspect .querybridge-data/demo.sqlite3
python -B -m querybridge query .querybridge-data/demo.sqlite3 "SELECT count(*) AS transactions, sum(total_revenue_cents) AS revenue, sum(total_cost_cents) AS cost, sum(total_profit_cents) AS profit FROM sale_transactions WHERE event_id = ? AND status = 'completed'" --params '[1]'
```

The query returns `[[4,700,450,250]]` without truncation. The money values are integer cents. The synthetic fixture also includes a reversed sale and a sale for another event.

```sh
python -B -m querybridge query .querybridge-data/demo.sqlite3 "SELECT payment_type FROM sale_transactions ORDER BY id" --max-rows 2
python -B -m querybridge query .querybridge-data/demo.sqlite3 "PRAGMA query_only = OFF"
```

The first command returns `[["cash"],["square"]]` with `truncation_reason` set to `row_limit`. The second exits with status `1` and emits `rejected_query` on stderr.

The test suite runs with synthetic fixtures. The optional upstream check and its findings are documented separately. [SQLite connector contract](docs/sqlite.md) · [Concession tracker findings](docs/concession-tracker.md) · [Phase roadmap](docs/roadmap.md)
