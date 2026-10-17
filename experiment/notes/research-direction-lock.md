# Clarification value in interactive Text-to-SQL

Three completed studies compare clarification, retry, and combinations of
asking and stopping rules on BIRD Dev and Mini-Interact.

## Central question

Does reactive clarification recover more execution-correct Text-to-SQL answers
than simply giving the model the same number of additional SQL attempts?

The primary experiment answers this with three task-paired controllers on all
1,534 original BIRD Dev questions. They physically share the first SQL. After
hidden rejection, the initial-attempt baseline stops, SQL-only recovery retries without asking, and
reactive clarification asks a target-aware simulated user before regenerating.
Both recovery controllers permit five normal SQL generations.

## Study sequence and contribution

The studies adapt and extend Sphinteract's evaluation.
Sphinteract already compares clarification with retry using shared initial SQL,
equal normal-generation limits, and immediate invalid-SQL repair. Our local
refinement also shares the initial repair. BIRD's released scorer defines
acceptance for all adapted controllers, rather than reproducing Sphinteract's
stopping decisions.

The logical sequence is:

1. Measure clarification's additional recovery over equally capped retry.
2. Compare complete asking and stopping configurations on the same benchmark.
3. Examine broader controllers with retrieval tools and action costs in Mini-Interact.

The extensions and paired analysis are the local contributions. The primary
clarification-versus-retry question and its core controls come from prior work.

## Final evidence

| Primary controller | BIRD EX | Recovered after 872 initial failures | Mean user turns |
|---|---:|---:|---:|
| Initial-attempt baseline | 662/1,534 (43.16%) | 0 (0.0%) | 0.00 |
| SQL-only recovery | 769/1,534 (50.13%) | 107 (12.3%) | 0.00 |
| Reactive clarification | **1,189/1,534 (77.51%)** | **527 (60.4%)** | 1.57 |

Reactive clarification exceeds equally capped SQL-only recovery by **27.38
percentage points**, with a 95% paired task-bootstrap interval of
**[25.03, 29.73]**. Reactive alone succeeds on 437 tasks and SQL-only alone on
17. Excluding the 120 clarification-value development IDs gives a similar
+28.08-point effect.

The supporting five-controller BIRD study finds:

| Controller | BIRD EX | Mean user turns | Mean calls |
|---|---:|---:|---:|
| Initial-attempt baseline | 44.65% | 0.00 | 1.28 |
| Proactive-only | 58.02% | 0.55 | 5.38 |
| Reactive | 78.29% | 1.51 | 6.70 |
| Hybrid | 78.36% | 1.68 | 7.41 |
| Reactive with early stop | 64.99% | 0.71 | 4.28 |

Hybrid minus reactive is +0.07 points with interval [-1.76, 1.89]. Adding a
proactive opportunity therefore provides no detected final-accuracy benefit
and costs more interaction and computation. Early stopping saves interaction
but loses 13.30 accuracy points.

The 240-task Mini-Interact adaptation gives 38.33% for the flexible reference
port, 22.08% for reactive, 22.92% for proactive, and 31.25% for hybrid. Hybrid
beats the two constrained single-timing bundles but remains 7.08 points below
the reference. This is whole-controller evidence in a richer action setting,
not a direct comparison with original-BIRD EX or a leaderboard submission.

## Literature comparison

Sphinteract reports 32.07% with no interaction, 36.38% with Simple retry, and
58.93% with clarification questions on original BIRD Dev. Its clarification
effect over retry is +22.55 points. Our difference is +27.38 points under BIRD
scoring and +25.75 points when the same recorded queries are scored using
Sphinteract's rule. Only BIRD's score controlled our runs. Sphinteract's own
scorer controlled its original experiments, so this second score does not
reproduce its stopping decisions. The studies show the same method ordering,
while models, prompts, simulators, and trajectories also differ.

## Defensible claim

> Under a task-paired, schema-only protocol on all 1,534 original BIRD Dev
> questions, reactive clarification with target-aware simulated answers
> improves execution accuracy substantially more than equally capped SQL-only
> retry after the same first SQL.

This measures oracle-assisted recovery. The hidden evaluator decides when a
query is wrong, and the simulated user can see the target SQL. The measured
gain belongs to the whole clarification process, including extra information
and computation. It is neither a formal upper bound nor evidence of deployment
or leaderboard performance.

## Run integrity

- Model setting: `gpt-5.6-luna`, high reasoning, OpenAI Responses API.
- Primary BIRD: 1,534 tasks x 3 controllers, zero final task/arm errors.
- Supporting BIRD: 1,534 tasks x 5 controllers, zero final task/arm errors.
- Mini-Interact: 240 tasks x 4 bundles, zero final system-error terminations.
- Production scope: 13,232 logical outcomes or trajectories.
- Conservative local spend ledger: US$103.50.
- Every final study has a pre-run hash freeze and retained aggregate hashes.
- The requested model alias is mutable and one trajectory is retained per
  task/controller, so claims concern these paired runs rather than all possible
  model draws.

## Authoritative artefacts

- Primary result: `manifests/api-bird-dev-clarification-value-v1-full1534-result-2026-08-30.json`
- Primary replay: `manifests/api-bird-dev-clarification-value-v1-public-analysis-2026-08-30.json`
- Supporting result: `manifests/api-bird-dev-matched-v4-full1534-result-2026-08-30.json`
- Supporting public analysis: `manifests/api-bird-dev-matched-v4-public-analysis-2026-08-30.json`
- Mini-Interact result: `manifests/api-official-mini-interact-stress-comparison-v2-result-2026-08-30.json`
- API accounting: `notes/api-benchmark-accounting-2026-08-30.md`
