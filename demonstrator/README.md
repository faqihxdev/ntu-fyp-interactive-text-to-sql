# SQL interaction explorer

A local research demonstrator for the final three-method BIRD study. Ten real
cases show where clarification helps, where retry is enough, and where both fail.
A separate **Try a question** mode lets users select a database and explore
their own requests, with clarification and SQL revision on demand.

## Start

From this `demonstrator/` directory in Git Bash:

```bash
bash ../run-demo.dev.sh
```

Open **http://127.0.0.1:8765/**, also printed in the terminal. Frontend edits update
live. The API runs on port 8766 and reloads when `explorer/*.py` changes.
Ctrl+C stops both services. Run the same command again to replace existing demo
processes, or use `bash ../run-demo.dev.sh --stop` to stop them without restarting.

Only recognised processes from this demonstrator are stopped. An unrelated port
owner blocks startup with its PID. Close it yourself or choose two different ports:
`bash ../run-demo.dev.sh --port 8775 --api-port 8776`. Both servers bind to loopback only.
API reloads/restarts clear in-memory live branches; saved results and `.local/`
branch logs are untouched. API startup logs go to `.local/dev-*-api.*.log`.

The `.sh` entry point delegates Windows process management to `../run-demo.dev.ps1`.
Use Git Bash, not WSL; this project uses Windows dependencies and paths.
PowerShell users can run `..\run-demo.dev.ps1` directly. Both scripts resolve
their paths from their own location, independently of the terminal directory.

The launcher syncs frozen Python dependencies and runs `npm ci` only when the
frontend lockfile changes or Vite is missing. It does not make model calls.
To serve the built frontend locally, run `& '.\start.ps1'` from this directory.
Use `-SkipBuild` to reuse an existing frontend build.

Requires `uv`, Node.js, the adjacent `experiment/` directory, the original BIRD Dev
databases, the pinned Sphinteract source notebook, and the local final API run:

`../experiment/runs/api-bird-dev-clarification-value-v1-full1534-luna-high-20260830/`

The app reads these artefacts without modifying them. Mini-Interact data is
not required. Missing saved prompts or hash mismatches produce an error.

## Use it

1. Choose a case. The three method cards show their complete saved outcomes.
2. Each case and method opens with its full saved conversation visible. Use
   **Restart replay** or **Play** to go through it step by step. No API calls.
3. Inspect a SQL result, schema, saved prompt, or structured SRA output.
4. At a clarification, select **Try your reply**. Only the prefix up to that
   question is copied. Later saved replies never enter the new model context.
5. Type a reply, reuse the saved reply for free, or request a generated suggestion.
   Every paid action has an **API** label. No background model loop runs.

The interface uses neutral dark surfaces, neon cyan actions, green matches,
amber mismatches and violet clarification, with filled, borderless controls.
The info icon beside **Case library** explains the
three outcome indicators, in the order initial-attempt baseline, SQL-only
recovery, reactive clarification. **Study results & information flow** contains
the full-study figures read from the frozen analysis and explains the roles of
the model, evaluator and simulated user. SQL changes and scoring details are
collapsible. The Sphinteract-compatible score is a reporting-only check of saved
queries; BIRD controlled the original trajectories.

### Ask a new question

1. Select **Try a question**, then one of the 11 available BIRD databases.
2. Read its description or expand **Inside this database** for its tables and
   schema. Each database has three new starter questions, separate from saved
   benchmark questions. Selecting an example fills the input without sending it.
3. Select **Ask AI · API** to make one model request. It returns a query or asks
   one clarification. Selecting a clarification option only fills your reply.
4. Send your answer, request a SQL revision, or finish. Each **API** button makes
   at most one model request. Execution errors allow one explicit repair.

