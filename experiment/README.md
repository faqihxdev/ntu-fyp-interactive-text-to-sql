# Text-to-SQL experiments

Three studies evaluate SQL recovery and clarification using the OpenAI Responses
API. See [methods and scoring](interaction/README.md) and [results](RESULTS.md).

## Setup

From `experiment/`:

```powershell
uv sync --frozen
```

Follow [dataset setup](readiness/README.md) for the pinned source dependencies
and benchmark inputs. For model runs, copy `.env.example` to `.env` and supply
an API key. The optional [Codex adapter](codex_proxy/README.md) is for development;
the reported results use the Responses API.

## Offline checks

The test suites use fixtures and make no model calls. They require the pinned
public Mini-Interact evaluator, but not private answer keys or full datasets.

```powershell
uv run --frozen python -m unittest discover -s interaction/tests -v
uv run --frozen python -m unittest discover -s readiness -p "test_*.py" -v
uv run --frozen python -m unittest codex_proxy.test_runner -v
uv run --frozen ruff check interaction readiness codex_proxy
```

With local datasets present:

```powershell
uv run --frozen python -m readiness.prepare_mini_interact
uv run --frozen python -m readiness.check_datasets
```

These verify the merge, data hashes and evaluator source without model calls.

## Reproduce analyses

The included records cover all three studies. From `experiment/`:

```powershell
uv run --frozen python -m interaction.verify_results
```

The check takes several minutes and needs no datasets, evaluator download or API
key. It verifies file hashes and recalculates scores, paired tests, confidence
intervals, development-exclusion checks, usage and Mini-Interact event counts.
It exits with an error on a mismatch and saves analyses under `runs/verification/`.

`results/` contains both complete BIRD task-record files, four original
Mini-Interact metrics files and a text-free Mini event projection. The BIRD files
retain generated SQL and clarification exchanges. Mini records retain public IDs,
outcomes, event order, usage and derived flags. Their source hashes identify the
retained runs. The command checks calculations from recorded judgments; checking
Mini SQL correctness or its text-derived flags independently requires the
restricted traces and authorised answer-key access.

## Model runs and frozen records

Runners are `interaction.run_bird_dev_clarification_value`,
`interaction.run_bird_dev_matched` and `interaction.run_official_mini_interact`.
Use `--help` for required inputs, budgets and approval flags. All three default
to `--provider openai`. API calls require a key and a positive `--max-spend-usd`.
The optional Codex adapter requires an explicit `--provider codex`.

Freeze a new API run with `interaction.freeze_openai_bird_run` or
`interaction.freeze_openai_mini_comparison`. A freeze records inputs, settings
and file hashes. Resume requires the original implementation and run contract;
changed code requires a new freeze. Existing result analysis needs no rerun.

`manifests/` contains immutable run records, selections and aggregate evidence.
Their dated identifiers and exact bytes are preserved because other records
reference their hashes. Recorded paths resolve against the local experiment
directory. Historical protocol snapshots remain available in Git history.

`interaction/` contains controllers, analysis and tests; `readiness/` contains
data checks and evaluator adapters.
`results/` holds verification evidence. Downloaded datasets and new runs belong
in ignored `datasets/` and `runs/`.
