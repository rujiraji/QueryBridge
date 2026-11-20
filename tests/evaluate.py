"""Evaluate synthetic questions by result values and decision kind."""

import argparse
import json
import platform
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic

import chromadb

from querybridge import SQLiteConnector
from querybridge._json import encode_json, json_row
from querybridge.connector import QueryBridgeError
from querybridge.decisions import Clarification, Unsupported
from querybridge.ollama import OllamaClient, OllamaEmbeddings
from querybridge.pipeline import QueryPipeline
from querybridge.profiles import BusinessProfile
from querybridge.retrieval import ChromaIndex

from .fixtures import create_concessions, create_library
from .nl_support import TestEmbeddings


@dataclass(frozen=True)
class Case:
    id: str
    fixture: str
    question: str
    expected: tuple | str
    offline_decision: dict
    focus: str = ""


TERMS = "event_id = ? AND status = 'completed'"
CASES = (
    Case("event-totals", "concessions",
         "For the Fall fair (event 1), give completed transaction count, recorded revenue, total cost and "
         "recorded profit in that order, with money in cents.", ((4, 700, 450, 250),),
         {"kind": "query", "sql": "SELECT count(*), sum(total_revenue_cents), sum(total_cost_cents), "
          f"sum(total_profit_cents) FROM sale_transactions WHERE {TERMS}", "parameters": [1]}),
    Case("water-revenue", "concessions",
         "What was the recorded revenue in cents for Water in completed transactions at event 1?", ((400,),),
         {"kind": "query", "sql": "SELECT sum(i.revenue_cents) FROM sale_transaction_items i "
          "JOIN products p ON p.id = i.product_id JOIN sale_transactions t ON t.id = i.transaction_id "
          f"WHERE {TERMS} AND p.name = ?", "parameters": [1, "Water"]}),
    Case("paid-units", "concessions", "How many paid units only were sold at event 1?", ((3,),),
         {"kind": "query", "sql": "SELECT sum(i.quantity) FROM sale_transaction_items i "
          "JOIN sale_transactions t ON t.id = i.transaction_id "
          "WHERE t.event_id = ? AND t.status = ? AND t.payment_type IN (?, ?)",
          "parameters": [1, "completed", "cash", "square"]}),
    Case("reversals", "concessions", "Count reversed transactions for event 1.", ((1,),),
         {"kind": "query", "sql": "SELECT count(*) FROM sale_transactions WHERE event_id = :event AND status = :status",
          "parameters": {"event": 1, "status": "reversed"}}),
    Case("waste-cost", "concessions",
         "How much did completed recorded waste sales cost at event 1, in cents?", ((180,),),
         {"kind": "query", "sql": f"SELECT sum(total_cost_cents) FROM sale_transactions WHERE {TERMS} "
          "AND payment_type = ?", "parameters": [1, "waste"]}),
    Case("timezone", "concessions", "How much recorded revenue did event 1 make yesterday?", "clarification",
         {"kind": "clarification", "question": "Which timestamps and timezone should I use?"}, focus="timezone"),
    Case("units-scope", "concessions", "How many units were sold at event 1?", "clarification",
         {"kind": "clarification", "question": "Should I include free units?"}, focus="free"),
    Case("square-scope", "concessions", "What were Square sales at event 1?", "clarification",
         {"kind": "clarification", "question": "Do you mean recorded or net revenue?"}, focus="net"),
    Case("square-fees", "concessions", "How many cents did Square deduct in payment processing fees at event 1?",
         "unsupported", {"kind": "unsupported", "reason": "The fixture does not store processing fees."}, focus="fee"),
    Case("branch-copies", "library", "Show branch labels and their total book copies, ordered alphabetically by label.",
         (("North", 2), ("South", 6)),
         {"kind": "query", "sql": 'SELECT b.label, sum(c.copies) FROM "Library branches" b '
          'JOIN "Book ""catalog""" c ON c.district = b.district AND c.branch_code = b.code '
          "GROUP BY b.label ORDER BY b.label", "parameters": []}),
    Case("librarian-salary", "library", "What is the average librarian salary?", "unsupported",
         {"kind": "unsupported", "reason": "The library fixture contains no personnel or salary data."},
         focus=r"\bsalar(?:y|ies)\b"),
)


