"""The structured model response contract."""

import re
import sqlite3
from dataclasses import dataclass
from math import isfinite

from ._json import decode_json
from .connector import QueryBridgeError

type BindValue = str | int | float | bool | None


class GenerationError(QueryBridgeError):
    code = "generation_error"


@dataclass(frozen=True)
class Proposal:
    sql: str
    parameters: tuple[BindValue, ...] | dict[str, BindValue]


@dataclass(frozen=True)
class Clarification:
    question: str


@dataclass(frozen=True)
class Unsupported:
    reason: str


type Decision = Proposal | Clarification | Unsupported
_SCALAR = {"type": ["string", "number", "boolean", "null"]}


def decision_schema() -> dict:
    variants = [
        ("query", {"sql": {"type": "string"}, "parameters": {"anyOf": [
            {"type": "array", "items": _SCALAR},
            {"type": "object", "additionalProperties": _SCALAR},
        ]}}),
        ("clarification", {"question": {"type": "string"}}),
        ("unsupported", {"reason": {"type": "string"}}),
    ]
    return {"oneOf": [
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": [kind]}, **fields},
            "required": ["kind", *fields],
            "additionalProperties": False,
        }
        for kind, fields in variants
    ]}


# Recognize statement boundaries outside SQLite literals, identifiers and comments.
_SQL_TOKENS = re.compile(
    r"'(?:[^']|'')*(?:'|\Z)|\"(?:[^\"]|\"\")*(?:\"|\Z)|"
    r"\x60(?:[^\x60]|\x60\x60)*(?:\x60|\Z)|\[[^\]]*(?:\]|\Z)|"
    r"--[^\n]*|/\*[\s\S]*?(?:\*/|\Z)|[\x09-\x0d \ufeff]+|;|[^\s;]"
)


def _single_statement(sql: str) -> None:
    ended = False
    meaningful = False
    for token in _SQL_TOKENS.finditer(sql):
        value = token.group()
        if value.startswith(("--", "/*")) or not value.strip("\t\n\v\f\r \ufeff"):
            continue
        if ended:
            raise ValueError("Return a single SQL statement.")
        if value == ";":
            ended = sqlite3.complete_statement(sql[:token.end()])
        else:
            meaningful = True
    if not meaningful or "\0" in sql:
        raise ValueError("Return a nonempty SQL statement.")


def _text(value: object, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > limit:
        raise ValueError("Expected nonempty text within the size limit.")
    return value


def _bind_value(value: object) -> BindValue:
    if value is None or type(value) is bool:
        return value
    if type(value) is int and -(2 ** 63) <= value < 2 ** 63:
        return value
    if type(value) is float and isfinite(value):
        return value
    if isinstance(value, str):
        value.encode("utf-8")
        return value
    raise ValueError("Bind parameters must be SQLite-compatible JSON scalars.")


def parse_decision(raw: str) -> Decision:
    try:
        if len(raw.encode("utf-8")) > 131_072:
            raise ValueError("The decision exceeds 128 KiB.")
        value = decode_json(raw)
        if isinstance(value, dict):
            match value.get("kind"), value.keys():
                case "query", keys if keys == {"kind", "sql", "parameters"}:
                    sql = _text(value["sql"], 65_536)
                    _single_statement(sql)
                    params = value["parameters"]
                    if isinstance(params, list):
                        return Proposal(sql, tuple(map(_bind_value, params)))
                    if isinstance(params, dict):
                        return Proposal(sql, {_text(key, 1024): _bind_value(item) for key, item in params.items()})
                    raise ValueError("Parameters must be a JSON array or named object.")
                case "clarification", keys if keys == {"kind", "question"}:
                    return Clarification(_text(value["question"], 2_048))
                case "unsupported", keys if keys == {"kind", "reason"}:
                    return Unsupported(_text(value["reason"], 2_048))
        raise ValueError("The object does not match a decision variant.")
    except (ValueError, RecursionError) as error:
        raise GenerationError(f"Invalid model decision. {error}") from error
