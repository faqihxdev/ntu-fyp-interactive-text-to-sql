# Codex development adapter

`runner.py` implements `CodexSubscriptionProxy`, selected by
`--provider codex` in experiment runners. It runs one tool-disabled, ephemeral
`codex exec` call per model request in an empty temporary directory.

Each request supplies an instruction file and JSON output schema. The adapter
removes inherited API keys, applies strict configuration, records the CLI
version and rejects tool activity, missing completion events or invalid output.
Eligible connection failures receive bounded, logged retries. Controllers
handle SQL repair and clarification separately.

Check the installed client before use:

```powershell
codex --version
codex login status
codex exec --help
```

ChatGPT authentication and all flags used by `build_codex_command` are required.
Pin the CLI version for a run; do not silently drop rejected configuration.
`minimal-benchmark-instructions.md` restricts tool behaviour, and
`smoke-prompt.txt` provides a literal-output preflight prompt.

From `experiment/`, offline tests require no credentials:

```powershell
uv run --frozen python -m unittest codex_proxy.test_runner -v
```

Record these runs as `codex_subscription_proxy`. They measure that client
configuration, not direct API latency or cost. All [reported study results](../RESULTS.md)
use the separate OpenAI Responses API adapter.
