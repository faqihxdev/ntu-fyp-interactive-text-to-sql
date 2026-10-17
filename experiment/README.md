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

The primary analysis needs only the tracked public outcome projection.
Supporting BIRD and Mini-Interact analyses need the retained local run files.

```powershell
New-Item -ItemType Directory -Force runs/replay-check | Out-Null

uv run --frozen python -m interaction.analyze_bird_dev_clarification_value_public `
  --projection manifests/api-bird-dev-clarification-value-v1-public-outcomes-2026-08-30.json `
  --output runs/replay-check/primary-analysis.json

uv run --frozen python -m interaction.analyze_bird_dev_matched `
  --run-root runs/api-bird-dev-matched-v4-full1534-luna-high-20260830 `
  --output runs/replay-check/supporting-analysis.json `
  --sensitivity-exclusions manifests/bird-dev-matched-v4-full1534.json

uv run --frozen python -m interaction.analyze_standardized_results `
  runs/api-official-mini-stress-comparison-v2-luna-high-reserve240-20260830 `
  --output-json runs/replay-check/mini-analysis.json `
  --output-markdown runs/replay-check/mini-analysis.md `
  --figure-dir runs/replay-check/figures `
  --bootstrap-samples 10000 --bootstrap-seed 20260822
```

BIRD plots use `interaction.plot_bird_dev_clarification_value` and
`interaction.plot_bird_dev_matched`. Each module provides `--help`.

## Model runs and frozen records

Runners are `interaction.run_bird_dev_clarification_value`,
`interaction.run_bird_dev_matched` and `interaction.run_official_mini_interact`.
Use `--help` for required inputs, budgets and approval flags. Calls use API credits.

Freeze a new API run with `interaction.freeze_openai_bird_run` or
`interaction.freeze_openai_mini_comparison`. A freeze records inputs, settings
and file hashes. Resume requires the original implementation and run contract;
changed code requires a new freeze. Existing result analysis needs no rerun.

`manifests/` contains immutable run records, selections and aggregate evidence.
Their dated identifiers and exact bytes are preserved because other records
reference their hashes. Recorded paths resolve against the local experiment
directory. Historical protocol snapshots remain available in Git history.

`interaction/` contains controllers, analysis and tests; `readiness/` contains
data checks and evaluator adapters; `figures/` contains result plots.
Datasets and raw trajectories belong in ignored `datasets/` and `runs/`.
