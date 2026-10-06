# Contributing

Keep changes focused on traceable knowledge question answering. Discuss major changes in an issue first.

1. Create a branch for your change.
2. Run `uv sync --locked --extra test` and `uv run --locked --extra test pytest -q`.
3. Add meaningful regression coverage for changed behavior, especially citation, snapshot, retry, or evaluation boundaries.
4. Explain the user-visible change and actual validation in your pull request.

Tests must use synthetic data or mocked transports. Do not call live providers in CI. Do not commit keys, traces, account identifiers, full scraped documents, or human-labelled data without explicit redistribution permission. Preserve the distinction between MOCK, live results, and unverified semantic quality.

Report security issues using the procedure in SECURITY.md rather than a public exploit or credential dump.
