# BIRD and Sphinteract-compatible scoring

The BIRD studies use result-set equality to accept a query and stop its
trajectory. Sphinteract-compatible scores describe the same saved candidates
under a second comparison rule.

## Scoring rules

- **BIRD:** compare sets of row tuples. Row order and duplicate rows are ignored;
  column positions within each row are retained.
- **Sphinteract-compatible:** require equal row counts and ignore column order
  within each row. Compare row sequence when the gold SQL contains the literal
  uppercase `ORDER BY`; otherwise compare normalised row sets.

The second rule is different, rather than uniformly stricter.
Implementations are in [SQLite evaluation](../interaction/sqlite_env.py) and
[the BIRD controller](../interaction/bird_dev_matched.py).
The original rule is in the
[pinned Sphinteract notebook](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb).

## Recorded outcomes

Each row covers 1,534 tasks. A task succeeds if any saved candidate passes the
named scorer. These counts were checked on 6 October 2026.

| Study and method | BIRD successes | Sphinteract-compatible successes |
| --- | ---: | ---: |
| Primary initial baseline | 662 | 619 |
| Primary SQL-only recovery | 769 | 720 |
| Primary reactive clarification | 1,189 | 1,115 |
| Supporting initial baseline | 685 | 648 |
| Supporting proactive | 890 | 826 |
| Supporting reactive | 1,201 | 1,132 |
| Supporting hybrid | 1,202 | 1,132 |
| Supporting early stop | 997 | 936 |

The primary clarification-minus-retry difference is 27.38 percentage points
under BIRD and 25.75 points under Sphinteract-compatible scoring. The BIRD
confidence interval applies only to the BIRD difference. Hybrid and reactive
tie under the second rule in the supporting study.

The [primary result](../manifests/api-bird-dev-clarification-value-v1-full1534-result-2026-08-30.json)
and [supporting analysis](../manifests/api-bird-dev-matched-v4-public-analysis-2026-08-30.json)
contain the retained aggregates.

## Why stopping matters

Sphinteract uses its own scorer during generation. These studies use BIRD.
Changing the scorer can change whether recovery starts, how long it continues,
and which questions and SQL queries are generated.

For example, supporting task 71 is accepted by BIRD at the initial query but
rejected by the alternative rule. Supporting task 33 first passes the alternative
rule at turn 2, while its BIRD-controlled trajectory continues to four turns.

Rescoring saved queries cannot supply the continuations that were never
generated. The second score therefore checks the recorded outcome pattern;
it does not reproduce a Sphinteract-controlled run.
