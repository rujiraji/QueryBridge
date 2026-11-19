"""A shared JSON representation for CLI output and query result sizing."""

import json
from base64 import b64encode
from math import isfinite

from .connector import Scalar


def encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))


def json_row(row: tuple[Scalar, ...]) -> list[object]:
    return [_json_value(value) for value in row]


def _json_value(value: Scalar) -> object:
    if isinstance(value, bytes):
        return {"blob_base64": b64encode(value).decode("ascii")}
    if isinstance(value, float) and not isfinite(value):
        return {"real": str(value)}
    return value
