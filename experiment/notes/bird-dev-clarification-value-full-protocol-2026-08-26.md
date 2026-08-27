# Full BIRD clarification-value replication

**Status before model calls:** authorized and frozen after the disjoint 100-task
pilot passed every mechanical check. No full-run trajectories existed when this
protocol was frozen.

## Research question

> Under the same hidden rejection signal and five-normal-generation opportunity
> cap, does reactive clarification recover more original-BIRD Dev tasks than
> SQL-only retry?

The pilot found 49% no-user EX, 53% SQL-only recovery EX, and 79% reactive
clarification EX. Its primary paired difference was +26 percentage points (95%
task-bootstrap interval +18 to +35), with 26 reactive-only successes and no
SQL-only-only successes. The full run estimates this contrast more precisely
on every original-BIRD Dev task.

## Frozen comparison

All three arms receive one physically shared initial normal-attempt prefix,
including any bounded invalid-SQL repair:

| Arm | Action after hidden rejection | Limits |
|---|---|---|
| No user | Stop | 1 normal generation, 0 questions |
| SQL-only recovery | Generate another SQL without a question or answer | 5 normal generations, 0 questions |
| Reactive clarification | SRA question, gold-conditioned simulated reply, then SQL regeneration | 5 normal generations, 4 questions |

The two recovery arms use the same normal-generation cap and one invalid-SQL
repair allowance per normal generation. Their execution order is deterministic SHA-256
parity by question ID. The policy sees only the natural-language question and
`CREATE TABLE` schema. BIRD evidence, values, descriptions, and gold SQL remain
hidden from it.

## Population and analysis

- Primary population: all 1,534 original-BIRD Dev tasks in released order.
- Primary outcome: original-BIRD execution equality.
- Primary contrast: reactive clarification minus SQL-only recovery.
- Statistics: paired difference, 10,000-sample task-bootstrap 95% interval,
  exact two-sided McNemar test, discordant outcomes, turns, attempts, calls,
  tokens, and terminations.
- Prespecified sensitivity: repeat arm summaries and paired contrasts after
  excluding the 20 smoke and 100 pilot IDs, leaving 1,414 tasks not used during
  protocol development.

The run is valid only with zero final task or arm errors, an exact shared
candidate prefix, zero SQL-only interaction, and no budget violation. Rerunning
the same command resumes completed tasks and retries only recorded protocol
errors under the unchanged run contract.

## Claim boundary

The estimand is the value of the complete clarification bundle over matched
SQL-only retry opportunity under a hidden correctness signal and a
gold-conditioned simulator. It does not isolate question wording from answer
content, provide a deployable rejection detector, or measure real users. The
requested `gpt-5.6-luna` subscription alias does not expose an immutable served
snapshot, so the proxy surface, reasoning setting, CLI version, and observed
usage are recorded.
