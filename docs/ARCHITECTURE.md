# Architecture and decisions

The application is a synchronous, single-user Python CLI. It keeps the retrieval and answering core callable directly, so the evaluator exercises the same code as terminal chat.

| Component | Responsibility |
| --- | --- |
| `cli.py` | Native chat, data/index and evaluation commands. |
| `chat.py` | Bounded sessions, fictional demo, explicit model messages, citation checks and traces. |
| `providers.py` | Authorized HTTPS embedding/chat requests; sanitized errors and bounded 429 retries. |
| `index.py` | SQLite index, config/content-addressed embedding cache, integrity checks and cosine ranking. |
| `data.py` / `common.py` | Fixed URL allowlist, cleaning, preserved provenance, hashes and local path handling. |
| `evaluation.py` / `native.py` | Per-question isolation, automated reporting, experiment identity, freeze/resume and exposure ledger. |

## Deliberate trade-offs

**SQLite and exact cosine ranking.** The entire small index is loaded into memory. This avoids operating a vector database service and makes the stored vectors easy to inspect. It is not designed for a large concurrent corpus.

**Explicit context assembly.** A system contract, selected document passages, bounded conversation history and the current question are serialized into the model messages. Both full retrieval and actually sent chunks are recorded, including budget exclusions. Previous generated answers are not policy evidence. A bounded previous user question can help retrieval on follow-ups; this simple query combination can also introduce irrelevant prior context.

**Citation validation.** The model returns JSON with status, answer, citation_chunk_ids and optional clarification. Cited IDs must occur in the passages actually sent. Links come from the stored source metadata. `support_verified=false` remains explicit because locator validation is not semantic verification.

**Immutable experiments.** Dataset/snapshot, chunks, vectors, embedding configuration and dimension are checked. Index files are atomically replaced after successful construction. Evaluation identities include code, prompt, dependency lock, configuration, index binding and question hashes. Resume rejects changed identities. Remote model IDs do not guarantee unchanged model implementations or identical responses.

**Limited retries.** A known 429 may be retried with a bound. Ambiguous completion of chargeable POST requests is not repeated automatically. Error responses never become scripted live answers.

**Minimal interface.** Standard `input()` and `print()`, four slash commands, in-memory bounded conversation, local traces. No web server, Dify dependency, streaming framework or tool execution loop.

Earlier adapter/provenance compatibility helpers remain regression-tested inside the package. Account-specific bindings, exported workflows, deployment screenshots and historical operational documents are excluded from the public release. They are not required by the native CLI.

## Boundaries

Document instruction handling is a prompt design plus citation validation, not a proof of injection resistance. User-provided configuration/data remain local inputs. The application does not establish employment policies, grant approvals, enforce permissions or execute business processes.
