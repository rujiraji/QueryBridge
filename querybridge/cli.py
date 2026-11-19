"""Command-line access to the SQLite connector."""

import argparse
import json
import sys
from dataclasses import asdict

from ._json import encode_json, query_payload
from .connector import InvalidQueryError, Parameters, QueryBridgeError, QueryLimits
from .nl_cli import add_nl_commands, run_nl_command
from .sqlite import SQLiteConnector


def _parameters(raw: str) -> Parameters:
    try:
        value = json.loads(raw)
    except ValueError as error:
        raise InvalidQueryError(f"Invalid parameter JSON. {error}") from error
    if isinstance(value, dict):
        values = value.values()
    elif isinstance(value, list):
        values = value
    else:
        raise InvalidQueryError("Parameters must be a JSON array or object.")
    if any(item is not None and not isinstance(item, (str, int, float)) for item in values):
        raise InvalidQueryError("Bind scalar JSON values only.")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="querybridge", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Discover the SQLite schema")
    query = commands.add_parser("query", help="Execute a single read query")
    defaults = QueryLimits()
    for command in (inspect, query):
        command.add_argument("database", help="Path to an existing SQLite database")
        command.add_argument("--timeout", type=float, default=defaults.timeout_seconds,
                             help="Time limit in seconds (default %(default)s)")
    query.add_argument("sql", help="SQL statement")
    query.add_argument("--params", default="[]", help="Positional JSON array or named JSON object")
    query.add_argument("--max-rows", type=int, default=defaults.max_rows,
                       help="Maximum returned rows (default %(default)s)")
    query.add_argument("--max-result-bytes", type=int, default=defaults.max_result_bytes,
                       help="Maximum encoded column and row bytes (default %(default)s)")
    add_nl_commands(commands)
    args = parser.parse_args(argv)

    try:
        if args.command == "inspect":
            connector = SQLiteConnector(args.database, QueryLimits(timeout_seconds=args.timeout))
            payload = {"status": "ok", **asdict(connector.inspect())}
        elif args.command == "query":
            limits = QueryLimits(args.timeout, args.max_rows, args.max_result_bytes)
            result = SQLiteConnector(args.database, limits).execute(args.sql, _parameters(args.params))
            payload = query_payload(result)
        else:
            payload = run_nl_command(args)
    except QueryBridgeError as error:
        print(encode_json({"status": error.code, "message": str(error)}), file=sys.stderr)
        return 1
    except ValueError as error:
        parser.error(str(error))

    print(encode_json(payload))
    return 0
