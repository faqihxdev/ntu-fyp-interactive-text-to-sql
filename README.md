# Interactive Text-to-SQL

Experiments and a web demonstrator for **Natural Language Interface to Databases
Using LLMs**, an NTU Bachelor of Computing final-year project by Muhammad Faqih Akmal.

## Demonstrator

Replay ten saved BIRD cases, try alternative clarification replies, or select a
database and ask a new question. Live exploration can inspect values, clarify
intent and generate read-only SQL. Every model call requires a user action.

On Windows, install Git, Python 3.12+, uv and Node.js/npm. From this directory
in Git Bash:

```bash
bash run-demo.dev.sh
```

The launcher installs frozen dependencies and prints the local URL, normally
`http://127.0.0.1:8765/`. Ctrl+C stops both servers. Use `--stop` to stop recognised
demo processes, or `--port 8775 --api-port 8776` to change ports. PowerShell users
can run `.\run-demo.dev.ps1`.

Replay requires local BIRD databases and saved run records. The launcher does
not download them. See the [demo guide](demonstrator/README.md) for inputs,
credentials, controls and tests.

## Experiments

Three studies compare clarification and SQL-only recovery on BIRD Dev, asking
and stopping configurations on BIRD Dev, and controllers on Mini-Interact.

- [Setup and analysis commands](experiment/README.md)
- [Methods and scoring](experiment/interaction/README.md)
- [Results and saved evidence](experiment/RESULTS.md)
- [Dataset setup](experiment/readiness/README.md)

The primary outcome analysis runs offline using tracked data. Full trajectory
analysis requires local run records. Each component has its own dependency lock;
keep `experiment/` and `demonstrator/` beside each other.

Datasets, raw trajectories, credentials and demo sessions are Git-ignored.
Private Mini-Interact answer keys must not be redistributed.
See [third-party notices](THIRD_PARTY_NOTICES.md) for attribution and licences.
