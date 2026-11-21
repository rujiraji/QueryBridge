"""Bounded requests to a local Ollama server."""

import http.client
import re
import socket
from dataclasses import dataclass
from functools import cached_property
from ipaddress import ip_address
from math import isfinite
from threading import Event, Timer
from time import monotonic
from urllib.parse import urlsplit

from ._json import decode_json, encode_json
from .connector import QueryBridgeError
from .decisions import GenerationError, decision_schema


class OllamaError(QueryBridgeError):
    code = "ollama_error"


class OllamaUnavailable(OllamaError):
    code = "ollama_unavailable"


class OllamaTimeout(OllamaError):
    code = "ollama_timeout"


class OllamaResponseError(OllamaError):
    code = "ollama_response_error"


class ModelUnavailable(OllamaError):
    code = "model_unavailable"


@dataclass(frozen=True)
class LocalModel:
    name: str
    digest: str


def _model_name(name: str) -> str:
    tail = name.rsplit("/", 1)[-1]
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*(?::[a-zA-Z0-9_.-]+)?", name):
        raise ValueError("Use an Ollama model name such as qwen2.5-coder:7b.")
    return name if ":" in tail else f"{name}:latest"


class OllamaClient:
    def __init__(self, url: str = "http://127.0.0.1:11434", timeout: float = 60,
                 max_response_bytes: int = 2 * 1024 * 1024):
        address = urlsplit(url)
        host = "127.0.0.1" if address.hostname == "localhost" else address.hostname
        if (address.scheme != "http" or host is None or address.path not in ("", "/")
                or address.query or address.fragment or address.username is not None
                or not ip_address(host).is_loopback):
            raise ValueError("Ollama URL must be an HTTP origin on a literal loopback address or localhost.")
        if not isfinite(timeout) or timeout <= 0 or max_response_bytes <= 0:
            raise ValueError("Ollama time and response limits must be positive and finite.")
        self.host = host
        self.port = address.port if address.port is not None else 11434
        self.timeout = timeout
        self.max_response_bytes = max_response_bytes
        self.identity = f"http://[{self.host}]:{self.port}" if ":" in self.host else f"http://{self.host}:{self.port}"

    def _request(self, endpoint: str, payload: dict | None = None) -> dict:
        deadline = monotonic() + self.timeout
        connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        expired = Event()
        timer = None
        body = None if payload is None else encode_json(payload).encode("ascii")

        def cancel(sock):
            expired.set()
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

        try:
            connection.connect()
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError
            # Bound the entire response, including headers and slow streams of bytes.
            timer = Timer(remaining, cancel, (connection.sock,))
            timer.daemon = True
            timer.start()
            connection.request("GET" if body is None else "POST", endpoint, body=body,
                               headers={"Content-Type": "application/json", "Accept": "application/json"})
            with connection.getresponse() as response:
                status = response.status
                length = response.length
                if length is not None and length > self.max_response_bytes:
                    raise OllamaResponseError(f"Ollama response exceeds the configured limit for {endpoint}.")
                data = response.read(self.max_response_bytes + 1)
                if length is not None and len(data) != length:
                    raise http.client.IncompleteRead(data, length - len(data))
            if expired.is_set() or monotonic() >= deadline:
                raise TimeoutError
        except TimeoutError as error:
            raise OllamaTimeout(f"Ollama exceeded {self.timeout:g}s for {endpoint}.") from error
        except (OSError, http.client.HTTPException) as error:
            if expired.is_set() or monotonic() >= deadline:
                raise OllamaTimeout(f"Ollama exceeded {self.timeout:g}s for {endpoint}.") from error
            if isinstance(error, (http.client.IncompleteRead, http.client.BadStatusLine,
                                  http.client.LineTooLong, http.client.UnknownProtocol)) and not isinstance(
                                      error, http.client.RemoteDisconnected):
                raise OllamaResponseError(f"Malformed HTTP response from Ollama at {endpoint}.") from error
            raise OllamaUnavailable(f"Could not reach Ollama at {self.identity} for {endpoint}.") from error
        finally:
            if timer is not None:
                timer.cancel()
            connection.close()

        if len(data) > self.max_response_bytes:
            raise OllamaResponseError(f"Ollama response exceeds the configured limit for {endpoint}.")
        if status == 404 and payload is not None:
            raise ModelUnavailable(f"Ollama could not serve {payload['model']!r} at {endpoint} (HTTP 404).")
        if status != 200:
            detail = data[:512].decode("utf-8", errors="replace").strip()
            raise OllamaError(f"Ollama returned HTTP {status} for {endpoint}. {detail}")
        try:
            value = decode_json(data)
            if isinstance(value, dict):
                return value
        except (ValueError, RecursionError):
            pass
        raise OllamaResponseError(f"Expected a JSON object from Ollama at {endpoint}.")

    def local_models(self) -> dict[str, LocalModel]:
        response = self._request("/api/tags")
        entries = response.get("models")
        if not isinstance(entries, list):
            raise OllamaResponseError("Ollama model list is missing the models array.")
        models = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise OllamaResponseError("Ollama returned an invalid model entry.")
            name, digest = entry.get("model") or entry.get("name"), entry.get("digest")
            if not isinstance(name, str) or not isinstance(digest, str) or not digest:
                raise OllamaResponseError("An Ollama model entry is missing its name or digest.")
            if entry.get("remote_host") or entry.get("remote_model"):
                continue
            try:
                name = _model_name(name)
            except ValueError as error:
                raise OllamaResponseError("Ollama returned an invalid model name.") from error
            tag = name.rsplit(":", 1)[-1].lower()
            if tag != "cloud" and not tag.endswith("-cloud"):
                models[name] = LocalModel(name, digest)
        return models

    @cached_property
    def _registry(self) -> dict[str, LocalModel]:
        return self.local_models()

    def model(self, name: str) -> LocalModel:
        try:
            name = _model_name(name)
        except ValueError as error:
            raise ModelUnavailable(str(error)) from error
        if name not in self._registry:
            raise ModelUnavailable(f"Local Ollama model {name!r} is missing. Check ollama list and pull its local tag.")
        return self._registry[name]

    def version(self) -> str:
        version = self._request("/api/version").get("version")
        if not isinstance(version, str) or not version:
            raise OllamaResponseError("Ollama version is missing.")
        return version

    def generate(self, model: LocalModel, system: str, prompt: str) -> str:
        response = self._request("/api/chat", {
            "model": model.name,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            "stream": False,
            "think": False,
            "truncate": False,
            "shift": False,
            "format": decision_schema(),
            "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 1536},
        })
        if response.get("done_reason") == "length":
            raise GenerationError("The model reached its output limit before completing the decision.")
        message = response.get("message")
        if (response.get("done") is not True or not isinstance(message, dict)
                or message.get("role") != "assistant" or not isinstance(message.get("content"), str)
                or message.get("tool_calls")):
            raise OllamaResponseError("Ollama did not return a completed assistant message.")
        return message["content"]


@dataclass(frozen=True)
class OllamaEmbeddings:
    client: OllamaClient
    model_name: str

    @cached_property
    def model(self) -> LocalModel:
        return self.client.model(self.model_name)

    @property
    def identity(self) -> str:
        return f"{self.client.identity}/api/embed/{self.model.name}@{self.model.digest}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        response = self.client._request("/api/embed", {
            "model": self.model.name, "input": texts, "truncate": False,
        })
        vectors = response.get("embeddings")
        if (not isinstance(vectors, list) or len(vectors) != len(texts) or not vectors
                or any(not isinstance(v, list) or not v or len(v) != len(vectors[0]) for v in vectors)
                or any(type(x) not in (float, int) or not -3.4e38 <= x <= 3.4e38 for v in vectors for x in v)):
            raise OllamaResponseError("Ollama returned invalid embedding vectors.")
        return [[float(x) for x in v] for v in vectors]
