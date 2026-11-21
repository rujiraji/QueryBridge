# SQLite connector

`Connector` defines `inspect()` and `execute(sql, parameters)`. `SQLiteConnector` takes the path to a selected SQLite file and optional `QueryLimits`. The service returns typed rows, preserves `NULL` as `None`, and accepts positional or named bind parameters. Each operation opens its own read-only snapshot. The CLI is a thin JSON wrapper over the same methods.

## Schema discovery

Inspection reports ordinary tables and supported views in the `main` schema, with their stored `CREATE` SQL, column types and defaults, SQLite's `notnull` flags, ordered primary keys, and declared foreign keys. Foreign keys preserve the order of composite columns and include update and delete actions. A `null` referenced column denotes an implicit reference to the parent's primary key. Index and trigger definitions are returned when SQLite stores their SQL. Identifiers are quoted as SQLite identifiers when used in a trusted metadata statement. [SQLite catalog PRAGMAs](https://www.sqlite.org/pragma.html#pragma_table_list)

The fingerprint is SHA-256 over the ordered `type`, `name`, owner, and DDL entries in `main.sqlite_schema` with explicit SQL, excluding SQLite's internal entries. It changes with added, removed, or edited definitions and is stable across row updates and changes to file location. It reflects SQLite's stored definition text, so formatting changes can also change the fingerprint.

Both kinds of generated columns appear in the catalog and their expressions are available in table DDL. Stored generated columns are queryable. Virtual generated columns are metadata-only because SQLite evaluates their expressions while reading and skips function authorization for those expressions. Virtual and shadow tables and unresolved or restricted views carry an `unsupported_reason`. Internal catalog and statistics tables are excluded. [SQLite authorizer caveats](https://www.sqlite.org/c3ref/set_authorizer.html)

## Restricted execution

SQLite opens the selected file with `mode=ro`. The connector enables `query_only` and defensive mode, disables trusted schema and extension loading, and gives the connection zero attachment slots. A trusted catalog read supplies the relation list in the same transaction the query uses. The authorizer then permits SELECT and recursive CTE actions, reads of supported objects in `main`, reporting built-ins, and the exact timestamp helper `qb_utc_us`. SQLite checks view dependencies under the same policy. `execute` accepts a single statement through Python's `sqlite3` interface. [SQLite URI filenames](https://www.sqlite.org/uri.html) · [Python sqlite3 execution](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Cursor.execute)

The policy covers ordinary joins, filters, aggregates, subqueries, `VALUES`, and read-only CTEs. Use double quotes for identifiers and single quotes for string literals. This also applies to views as SQLite resolves them. The allowed functions are listed in `querybridge/sqlite.py`. Examples include `sum`, `count`, `round`, `coalesce`, `strftime`, `lower`, and `row_number`. SQLite version and build determine built-in availability. QueryBridge registers its own timestamp helper; other application-specific collations and functions are not loaded. The policy rejects writes, schema and transaction commands, PRAGMAs, attachments, extension loading, and direct access to virtual and internal objects. The connector exposes permitted columns in the selected file and has whole-file access scope.

## Exact UTC comparisons

`qb_utc_us(value)` returns integer microseconds since `1970-01-01T00:00:00Z`. It accepts offset-aware ISO date-times with `Z` or `+/-HH:MM`, a `T` or space separator, minute or second precision, and at most six fractional-second digits. Offset forms representing the same instant compare equally. Negative values represent instants before the epoch. Invalid text, missing offsets, unsupported precision, non-text values, and SQL `NULL` return `NULL`; the helper never assumes a local timezone. Comparisons involving those `NULL` values do not match a WHERE predicate.

For a caller-specified inclusive start and exclusive end, normalize both the column and the bound parameters:

```sql
WHERE qb_utc_us("created_at") >= qb_utc_us(:start_utc)
  AND qb_utc_us("created_at") < qb_utc_us(:end_utc)
```

This preserves adjacent microseconds and avoids comparing `Z` text with `+00:00` text. SQLite date functions have coarser fractional precision for these cases. The helper is registered on QueryBridge's connections, so exported SQL using it requires the same registration in another SQLite client. It does not install anything in the source file. It remains unavailable inside untrusted source views under `trusted_schema=OFF`. Normalizing a source column can require a scan; the existing SQLite deadline still applies. [SQLite date precision](https://www.sqlite.org/lang_datefunc.html) · [Python function registration](https://docs.python.org/3.12/library/sqlite3.html#sqlite3.Connection.create_function)

## Limits and results

The default deadline is 5 seconds, with a SQLite progress handler and elapsed-time checks during setup and retrieval. The connection reports lock contention immediately. SQLite's progress handler is cooperative and cannot impose a hard deadline on an individual native call or filesystem operation. SQLite limits SQL to 64 KiB and strings, BLOBs, and row encodings to at most 4 MiB during query execution. [SQLite progress callbacks](https://www.sqlite.org/c3ref/progress_handler.html) · [SQLite limits](https://www.sqlite.org/limits.html)

Results default to 1,000 rows and 1 MiB of compact JSON for the `columns` and `rows` payload. The byte count includes column names, separators, escaped text, and Base64 for BLOBs. The service preserves raw BLOBs as `bytes`. The CLI encodes BLOBs as `{"blob_base64":"…"}` and infinite REALs as `{"real":"inf"}` or `{"real":"-inf"}`. A result reports `row_limit` or `byte_limit` when a whole row is omitted. Headers that exceed the byte budget reject the query. The returned `sql` preserves the statement text and placeholders, with values bound separately.

The CLI returns `ok` or `empty` for successful queries. A single row containing `NULL` is a result row. Errors exit with status `1` and use `rejected_query`, `invalid_query`, `timeout`, or `database_error` on stderr. Argument errors exit with status `2`. Missing files fail before connection and existing zero-byte files are treated as empty SQLite databases.

QueryBridge stores no application state in the source database. SQLite can use WAL sidecars for read coordination, including creating `-wal` and `-shm` on a writable filesystem. For a live WAL database, keep the sidecars available. [SQLite read-only WAL databases](https://www.sqlite.org/wal.html#read_only_databases)

The natural-language pipeline provides the inspected fingerprint to `SQLiteConnector.execute`. The connector checks it inside the read snapshot before preparing the proposed statement. A mismatch returns `schema_changed`. Direct SQL keeps the existing interface and result shape. [Local pipeline](local-pipeline.md)
