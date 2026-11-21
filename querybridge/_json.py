"""A shared JSON representation for CLI output and query result sizing."""

import json
from base64 import b64encode
from math import isfinite

from .connector import QueryResult, Scalar


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def decode_json(value: str | bytes) -> object:
    def object_from_pairs(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate JSON keys.")
        return result

    def reject_constant(name):
        raise ValueError(f"Invalid JSON constant {name}.")

    return json.loads(value, object_pairs_hook=object_from_pairs, parse_constant=reject_constant)


def json_row(row: tuple[Scalar, ...]) -> list[object]:
    return [_json_value(value) for value in row]


def query_payload(result: QueryResult) -> dict:
    return {
        "status": "empty" if result.is_empty else "ok",
        "sql": result.sql,
        "columns": result.columns,
        "rows": [json_row(row) for row in result.rows],
        "result_bytes": result.result_bytes,
        "truncated": result.truncated,
        "truncation_reason": result.truncation_reason,
    }


def _json_value(value: Scalar) -> object:
    if isinstance(value, bytes):
        return {"blob_base64": b64encode(value).decode("ascii")}
    if isinstance(value, float) and not isfinite(value):
        return {"real": str(value)}
    return value
