# Dataset and evaluator setup

Paths below are relative to `experiment/`. All dataset directories are Git-ignored.

| Input | Location |
|---|---|
| Original BIRD Dev metadata and 11 databases | `datasets/bird-dev/` |
| Public Mini-Interact release, 300 tasks | `datasets/mini-interact/` |
| Private answer-key bundle and UTF-8 merge | `datasets/private/mini-interact/` |
| Pinned Mini-Interact evaluator | `datasets/bird-interact-evaluator/` |
| Pinned Sphinteract notebook and licence | `datasets/sphinteract-source-21c6fcd/` |

## Public sources

From `experiment/`:

```powershell
git clone -c core.autocrlf=false --filter=blob:none --no-checkout https://github.com/bird-bench/BIRD-Interact.git datasets/bird-interact-evaluator
git -C datasets/bird-interact-evaluator checkout 451fe2c3518ee1cf908d8139e2913483bd519381
git clone -c core.autocrlf=false --filter=blob:none --no-checkout https://github.com/ZhaoFuheng/Sphinteract.git datasets/sphinteract-source-21c6fcd
git -C datasets/sphinteract-source-21c6fcd checkout 21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b
```

Keep automatic line-ending conversion disabled so source hashes match.
Mini-Interact supplies the prompts and scorer used by offline tests. The
Sphinteract notebook supplies BIRD generation and clarification prompts.

Obtain original BIRD Dev from the [BIRD project](https://bird-bench.github.io/)
and public Mini-Interact tasks from
[the dataset release](https://huggingface.co/datasets/birdsql/mini-interact).
Private Mini-Interact ground truth requires permission from its authors; follow
[their merge instructions](https://github.com/bird-bench/BIRD-Interact#combine-the-public-data-with-the-ground-truth-and-test-cases).
The supplied private bundle is for academic use with a non-redistribution
commitment. Keep solution SQL, oracle knowledge and test material local.

## Verification

From `experiment/`:

```powershell
uv run --frozen python -m readiness.prepare_mini_interact
uv run --frozen python -m readiness.check_datasets
```

The first command verifies an existing merge. Use `--merge` to create one;
overwriting requires `--force`. The second checks data and evaluator hashes and
writes `runs/dataset-readiness.json`. Neither makes model calls.

Tracked records identify the [private bundle checksum](../manifests/mini-interact-private-bundle-20251112.json),
[validated merge](../manifests/mini-interact-merge-smoke-2026-08-20.json)
and [SQLite adapter](../manifests/mini-interact-evaluator-adapter-2026-08-20.json).
Final study freezes identify their exact inputs.

The SQLite adapter invokes the pinned single-instance evaluator on a temporary
database copy. The supplied bundle has no custom tests, so the authors' default
gold-query test determines success. Source databases and private inputs remain
unchanged. See [scoring](../interaction/README.md#scoring) for the distinction
from BIRD EX, and the [demo guide](../../demonstrator/README.md) for replay inputs.
