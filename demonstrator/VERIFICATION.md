# Demonstrator testing

Run Python checks from `demonstrator/` and frontend checks from
`demonstrator/frontend/`. Tests mock paid model calls.

## Packaging and static checks

These checks require no databases or API key:

```powershell
uv run --frozen pytest -q tests/test_packaging.py
uv run --frozen pytest -q tests/test_replay_paths.py
uv run --frozen ruff check .
& '.\tests\test_dev_processes.ps1'
```

Packaging tests cover launcher paths, argument forwarding, invalid options,
Git ignore rules and byte-preserving storage of frozen manifests.
Process tests use fixtures to check ownership before stopping a server.
Replay-path tests verify saved instruction hashes and reject missing,
ambiguous or changed instructions.

## Backend integration

```powershell
uv run --frozen pytest -q tests
```

The full suite requires the databases, saved run records and pinned prompts
listed in the [setup guide](README.md#start). Coverage includes saved outcomes,
prompt hashes, prefix-only branches, hidden-information boundaries, read-only
SQL, custom questions, call limits, idempotency and same-origin protection.

## Frontend

Start the local server, then run:

```powershell
npm run build
npx playwright install chromium --only-shell
npm test
```

Browser checks cover replay, live-branch controls, custom questions, keyboard
access, narrow layouts, scrolling and accessibility. Paid actions are mocked.
Screenshots and browser reports are written to ignored test-output directories.

## Optional live smoke

```powershell
uv run --frozen python scripts/smoke_live.py --live
```

This makes two paid requests: a suggested reply for task 1389, followed by SQL
generation from that reply. It checks the API connection and branch state
transitions. It is separate from the benchmark results.
