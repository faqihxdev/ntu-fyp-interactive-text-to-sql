# Interactive Text-to-SQL

Code for **Natural Language Interface to Databases Using LLMs**, an NTU
Bachelor of Computing final-year project by Muhammad Faqih Akmal.
It includes experiments on SQL generation, retry and clarification, with a
web demonstrator for inspecting saved conversations and asking database questions.

- `experiment/` contains controllers, evaluators, analysis scripts and result records.
- `demonstrator/` contains the web interface and its local server.

Commands below run from this directory.

## Start the demonstrator

Use Git Bash on Windows with Git, Python 3.12 or later, `uv` and Node.js/npm:

```bash
bash run-demo.dev.sh
```

The launcher prints the link, normally `http://127.0.0.1:8765/`. It starts the
reloading backend on port 8766 and Vite on 8765. Ctrl+C stops both.
Use `--stop` to stop recognised demo processes. Use
`--port 8775 --api-port 8776` to choose other ports. Startup stops with an error
if an unrelated process occupies either port. PowerShell users can run
`.\run-demo.dev.ps1`. Use `-Help` or Bash `--help` for the options.

The launcher installs frozen dependencies when needed. Opening the app or
replaying saved cases makes no model calls. Live actions use API credits only
when the user selects an API-labelled button.

The demo requires BIRD databases, the pinned Sphinteract notebook and saved
primary-run records under `experiment/`. Supply these Git-ignored inputs before
starting the demo. The launcher installs dependencies but does not download data.
See the [demo guide](demonstrator/README.md) for input paths, credentials,
controls and tests.

## Experiments and evidence

- [Experiment guide](experiment/README.md): setup, offline checks, frozen-run
  requirements and analysis commands.
- [Controller guide](experiment/interaction/README.md): the three studies,
  shared implementation and method names.
- [Dataset guide](experiment/readiness/README.md): required data, provenance
  and local integrity checks.
- [Research summary](experiment/notes/research-direction-lock.md): scope,
  findings and interpretation limits.
- [Worked examples](experiment/notes/worked-examples.md): saved clarification
  and SQL-retry cases.

The primary aggregate analysis uses public task outcomes included in the
repository. It requires neither raw trajectories nor model calls.

## Data and dependencies

The experiment and demo use separate Python environments and dependency locks.
The demo imports shared functions from the adjacent `experiment/` directory.
Keep `experiment/` and `demonstrator/` at the same directory level.

Datasets and raw trajectories belong in `experiment/datasets/` and
`experiment/runs/`. Demo sessions and logs belong in `demonstrator/.local/`.
Git ignores these directories, credentials, environments and generated caches.
Private Mini-Interact data and raw logs must remain outside version control.

See [third-party notices](THIRD_PARTY_NOTICES.md) for the sources and licences
of the adapted protocols and evaluator interfaces.
