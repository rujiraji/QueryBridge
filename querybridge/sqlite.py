"""SQLite schema discovery and execution on short-lived read-only snapshots."""

import hashlib
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import asdict
from itertools import groupby
from os import PathLike
from pathlib import Path
from stat import S_ISREG
from time import monotonic

from ._json import encode_json, json_row
from .temporal import utc_microseconds
from .connector import (
    Column,
    DatabaseError,
    DatabaseSchema,
    Definition,
    ForeignKey,
    InvalidQueryError,
    Parameters,
    QueryLimits,
    QueryRejectedError,
    QueryResult,
    QueryTimeoutError,
    Relation,
    Scalar,
    SchemaChangedError,
    Truncation,
)

_MAX_SQL_BYTES = 65_536
_MAX_SQLITE_BYTES = 4 * 1024 * 1024
_RELATION_TYPES = {"table", "view"}
_COLUMN_KINDS = {0: "ordinary", 1: "hidden", 2: "virtual_generated", 3: "stored_generated"}
_SQLITE_CASE_MAP = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")

# These built-ins cover typical reporting queries. SQLite also requires its
# own trusted-schema checks to pass when a function is invoked from a view.
_BUILTIN_FUNCTIONS = frozenset({
    "abs", "avg", "coalesce", "concat", "concat_ws", "count", "date", "datetime",
    "dense_rank", "first_value", "glob", "group_concat", "ifnull", "instr",
    "julianday", "lag", "last_value", "lead", "length", "like", "lower", "ltrim",
    "max", "min", "nullif", "rank", "replace", "round", "row_number", "rtrim",
    "strftime", "string_agg", "substr", "substring", "sum", "time", "total",
    "trim", "typeof", "unixepoch", "upper",
})
_FUNCTIONS = _BUILTIN_FUNCTIONS | {"qb_utc_us"}


class SQLiteConnector:
    """Inspect and query the main schema of one SQLite file."""

    def __init__(self, database: str | PathLike[str], limits: QueryLimits = QueryLimits()):
        self._database = Path(database).absolute()
        self._limits = limits

    def inspect(self) -> DatabaseSchema:
        """Return declared schema metadata and a fingerprint of its definitions."""
        deadline = _Deadline(self._limits.timeout_seconds)
        with _read_snapshot(self._database, deadline) as connection:
            listed, table_columns = _relation_catalog(connection)
            definitions = _schema_definitions(connection)
            relation_sql = {
                entry.name: entry.sql for entry in definitions if entry.kind in _RELATION_TYPES
            }
            authorization = _ReadAuthorizer(listed, table_columns)
            relations = []
            for row in listed:
                deadline.check()
                name, kind = row["name"], row["type"]
                columns, foreign_keys, reason = (), (), None
                if kind == "table":
                    columns = table_columns[name]
                    foreign_keys = _foreign_keys(connection, name)
                elif kind == "view":
                    columns, reason = _view_columns(connection, name, authorization, deadline)
                else:
                    reason = f"SQLite {kind} tables are outside the query policy."
                primary_key = tuple(
                    column.name for column in sorted(
                        (column for column in columns if column.primary_key_position is not None),
                        key=lambda column: column.primary_key_position,
                    )
                )
                relations.append(Relation(
                    name=name, kind=kind, definition_sql=relation_sql.get(name),
                    columns=columns, primary_key=primary_key, foreign_keys=foreign_keys,
                    without_rowid=bool(row["wr"]), strict=bool(row["strict"]),
                    unsupported_reason=reason,
                ))

            fingerprint = _fingerprint(definitions)
            deadline.check()
            return DatabaseSchema(
                fingerprint=fingerprint,
                relations=tuple(relations),
                indexes_and_triggers=tuple(
                    entry for entry in definitions if entry.kind in {"index", "trigger"}
                ),
            )

    def execute(
        self, sql: str, parameters: Parameters = (), *, expected_fingerprint: str | None = None,
    ) -> QueryResult:
        """Execute one authorized statement and return a bounded result."""
        deadline = _Deadline(self._limits.timeout_seconds)
        with _read_snapshot(self._database, deadline) as connection:
            listed, table_columns = _relation_catalog(connection)
            if expected_fingerprint is not None and _fingerprint(_schema_definitions(connection)) != expected_fingerprint:
                deadline.check()
                raise SchemaChangedError("The SQLite schema changed during generation. Ask the question again.")
            authorization = _ReadAuthorizer(listed, table_columns)
            connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, _MAX_SQL_BYTES)
            connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, _MAX_SQLITE_BYTES)
            connection.set_authorizer(authorization)
            bound = dict(parameters) if isinstance(parameters, Mapping) else tuple(parameters)
            try:
                deadline.check()
                cursor = connection.execute(sql, bound)
                if cursor.description is None:
                    raise InvalidQueryError("Supply a query that returns columns.")
                result = _collect(cursor, sql, self._limits, deadline)
            except (OverflowError, UnicodeError) as error:
                raise InvalidQueryError(str(error)) from error
            except sqlite3.Error as error:
                deadline.check()
                raise _query_error(error, authorization.denial) from error
            if authorization.denial:
                raise QueryRejectedError(authorization.denial)
            return result


