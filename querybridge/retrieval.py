"""Persistent Chroma retrieval using vectors explicitly supplied by an embedder."""

import hashlib
import os
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from itertools import combinations
from pathlib import Path
from typing import Protocol

from ._json import encode_json
from .connector import DatabaseSchema, QueryBridgeError, Relation
from .profiles import BusinessProfile
from .sqlite import _SQLITE_CASE_MAP


class RetrievalError(QueryBridgeError):
    code = "index_error"


class Embedder(Protocol):
    @property
    def identity(self) -> str: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class IndexStatus:
    collection: str
    revision: str
    documents: int
    refreshed: bool


@dataclass(frozen=True)
class Relationship:
    table: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]


def _digest(value: object) -> str:
    return hashlib.sha256(encode_json(value).encode("ascii")).hexdigest()


def _relation_data(relation: Relation) -> dict:
    return {
        "name": relation.name, "kind": relation.kind,
        "columns": [
            {"name": column.name, "type": column.declared_type}
            for column in relation.columns if column.kind in ("ordinary", "stored_generated")
        ],
        "primary_key": relation.primary_key,
    }


class _Catalog:
    def __init__(self, schema: DatabaseSchema):
        tables = {
            item.name.translate(_SQLITE_CASE_MAP): item for item in schema.relations
            if item.kind in ("table", "view") and item.unsupported_reason is None
        }
        self.relations = {item.name: _relation_data(item) for item in tables.values()}
        self.edges = []
        self.neighbors = {name: set() for name in self.relations}
        column_names = {
            name: {column["name"].translate(_SQLITE_CASE_MAP): column["name"] for column in item["columns"]}
            for name, item in self.relations.items()
        }
        for item in tables.values():
            for key in item.foreign_keys:
                target = tables.get(key.referenced_table.translate(_SQLITE_CASE_MAP))
                if target is None:
                    continue
                refs = target.primary_key if all(name is None for name in key.referenced_columns) else key.referenced_columns
                if len(refs) != len(key.columns) or any(name is None for name in refs):
                    continue
                try:
                    children = tuple(column_names[item.name][name.translate(_SQLITE_CASE_MAP)] for name in key.columns)
                    parents = tuple(column_names[target.name][name.translate(_SQLITE_CASE_MAP)] for name in refs)
                except KeyError:
                    continue
                self.edges.append(Relationship(item.name, children, target.name, parents))
                self.neighbors[item.name].add(target.name)
                self.neighbors[target.name].add(item.name)

    def _path(self, start: str, end: str) -> tuple[str, ...]:
        queue = deque([(start,)])
        visited = {start}
        while queue:
            path = queue.popleft()
            if path[-1] == end:
                return path
            for neighbor in sorted(self.neighbors[path[-1]] - visited):
                visited.add(neighbor)
                queue.append((*path, neighbor))
        return ()

    def expand(self, names: list[str]) -> tuple[list[dict], list[dict]]:
        selected = dict.fromkeys(names)
        for start, end in combinations(names, 2):
            selected.update(dict.fromkeys(self._path(start, end)))
        return (
            [self.relations[name] for name in selected],
            [asdict(edge) for edge in self.edges if edge.table in selected and edge.referenced_table in selected],
        )


class ChromaIndex:
    def __init__(self, path: Path, embeddings: Embedder):
        self.path = path.resolve()
        self.embeddings = embeddings

    @contextmanager
    def _client(self) -> Iterator:
        try:
            import chromadb
            from chromadb.config import Settings
            from filelock import FileLock
        except ImportError as error:
            raise RetrievalError("Install QueryBridge with the nl extra to use Chroma retrieval.") from error
        try:
            self.path.mkdir(parents=True, exist_ok=True)
            with FileLock(self.path / ".querybridge.lock", timeout=5):
                with chromadb.PersistentClient(
                    path=str(self.path), settings=Settings(anonymized_telemetry=False, chroma_otel_granularity="none"),
                ) as client:
                    yield client
        except QueryBridgeError:
            raise
        except Exception as error:
            # Native storage and locking errors belong to this service boundary.
            raise RetrievalError(f"Chroma index operation failed. {error}") from error

    def index(self, database: Path, schema: DatabaseSchema, profile: BusinessProfile) -> IndexStatus:
        with self._client() as client:
            _, status, _ = self._ensure(client, database, schema, profile)
            return status

    def check_storage(self) -> None:
        with self._client() as client:
            client.heartbeat()

    def retrieve(self, database: Path, schema: DatabaseSchema, profile: BusinessProfile, question: str) -> dict:
        with self._client() as client:
            collection, _, catalog = self._ensure(client, database, schema, profile)
            if not catalog.relations:
                return {"relations": [], "relationships": [], "definitions": []}
            vector = self.embeddings.embed([question])
            selected = collection.query(
                query_embeddings=vector, n_results=min(5, len(catalog.relations)),
                where={"kind": "relation"}, include=[],
            )["ids"][0]
            relations, edges = catalog.expand([name.removeprefix("relation:") for name in selected])
            definitions = {f"definition:{item.id}": asdict(item) for item in profile.definitions}
            matched = (
                collection.query(
                    query_embeddings=vector, n_results=min(5, len(definitions)),
                    where={"kind": "definition"}, include=[],
                )["ids"][0] if definitions else []
            )
        return {"relations": relations, "relationships": edges, "definitions": [definitions[key] for key in matched]}

    def _ensure(self, client, database: Path, schema: DatabaseSchema, profile: BusinessProfile):
        catalog = _Catalog(schema)
        scope = _digest([os.path.normcase(str(database.resolve())), profile.id])
        name = f"qb-{scope}"
        documents = {
            f"relation:{table}": ("relation", encode_json({
                **data, "declared_relationships": [
                    asdict(edge) for edge in catalog.edges if table in (edge.table, edge.referenced_table)
                ],
            }))
            for table, data in catalog.relations.items()
        }
        documents.update({
            f"definition:{item.id}": ("definition", f"{item.id}: {item.text}") for item in profile.definitions
        })
        if any(len(text.encode("utf-8")) > 12_000 for _, text in documents.values()):
            raise RetrievalError("A schema or business document exceeds the 12,000-byte embedding limit.")
        revision = _digest([1, schema.fingerprint, asdict(profile), self.embeddings.identity, documents])

        from chromadb.errors import NotFoundError

        try:
            collection = client.get_collection(name, embedding_function=None)
        except NotFoundError:
            collection = None
        if (collection is not None and (collection.metadata or {}).get("revision") == revision
                and collection.count() == len(documents)):
            return collection, IndexStatus(name, revision, len(documents), False), catalog

        # Keep the old collection if an embedding request fails. A partial
        # collection after an interrupted write is rebuilt on the next access.
        records = list(documents.items())
        batches = []
        for offset in range(0, len(records), 16):
            batch = records[offset:offset + 16]
            texts = [record[1][1] for record in batch]
            batches.append((batch, texts, self.embeddings.embed(texts)))
        if collection is not None:
            client.delete_collection(name)
        collection = client.create_collection(
            name, embedding_function=None, metadata={"revision": revision},
            configuration={"hnsw": {"space": "cosine"}},
        )
        for batch, texts, vectors in batches:
            collection.add(
                ids=[key for key, _ in batch], documents=texts, embeddings=vectors,
                metadatas=[{"kind": record[0]} for _, record in batch],
            )
        return collection, IndexStatus(name, revision, len(documents), True), catalog
