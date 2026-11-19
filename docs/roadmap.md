# QueryBridge roadmap

| Phase | Status | Scope |
| --- | --- | --- |
| 1. SQLite foundation | Independently reviewed | Discover schemas and declared relationships, fingerprint DDL, and execute bounded read queries through the connector and CLI. |
| 2. Natural-language query pipeline | Implemented and awaiting independent review. Offline verification complete. Live Ollama accuracy unverified. | Use local generation and embedding models with ChromaDB retrieval. Validate decisions and run proposals through the SQLite connector. |
| 3. API and React interface | Planned | Select a database, ask questions, and view the executed SQL and structured results. |
| 4. Analysis and exports | Planned | Add summaries, customized tables, charts, and reports with CSV, image, PDF, and basic presentation exports. |
| 5. Deployment | Planned | Package local services in containers and document setup and operation. |
