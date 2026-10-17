# Results

The three studies use `gpt-5.6-luna` with high reasoning through the OpenAI
Responses API. BIRD results measure recovery with hidden correctness feedback
and gold-aware simulated answers. They are not autonomous leaderboard scores.
See [methods and scoring](interaction/README.md) for controls and limitations.

The [offline verification command](README.md#reproduce-analyses) recalculates the
three studies from the task records and numerical evidence in `results/`.

## Primary BIRD: clarification versus retry

The methods share one initial SQL attempt per task. Of 1,534 tasks, 872 initially fail.

| Method | BIRD EX | Recovered initial failures | Mean replies/task |
|---|---:|---:|---:|
| Initial-attempt baseline | 662/1,534, 43.16% | 0 | 0.00 |
| SQL-only recovery | 769/1,534, 50.13% | 107/872, 12.27% | 0.00 |
| Reactive clarification | 1,189/1,534, 77.51% | 527/872, 60.44% | 1.57 |

Clarification adds **27.38 percentage points**, with a paired 95% interval
of 25.03 to 29.73 and exact McNemar p < 0.001. Clarification alone succeeds on
437 tasks; retry alone on 17. Excluding 120 development tasks leaves a
28.08-point difference.

Evidence: [result manifest](manifests/api-bird-dev-clarification-value-v1-full1534-result-2026-08-30.json),
[public outcomes](manifests/api-bird-dev-clarification-value-v1-public-outcomes-2026-08-30.json),
[paired analysis](manifests/api-bird-dev-clarification-value-v1-public-analysis-2026-08-30.json).

## Supporting BIRD: asking and stopping

| Method | BIRD EX | Mean replies/task | Mean calls/task |
|---|---:|---:|---:|
| Initial-attempt baseline | 44.65% | 0.00 | 1.28 |
| Proactive-only | 58.02% | 0.55 | 5.38 |
| Reactive CQ | 78.29% | 1.51 | 6.70 |
| Hybrid | 78.36% | 1.68 | 7.41 |
| Reactive CQ with early stop | 64.99% | 0.71 | 4.28 |

Hybrid minus reactive is +0.07 points, with interval [-1.76, 1.89], while using
more replies and calls. Early stopping reduces replies by 0.80 per task and
accuracy by 13.30 points. These are whole-controller comparisons on 1,534 tasks.

Evidence: [result manifest](manifests/api-bird-dev-matched-v4-full1534-result-2026-08-30.json),
[paired analysis and development-exclusion checks](manifests/api-bird-dev-matched-v4-public-analysis-2026-08-30.json).

## Mini-Interact: action policies

| Controller | Success | Mean replies/task | Mean calls/task | Mean tokens/task |
|---|---:|---:|---:|---:|
| Reference | 92/240, 38.33% | 1.32 | 12.83 | 164,971 |
| Reactive | 53/240, 22.08% | 1.55 | 14.18 | 151,366 |
| Proactive | 55/240, 22.92% | 0.99 | 12.09 | 126,030 |
| Hybrid | 75/240, 31.25% | 2.33 | 14.22 | 125,239 |

Hybrid improves on reactive by 9.17 points, with interval [3.75, 14.58],
but falls 7.08 points below the reference, with interval [-12.50, -2.08].
All four fail on the same 129 tasks. Shared failures identify outcomes, not
a single cause. The 240-task comparison excludes 60 development tasks.

Evidence: [results, paired tests, SRA compliance and failure counts](manifests/api-official-mini-interact-stress-comparison-v2-result-2026-08-30.json).

## Alternative scoring

Sphinteract-compatible scores of saved primary queries are 40.35%, 46.94% and
72.69% for the baseline, retry and clarification. The clarification-minus-retry
difference is 25.75 points. Supporting reactive and hybrid both score 73.79%.
BIRD controlled the original stopping decisions; this rescore cannot generate
missing continuations under a different scorer.

## Resource use

| Retained study records | Recorded calls | Input tokens | Output tokens | Estimated US$ |
|---|---:|---:|---:|---:|
| Primary BIRD | 12,071 | 20,870,848 | 6,422,957 | 12.61 |
| Supporting BIRD | 32,698 | 53,967,398 | 12,828,961 | 27.27 |
| Mini-Interact | 12,796 | 125,171,252 | 11,054,168 | 44.13 |

BIRD totals count shared initial work once; per-method means include it for each
method. BIRD call counts include connection retries. Mini-Interact counts action
decisions plus two simulator calls per answer. Reasoning tokens are included
in output tokens, and cached tokens are included in input tokens.

[Usage details](manifests/usage-summary.json) contain per-method counts, cache
tokens, estimated costs and source hashes. Estimates use the recorded rates and
cover retained results, not total billed expenditure, development or discarded runs.

## Example cases

- **Task 780, superhero:** "List the powers of Hunter Zolomon." All five retry
  attempts filter `superhero_name` and return zero rows. A clarification answer
  selects `full_name`; the second query returns four matching rows.
- **Task 1342, student_club:** retry fixes `2019/9/4` to `2019-09-04` and succeeds
  on attempt two. Clarification retains the original date literal through five
  failed attempts and four answers.

Both scorers agree on these cases. The [demonstrator](../demonstrator/README.md)
replays them alongside eight other selected examples.
