# Local natural-language queries

Install the pipeline dependencies with `python -m pip install ".[nl]"`. Start a recent local Ollama version that supports structured chat output and the batch embedding API. For a local-only server, set `OLLAMA_NO_CLOUD=1` before starting it, or set `disable_ollama_cloud` in Ollama's server configuration and restart it. QueryBridge connects to a loopback HTTP origin and only selects locally installed tags. Cloud tags and model entries with remote upstreams are excluded. [Ollama installation on Windows](https://docs.ollama.com/windows) · [Local-only server settings](https://docs.ollama.com/faq#how-do-i-disable-ollamas-cloud-features)

For a manual PowerShell startup, run the following in a separate terminal after closing any existing Ollama server.

```powershell
$env:OLLAMA_NO_CLOUD = '1'
ollama serve
```

The review workspace has a standalone Ollama runtime and model cache in ignored storage. To restart that server from the repository root:

```powershell
$env:OLLAMA_NO_CLOUD = '1'
$env:OLLAMA_HOST = '127.0.0.1:11434'
$env:OLLAMA_MODELS = (Resolve-Path '.querybridge-data/ollama-phase2-models').Path
& '.querybridge-data/ollama-v0.40.16/ollama.exe' serve
```

After starting Ollama, pull the model tags in a second terminal. The live evaluation record includes the observed accuracy and latency for the tested tags.

```powershell
ollama pull qwen2.5-coder:7b
ollama pull nomic-embed-text:latest
$env:QUERYBRIDGE_GENERATION_MODEL = 'qwen2.5-coder:7b'
$env:QUERYBRIDGE_EMBEDDING_MODEL = 'nomic-embed-text:latest'
python -m querybridge ready
```

`ready` checks the installed generation tag, calls the configured embedding model, and opens the local Chroma store. Its JSON includes the Ollama version, model names and digests, and embedding dimension. Generation behavior is checked with the live evaluator. [Local generation model](https://ollama.com/library/qwen2.5-coder:7b) · [Local embedding model](https://ollama.com/library/nomic-embed-text:latest)

## Ask about the concession fixture

Choose a fresh fixture path and run these commands from the repository root.

```powershell
python -B -m tests.fixtures concessions .querybridge-data/nl-demo.sqlite3
python -m querybridge index .querybridge-data/nl-demo.sqlite3 --profile profiles/concessions.json
python -m querybridge ask .querybridge-data/nl-demo.sqlite3 "For event 1, give the completed transaction count, recorded revenue, total cost, and recorded profit in that order. Give amounts in cents." --profile profiles/concessions.json
```

The expected fixture values are `[[4,700,450,250]]`. A successful response contains the SQL actually sent to SQLite, its positional array or named parameter object, column names, rows, and truncation fields. A query with zero matching rows returns `empty`. The command “What was the revenue yesterday?” with this profile returns a focused question about the time range, timezone, and reporting-day boundary. [Fixture arithmetic and outcomes](evaluation.md)

## Configuration

| Setting | Environment | CLI |
| --- | --- | --- |
| Generation tag | `QUERYBRIDGE_GENERATION_MODEL` | `--generation-model` for `ready` and `ask` |
| Embedding tag | `QUERYBRIDGE_EMBEDDING_MODEL` | `--embedding-model` |
| Ollama origin | `QUERYBRIDGE_OLLAMA_URL` | `--ollama-url`, default `http://127.0.0.1:11434` |
| Chroma directory | `QUERYBRIDGE_INDEX_DIR` | `--index-dir`, default `.querybridge-data/chroma` |
| Deadline per HTTP request | | `--ollama-timeout`, default 60 seconds |
| SQLite limits | | `--timeout`, `--max-rows`, `--max-result-bytes` on `ask` |
| Business definitions | | `--profile PATH` on `index` and `ask`, default generic profile |

CLI flags take precedence over environment values. A profile is a local JSON file with an `id`, `version`, an array of `definitions` with unique `id` and `text` fields, and an ordered `clarifications` array. Every definition is required prompt context. A clarification rule has a case-insensitive `when` regular expression and a `question` to return. An optional `unless` expression or `unless_utc_interval` boolean can exempt an explicitly scoped question. The interval exemption requires valid, increasing ISO date-time bounds with UTC specified on each bound or on the range. For example, “yesterday, from 2026-06-01T00:00:00Z to 2026-06-02T00:00:00Z” reaches generation under concession profile version 3. Bare “yesterday” still prompts for clarification. Review profiles as application configuration and increment the version when definitions change. Reporting-day, free-unit, combined waste, and net proceeds policy remain open.

## Execution and index behavior

Chroma receives explicit vectors from Ollama's `/api/embed` and queries by vector. It stores schema metadata and business definitions without source rows, with telemetry disabled. Each database path and profile ID has a collection. The revision includes the SQLite fingerprint, profile content and version, queryable catalog representation, and embedding endpoint, tag and digest. QueryBridge replaces stale collections within a scope and rebuilds incomplete ones on access. Access to a QueryBridge index directory is serialized with a local lock. Use an index directory dedicated to QueryBridge. [Chroma explicit embeddings](https://docs.trychroma.com/docs/collections/manage-collections#embedding-functions) · [Ollama embedding API](https://docs.ollama.com/api/embed)

`ask` can build the index on demand. It retrieves up to five relation documents by similarity and adds declared join paths between selected relations. The prompt includes every definition from the selected profile in profile order. Restricted views, virtual and shadow tables, and virtual generated columns are excluded from the queryable catalog. The question and context JSON must fit the existing 24 KiB budget. An oversized prompt returns `index_error` before the model is called.

Ollama gets one nonstreaming `/api/chat` request per question that reaches generation, with a JSON Schema in `format`. The application checks the decision's exact fields and bound values, then sends query proposals to the existing restricted connector. The selected schema fingerprint is checked again inside the execution snapshot. QueryBridge returns SQLite values directly. [Ollama chat API](https://docs.ollama.com/api/chat) · [SQLite execution contract](sqlite.md)

QueryBridge asks Ollama to disable prompt truncation and context shifting. If the rendered prompt exceeds the model's context window, a server error is surfaced as `ollama_error` on runtimes that support these controls. QueryBridge requests direct responses on models with a thinking toggle to preserve the bounded output budget for the structured decision. [Ollama 0.40.16 chat request fields](https://github.com/ollama/ollama/blob/v0.40.16/api/types.go#L2362-L2413) · [Ollama thinking controls](https://docs.ollama.com/capabilities/thinking)

Responses with `clarification` or `unsupported` exit successfully without query execution. Operational and generation failures use JSON on stderr with exit code `1`, including `ollama_unavailable`, `model_unavailable`, `ollama_timeout`, `ollama_response_error`, `generation_error`, `index_error`, and `schema_changed`. SQLite policy rejections, invalid SQL, query timeouts, and database failures retain their Phase 1 statuses. Each Ollama response body is capped at 2 MiB. The HTTP deadline applies during connection, request, and response. SQLite uses its own separate deadline.
