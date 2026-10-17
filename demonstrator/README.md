# SQL interaction explorer

A local web app for replaying saved BIRD conversations, trying alternative
replies and asking new database questions. React and Tailwind provide the
interface; FastAPI serves the local API.

## Setup

Requires Windows, Git Bash, Python 3.12+, uv and Node.js/npm. Place these inputs
under the adjacent `experiment/` directory:

| Input | Path |
|---|---|
| BIRD metadata | `datasets/bird-dev/dev.json` |
| Eleven BIRD databases | `datasets/bird-dev/dev_databases/dev_databases/<db>/<db>.sqlite` |
| Pinned Sphinteract source | `datasets/sphinteract-source-21c6fcd/zeroshot_experiments.ipynb` |
| Saved primary run, including prompts and schemas | `runs/api-bird-dev-clarification-value-v1-full1534-luna-high-20260830/` |

See [dataset setup](../experiment/readiness/README.md) for public downloads.
Saved trajectories are separate local inputs. Replay checks their prompt hashes.

From `demonstrator/` in Git Bash:

```bash
bash ../run-demo.dev.sh
```

Open `http://127.0.0.1:8765/`. The reloading API uses port 8766. Ctrl+C stops both.
Use `--stop` to stop recognised demo processes or `--port 8775 --api-port 8776`
to change ports. Unrelated port owners block startup. Both servers bind to loopback.
The Bash launcher uses Windows PowerShell, not WSL. PowerShell users can run
`..\run-demo.dev.ps1`; `-Help` lists options. To build and serve the production
frontend locally, use `& '.\start.ps1'`, or add `-SkipBuild` to reuse a build.

Live actions read credentials from `../experiment/.env`, with overrides from
the demo's `.env`. See [.env.example](.env.example). The default model is
`gpt-5.6-luna` with high reasoning. API keys stay on the server; replay needs no key.

## Modes

| Mode | Behaviour |
|---|---|
| Saved cases | Shows the full conversation, SQL, results and recorded model context. Restart or play steps individually. No model calls. |
| Try your reply | Copies history through the selected clarification and continues with reactive Summarize-Review-Ask. Original-target mode uses hidden BIRD scoring and optional simulated answers; changed-intent mode uses user feedback. |
| Try a question | Offers 11 databases, descriptions, schemas and three starter questions per database. The model can inspect values, ask about intent or generate an answer query. |

Selecting a starter or reply option fills the input without sending it. Each
API-labelled action makes at most one model request. Database inspections pause
for **Continue · API**; **Add context** includes further user instructions.
Custom results are labelled **Executed · unverified** because users judge the
answer. Custom mode has no answer key or simulated user.

Custom exploration adapts the flexible action pattern of the
[Mini-Interact reference controller](https://github.com/bird-bench/BIRD-Interact/blob/451fe2c3518ee1cf908d8139e2913483bd519381/mini_interact/knowledge_based/mini_interact_agent/experiments/utils/prompts.py).
It supplies schema upfront and uses real user replies and explicit continuation.
Its accuracy is separate from the [reported experiments](../experiment/RESULTS.md).

## Limits and information flow

- Each live conversation allows 12 model calls, five normal SQL generations
  and four clarification replies. Forked conversations count their saved prefix
  towards the query/reply limits. One invalid-SQL repair per query is explicit.
- Custom inspections share the SQL limit. The final query slot and final call
  permit an answer, not another inspection. Observations contain at most eight
  rows, 12 columns and 12 KB of JSON; truncation is shown and binary values omitted.
- SQL is read-only, with a 15-second execution limit and 50,000-row cap.
- The cumulative estimated spend guard defaults to US$2, reserving US$0.10 per
  request, with 4,096 maximum output tokens and no automatic connection retries.
  It is a local estimate, not an exact billing cap.
- Sessions and private prompts are stored in ignored `.local/`. Logs can contain
  user text, database values and simulator-side gold SQL. Do not publish them.
  A restart clears in-memory sessions; refresh returns the interface to replay.

The case library has two examples for each outcome: clarification alone, retry
alone, both recover, initial success and neither recovers. These are selected
examples, not an accuracy sample. BIRD scoring controlled the saved trajectories;
Sphinteract-compatible scores only rescore their saved queries.

## Tests

From `demonstrator/`, packaging tests need no data or API key:

```powershell
uv run --frozen pytest -q tests/test_packaging.py tests/test_replay_paths.py
& '.\tests\test_dev_processes.ps1'
```

With the local inputs above, run all backend tests and lint:

```powershell
uv run --frozen pytest -q tests
uv run --frozen ruff check .
```

With the dev server running, from `frontend/`:

```powershell
npm run build
npx playwright install chromium --only-shell
npm test
```

Tests cover replay hashes, fork history, read-only execution, answer-key isolation,
call limits, explicit continuation, keyboard access and mobile layout. Browser
tests mock paid actions. The optional `uv run --frozen python scripts/smoke_live.py --live`
performs two paid calls.

## Code

`explorer/replay.py` reads saved cases; `live.py` manages branches and API limits;
`custom.py` handles new questions; `databases.py` supplies database guides and
execution; `server.py` exposes the API. `frontend/src/` contains the interface.
The app imports shared SQL, scoring and inference code from `experiment/`.
