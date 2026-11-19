# Verification and evaluation

## Phase 1 review

The independent review of `8045064b6e32752596c9ebb2bf700aadd16fb972` passed all 26 generic tests on Python 3.12.14 with SQLite 3.53.1. Additional authorization and concurrent-WAL snapshot checks passed. The installed wheel CLI reproduced the fixture totals and preserved the source bytes. The reviewer could not independently rerun the authenticated tracker check. The Phase 2 implementer reran `python -B -m tests.check_tracker ../concession-stand-tracker` against `60bcd03787145a16ee5d0073bea36aabb224a982` and matched 17 tables, 19 declared foreign keys, all expected report totals, and five remaining units. Live tracker data and older deployed schemas remain unverified. [Tracker findings](concession-tracker.md)

## Phase 2 offline

```sh
python -m pip install ".[nl]"
python -B -m unittest discover -v
python -B -m tests.evaluate
```

On Windows AMD64 with Python 3.12.13, SQLite 3.50.4 and ChromaDB 1.5.9, all 47 unit and integration tests passed. The suite uses real temporary Chroma storage, deterministic embeddings, and a fake loopback Ollama server for HTTP and CLI integration. The scripted evaluation passed all 10 cases. It exercises retrieval, decision handling, and restricted execution, with scripted decisions in place of Ollama generation.

| Case | Independent expected result | Offline outcome |
| --- | --- | --- |
| Completed event 1 totals | Four records. Revenue 400 + 300 = 700 cents. Cost 120 + 90 + 60 + 180 = 450 cents. Profit 250 cents. | Pass |
| Water revenue | Two paid units at the saved 200 cents each and one free unit yield 400 cents. | Pass |
| Reversals | One reversed event 1 transaction. | Pass |
| Recorded waste sales cost | Two wasted units at 90 cents each yield 180 cents. | Pass |
| Yesterday's revenue | Ask for range, timezone and day boundary. | Pass |
| Units sold | Ask whether to include free units. | Pass |
| Square sales | Ask for recorded gross or net proceeds. | Pass |
| Actual Square processing fees | Unsupported because the fixture has no fee records. | Pass |
| Library copies by branch | North has 2 + 0 = 2. South has 5 + 1 = 6. | Pass |
| Average librarian salary | Unsupported because the fixture has no personnel or salary records. | Pass |

The evaluator compares rows, decision kinds, and the topic of each clarification or unsupported response. It checks the fixture bytes after each case and records per-fixture indexing times and per-case latency.

## Phase 2 live model

The development environment had no Ollama executable on PATH, no local model manifests at the configured or default location, and no listener at `127.0.0.1:11434`. With the example tags configured, `python -B -m querybridge ready` and the live command below both exited with code `1` and `ollama_unavailable` at `/api/tags`. Live model verification is incomplete, with no measured model accuracy or inference latency. After installing Ollama and pulling configured local tags, run the same evaluation with live generation and embeddings.

```sh
python -B -m tests.evaluate --live --generation-model qwen2.5-coder:7b --embedding-model nomic-embed-text:latest
```

The live evaluator reports the Ollama version, configured tags and digests, Python, SQLite, Chroma version, platform, indexing times, and each case's result, failures, and latency. [Local setup](local-pipeline.md)
