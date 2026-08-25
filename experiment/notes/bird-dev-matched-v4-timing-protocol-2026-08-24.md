# Original-BIRD v4 clarification-timing protocol

**Status:** implemented and completed on all 1,534 tasks. The frozen protocol
below remains unchanged; final results are recorded in
`bird-dev-matched-v4-full-result-2026-08-25.md`.

## Purpose

The original-BIRD study is the primary comparison because it reruns
the closest published baseline, Sphinteract CQ, on the same 1,534 tasks with the
same model, schema-only context, simulator, SQL limits, and evaluator.

The central question is:

> Under a matched multi-round Text-to-SQL protocol, how do proactive-only,
> reactive, and hybrid clarification policies differ in execution accuracy and
> user turns?

## Frozen controller roles

| Arm | Before the first SQL | After a rejected SQL | Limits | Role |
|---|---|---|---|---|
| No user | Shared initial SQL | Stop | 1 SQL, 0 questions | Recovery baseline |
| Proactive-only | One SRA decision; ask once or reuse the shared initial SQL | SQL-only self-debug using the existing answer and rejected SQL history | 5 SQLs, at most 1 question | Proactive policy |
| Reactive CQ | Shared initial SQL | Source-derived SRA must ask, then regenerate | 5 SQLs, at most 4 questions | Direct Sphinteract method baseline |
| Hybrid | Same proactive-checkpoint implementation as proactive-only | The same non-early-stop reactive loop as Reactive CQ | 5 SQLs, at most 4 questions total | Combined policy |
| Reactive CQ&ES | Shared initial SQL | Source-derived SRA may ask or stop | 5 SQLs, at most 4 questions | Secondary stopping ablation |

The main timing analysis uses the first four arms. CQ&ES versus CQ is reported
separately so model-decided early stopping is not mixed into the hybrid timing
contrast. Model calls are independently sampled per arm even when they use the
same implementation and prompt.

## Shared experimental controls

- Original BIRD Dev question, database, and official set-of-result-rows scorer.
- Requested `gpt-5.6-luna` with high reasoning for policy and user simulator.
- Schema-only model context; BIRD evidence and gold SQL are hidden from policy.
- Gold-conditioned simulator behind the evaluator boundary, with evaluator
  labels neutralized and SQL-shaped replies rejected.
- At most five normal SQL attempts, one invalid-SQL repair per attempt, and four
  user answers for multi-round arms.
- Paired task IDs and task-level differences; EX by user turn, mean user turns,
  SQL attempts, model calls, tokens, terminations, and scorer disagreements.
- Task-bootstrap intervals plus database-cluster bootstrap and leave-one-database-
  out sensitivity for paired success differences.
- Exact McNemar tests with Holm adjustment across the three primary timing
  contrasts: proactive versus reactive, hybrid versus proactive, and hybrid
  versus reactive.
- Standard all-1,534 reporting plus a prespecified sensitivity excluding the 10
  smoke and 100 cost-pilot IDs used during protocol development.

## Primary comparisons

1. Reactive CQ minus no-user: multi-round recovery value.
2. Proactive-only versus reactive CQ: proactive versus post-rejection timing.
3. Hybrid versus proactive-only: value of adding reactive clarification after
   the same proactive policy definition.
4. Hybrid versus reactive CQ: value or cost of adding the proactive checkpoint.
5. CQ&ES versus CQ: stopping ablation only.

Published Sphinteract percentages remain historical anchors because the
published model stack differs. The direct result is the local paired arm
comparison.

## Evidence hierarchy and preserved work

The completed 240-task Mini-Interact study is retained unchanged. It remains
useful because it evaluates full controller bundles under database tools,
action costs, benchmark budgets, and richer terminal behavior. Once v4 is run:

- original BIRD is the primary matched timing and literature-comparator study;
- Mini-Interact is the secondary external-validity and failure-analysis study.

The old 100-task original-BIRD v2 pilot is also retained unchanged. It remains
valid for feasibility, cost estimation, and the warning that early stopping may
hurt. It cannot answer the v4 timing question because it omitted proactive-only
and its hybrid used early stopping.

## Completed boundary

The 10-ID live smoke completed all 50 arm outcomes with zero task or arm errors
and remains a development diagnostic. The authorized full run subsequently
completed all 1,534 tasks across five arms. Its final score-blind error-recovered
aggregate contains zero task and arm errors, retains the frozen implementation
and selection hashes, and is the primary timing evidence. Intermediate
selection sizes remain refused by the runner.
