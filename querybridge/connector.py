"""The database interface and result types used by QueryBridge."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import ClassVar, Literal, Protocol

type Scalar = None | int | float | str | bytes
type Parameters = Sequence[Scalar] | Mapping[str, Scalar]
type Truncation = Literal["row_limit", "byte_limit"]


@dataclass(frozen=True, slots=True)
class Column:
    position: int
    name: str
    declared_type: str
    not_null: bool
    default_sql: str | None
    primary_key_position: int | None
    kind: Literal["ordinary", "hidden", "virtual_generated", "stored_generated"]


@dataclass(frozen=True, slots=True)
class ForeignKey:
    id: int
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str | None, ...]
    on_update: str
    on_delete: str
    match: str


@dataclass(frozen=True, slots=True)
class Relation:
    name: str
    kind: str
    definition_sql: str | None
    columns: tuple[Column, ...] = ()
    primary_key: tuple[str, ...] = ()
    foreign_keys: tuple[ForeignKey, ...] = ()
    without_rowid: bool = False
    strict: bool = False
    unsupported_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Definition:
    kind: str
    name: str
    owner: str
    sql: str


@dataclass(frozen=True, slots=True)
class DatabaseSchema:
    fingerprint: str
    relations: tuple[Relation, ...]
    indexes_and_triggers: tuple[Definition, ...]


@dataclass(frozen=True, slots=True)
class QueryLimits:
    timeout_seconds: float = 5.0
    max_rows: int = 1_000
    max_result_bytes: int = 1_048_576

    def __post_init__(self) -> None:
        if not isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive and finite.")
        for name in ("max_rows", "max_result_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")


@dataclass(frozen=True, slots=True)
class QueryResult:
    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Scalar, ...], ...]
    result_bytes: int
    truncation_reason: Truncation | None

    @property
    def truncated(self) -> bool:
        return self.truncation_reason is not None

    @property
    def is_empty(self) -> bool:
        return not self.rows and not self.truncated


class QueryBridgeError(Exception):
    code: ClassVar[str]


class DatabaseError(QueryBridgeError):
    code = "database_error"


class SchemaChangedError(QueryBridgeError):
    code = "schema_changed"


class QueryRejectedError(QueryBridgeError):
    code = "rejected_query"


class InvalidQueryError(QueryBridgeError):
    code = "invalid_query"


class QueryTimeoutError(QueryBridgeError):
    code = "timeout"


class Connector(Protocol):
    def inspect(self) -> DatabaseSchema: ...

    def execute(self, sql: str, parameters: Parameters = ()) -> QueryResult: ...
