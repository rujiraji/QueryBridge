import http.client
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep
from unittest.mock import patch

from querybridge.cli import main
from querybridge.ollama import ModelUnavailable, OllamaClient, OllamaEmbeddings, OllamaResponseError
from querybridge.ollama import OllamaTimeout, OllamaUnavailable

from .fixtures import create_concessions


class OllamaTests(unittest.TestCase):
    def setUp(self):
        self.requests = []
        self.routes = {
            "/api/tags": (200, {"models": [
                {"name": "gen:latest", "digest": "gen-123"},
                {"model": "embed:latest", "digest": "embed-456"},
                {"name": "test:cloud", "digest": "cloud"},
                {"name": "alias:latest", "digest": "alias", "remote_host": "ignored"},
            ]}),
            "/api/version": (200, {"version": "test-server"}),
            "/api/embed": self._embed,
            "/api/chat": (200, {
                "message": {"role": "assistant", "content": (
                    '{"kind":"query","sql":"SELECT name FROM events WHERE id = ?","parameters":[1]}'
                )},
                "done": True,
            }),
        }
        requests, routes = self.requests, self.routes

        class Handler(BaseHTTPRequestHandler):
            def respond(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                payload = json.loads(raw) if raw else None
                requests.append((self.command, self.path, payload))
                route = routes[self.path]
                if callable(route):
                    route = route(self, payload)
                if route is None:
                    return
                status, value = route
                body = value if isinstance(value, bytes) else json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = do_POST = respond

            def log_message(self, *args):
                pass

        server = self.enterContext(ThreadingHTTPServer(("127.0.0.1", 0), Handler))
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}"
        self.client = OllamaClient(self.url, timeout=3)

    def _embed(self, handler, params):
        return 200, {"embeddings": [[1.0, 2.0] for _ in params["input"]]}

    def test_request_shapes_and_local_model_selection(self):
        self.assertEqual(self.client.model("gen").digest, "gen-123")
        for name in ("missing", "test:cloud", "alias"):
            with self.subTest(name=name), self.assertRaises(ModelUnavailable):
                self.client.model(name)
        embeddings = OllamaEmbeddings(self.client, "embed")
        self.assertEqual(embeddings.embed(["one", "two"]), [[1.0, 2.0], [1.0, 2.0]])
        self.assertIn("embed-456", embeddings.identity)
        raw = self.client.generate(self.client.model("gen"), "rules", "question")
        self.assertEqual(json.loads(raw)["parameters"], [1])
        self.assertEqual([call[:2] for call in self.requests], [
            ("GET", "/api/tags"), ("POST", "/api/embed"), ("POST", "/api/chat"),
        ])
        self.assertIs(self.requests[1][2]["truncate"], False)
        self.assertIs(self.requests[2][2]["stream"], False)
        self.assertIn("oneOf", self.requests[2][2]["format"])

    def test_service_refused_and_remote_origins_are_rejected(self):
        with patch.object(http.client.HTTPConnection, "connect", side_effect=ConnectionRefusedError):
            with self.assertRaises(OllamaUnavailable):
                self.client.local_models()
        with self.assertRaises(ValueError):
            OllamaClient("https://ollama.com")

    def test_response_timeout_covers_trickle(self):
        def trickle(handler, _):
            handler.send_response(200)
            handler.end_headers()
            try:
                for _ in range(120):
                    handler.wfile.write(b" ")
                    sleep(0.01)
            except OSError:
                pass
        self.routes["/api/tags"] = trickle
        start = monotonic()
        with self.assertRaises(OllamaTimeout):
            OllamaClient(self.url, timeout=0.15).local_models()
        self.assertLess(monotonic() - start, 1.1)

    def test_malformed_and_oversized_responses(self):
        for value in (b"invalid json", {"unexpected": 1}, {"models": [None]}, {"models": [{"name": "?"}]}):
            with self.subTest(value=value):
                self.routes["/api/tags"] = (200, value)
                with self.assertRaises(OllamaResponseError):
                    self.client.local_models()
        self.routes["/api/tags"] = (200, b" " * 100)
        with self.assertRaises(OllamaResponseError):
            OllamaClient(self.url, max_response_bytes=25).local_models()

        def unbounded(handler, _):
            handler.send_response(200)
            handler.end_headers()
            handler.wfile.write(b" " * 100)
        self.routes["/api/tags"] = unbounded
        with self.assertRaises(OllamaResponseError):
            OllamaClient(self.url, max_response_bytes=25).local_models()

    def test_malformed_embeddings_and_model_disappearing(self):
        embeddings = OllamaEmbeddings(self.client, "embed")
        for value in ([[1.0], [1.0, 2.0]], [[1.0], [False]], [[1.0]], [0, [1.0]]):
            self.routes["/api/embed"] = (200, {"embeddings": value})
            with self.subTest(value=value), self.assertRaises(OllamaResponseError):
                embeddings.embed(["one", "two"])
        self.routes["/api/chat"] = (200, {"message": {"role": "assistant"}, "done": True})
        with self.assertRaises(OllamaResponseError):
            self.client.generate(self.client.model("gen"), "rules", "question")
        self.routes["/api/chat"] = (404, {"error": "model disappeared"})
        with self.assertRaises(ModelUnavailable):
            self.client.generate(self.client.model("gen"), "rules", "question")

    def test_cli_readiness_index_and_full_question_workflow(self):
        directory = Path(self.enterContext(TemporaryDirectory()))
        database = directory / "demo.sqlite3"
        create_concessions(database)
        before = database.read_bytes()

        def run(*args):
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main([*args, "--ollama-url", self.url, "--embedding-model", "embed",
                             "--index-dir", str(directory / "chroma")])
            self.assertEqual(code, 0, err.getvalue())
            return json.loads(out.getvalue())

        ready = run("ready", "--generation-model", "gen")
        self.assertEqual((ready["status"], ready["embedding_dimensions"]), ("ready", 2))
        indexed = run("index", str(database))
        self.assertEqual((indexed["status"], indexed["documents"]), ("indexed", 4))
        answer = run("ask", str(database), "What is the name of event 1?", "--generation-model", "gen")
        self.assertEqual((answer["status"], answer["parameters"], answer["rows"]), ("ok", [1], [["Fall fair"]]))
        self.assertFalse(answer["truncated"])
        self.assertEqual(database.read_bytes(), before)
