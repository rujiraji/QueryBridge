# QueryBridge roadmap

| Phase | Status | Scope |
| --- | --- | --- |
| 1. SQLite foundation | Implemented, awaiting independent review | Discover schemas and declared relationships, fingerprint DDL, and execute bounded read queries through the connector and CLI. |
| 2. Natural-language query pipeline | Planned | Use local Ollama models and ChromaDB retrieval of schema and business definitions. Validate generated SQL and evaluate answers against concession and unrelated datasets. |
| 3. API and React interface | Planned | Select a database, ask questions, and view the executed SQL and structured results. |
| 4. Analysis and exports | Planned | Add summaries, customized tables, charts, and reports with CSV, image, PDF, and basic presentation exports. |
| 5. Deployment | Planned | Package local services in containers and document setup and operation. |
