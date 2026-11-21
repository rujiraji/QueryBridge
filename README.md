# QueryBridge

QueryBridge, formerly Usense, lets users ask questions about relational data. The SQLite foundation and experimental local query pipeline have been merged after independent review. Accuracy work adds exact UTC comparisons, quoted schema identifiers, and stronger evaluation fixtures. The evaluation record includes live model results and remaining reporting errors. [Evaluation record](docs/evaluation.md)

## Run locally

Use Python 3.12 or newer with SQLite 3.37 or newer. From the repository root, run `python -m querybridge --help`. Installing with `python -m pip install .` provides the `querybridge` command and the standard-library SQLite workflows.

For the local model and retrieval workflows, install `python -m pip install ".[nl]"`. Use `ready` to check Ollama and ChromaDB, `index` to prepare a database, and `ask` to run a question through the restricted SQLite connector. [Setup and complete example](docs/local-pipeline.md)

## Verify Phase 1

Run the SQLite tests with `python -B -m unittest tests.test_sqlite tests.test_concessions tests.test_temporal -v`. With the `nl` extra installed, `python -B -m unittest discover -v` also covers the local pipeline and regression replays. `python -B -m tests.evaluate` runs 21 scripted cases; add `--case utc-boundary-revenue` to run a selected case. [Evaluation results](docs/evaluation.md)

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