def run(live: bool, client: OllamaClient | None, generation: str | None, embedding: str | None) -> bool:
    print(f"{'live' if live else 'offline scripted'} | Python {platform.python_version()} | "
          f"SQLite {sqlite3.sqlite_version} | Chroma {chromadb.__version__} | {platform.system()} {platform.machine()}")
    if live:
        model = client.model(generation)
        embeddings = OllamaEmbeddings(client, embedding)
        print(f"Ollama {client.version()} at {client.identity} | {model.name}@{model.digest} | {embeddings.identity}")
    else:
        embeddings = TestEmbeddings()

    passed = 0
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        databases = {name: root / f"{name}.sqlite3" for name in ("concessions", "library")}
        create_concessions(databases["concessions"])
        create_library(databases["library"])
        baseline = {name: path.read_bytes() for name, path in databases.items()}
        profiles = {
            "concessions": BusinessProfile.load(Path(__file__).resolve().parents[1] / "profiles/concessions.json"),
            "library": BusinessProfile(),
        }
        store = ChromaIndex(root / "chroma", embeddings)
        for fixture, path in databases.items():
            start = monotonic()
            store.index(path, SQLiteConnector(path).inspect(), profiles[fixture])
            print(f"{fixture} index {monotonic() - start:.3f}s")
        for case in CASES:
            path = databases[case.fixture]
            context = None
            output = None
            generation_seconds = None

            def generate(system, prompt):
                nonlocal context, output, generation_seconds
                context = json.loads(prompt)["selected_context"]
                generation_start = monotonic()
                try:
                    output = (client.generate(model, system, prompt) if live else encode_json(case.offline_decision))
                    return output
                finally:
                    generation_seconds = round(monotonic() - generation_start, 3)

            pipeline = QueryPipeline(path, SQLiteConnector(path), profiles[case.fixture], store, generate)
            start = monotonic()
            try:
                answer = pipeline.ask(case.question)
                if isinstance(answer, Clarification):
                    correct = case.expected == "clarification" and bool(re.search(case.focus, answer.question, re.I))
                    detail = {"status": "clarification", "question": answer.question}
                elif isinstance(answer, Unsupported):
                    correct = case.expected == "unsupported" and bool(re.search(case.focus, answer.reason, re.I))
                    detail = {"status": "unsupported", "reason": answer.reason}
                else:
                    correct = answer.result.rows == case.expected and not answer.result.truncated
                    detail = {
                        "status": "query", "sql": answer.result.sql, "parameters": answer.parameters,
                        "rows": [json_row(row) for row in answer.result.rows], "truncated": answer.result.truncated,
                    }
            except QueryBridgeError as error:
                correct, detail = False, {"status": error.code, "message": str(error)}
            elapsed = round(monotonic() - start, 3)
            source_preserved = path.read_bytes() == baseline[case.fixture]
            correct = correct and source_preserved
            passed += correct
            if context is not None:
                source = "ollama" if live else "scripted"
                detail.update({
                    "generation_seconds": generation_seconds,
                    "context": {
                        "relations": [item["name"] for item in context["relations"]],
                        "definition_ids": [item["id"] for item in context["definitions"]],
                        "relationship_count": len(context["relationships"]),
                    },
                })
            else:
                source = "profile_rule" if detail["status"] == "clarification" else "pipeline_preflight"
            if not correct:
                detail.update({"expected": case.expected, "expected_focus": case.focus})
                if context is not None:
                    detail["context"] = context
                if output is not None and detail["status"] not in {"query", "clarification", "unsupported"}:
                    detail["decision_output"] = output[:8192]
            print(encode_json({
                "case": case.id, "passed": correct, "seconds": elapsed, "decision_source": source,
                "source_preserved": source_preserved, **detail,
            }))
    print(f"{passed}/{len(CASES)} passed")
    return passed == len(CASES)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-timeout", type=float, default=60)
    parser.add_argument("--generation-model")
    parser.add_argument("--embedding-model")
    args = parser.parse_args()
    if args.live and (not args.generation_model or not args.embedding_model):
        parser.error("Live evaluation needs --generation-model and --embedding-model.")
    try:
        client = OllamaClient(args.ollama_url, args.ollama_timeout) if args.live else None
        success = run(args.live, client, args.generation_model, args.embedding_model)
    except QueryBridgeError as error:
        parser.exit(1, f"{error.code} {error}\n")
    raise SystemExit(0 if success else 1)
