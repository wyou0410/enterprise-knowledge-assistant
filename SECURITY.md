# Security

This prototype is a single-user local CLI. It is not a multi-tenant service or an enterprise access-control system.

Keep keys in an ignored local `.env` or process environment. Provider URLs must be official HTTPS endpoints. Live mode uploads inputs and selected passages to those providers; logs remain local but can contain sensitive text. Never include them in issues or pull requests. Review the source allowlist and document permissions before collecting data.

To report a suspected vulnerability, use the repository's **Security → Report a vulnerability** feature when enabled. If unavailable, open an issue requesting a private reporting channel without including exploit details or secrets. No response-time guarantee is made.

Only the current main branch is maintained. A citation can be technically valid while the answer is wrong; the project does not claim prompt-injection immunity or validated policy accuracy.
