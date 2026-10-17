# Codex subscription benchmark adapter

## Scope

The adapter runs one-shot inference through Codex authenticated with a ChatGPT subscription.

This is a **temporary proxy**, not an API-equivalent run. The benchmark is simple data-in/result-out inference: no file editing, shell commands, browsing, connectors, or multi-agent work should occur.

Label all results as:

> Codex subscription proxy: `<model>`, `<reasoning effort>`, Codex CLI `<version>`, tool-disabled configuration

Do **not** label the results as “OpenAI API,” “Responses API,” or “ChatGPT UI.”

## Why use `codex exec`

Use `codex exec`, not an interactive Codex session. It is the supported non-interactive interface, accepts a prompt on stdin, emits machine-readable JSONL, can write the final answer separately, and supports a JSON output schema.

For this one-turn benchmark, wrapping `codex exec` as a subprocess is simpler and more auditable than the Codex SDK. The SDK controls the same local Codex agent/app-server; it does not turn subscription access into a raw Responses API endpoint.

## Inference interface and reproducibility

Even after stripping tools and extra context, Codex is not identical to a direct Responses API call:

- Codex still supplies a client/runtime layer and request metadata.
- ChatGPT-authenticated model availability or presets may differ from API-key availability.
- Codex does not expose the full raw API request surface. In particular, its documented config does not provide direct equivalents for every API parameter such as `temperature`, `top_p`, `seed`, `max_output_tokens`, storage, or logprobs.
- A matching model name does not prove identical serving configuration.
- Codex startup and agent overhead invalidate direct API latency, throughput, and cost comparisons.

This proxy is suitable for provisional task-accuracy or output-quality measurements. It is not suitable for publishing raw-model, API-latency, token-cost, or provider-fairness claims without later calibration against the real API.

## 1. Preflight

Run and save the output of:

```bash
codex --version
codex login status
codex exec --help
codex features list
```

Requirements:

1. `codex login status` must report ChatGPT authentication, not API-key authentication.
2. The benchmark process must not inherit `OPENAI_API_KEY` or `CODEX_API_KEY`.
3. Pin and record the Codex CLI version. Do not upgrade halfway through a benchmark run.
4. Confirm the installed version accepts `--ephemeral`, `--ignore-user-config`, `--ignore-rules`, `--json`, `--output-last-message`, and `--output-schema` if a schema is required.

If ChatGPT authentication is not active:

```bash
codex logout
codex login
codex login status
```

Do not copy or share `~/.codex/auth.json`. Treat it as a password. Run the benchmark only on the trusted machine/account where the user logged in.

## 2. Match the intended API configuration

Before running any cases, write down:

- Intended API model
- Codex proxy model
- Reasoning effort
- Verbosity
- Original system/developer instructions
- Original output format or JSON Schema
- Any API parameter that cannot be mapped

Rules:

- Pin `--model`; never accept silent model substitution.
- Set `model_reasoning_effort` explicitly.
- Set `model_verbosity` explicitly when supported.
- Keep the default service tier; do not enable Codex Fast mode.
- If the original API call had system/developer instructions, place the same text in the instruction file described below.
- If the intended API model is unavailable through ChatGPT-authenticated Codex, choose and declare a proxy model. Do not describe it as equivalent.

## 3. Use an isolated empty working directory

Create a dedicated empty directory outside the benchmark repository. It must contain no:

- `.git` directory
- `AGENTS.md`
- `.codex/` directory
- hooks
- skills
- plugins
- benchmark source code

Pass that directory explicitly with `--cd` and use `--skip-git-repo-check`. This prevents repository instructions and project configuration from entering the prompt.

Do not run from a parent directory containing an `AGENTS.md`; Codex discovers project instructions by walking upward from the working directory.

## 4. Minimal model-instructions file

Create an absolute-path text file named `minimal-benchmark-instructions.md`.

Best mapping:

- If the original API request supplied system/developer instructions, copy them exactly and append only the no-tool/output constraint if necessary.
- If the original API request supplied no instructions, use this neutral shim:

```text
Follow the user input exactly.
Do not call, propose, or refer to tools, commands, files, browsing, connectors, plans, or other agents.
Return only the requested final output. Do not add progress updates or commentary.
```

Codex's `model_instructions_file` overrides the model's built-in Codex instructions. OpenAI's schema warns that this can degrade normal Codex agent performance. That is acceptable here only because the deliberate goal is to remove agent behavior and approximate a bare one-shot inference call. Record the exact file contents or its cryptographic hash with the run.

## 5. Strict proxy invocation

For every benchmark row, start a new process and a new ephemeral session. Pass the exact user message on stdin with `codex exec -`.

The following is the target invocation for current Codex versions. Replace the uppercase placeholders with absolute paths or pinned values:

