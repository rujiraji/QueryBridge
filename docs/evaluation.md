# Verification and evaluation

## Phase 1 review

The independent review of `8045064b6e32752596c9ebb2bf700aadd16fb972` passed all 26 generic tests on Python 3.12.14 with SQLite 3.53.1. Additional authorization and concurrent-WAL snapshot checks passed. The installed wheel CLI reproduced the fixture totals and preserved the source bytes. The reviewer could not independently rerun the authenticated tracker check. The Phase 2 implementer reran `python -B -m tests.check_tracker ../concession-stand-tracker` against `60bcd03787145a16ee5d0073bea36aabb224a982` and matched 17 tables, 19 declared foreign keys, all expected report totals, and five remaining units. Live tracker data and older deployed schemas remain unverified. [Tracker findings](concession-tracker.md)

## Phase 2 offline

```sh
python -m pip install ".[nl]"
python -B -m unittest discover -v
python -B -m tests.evaluate
```

The initial Phase 2 review reproduced 47 passing tests and 10 passing scripted cases. The reviewer found that the paid-unit question could omit the `status` definition from the model prompt. The original retrieval returned five definitions and excluded `status`. Counting cash and Square rows without a status filter gave four units, including the reversed cash sale. Applying the completed-only filter gave three.

The first context fix passed 50 unit and integration tests and 11 scripted cases. The date and event-scope follow-up passed 52 tests and all 13 scripted cases on Windows AMD64 with Python 3.12.13, SQLite 3.50.4, and ChromaDB 1.5.9. The suite uses real temporary Chroma storage, deterministic embeddings, and a fake loopback Ollama server. It covers required profile definitions, oversized context rejection, valid and unresolved UTC intervals, SQLite restrictions, and source preservation. Scripted cases verify pipeline behavior.

| Case | Independent expected result | Offline outcome |
| --- | --- | --- |
| Completed event 1 totals | Four records. Revenue 400 + 300 = 700 cents. Cost 120 + 90 + 60 + 180 = 450 cents. Profit 250 cents. | Pass |
| Completed inactive event 2 totals | One completed Square sale of two popcorn units at 300 cents each. Revenue 600 cents, cost 180 cents, profit 420 cents. Substituting active event 1 produces `[[4,700,450,250]]` instead of `[[1,600,180,420]]`. | Pass |
| Water revenue | Two paid units at the saved 200 cents each and one free unit yield 400 cents. | Pass |
| Paid units at event 1 | Two cash units plus one Square unit give three completed paid units. The fourth paid unit was reversed. | Pass |
| Reversals | One reversed event 1 transaction. | Pass |
| Recorded waste sales cost | Two wasted units at 90 cents each yield 180 cents. | Pass |
| Yesterday's revenue | Ask for range, timezone and day boundary. | Pass |
| Yesterday with explicit UTC bounds | From 2026-06-01 00:00 UTC inclusive to 2026-06-02 00:00 UTC exclusive, the only completed sale across all events is event 2's 600-cent Square sale. The interval reaches generation. | Pass |
| Units sold | Ask whether to include free units. | Pass |
| Square sales | Ask for recorded gross or net proceeds. | Pass |
| Actual Square processing fees | Unsupported because the fixture has no fee records. | Pass |
| Library copies by branch | North has 2 + 0 = 2. South has 5 + 1 = 6. | Pass |
| Average librarian salary | Unsupported because the fixture has no personnel or salary records. | Pass |

The evaluator compares rows, decision kinds, and the topic of each clarification or unsupported response. It checks fixture bytes against their state before indexing. Each case reports its decision source, total latency, and model-call latency when applicable. Query results include SQL and bound parameters. Cases that reach generation record the relation names, definition IDs, and relationship count. Failures include the full selected context and, for execution or parsing failures, the model's decision output.

## Earlier 11-case live model evaluation

