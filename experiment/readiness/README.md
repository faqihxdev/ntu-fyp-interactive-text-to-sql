# Dataset and evaluator setup

Paths below are relative to `experiment/`. All dataset directories are Git-ignored.
Saved-case replay and [result verification](../README.md#reproduce-analyses) work
without these downloads.

| Input | Location |
|---|---|
| Original BIRD Dev metadata and 11 databases | `datasets/bird-dev/` |
| Public Mini-Interact release, 300 tasks | `datasets/mini-interact/` |
| Private answer-key bundle and UTF-8 merge | `datasets/private/mini-interact/` |
| Pinned Mini-Interact evaluator | `datasets/bird-interact-evaluator/` |
| Pinned Sphinteract notebook and licence | `datasets/sphinteract-source-21c6fcd/` |

## Public downloads

From `experiment/`:

```powershell
python -m readiness.fetch_public_inputs demo
python -m readiness.fetch_public_inputs mini
```

`demo` installs original BIRD Dev and the pinned Sphinteract notebook. It downloads
about 346 MB and installs about 1.5 GB. `mini` installs the 139 public Mini-Interact
files, about 139 MB. Both verify SHA-256 hashes from the study records before
installing files. Existing mismatched files are left unchanged.

Sources: [original BIRD Dev](https://bird-bench.github.io/),
[Mini-Interact revision 10253f2](https://huggingface.co/datasets/birdsql/mini-interact/tree/10253f235dbc6092a97e9c0acd30dba6203d8e59),
and [Sphinteract revision 21c6fcd](https://github.com/ZhaoFuheng/Sphinteract/tree/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b).
Mini-Interact and Sphinteract text files use the line endings required by their
recorded hashes. SQLite bytes are unchanged. Mini-Interact acquisition records
its pinned revision in `.public-inputs.json`; verification rechecks the files.

For Mini-Interact evaluation and the experiment test suite, also install the
pinned evaluator:

```powershell
git clone -c core.autocrlf=false --filter=blob:none --no-checkout https://github.com/bird-bench/BIRD-Interact.git datasets/bird-interact-evaluator
git -C datasets/bird-interact-evaluator checkout 451fe2c3518ee1cf908d8139e2913483bd519381
```

Keep line-ending conversion disabled for this evaluator checkout. Its prompts
and scorer are used by offline tests. The Sphinteract notebook supplies BIRD
generation and clarification prompts.

## Private answer keys and API access

Request Mini-Interact ground truth from `bird.bench25@gmail.com`, with subject
`[mini-interact GT&Test Cases]`, following the
[authors' instructions](https://huggingface.co/datasets/birdsql/mini-interact#dataset-uses).
The recorded release uses `mini_interact_GTs_20251112.jsonl`. Save the authorised
attachment under `datasets/private/mini-interact/`, then run:

```powershell
uv run --frozen python -m readiness.prepare_mini_interact --merge
```

The merge verifies the recorded input hashes. A different release requires a
separate experiment configuration. Keep solution SQL, oracle knowledge and test
material local; the received bundle has a non-redistribution commitment.

For live model calls, copy `.env.example` to `.env` and set `OPENAI_API_KEY` using
your own API account. See the [demo guide](../../demonstrator/README.md) for live
configuration. Restricted Mini prompts and answer-bearing traces have no public
download; contact the repository maintainer for review arrangements subject to
the dataset authors' permission. Provider response artifacts and local sessions
are unnecessary for the bundled statistical checks.

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