```bash
env -u OPENAI_API_KEY -u CODEX_API_KEY \
  codex exec \
  --cd "$EMPTY_BENCH_DIR" \
  --skip-git-repo-check \
  --ephemeral \
  --ignore-user-config \
  --ignore-rules \
  --sandbox read-only \
  --model "$CODEX_MODEL" \
  --json \
  --output-last-message "$RESULT_FILE" \
  -c 'forced_login_method="chatgpt"' \
  -c 'service_tier="default"' \
  -c "model_instructions_file=\"$MINIMAL_INSTRUCTIONS_FILE\"" \
  -c "model_reasoning_effort=\"$REASONING_EFFORT\"" \
  -c "model_verbosity=\"$VERBOSITY\"" \
  -c 'personality="none"' \
  -c 'web_search="disabled"' \
  -c 'tools.web_search=false' \
  -c 'features.shell_tool=false' \
  -c 'features.unified_exec=false' \
  -c 'features.apps=false' \
  -c 'apps._default.enabled=false' \
  -c 'features.multi_agent=false' \
  -c 'features.hooks=false' \
  -c 'features.memories=false' \
  -c 'features.goals=false' \
  -c 'features.personality=false' \
  -c 'features.remote_plugin=false' \
  -c 'features.plugins=false' \
  -c 'features.tool_search=false' \
  -c 'features.tool_suggest=false' \
  -c 'skills.include_instructions=false' \
  -c 'skills.bundled.enabled=false' \
  -c 'include_apps_instructions=false' \
  -c 'include_collaboration_mode_instructions=false' \
  -c 'include_environment_context=false' \
  -c 'include_permissions_instructions=false' \
  - < "$PROMPT_FILE" > "$EVENTS_FILE"
```

Notes:

- `PROMPT_FILE` must contain the exact single user message for that case. Do not add a filename, delimiter, row number, or wrapper unless the original API benchmark did so.
- `RESULT_FILE` contains the final answer.
- `EVENTS_FILE` contains JSONL events and token-usage metadata.
- Capture stderr separately; do not merge it into the model output.
- `--ignore-user-config` removes the user's `config.toml`, but system/managed requirements may still apply.
- The final four `include_*` settings and the `skills.*` settings are present in OpenAI's current configuration schema but are less prominently documented. Pin the CLI version and fail preflight if the installed version rejects them.
- If a config key is rejected, do not silently remove it. Mark the environment as incompatible or explicitly record a degraded proxy configuration.

### Structured output

If the benchmark requires a JSON output schema, add:

```bash
--output-schema "$OUTPUT_SCHEMA_FILE"
```

Use the same logical schema as the API benchmark. Do not introduce a schema solely to make parsing easier; structured output can change model behavior.

## 6. Tool-use audit: reject contaminated cases

Every case must be inspected for non-response items. OpenAI documents JSONL item types for command execution, file changes, MCP calls, web searches, plan updates, reasoning, and agent messages.

Treat a case as contaminated if any `item.*` event is not an `agent_message` or `reasoning` item:

```bash
if jq -e '
  select(.type | startswith("item.")) |
  select(.item.type != "agent_message" and .item.type != "reasoning")
' "$EVENTS_FILE" >/dev/null; then
  echo "INVALID: Codex attempted an agent/tool action" >&2
  exit 1
fi
```

Also reject the case when:

- `codex exec` exits nonzero
- the final result file is missing or empty
- no `turn.completed` event exists
- the requested model was rejected or substituted
- a schema was required and the output fails independent schema validation
- rate limiting or authentication errors occurred

Do not count a contaminated or transport-failed case as an incorrect model answer. Record it separately as an infrastructure/proxy failure.

## 7. Dataset and retry protocol

For each case:

1. Materialize the exact prompt bytes.
2. Start a fresh `codex exec --ephemeral` process.
3. Save input, final output, JSONL events, stderr, exit status, and timestamps.
4. Run the tool-use audit.
5. Validate the output format.
6. Score the answer with the existing benchmark scorer.

Predeclare retry behavior:

- Retry transient transport/rate-limit failures at most a fixed number of times with logged backoff.
- Do not retry valid but incorrect answers.
- Do not retry malformed model answers unless the original API benchmark used the same retry policy.
- Retain every attempt so retries cannot hide unfavorable outputs.

Because model outputs are variable, use multiple trials per case if quota permits. Report the number of trials and aggregation rule before inspecting results.

## 8. Required run manifest

Save a manifest containing at least:

```json
{
  "surface": "codex_subscription_proxy",
  "codex_cli_version": "...",
  "authentication": "chatgpt",
  "model": "...",
  "reasoning_effort": "...",
  "verbosity": "...",
  "service_tier": "default",
  "ephemeral": true,
  "user_config_ignored": true,
  "tools_expected": false,
  "instruction_file_sha256": "...",
  "output_schema_sha256": "... or null",
  "dataset_sha256": "...",
  "trials_per_case": 1,
  "retry_policy": "...",
  "known_api_mismatches": []
}
```

Also extract and store the `turn.completed.usage` object from each JSONL stream. It is useful for diagnosing prompt overhead, but subscription usage/credits must not be reported as API cost.

## 9. Calibration plan

For calibration against the Responses API, rerun a stratified sample through that API using the intended model and parameters. Use at least 20 representative cases for an initial check.

Compare:

- exact-match/task score
- output-format validity
- refusal rate
- answer length
- disagreement rate between Codex proxy and API

If disagreement is material, do not extrapolate the proxy result to the API. Keep the Codex run as a separate system benchmark.

## Recommended interpretation

Use this phrasing in reports:

> We temporarily evaluated the task through ChatGPT-authenticated Codex using a one-turn, tool-disabled, ephemeral harness. The result estimates performance of that Codex configuration and is not a direct OpenAI API measurement. A later API calibration run is required before making API-specific claims.

## Official references

- [Codex authentication](https://learn.chatgpt.com/docs/auth)
- [Non-interactive `codex exec`](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Advanced Codex configuration and one-off overrides](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
- [Current Codex configuration schema](https://learn.chatgpt.com/docs/config-schema.json)
- [Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- [OpenAI evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)
