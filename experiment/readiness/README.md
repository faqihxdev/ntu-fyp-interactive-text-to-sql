# Dataset and evaluator checks

Dataset integrity checks and evaluator adapters for original BIRD Dev and Mini-Interact.

| Input | Location |
|---|---|
| Original BIRD Dev metadata and 11 databases | `datasets/bird-dev/` |
| Public Mini-Interact release, 300 tasks | `datasets/mini-interact/` |
| Original private GT bundle and validated UTF-8 merge | `datasets/private/mini-interact/` |
| Pinned released Mini-Interact evaluator | `datasets/bird-interact-evaluator/` |
| Pinned Sphinteract notebook and licence | `datasets/sphinteract-source-21c6fcd/` |

Paths are relative to `experiment/`. Git ignores all listed data directories.

## Public source dependencies

From `experiment/`, download the versions used by the adapters:

```powershell
git clone -c core.autocrlf=false --filter=blob:none --no-checkout https://github.com/bird-bench/BIRD-Interact.git datasets/bird-interact-evaluator
git -C datasets/bird-interact-evaluator checkout 451fe2c3518ee1cf908d8139e2913483bd519381
git clone -c core.autocrlf=false --filter=blob:none --no-checkout https://github.com/ZhaoFuheng/Sphinteract.git datasets/sphinteract-source-21c6fcd
git -C datasets/sphinteract-source-21c6fcd checkout 21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b
```

The Mini-Interact checkout supplies the public prompts and scorer needed by
the experiment test suites. Those tests use synthetic database fixtures and
require neither private ground truth nor API credentials. The Sphinteract
notebook supplies the BIRD prompts used by the runners and demonstrator.
Keep Git's automatic line-ending conversion disabled in these two checkouts
so their source-file hashes match the pinned versions.

Obtain original BIRD Dev from the [BIRD project](https://bird-bench.github.io/)
and the public Mini-Interact tasks from
[the dataset release](https://huggingface.co/datasets/birdsql/mini-interact).
Use the paths in the table above. Mini-Interact's private ground truth requires
author access; follow the
[upstream merge instructions](https://github.com/bird-bench/BIRD-Interact#combine-the-public-data-with-the-ground-truth-and-test-cases).
Saved demonstrator trajectories are separate inputs, listed in the
[demo guide](../../demonstrator/README.md).

## Provenance

The Mini-Interact bundle was received from `bird.bench25@gmail.com` on
20 August 2026 following an academic-use request with a non-redistribution
commitment. Its solution SQL, oracle knowledge and test material were merged
with the public 300-task release using the authors' script in UTF-8 mode.

Tracked records preserve the
[private bundle checksum](../manifests/mini-interact-private-bundle-20251112.json),
[validated merge](../manifests/mini-interact-merge-smoke-2026-08-20.json) and
[Windows evaluator adaptation](../manifests/mini-interact-evaluator-adapter-2026-08-20.json).
The acquisition record `manifests/datasets-readiness.json` includes development inputs.
The three final API freeze manifests identify the inputs used in each study.

Private ground truth and trajectories must remain local and must not enter
the SQL model's prompts or a Git commit.

## Checks

Run these commands from `experiment/`:

```powershell
uv run --frozen python -m unittest discover -s readiness -p "test_*.py" -v
uv run --frozen python -m readiness.prepare_mini_interact
uv run --frozen python -m readiness.check_datasets
```

The merge command verifies the existing file by default. It only creates a
merge when `--merge` is explicitly supplied, and refuses to overwrite an
existing output without `--force`. The official merger runs in UTF-8 mode.

The dataset check verifies original-BIRD hashes against the final primary
freeze, checks the public/private Mini-Interact correspondence and validates
the evaluator source. It writes `runs/dataset-readiness.json`.
It makes no model calls and performs no full gold-query sweep.

## Mini-Interact evaluator

The Windows adapter invokes the pinned official single-instance scorer with
the active interpreter and absolute paths. The supplied 300-task private bundle
has no custom test functions, so scoring uses the authors' default gold-query
test. This differs from the original-BIRD scorer.

The scorer resets a database copy from its template. It leaves the source
databases, templates and private input files unchanged.