class _Deadline:
    def __init__(self, seconds: float):
        self._end = monotonic() + seconds

    def expired(self) -> bool:
        return monotonic() >= self._end

    def check(self) -> None:
        if self.expired():
            raise QueryTimeoutError("The SQLite operation exceeded its time limit.")


@contextmanager
def _read_snapshot(database: Path, deadline: _Deadline) -> Iterator[sqlite3.Connection]:
    if sqlite3.sqlite_version_info < (3, 37, 0):
        raise DatabaseError("QueryBridge requires SQLite 3.37 or newer.")
    try:
        path = database.resolve(strict=True)
        if not S_ISREG(path.stat().st_mode):
            raise DatabaseError(f"The database path must be a regular file: {database}")
    except OSError as error:
        raise DatabaseError(f"Cannot access SQLite database {database}: {error}") from error

    try:
        deadline.check()
        with closing(sqlite3.connect(
            f"{path.as_uri()}?mode=ro", uri=True, autocommit=True, timeout=0,
            cached_statements=0,
        )) as connection:
            connection.row_factory = sqlite3.Row
            connection.create_function("qb_utc_us", 1, utc_microseconds, deterministic=True)
            connection.set_progress_handler(deadline.expired, 1_000)
            connection.setconfig(sqlite3.SQLITE_DBCONFIG_DEFENSIVE, True)
            connection.setconfig(sqlite3.SQLITE_DBCONFIG_DQS_DML, False)
            if hasattr(connection, "enable_load_extension"):
                connection.enable_load_extension(False)
            connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.execute("PRAGMA query_only = ON")
            # The first catalog read establishes the snapshot used by all later reads.
            connection.execute("BEGIN")
            yield connection
    except sqlite3.Error as error:
        deadline.check()
        raise DatabaseError(f"Cannot read SQLite database {database}: {error}") from error


def _relation_catalog(
    connection: sqlite3.Connection,
) -> tuple[tuple[sqlite3.Row, ...], dict[str, tuple[Column, ...]]]:
    listed = tuple(sorted(
        (row for row in connection.execute("PRAGMA main.table_list")
         if row["schema"] == "main" and not row["name"].startswith("sqlite_")),
        key=lambda row: row["name"],
    ))
    columns = {
        row["name"]: _columns(connection, row["name"]) for row in listed if row["type"] == "table"
    }
    return listed, columns


def _schema_definitions(connection: sqlite3.Connection) -> tuple[Definition, ...]:
    return tuple(
        Definition(row["type"], row["name"], row["tbl_name"], row["sql"])
        for row in connection.execute(
            "SELECT type, name, tbl_name, sql FROM main.sqlite_schema "
            "WHERE sql IS NOT NULL ORDER BY type COLLATE BINARY, name COLLATE BINARY"
        )
        if not row["name"].startswith("sqlite_")
    )


def _fingerprint(definitions: tuple[Definition, ...]) -> str:
    catalog_json = encode_json([asdict(entry) for entry in definitions])
    return hashlib.sha256(catalog_json.encode("ascii")).hexdigest()


def _quote_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _columns(connection: sqlite3.Connection, name: str) -> tuple[Column, ...]:
    return tuple(
        Column(
            position=row["cid"], name=row["name"], declared_type=row["type"],
            not_null=bool(row["notnull"]), default_sql=row["dflt_value"],
            primary_key_position=row["pk"] or None, kind=_COLUMN_KINDS.get(row["hidden"], "hidden"),
        )
        for row in connection.execute(f"PRAGMA main.table_xinfo({_quote_identifier(name)})")
    )


def _foreign_keys(connection: sqlite3.Connection, name: str) -> tuple[ForeignKey, ...]:
    rows = sorted(
        connection.execute(f"PRAGMA main.foreign_key_list({_quote_identifier(name)})"),
        key=lambda row: (row["id"], row["seq"]),
    )
    foreign_keys = []
    for key, group in groupby(rows, key=lambda row: row["id"]):
        parts = tuple(group)
        first = parts[0]
        foreign_keys.append(ForeignKey(
            id=key, columns=tuple(row["from"] for row in parts),
            referenced_table=first["table"], referenced_columns=tuple(row["to"] for row in parts),
            on_update=first["on_update"], on_delete=first["on_delete"], match=first["match"],
        ))
    return tuple(foreign_keys)


