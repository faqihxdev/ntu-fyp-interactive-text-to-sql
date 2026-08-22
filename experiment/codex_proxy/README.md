# Codex subscription proxy fixtures

These files support a strict, one-turn, tool-disabled `codex exec` proxy. They
do not implement the shared interaction contract or a Sphinteract loop.

- `minimal-benchmark-instructions.md` removes normal agent/tool behavior.
- `smoke-prompt.txt` is the literal-output preflight case.
- The passed 20 August 2026 run is recorded in
  [`../manifests/codex-proxy-smoke-2026-08-20.json`](../manifests/codex-proxy-smoke-2026-08-20.json).
- The complete isolation, audit, labeling, and calibration rules remain in
  [`../notes/codex_subscription_benchmark_proxy_guide.md`](../notes/codex_subscription_benchmark_proxy_guide.md).

Do not report this surface as the OpenAI API. Do not start a large run until a
task-specific prompt, scorer, retry policy, and manifest have been frozen.
