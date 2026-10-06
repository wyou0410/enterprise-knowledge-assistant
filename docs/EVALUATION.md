# Automated evaluation and evidence

The current release intentionally omits human answer scoring and does not call a separate model judge. Automated metrics describe engineering behavior and document retrieval, not answer correctness.

## Reproducible public checks

- Original synthetic cases: Chinese/English, role clarification, follow-up, partial support and missing evidence.
- Provider tests use mocked transports; no keys, paid API requests or real holdout data are used in CI.
- Regression tests cover messages containing the retrieved body, unknown citations, invalid vectors, index/config/hash mismatch, stable ranking, cache reuse, retry boundaries, question isolation and freeze/resume behavior.
- Native reports do not require or produce a human scoring table. Missing retrieval traces are excluded explicitly rather than being counted as measured failures.

Run:

```bash
uv sync --locked --extra test
uv run --locked --extra test pytest -q
uv run --locked knowledge-assistant evaluate --mode mock --run-id reproducibility-check
```

These engineering checks are independently reproducible from the repository. MOCK output is scripted; its success is not evidence of model accuracy.

## Recorded real-data development runs

The sanitized [aggregate JSON](../evaluations/results/dev-summary.json) was extracted from previously recorded live runs. No live requests were made to prepare this public release. Both runs used 30 checked English development questions and 35 turns, the same 20-document/726-chunk snapshot, prompt, DeepSeek model, Tongyi embeddings and implementation. Only Top-K changed from 3 to 5. B1 is retained rather than overwritten.

Of the 30 questions, 25 had reference doc_ids and 7 required multiple documents. Coverage is computed from the union of each question's recorded full retrieval traces; citations are not substituted for the full Top-K trace.

| Measure | Baseline Top-K3 | Candidate Top-K5 |
| --- | ---: | ---: |
| Question-level engineering/validation failures | 1/30 | 0/30 |
| Any reference document | 24/25 | 24/25 |
| All reference documents | 19/25 | 21/25 |
| All documents for multi-document references | 2/7 | 4/7 |

The baseline error came from a model answer containing URLs rejected by the response contract. The candidate's different result is an observation from one run, not a statistically established improvement in answer quality. Reported latency describes that recorded environment, not a performance guarantee. Currency cost remains unknown.

## What is not established

- Human answer accuracy, fact support, semantic completeness and the appropriateness of refusals were not scored.
- The 20-question holdout has not been formally run; it is not used to select the Top-K candidate.
- The real text, reference questions and raw traces are not publicly redistributed because a complete permission/attribution package has not been established.
- Consequently, these real-data aggregates are reported evidence, not an independently replayable public benchmark. A future permitted frozen bundle would be needed for exact corpus replay.
- Fetching current web pages creates a new snapshot. API model changes can alter outputs even with fixed parameters.

## Experiment discipline

Prepare and check references before real evaluation, keep near-duplicate question families in one split, and give every question a separate conversation. Freeze the final experiment identity before holdout. The ledger restricts rerunning an already exposed holdout version; a changed experiment needs a new independent test set or explicit reuse limitations. Do not silently reset it.

Reference question checking and data licensing are retained; neither is human scoring of generated answers. This release's acceptance is an operational CLI with automated evidence and explicit semantic-quality limitations.