class _ReadAuthorizer:
    def __init__(self, listed: tuple[sqlite3.Row, ...], columns: dict[str, tuple[Column, ...]]):
        self._relations = {
            row["name"].translate(_SQLITE_CASE_MAP) for row in listed if row["type"] in _RELATION_TYPES
        }
        self._blocked_columns = {
            (table.translate(_SQLITE_CASE_MAP), column.name.translate(_SQLITE_CASE_MAP))
            for table, table_columns in columns.items()
            for column in table_columns if column.kind in {"hidden", "virtual_generated"}
        }
        self.denial: str | None = None

    def __call__(
        self, action: int, target: str | None, detail: str | None,
        database: str | None, _source: str | None,
    ) -> int:
        if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            # Unqualified reads without a column (such as COUNT(*)) can omit
            # the database name. This connection has no temp or attached objects.
            main_read = database == "main" or (database is None and detail == "")
            table = target.translate(_SQLITE_CASE_MAP) if target is not None else None
            column = detail.translate(_SQLITE_CASE_MAP) if detail is not None else None
            if main_read and table in self._relations:
                if (table, column) not in self._blocked_columns:
                    return sqlite3.SQLITE_OK
                reason = f"Read access to column {target}.{detail} is restricted."
            else:
                reason = f"Read access to relation {target!r} is restricted."
        elif action == sqlite3.SQLITE_FUNCTION:
            if detail is not None and detail.lower() in _FUNCTIONS:
                return sqlite3.SQLITE_OK
            reason = f"SQL function {detail!r} is outside the query policy."
        else:
            reason = "Only read queries against supported SQLite objects are allowed."

        self.denial = self.denial or reason
        return sqlite3.SQLITE_DENY


def _view_columns(
    connection: sqlite3.Connection, name: str, authorization: _ReadAuthorizer, deadline: _Deadline,
) -> tuple[tuple[Column, ...], str | None]:
    # This generated probe applies the query policy to every projected column.
    authorization.denial = None
    connection.set_authorizer(authorization)
    try:
        with closing(connection.execute(f"SELECT * FROM main.{_quote_identifier(name)} LIMIT 0")):
            pass
    except sqlite3.Error as error:
        deadline.check()
        if not authorization.denial and _primary_code(error) != sqlite3.SQLITE_ERROR:
            raise
        return (), authorization.denial or f"SQLite could not resolve the view: {error}"
    finally:
        connection.set_authorizer(None)
    if authorization.denial:
        return (), authorization.denial
    return _columns(connection, name), None


def _primary_code(error: sqlite3.Error) -> int:
    return getattr(error, "sqlite_errorcode", 0) & 0xFF


def _query_error(error: sqlite3.Error, denial: str | None) -> DatabaseError | QueryRejectedError | InvalidQueryError:
    if denial:
        return QueryRejectedError(denial)
    # VACUUM can fail on the read transaction before reaching the authorizer.
    if str(error) in {
        "You can only execute one statement at a time.",
        "query string is too large",
        "cannot VACUUM from within a transaction",
    }:
        return QueryRejectedError(str(error))
    code = _primary_code(error)
    if code in {sqlite3.SQLITE_AUTH, sqlite3.SQLITE_READONLY, sqlite3.SQLITE_TOOBIG}:
        return QueryRejectedError(str(error))
    if code in {sqlite3.SQLITE_NOTADB, sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_IOERR,
                sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return DatabaseError(str(error))
    return InvalidQueryError(str(error))


def _collect(cursor: sqlite3.Cursor, sql: str, limits: QueryLimits, deadline: _Deadline) -> QueryResult:
    columns = tuple(column[0] for column in cursor.description)
    result_bytes = len(encode_json({"columns": columns, "rows": []}))
    if result_bytes > limits.max_result_bytes:
        raise QueryRejectedError("Result column names exceed the byte limit.")

    rows: list[tuple[Scalar, ...]] = []
    truncation: Truncation | None = None
    for record in cursor:
        deadline.check()
        if len(rows) == limits.max_rows:
            truncation = "row_limit"
            break
        row = tuple(record)
        row_bytes = len(encode_json(json_row(row))) + bool(rows)  # Include the separating comma.
        if result_bytes + row_bytes > limits.max_result_bytes:
            truncation = "byte_limit"
            break
        result_bytes += row_bytes
        rows.append(row)

    deadline.check()
    return QueryResult(sql, columns, tuple(rows), result_bytes, truncation)
