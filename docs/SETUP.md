# Setup and live use

Run commands from the repository root. Python 3.11/3.12 and uv are required for the locked workflow. The default `chat` command is the original fictional MOCK demo and needs no keys.

## 1. Configure local credentials

Copy `.env.example` to `.env` if you do not already have one (`Copy-Item .env.example .env` in PowerShell; `cp .env.example .env` on Linux). Fill it locally:

```text
DEEPSEEK_API_KEY=<your authorized local key>
DASHSCOPE_API_KEY=<your authorized local key>
NATIVE_ALLOW_LIVE=true
```

Do not paste keys into issues or chat. The file is loaded only with explicit `--env-file`; process variables take precedence. `true` authorizes real requests, which may incur provider costs. No account or prepaid credit is included.

Set `embedding.base_url` in `configs/native-baseline.yaml` to the official **compatible-mode/v1** address for your account and region. For example, current provider documentation describes the Beijing form `https://<workspace-id>.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`. Replace the workspace ID with your own. Do not use the native `/api/v1` address with this compatible adapter. Confirm `chat_model.model`, `embedding.model`, and `dimensions` against the models enabled in your account.

The configuration includes the API IDs used in the recorded experiment, `deepseek-v4-pro` and `text-embedding-v4`/1024 dimensions. They are examples, not a guarantee of account access or future availability. Reference interfaces: [DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) and [Tongyi embedding](https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api).

## 2. Acquire a permitted corpus

No real corpus comes with this repository. The acquisition tool is deliberately restricted to an explicit GitLab Handbook HTTPS URL whitelist in `configs/sources.yaml`, without link discovery or recursive crawling. Confirm permission to use the documents and comply with their own terms; project MIT licensing does not license those documents.

```bash
uv run --locked knowledge-assistant fetch --config configs/sources.yaml
uv run --locked knowledge-assistant validate-data
uv run --locked knowledge-assistant prepare --manifest data/manifests/documents.csv
```

Read the results: pages can fail, change, or redirect outside the allowlist and be excluded. `prepare` returns the actual snapshot ID. Set it as `knowledge_snapshot` in both native configs if comparing them. Do not use a historical snapshot ID without its exact files. Character-based chunks preserve whole paragraphs; oversized inputs are rejected rather than silently truncated.

Raw HTML, cleaned text, source URL, acquisition time, content hashes, manifest, and chunks stay under ignored `data/`. Acquisition time is not policy update time. Existing source snapshots are not rewritten by live chat.

## 3. Build and chat

```bash
uv run --locked knowledge-assistant build-index --config configs/native-baseline.yaml --env-file .env
uv run --locked knowledge-assistant chat --mode live --config configs/native-baseline.yaml --env-file .env
```

Indexing reports its upper-bound batch count. Content/config-addressed embedding cache entries are reused; completed batches are committed locally. `knowledge_snapshot` and the embedding configuration must match the index. Only explicit HTTP 429 gets bounded retries; ambiguous timeouts/connection failures and 5xx are not automatically repeated. Cost is reported as unknown unless independently established.

Use `/source 1` to inspect the last citation, `/new` to clear conversation history, `/help` for commands, and `/exit` to quit. Citations link to stored document URLs; the CLI prints passages without launching a browser. EOF exits normally. Interrupting a live call does not prove the remote request was cancelled.

## 4. Inspect a trace

Default logs are under `data/native/chat-logs/`. They include retrieval, the actual messages sent before model completion, hashes, configuration identity, response, citations, usage and errors. They do not include authorization headers. Logs can contain private questions/passages: keep them local.

Citation validation rejects unknown chunk IDs, unsupported response shapes, and model-supplied URLs. Citation presence alone does not validate an answer's claims. An embedding failure has no claimed retrieval trace. The app does not silently substitute a demo answer when live mode fails.

## 5. Automated evaluation

The original synthetic cases work immediately:

```bash
uv run --locked knowledge-assistant evaluate --mode mock --run-id local-demo
uv run --locked knowledge-assistant report --run-dir evaluations/runs/local-demo
```

For real data, author a question file using the fields validated in `evaluation.py`. Check the references and split membership before setting the review metadata; this is question/reference preparation, not human scoring of generated answers. Formal real questions are currently English. Chinese functionality can be tried interactively but is not a formal Chinese quality benchmark.

```bash
uv run --locked knowledge-assistant evaluate --version native-baseline --mode live --split dev --questions <your-reviewed-question-file> --env-file .env
```

Each question gets its own conversation. Native evaluation does not generate or depend on `scores.csv`. Real evaluation requires genuine reviewed reference questions; mock cannot run real questions or holdout. Preserve fixed split membership. Freeze before formal holdout; the ledger prevents silently resetting exposure. Details are in [EVALUATION.md](EVALUATION.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Live disabled | Explicit `NATIVE_ALLOW_LIVE=true`; environment values override the env file. |
| Missing snapshot/index | Run data preparation, set the returned snapshot ID, then build the index. |
| Official supplier URL required | Configure the appropriate HTTPS compatible endpoint and account region. |
| Invalid vectors or stale index | Confirm dimension/model configuration; rebuild without overwriting the old experiment. |
| Timeout completion unknown | Inspect local records and provider activity before manually retrying. |
| Answer/citation validation failed | Inspect the raw local trace; no source or supported answer is invented to hide failure. |
