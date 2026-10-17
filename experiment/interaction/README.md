# Text-to-SQL controllers

The package implements the three studies described below. The final API freezes
identify the code, prompts, data and settings used for the reported results.

## Method names

The **initial-attempt baseline** stops after the shared first SQL attempt and
any permitted invalid-SQL repair. **SQL-only recovery** receives correctness
feedback and retries without asking clarification questions. Saved records use
`no_user` as the identifier for the initial-attempt baseline.

[Sphinteract, Table 2 and §6.2](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf)
uses `Baseline`, `Simple`, `CQs` and `CQs & ES`. Its Simple method also receives
correctness feedback. The report labels describe the local implementations.
[Wu et al.](https://aclanthology.org/2024.emnlp-main.131/) instead distinguishes
conditions with and without supplied evidence. Its labels describe a different
support protocol.

## Primary clarification versus retry

`bird_dev_clarification_value.py` and `run_bird_dev_clarification_value.py`
compare the initial-attempt baseline, SQL-only recovery and reactive clarification
on all 1,534 original BIRD Dev tasks.

All methods share the first SQL and its immediate invalid-SQL repair, if needed.
After a hidden rejection, the baseline stops, SQL-only recovery retries, and
reactive clarification asks a target-aware simulated user before retrying.
Both recovery methods permit five normal SQL generations.

The final BIRD execution accuracies are 43.16%, 50.13% and 77.51%.
Clarification exceeds retry by 27.38 percentage points, with a paired 95%
interval of 25.03 to 29.73. See the
[primary result](../notes/api-bird-dev-clarification-value-v1-full-result-2026-08-30.md).

## Supporting asking and stopping configurations

`bird_dev_matched.py` and `run_bird_dev_matched.py` implement five methods:
the initial-attempt baseline, proactive-only, reactive CQ, hybrid and reactive
CQ with early stopping. Their final BIRD accuracies are 44.65%, 58.02%, 78.29%,
78.36% and 64.99%.

The baseline and reactive methods share the first SQL. Proactive and hybrid
reuse it when their initial decision is `no_ambiguity`; when they ask, they
generate SQL after the answer. Reactive CQ and hybrid share the later
non-early-stopping loop.

The comparison concerns complete controller configurations. It does not isolate
question timing from differences in information, budgets and stopping.
See the [protocol](../notes/bird-dev-matched-v4-timing-protocol-2026-08-24.md)
and [supporting result](../notes/api-bird-dev-matched-v4-full-result-2026-08-30.md).

Both BIRD studies supply schema-only generation context. BIRD's released
set-of-result-rows rule controls acceptance and stopping. Sphinteract-compatible
scores are calculated on the saved queries afterwards.

## Mini-Interact controller comparison

`official_protocol.py` and `run_official_mini_interact.py` implement the local
Mini-Interact Stress adaptation. The final comparison uses 240 paired tasks:

| Controller | Asking behaviour | Success |
|---|---|---|
| Local reference port | Model chooses among all nine released actions | 38.33% |
| Reactive | SRA after rejected SQL, with early stopping | 22.08% |
| Proactive | One SRA opportunity after schema retrieval | 22.92% |
| Hybrid | Proactive opportunity followed by reactive SRA | 31.25% |

All four use the same paid retrieval interface, coin budget formula
`12 + 2 * ambiguity_count`, 60-action cap, sanitised two-stage simulator and
released SQLite submission evaluator. The reference prompt is adapted to
structured JSON. The SRA controllers also impose their own question,
submission and stopping rules.

The gold SQL, annotations and test material stay on the evaluator/simulator
side. Policy histories receive the permitted tool observations and user replies.
The result is a local whole-controller comparison, separate from the official
leaderboard. See the
[adaptation account](../notes/official-mini-interact-adaptation-account-2026-08-23.md)
and [final result](../notes/api-official-mini-interact-stress-comparison-v2-result-2026-08-30.md).

## Shared modules

`inference_runtime.py` selects OpenAI or the optional Codex development adapter.
`codex_official_policy.py` and the two `codex-*-instructions.md` files also
serve the final API path. Their names describe their origin, not the provider
used in a particular result.

`controller.py`, `recovery.py`, `context.py`, `model.py` and the trajectory,
oracle and metric modules provide shared types and functions.

The Mini-Interact selections contain 10 pilot IDs, 50 exploratory IDs and
240 comparison IDs. The selection records also include the original 290-ID
parent group. These records identify task allocation and prior evaluation exposure.

## Verification and analysis

Run these commands from `experiment/`:

```powershell
uv run --frozen python -m unittest discover -s interaction/tests -v
uv run --frozen ruff check interaction readiness codex_proxy
```

[The experiment README](../README.md) lists commands to reproduce the recorded
analyses without model calls. Runners require explicit settings and approval
flags for large runs. Private trajectories stay under the ignored `runs/` folder.
