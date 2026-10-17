# SQL interaction explorer

A local web app for replaying saved BIRD conversations, trying alternative
replies and asking new database questions. React and Tailwind provide the
interface; FastAPI serves the local API.

## Setup

Requires Windows, Git Bash, Python 3.12+, uv and Node.js/npm. The ten saved cases,
schemas, model inputs and cached result previews are included in `data/replay.json`.
Replay checks the bundle, original record, schema and policy-prompt hashes.
No dataset download or API key is needed for replay.

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

For live questions and alternative replies, from `experiment/`:

```powershell
python -m readiness.fetch_public_inputs demo
```

This downloads original BIRD Dev (about 346 MB, 1.5 GB extracted) and the pinned
Sphinteract notebook, then verifies the recorded hashes. No model calls are made.
See [dataset setup](../experiment/readiness/README.md) for paths and source versions.

Live actions read credentials from `../experiment/.env`, with overrides from
the demo's `.env`. See [.env.example](.env.example). The default model is
`gpt-5.6-luna` with high reasoning. Restart the demo after changing `.env`.
API keys stay on the server; replay needs no key.

## Modes

| Mode | Behaviour |
|---|---|
| Saved cases | Shows the full conversation, SQL, cached result previews and recorded model context. Restart or play steps individually. No model calls. |
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

From `demonstrator/`, tests use no API key or paid calls:

```powershell
uv run --frozen pytest -q tests
uv run --frozen ruff check .
& '.\tests\test_dev_processes.ps1'
```

Bundle, packaging and synthetic-database tests run on a fresh clone. Integration
tests skip with an explanation when BIRD inputs or original raw run files are absent.

With the dev server running, from `frontend/`:

```powershell
npm run build
npx playwright install chromium --only-shell
npm test -- tests/explorer.spec.ts tests/offline.spec.ts
```

With the BIRD databases installed, `npm test` also checks custom questions.
Set `DEMO_BASE_URL` if the server uses a different port.
Tests cover replay hashes, fork history, read-only execution, answer-key isolation,
call limits, explicit continuation, keyboard access and mobile layout. Browser
tests mock paid actions. The optional `uv run --frozen python scripts/smoke_live.py --live`
performs two paid calls.

## Code

`explorer/replay.py` reads saved cases; `live.py` manages branches and API limits;
`custom.py` handles new questions; `databases.py` supplies database guides and
execution; `server.py` exposes the API. `frontend/src/` contains the interface.
The app imports shared SQL, scoring and inference code from `experiment/`.

`uv run --frozen python scripts/export_replay.py` rebuilds the replay bundle from
the original primary run, its policy prompt files and the matching BIRD databases.
The export retains recorded inputs and scores and adds cached previews; simulator
prompts stay private. See [data attribution](../THIRD_PARTY_NOTICES.md).