Custom exploration has no gold answer, acceptance feedback or simulated oracle.
**Executed · unverified** means the query ran. The user checks whether its
result answers their question. The model sees the question, table definitions,
conversation and execution errors, but no sampled values or preview rows.
Actual prompts, instructions, outputs and response formats can be inspected.
This mode is an exploratory demo, not one of the evaluated controllers.
It shares the 12-call spend guard and allows five normal queries and four
clarification replies per conversation. Database queries are read-only.
Switching modes preserves the current conversation; **New question** starts over.

For live branches, **original target** keeps the benchmark's hidden result
comparison and optional gold-conditioned simulator. **Changed intent** uses your
feedback instead, disables gold scoring and offers only possible reply suggestions.
The latter is a demonstration variant, not the evaluated research protocol.

The live branch inherits the original five-normal-attempt/four-reply limits. A
single invalid-SQL repair must be requested explicitly. A branch allows at most
12 model calls; the server processes one at a time. Live SQL has a 15-second,
50,000-row execution limit for responsiveness, lower than the benchmark's limits.
Live results therefore must not be substituted into the benchmark.

## Credentials and saved state

The server reads `../experiment/.env`. An optional local `.env` overrides it;
see `.env.example`. The API key never goes to the browser. Default live model is
`gpt-5.6-luna` with `high` reasoning. Replay works without an API key.

The local estimated spend guard defaults to $2 cumulatively, with a $0.10
reservation per request, 4,096 maximum output tokens and no automatic transport
retries. This is not an exact provider-side billing ceiling.

Live state and prompt logs go to ignored `.local/`, separate from frozen results.
They can contain typed replies, and private simulator call logs contain gold SQL.
Do not publish that folder. The interface returns to saved replay after a page
refresh; active branches remain available through their server IDs until a
restart. Live JSON logs remain on disk. There is a 64-branch server limit.
Replay remains available throughout.

## Selected evidence

| Outcome | Task IDs | Count in all 1,534 tasks |
| --- | --- | ---: |
| Only clarification recovers | 780, 1389 | 437 |
| Both recover | 794, 1364 | 90 |
| Only retry recovers | 1342, 1379 | 17 |
| Shared initial SQL succeeds | 737, 820 | 662 |
| Neither recovers | 407, 952 | 328 |

These are deliberately selected examples, not an accuracy sample. Match means
the official BIRD result-set comparison. The replay labels simulator replies and
rejection feedback; it is not an autonomous leaderboard demonstration.

## Checks

From this directory, the packaging checks need no databases or API key:

```powershell
uv run --frozen pytest -q tests/test_packaging.py
& '.\tests\test_dev_processes.ps1'
```

The full backend suite also needs the local data and saved records listed above:

```powershell
uv run --frozen pytest -q tests
uv run --frozen ruff check .
```

With the local server running, from `frontend/`:

```powershell
npm run build
npx playwright install chromium --only-shell
npm test
```

Browser tests replay real data, but mock paid live actions. They do not spend
tokens. Screenshots are saved under ignored `frontend/test-results/`.

An optional **paid, two-call** smoke is separate. From this `demonstrator/` directory:

```powershell
uv run --frozen python scripts/smoke_live.py --live
```

## Implementation

- `explorer/replay.py`: selected cases, authentic events, verified prompt loading,
  read-only result previews.
- `explorer/live.py`: prefix-only forks, explicit state transitions, isolated API
  calls, limits, and private simulator boundary.
- `explorer/databases.py`: allowlisted database discovery, descriptions, new
  starter questions and read-only execution for custom questions.
- `explorer/custom.py`: bounded user-led questions, clarification and revision,
  without reading saved benchmark tasks or gold answers.
- `explorer/server.py`: same-origin loopback API and frontend hosting.
- `frontend/src/`: React/TypeScript interface styled with Tailwind 4, including
  syntax-highlighted replay. Tailwind runs through Vite; no browser-side CDN compiler.

The app imports the experiment's SQL executor, scorer, API client and
prompt-building functions. Saved result files are read-only inputs.
