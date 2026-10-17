# Codex development adapter

The adapter runs tool-disabled `codex exec` calls and retries eligible transport
failures. Each attempt uses one model turn. Experiment controllers manage
clarification, SQL regeneration and scoring.

- `minimal-benchmark-instructions.md` restricts agent and tool behaviour.
- `smoke-prompt.txt` is the literal-output preflight case.
- The passed 20 August 2026 run is recorded in
  [`../manifests/codex-proxy-smoke-2026-08-20.json`](../manifests/codex-proxy-smoke-2026-08-20.json).
- Isolation, audit, labelling and calibration requirements are documented in
  [`../notes/codex_subscription_benchmark_proxy_guide.md`](../notes/codex_subscription_benchmark_proxy_guide.md).

Record Codex as the provider for runs made with this adapter. The reported
benchmark results use the separate OpenAI Responses API adapter.
Freeze the prompt, scorer, retry policy and manifest before a large run.