Live verification ran on November 20, 2026 on Windows 11 25H2 AMD64 (build 26200.9974), with an Intel Core Ultra 7 255H, 31.46 GiB of RAM, and an NVIDIA GeForce RTX 5050 Laptop GPU with 8,151 MiB of VRAM and driver 616.56. The host initially had no Ollama server. We verified the SHA-256 and extracted the official standalone Ollama 0.40.16 ZIP to ignored workspace storage, then started it on `127.0.0.1:11434`. The server reported `Ollama cloud disabled: true` and selected CUDA. Python was 3.12.13, SQLite 3.50.4, and ChromaDB 1.5.9. [Official Windows release](https://github.com/ollama/ollama/releases/tag/v0.40.16)

| Model | Role | Manifest SHA-256 |
| --- | --- | --- |
| `qwen2.5-coder:7b` | Generation, Q4_K_M | `dae161e27b0e90dd1856c8bb3209201fd6736d8eb66298e75ed87571486f4364` |
| `qwen3.5:4b` | Generation, Q4_K_M | `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04` |
| `nomic-embed-text:latest` | Embedding, 768 dimensions | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |

Ollama offloaded both generators and the embedding model to the NVIDIA GPU with the llama.cpp runner. The configured chat context and output limit were 8,192 and 1,536 tokens respectively.

```powershell
python -B -m querybridge ready --generation-model qwen2.5-coder:7b --embedding-model nomic-embed-text:latest --ollama-timeout 120
python -B -u -m tests.evaluate --live --generation-model qwen2.5-coder:7b --embedding-model nomic-embed-text:latest --ollama-timeout 120
```

`ready` passed. The first run scored 6/11. The paid-unit query applied the completed filter and counted two transactions. Product revenue and waste cost queries omitted the relevant filters, and the library query misquoted table names. The salary response correctly reported missing salary data, but the evaluator treated “salaries” as a different topic. We clarified unit aggregation, product scope, waste scope, and SQL quoting, and updated the evaluator to accept both salary forms.

The final direct-response run scored 8/11 and exited 1. It passed five of eight model decisions and all three profile-rule clarifications. The paid-unit query summed the completed cash and Square items and returned three. The concession and library indexes took 0.422 s and 0.094 s to build. Source-byte checks passed for every case.

| Case | Source | Observed outcome | Total / model seconds |
| --- | --- | --- | --- |
| Event totals | Ollama | Pass, `[[4,700,450,250]]` | 9.000 / 8.938 |
| Water revenue | Ollama | Fail, `[[700]]`. The query summed event revenue without a product filter. | 3.125 / 2.218 |
| Paid units | Ollama | Pass, `[[3]]` | 3.266 / 3.094 |
| Reversals | Ollama | Fail, returned unsupported and overlooked event 1 in the question. | 1.531 / 1.469 |
| Recorded waste cost | Ollama | Pass, `[[180]]` | 2.062 / 2.015 |
| Yesterday's revenue | Profile rule | Pass, asked for time range and timezone. | 0.000 / — |
| Units sold scope | Profile rule | Pass, asked whether to include free units. | 0.000 / — |
| Square proceeds scope | Profile rule | Pass, asked for gross or net. | 0.000 / — |
| Square fees | Ollama | Pass, unsupported due to missing fee data. | 2.422 / 2.359 |
| Branch copies | Ollama | Fail, invalid SQL near `catalog` from an unescaped identifier. | 2.797 / 2.718 |
| Librarian salary | Ollama | Pass, unsupported due to missing salary data. | 1.234 / 1.187 |

All seven concession definitions reached each model call. The Water and paid-unit requests both received the four concession tables and the three declared relationships. The library case received the composite branch key and the exact catalog table name. The failures came from generated decisions with the relevant context present. The evaluator made one generation attempt per case that reached Ollama.

The paid-unit SQL and bound parameters were:

```sql
SELECT SUM(sti.quantity) AS paid_units_sold FROM sale_transactions st JOIN sale_transaction_items sti ON st.id = sti.transaction_id WHERE st.event_id = ? AND st.status = 'completed' AND st.payment_type IN ('cash', 'square')
```

Parameters `[1]`. The local trace `.querybridge-data/phase2-live-qwen25-direct.log` contains all generated SQL, bind values, and failure context. A separate chat probe confirmed that Ollama returned HTTP 400 for a rendered input of 9,019 tokens against the 8,192-token window. QueryBridge surfaced it as `ollama_error`. [Local setup](local-pipeline.md)

### Qwen 3.5 comparison

```powershell
python -B -m querybridge ready --generation-model qwen3.5:4b --embedding-model nomic-embed-text:latest --ollama-timeout 120
python -B -u -m tests.evaluate --live --generation-model qwen3.5:4b --embedding-model nomic-embed-text:latest --ollama-timeout 120
```

`ready` passed. The initial comparison with the model's thinking default reached the output limit on its first recorded decision at 76.609 s. We stopped that run and requested direct structured responses with `think: false`. The direct run scored 9/11 and exited 1, passing six of eight model decisions and all three profile-rule clarifications. Concession and library index builds took 0.328 s and 0.094 s. All source-byte checks passed. [Ollama thinking controls](https://docs.ollama.com/capabilities/thinking)

| Case | Source | Observed outcome | Total / model seconds |
| --- | --- | --- | --- |
| Event totals | Ollama | Pass, `[[4,700,450,250]]` | 1.921 / 1.890 |
| Water revenue | Ollama | Pass, `[[400]]` | 1.829 / 1.750 |
| Paid units | Ollama | Fail, `[[2]]`. The query counted item rows and used the active event. | 1.765 / 1.719 |
| Reversals | Ollama | Pass, `[[1]]` | 1.485 / 1.454 |
| Recorded waste cost | Ollama | Pass, `[[180]]` | 1.968 / 1.922 |
| Yesterday's revenue | Profile rule | Pass, asked for time range and timezone. | 0.000 / — |
| Units sold scope | Profile rule | Pass, asked whether to include free units. | 0.000 / — |
| Square proceeds scope | Profile rule | Pass, asked for gross or net. | 0.000 / — |
| Square fees | Ollama | Pass, unsupported due to missing fee data. | 2.125 / 2.063 |
| Branch copies | Ollama | Fail, invalid SQL because the model removed the embedded quotes from `Book "catalog"`. | 1.641 / 1.594 |
| Librarian salary | Ollama | Pass, unsupported due to missing salary data. | 1.390 / 1.344 |

The paid-unit prompt contained the completed-only and unit aggregation rules. The generated SQL still counted rows and substituted the active-event flag:

```sql
SELECT COUNT(*) AS paid_units_sold FROM sale_transaction_items JOIN sale_transactions ON sale_transaction_items.transaction_id = sale_transactions.id JOIN events ON sale_transactions.event_id = events.id WHERE events.is_active = 1 AND sale_transactions.status = 'completed' AND sale_transactions.payment_type IN ('cash', 'square')
```

Parameters `[]`. The local trace `.querybridge-data/phase2-live-qwen35-direct.log` includes generated SQL, bind values, case times, and failure context. Live accuracy on these fixtures varies by model even with all profile definitions present.

<details>
<summary>Generated SQL and bound parameters from the final live runs</summary>

`qwen2.5-coder:7b`

```jsonl
{"case":"event-totals","sql":"SELECT COUNT(*) AS completed_transaction_count, SUM(total_revenue_cents) AS recorded_revenue, SUM(total_cost_cents) AS total_cost, SUM(total_profit_cents) AS recorded_profit FROM sale_transactions WHERE event_id = ? AND status = 'completed'","parameters":[1]}
{"case":"water-revenue","sql":"SELECT SUM(total_revenue_cents) AS recorded_revenue_cents FROM sale_transactions WHERE event_id = ? AND status = 'completed'","parameters":[1]}
{"case":"paid-units","sql":"SELECT SUM(sti.quantity) AS paid_units_sold FROM sale_transactions st JOIN sale_transaction_items sti ON st.id = sti.transaction_id WHERE st.event_id = ? AND st.status = 'completed' AND st.payment_type IN ('cash', 'square')","parameters":[1]}
{"case":"waste-cost","sql":"SELECT SUM(total_cost_cents) AS total_cost_cents FROM sale_transactions WHERE event_id = ? AND status = 'completed' AND payment_type = 'waste'","parameters":[1]}
{"case":"branch-copies","sql":"SELECT \"Library branches\".\"label\", SUM(\"Book \"catalog\".\"copies\") AS total_copies FROM \"Library branches\" JOIN \"Book \"catalog\" ON \"Library branches\".\"district\" = \"Book \"catalog\".\"district\" AND \"Library branches\".\"code\" = \"Book \"catalog\".\"branch_code\" GROUP BY \"Library branches\".\"label\" ORDER BY \"Library branches\".\"label\" ASC","parameters":[]}
```

`qwen3.5:4b`

```jsonl
{"case":"event-totals","sql":"SELECT COUNT(*) AS completed_transaction_count, SUM(st.total_revenue_cents) AS recorded_revenue, SUM(st.total_cost_cents) AS total_cost, SUM(st.total_profit_cents) AS recorded_profit FROM sale_transactions st WHERE st.event_id = 1 AND st.status = 'completed'","parameters":[]}
{"case":"water-revenue","sql":"SELECT SUM(si.revenue_cents) AS total_revenue_cents FROM sale_transaction_items si JOIN sale_transactions st ON si.transaction_id = st.id JOIN products p ON si.product_id = p.id WHERE p.name = 'Water' AND st.event_id = 1 AND st.status = 'completed'","parameters":[]}
{"case":"paid-units","sql":"SELECT COUNT(*) AS paid_units_sold FROM sale_transaction_items JOIN sale_transactions ON sale_transaction_items.transaction_id = sale_transactions.id JOIN events ON sale_transactions.event_id = events.id WHERE events.is_active = 1 AND sale_transactions.status = 'completed' AND sale_transactions.payment_type IN ('cash', 'square')","parameters":[]}
{"case":"reversals","sql":"SELECT COUNT(*) AS reversed_count FROM sale_transactions WHERE event_id = ? AND status = 'reversed'","parameters":[1]}
{"case":"waste-cost","sql":"SELECT SUM(st.total_cost_cents) AS total_cost_cents FROM sale_transactions st JOIN events e ON st.event_id = e.id WHERE st.status = 'completed' AND st.payment_type = 'waste' AND e.id = 1","parameters":[]}
{"case":"branch-copies","sql":"SELECT b.label, SUM(bc.copies) AS total_book_copies FROM \"Book catalog\" bc JOIN \"Library branches\" b ON bc.district = b.district AND bc.branch_code = b.code GROUP BY b.label ORDER BY b.label ASC","parameters":[]}
```

</details>

## UTC and inactive-event live rerun

The follow-up ran both previously tested model digests on the same Ollama 0.40.16 CUDA setup with cloud features disabled. The `ready` checks passed. The live commands were:

```powershell
python -B -u -m tests.evaluate --live --generation-model qwen2.5-coder:7b --embedding-model nomic-embed-text:latest --ollama-timeout 120
python -B -u -m tests.evaluate --live --generation-model qwen3.5:4b --embedding-model nomic-embed-text:latest --ollama-timeout 120
```

Qwen 2.5 Coder passed **9/13** cases and Qwen 3.5 passed **10/13**. Each run included three profile-rule clarifications and ten model decisions. Both evaluators exited 1 for the mismatches below. All source-byte checks passed. A separate scripted check deliberately bound event 1 for the inactive-event case and confirmed that the evaluator rejected `[[4,700,450,250]]`.

| Case | Qwen 2.5 Coder result / seconds | Qwen 3.5 result / seconds |
| --- | --- | --- |
| Event 1 totals | `[[4,700,450,250]]` / 6.641 | `[[4,700,450,250]]` / 11.375 |
| Inactive event 2 totals | `[[1,600,180,420]]` / 2.953 | `[[1,600,180,420]]` / 2.391 |
| Water revenue | Fail, summed all event revenue to 700 cents / 6.375 | Fail, used “Water” as the event name and returned NULL / 2.031 |
| Paid units | Fail, counted two rows / 2.313 | Fail, counted two rows and substituted the active-event predicate / 2.156 |
| Reversals | Fail, returned unsupported despite the explicit event ID / 1.593 | `[[1]]` / 1.516 |
| Recorded waste cost | `[[180]]` / 1.891 | `[[180]]` / 1.859 |
| Yesterday without bounds | Profile clarification / 0.000 | Profile clarification / 0.000 |
| Yesterday with explicit UTC bounds | Generated a query yielding `[[600]]` / 2.703 | Generated a query yielding `[[600]]` / 2.578 |
| Units sold scope | Profile clarification / 0.000 | Profile clarification / 0.000 |
| Square proceeds scope | Profile clarification / 0.000 | Profile clarification / 0.000 |
| Square fees | Unsupported / 2.219 | Unsupported / 1.844 |
| Branch copies | Fail, malformed quoting near `catalog` / 2.672 | Fail, looked for a table named `Book catalog` / 1.672 |
| Librarian salary | Unsupported / 1.172 | Unsupported / 1.234 |

Both models scoped the inactive event to Summer fair. The exact UTC interval reached both models, and both matched the expected fixture rows. Qwen 2.5 Coder used inclusive `BETWEEN` despite the exclusive end. Qwen 3.5 used `>=` and `<`. Both compared `Z` literals directly with timestamps stored using `+00:00`, which can misclassify rows at a bound. The fixture has its qualifying sale inside the interval, so the score measures the returned rows for this fixture. The scripted query uses `julianday` and a half-open interval.

<details>
<summary>Generated SQL and bind values for the two new cases</summary>

```jsonl
{"model":"qwen2.5-coder:7b","case":"inactive-event-totals","sql":"SELECT COUNT(st.id) AS completed_transaction_count, SUM(st.total_revenue_cents) AS recorded_revenue, SUM(st.total_cost_cents) AS total_cost, SUM(st.total_profit_cents) AS recorded_profit FROM sale_transactions st JOIN events e ON st.event_id = e.id WHERE e.name = 'Summer fair' AND st.status = 'completed' AND e.is_active = 0","parameters":[]}
{"model":"qwen2.5-coder:7b","case":"timezone-resolved","sql":"SELECT SUM(total_revenue_cents) AS total_revenue_cents FROM sale_transactions WHERE status = 'completed' AND created_at BETWEEN '2026-06-01T00:00:00Z' AND '2026-06-02T00:00:00Z'","parameters":[]}
{"model":"qwen3.5:4b","case":"inactive-event-totals","sql":"SELECT COUNT(*) AS completed_transaction_count, SUM(st.total_revenue_cents) AS recorded_revenue_cents, SUM(st.total_cost_cents) AS total_cost_cents, SUM(st.total_profit_cents) AS recorded_profit_cents FROM sale_transactions st JOIN events e ON st.event_id = e.id WHERE e.name = 'Summer fair' AND st.status = 'completed'","parameters":[]}
{"model":"qwen3.5:4b","case":"timezone-resolved","sql":"SELECT SUM(st.total_revenue_cents) AS total_revenue_cents FROM sale_transactions st JOIN events e ON st.event_id = e.id WHERE st.status = 'completed' AND st.created_at >= '2026-06-01T00:00:00Z' AND st.created_at < '2026-06-02T00:00:00Z'","parameters":[]}
```

</details>

The workspace traces `.querybridge-data/phase2-date-scope-qwen2.5-coder-7b.log` and `.querybridge-data/phase2-date-scope-qwen3.5-4b.log` contain the per-case timing, query text, bind values, and failure context.

## Accuracy hardening after the Phase 2 merge

This work starts from merged `main` at `f4190c0321ff79eb82a9ad70d828aeeedf793d6a` and awaits independent review. On Linux x86_64 with Python 3.12.14, SQLite 3.53.1, and ChromaDB 1.5.9, the following passed:

```sh
python -B -m unittest discover -v  # 59 tests
python -B -m tests.evaluate       # 21/21 scripted cases
```

Every evaluator source-byte check passed. The original 13 questions and their expected results remain unchanged. The scripted date decision now uses `qb_utc_us` to preserve microseconds. Eight added cases cover exact boundaries, adjacent microseconds, product quantities, and inactive-event quantities. The suite still uses real temporary Chroma storage and a fake local HTTP server. These are offline integration results, not new model accuracy measurements.

`uv build --wheel` also passed. The wheel was installed without the `nl` extra into a separate environment and invoked from outside the checkout with Python's isolated mode. Its query CLI returned boundary IDs `[[105],[106],[110]]`; the profile's named-product and paid-unit SQL patterns returned `[[400]]` and `[[3]]`. Each check preserved its fixture bytes. A focused `--case utc-start-microsecond --case inactive-paid-units` evaluator run passed 2/2 cases.

The prompt now supplies a `sql_name` for each relation and column, including executable escaping for embedded quotes. The concession profile remains seven definitions and advances to version 4 with parameterized product and paid-unit examples. All definitions remain required context. A tested prompt can copy both `"Book ""catalog"""` and a column such as `"shelf ""label"""` into executable SQL. The timestamp helper supplies exact integer arithmetic; generation still has to choose a correct predicate.

### Boundary ledger

The separate `utc-boundaries` fixture extends a copy of the original concessions fixture. For `[2026-06-01T00:00:00Z, 2026-06-02T00:00:00Z)`, its expected rows are independently specified:

| Transaction | Stored timestamp | Revenue cents | Included |
| --- | --- | ---: | --- |
| 108 | `2026-05-31T23:59:59.999999Z` | 50 | No |
| 106 | `2026-06-01T00:00:00+00:00` | 100 | Yes |
| 105 | `2026-06-01T16:00:00.000001+00:00` | 600 | Yes |
| 110 | `2026-06-01T23:59:59.999999Z` | 400 | Yes |
| 107 | `2026-06-02T00:00:00+00:00` | 200 | No |
| 109 | `2026-06-02T00:00:00.000001+00:00` | 25 | No |

Expected revenue is **100 + 600 + 400 = 1100 cents**. Listing IDs independently catches omissions and extra rows even if their money totals happen to cancel.

| Added case | Expected rows |
| --- | --- |
| UTC boundary revenue | `[[1100]]` |
| UTC boundary IDs, ordered by ID | `[[105],[106],[110]]` |
| First microsecond of the interval | `[[106]]` |
| Last microsecond of the interval | `[[110]]` |
| Event 1 interval `[16:00:00.000002Z,16:00:00.000004Z)` on September 1 | `[[101],[102]]` |
| Paid Water units at event 1 | `[[2]]` |
| Water units including free and excluding waste at event 1 | `[[3]]` |
| Paid units at inactive event 2 | `[[2]]` |

`tests.test_evaluate` replays 11 deliberately wrong decisions and requires the evaluator to reject each one while preserving source bytes. They include both recorded date predicates, an inclusive end after normalization, Julian-day precision loss, omitted product filters, a product name used as an event, row counts substituted for quantities, reversed paid units, active-event substitutions, and removed identifier quotes. No model is called during these replays.

### Fresh live evaluation pending

This execution environment has no Ollama runtime or model cache. The earlier **9/13** and **10/13** results describe commit `d3f21e0` and remain historical. They do not measure the new prompt, helper usage, or expanded 21-case evaluation. The remaining product, quantity, quoting, reversal, and boundary errors are not declared resolved by the offline results.

Rerun from this branch in the existing local model workspace, retaining the recorded model digests and cloud-disabled setup:

```powershell
python -B -u -m tests.evaluate --live --generation-model qwen2.5-coder:7b --embedding-model nomic-embed-text:latest --ollama-timeout 120
python -B -u -m tests.evaluate --live --generation-model qwen3.5:4b --embedding-model nomic-embed-text:latest --ollama-timeout 120
```

Use repeated `--case` flags for a focused rerun, for example `--case utc-boundary-revenue --case microsecond-window --case paid-product-units --case branch-copies`. Full runs now contain 18 model decisions and three profile-rule clarifications. Record the case outcomes, model digests, SQL, parameters, timings, and source-byte results before making an accuracy comparison.
