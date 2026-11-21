"""CLI configuration and output for the natural-language workflows."""

import argparse
import os
from dataclasses import asdict
from pathlib import Path

from ._json import query_payload
from .connector import QueryBridgeError, QueryLimits
from .decisions import Clarification, Unsupported
from .ollama import OllamaClient, OllamaEmbeddings
from .pipeline import QueryPipeline
from .profiles import BusinessProfile
from .retrieval import ChromaIndex
from .sqlite import SQLiteConnector


class ConfigurationError(QueryBridgeError):
    code = "configuration_error"


def add_nl_commands(commands) -> None:
    ready = commands.add_parser("ready", help="Check local models and index storage")
    index = commands.add_parser("index", help="Create or refresh the selected database index")
    ask = commands.add_parser("ask", help="Ask a question about the selected database")
    limits = QueryLimits()
    for command in (ready, index, ask):
        command.add_argument("--ollama-url", default=os.getenv("QUERYBRIDGE_OLLAMA_URL", "http://127.0.0.1:11434"))
        command.add_argument("--ollama-timeout", type=float, default=60, help="Per-request model timeout in seconds")
        command.add_argument("--embedding-model", default=os.getenv("QUERYBRIDGE_EMBEDDING_MODEL"))
        command.add_argument("--index-dir", type=Path,
                             default=os.getenv("QUERYBRIDGE_INDEX_DIR", ".querybridge-data/chroma"))
    for command in (ready, ask):
        command.add_argument("--generation-model", default=os.getenv("QUERYBRIDGE_GENERATION_MODEL"))
    for command in (index, ask):
        command.add_argument("database", type=Path, help="Path to the SQLite database")
        command.add_argument("--profile", type=Path, help="Path to a versioned business profile JSON file")
        command.add_argument("--timeout", type=float, default=limits.timeout_seconds, help="SQLite deadline in seconds")
    ask.add_argument("question")
    ask.add_argument("--max-rows", type=int, default=limits.max_rows)
    ask.add_argument("--max-result-bytes", type=int, default=limits.max_result_bytes)


def run_nl_command(args: argparse.Namespace) -> dict:
    if not args.embedding_model:
        raise ConfigurationError("Set --embedding-model or QUERYBRIDGE_EMBEDDING_MODEL to a local Ollama model.")
    if args.command in ("ready", "ask") and not args.generation_model:
        raise ConfigurationError("Set --generation-model or QUERYBRIDGE_GENERATION_MODEL to a local Ollama model.")

    try:
        client = OllamaClient(args.ollama_url, args.ollama_timeout)
    except ValueError as error:
        raise ConfigurationError(str(error)) from error
    embeddings = OllamaEmbeddings(client, args.embedding_model)
    store = ChromaIndex(args.index_dir, embeddings)
    if args.command == "ready":
        generation = client.model(args.generation_model)
        sample = embeddings.embed(["QueryBridge readiness probe"])
        store.check_storage()
        return {
            "status": "ready", "ollama_version": client.version(), "generation": asdict(generation),
            "embedding": asdict(embeddings.model), "embedding_dimensions": len(sample[0]), "index_dir": str(store.path),
        }

    profile = BusinessProfile.load(args.profile) if args.profile else BusinessProfile()
    defaults = QueryLimits()
    limits = QueryLimits(args.timeout, getattr(args, "max_rows", defaults.max_rows),
                         getattr(args, "max_result_bytes", defaults.max_result_bytes))
    database = args.database.resolve()
    connector = SQLiteConnector(database, limits)
    if args.command == "index":
        schema = connector.inspect()
        state = store.index(database, schema, profile)
        return {"status": "indexed", "schema_fingerprint": schema.fingerprint, **asdict(state)}

    answer = QueryPipeline(
        database, connector, profile, store,
        lambda system, prompt: client.generate(client.model(args.generation_model), system, prompt),
    ).ask(args.question)
    if isinstance(answer, Clarification):
        return {"status": "clarification", "question": answer.question}
    if isinstance(answer, Unsupported):
        return {"status": "unsupported", "reason": answer.reason}
    return {**query_payload(answer.result), "parameters": answer.parameters}
