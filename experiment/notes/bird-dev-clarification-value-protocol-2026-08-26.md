# BIRD clarification-value control

**Status:** the frozen 20-task smoke completed as a plumbing gate. A separate
post-smoke freeze binds the unchanged controller, corrected order-insensitive
audit, and disjoint 100-task pilot. No full-run mode exists in the runner.

The first audit incorrectly treated alphabetically sorted JSON object keys as
an arm-set violation. The check was corrected from sequence equality to set
equality; no controller output or model trajectory was changed or rerun. The
corrected smoke audit passes with zero task errors, zero arm errors, eight
initial misses available for recovery, 110 physical calls, and 483,096 tokens.

## Research question

> Under the same hidden rejection signal and five-SQL opportunity cap, does
> reactive clarification recover more original-BIRD tasks than SQL-only retry?

The experiment addresses the main confound in the completed timing study:
interactive arms received additional SQL attempts, so their recovery could not
be attributed to clarification alone.

## Frozen arms

| Arm | Shared initial SQL | After a rejection | Limits |
|---|---|---|---|
| No user | Yes | Stop | 1 normal SQL, 0 questions |
| SQL-only recovery | Yes | Source-derived self-debug SQL prompt; no question or simulated answer | 5 normal SQLs, 0 questions |
| Reactive clarification | Yes | Released SRA question, gold-conditioned natural-language simulator reply, then SQL regeneration | 5 normal SQLs, 4 questions |

The initial SQL is generated once per task and copied into all arm records. The
two recovery branches are executed in deterministic SHA-256-balanced order,
with each branch first on exactly half of each frozen selection, to
reduce systematic time-order bias from the mutable model service. Invalid-SQL
repair remains one auxiliary repair per normal attempt in both recovery arms.

## Controls and estimand

- Requested stack: Codex subscription proxy, `gpt-5.6-luna`, high reasoning,
  low verbosity, Codex CLI version recorded at run time.
- Context: natural-language question plus `CREATE TABLE` schema only; released
  BIRD evidence, database values, descriptions, and gold SQL are hidden from
  the policy model.
- Evaluator: original-BIRD execution equality; the released Sphinteract scorer
  remains diagnostic only.
- Recovery continues only after an evaluator-side mismatch. This is a hidden
  rejection oracle and must not be presented as a deployable correctness
  detector.
- Primary contrast: reactive clarification minus SQL-only recovery on paired
  official execution success.
- Secondary contrasts: each recovery arm minus no-user.

The primary contrast estimates the value of the **clarification bundle**—SRA
question, gold-conditioned answer, and answer-conditioned SQL regeneration—over
matched SQL-only retry opportunity. It does not separately identify question
wording, simulator answer content, or prompt-template effects.

## Prespecified checks

The smoke is plumbing evidence only. The pilot is usable only if:

1. there are zero task and arm errors;
2. every arm has the exact shared candidate prefix;
3. SQL-only recovery contains zero user turns and no question-answer history;
4. both recovery arms obey the five-normal-SQL cap;
5. every initial miss receives at least one retry, and reactive receives at
   least one question; and
6. the 100-task pilot contains at least 30 initial failures, giving at least 30
   paired opportunities to observe recovery.

The analysis reports paired effect size, task-bootstrap 95% interval, exact
McNemar test, discordant outcomes, resource use, branch order, and terminations.
Statistical significance is not required for the pilot to be informative;
near-parity or zero discordance is itself evidence against a large added value.

## Decision boundary

No large run follows automatically. A 1,534-task run would require a separate
runner or explicit authorization, a new full-run freeze, and a decision that
the pilot resolves the retry confound clearly enough to matter to the thesis.
