# Adaptation account for the final Mini-Interact comparison

**Scope:** exact description of the local controller bundles and wrappers used in the 240-task comparison. “Retained” does not mean byte-for-byte reproduction.

| Component | Retained from the cited/released design | Local translation or addition | Omitted or materially changed |
|---|---|---|---|
| Mini-Interact reference port | Released worked example, nine action meanings and Stress action prices; model chooses actions freely | XML/visible-thought output became schema-constrained JSON; common protocol guidance and SQL checks were added | Malformed actions terminate as scored failures rather than receiving the released small coin penalty |
| Sphinteract-derived reactive bundle | Reactive ordering after rejected SQL; Summarize–Review–Ask over AmbQuestion, AmbColumn, AmbOutput and AmbValue; three closed options plus one open option; up to four questions and five normal submissions; early stop | Mapped to Mini-Interact actions, Stress budget, scorer and private simulator; SQL history and sanitised user answers enter later policy calls | Not a reproduction of Sphinteract’s original BIRD prompt/model/oracle; its separate exception-repair routine is not reproduced |
| Proactive and hybrid bundles | Reuse the same local SRA format and reactive loop where applicable | Proactive SRA checkpoint after schema retrieval; hybrid combines that checkpoint with the reactive loop under one cap | New local ablations, not published Sphinteract variants |
| SRA normaliser | None | Deterministically supplies only missing audit summary/review boilerplate or the required open option; raw output and repairs remain logged | It is part of the three SRA bundles and is not assumed causally neutral relative to the reference port |
| User simulator | Private task intent is isolated from the policy; questions are classified and answered in separate model calls | Same requested Luna/high alias as policy, two-stage adapter, deterministic answer sanitiser | Not the original Sphinteract GPT-4o feedback oracle or a real user; lexical checks do not prove semantic non-leakage |
| Submission scorer | Released Mini-Interact SQLite `test_case_default` function and solution/test material remain evaluator-side | Temporary database copy, read-only pre-execution check and parent-process timeout; current code suppresses raw child exceptions/stderr | Candidate SQL may execute once in the precheck and again inside the released scorer; this is a local wrapper, not the official hosted service |

## Consequence for claims

The four-arm estimand is assignment to a complete local controller bundle. The comparison holds the outer task set, requested model setting, simulator implementation, scorer wrapper, action prices and budgets fixed, but it does not isolate clarification timing or any single prompt component.

## Comparison with the literature

| Setting | Tasks and environment | What can be compared |
| --- | --- | --- |
| This local Mini-Interact study | 240 selected SQLite tasks, four local controllers, shared Luna/high setting, simulator and outer Stress budget | Paired outcomes of the complete local controllers. See the [final API result](api-official-mini-interact-stress-comparison-v2-result-2026-08-30.md). |
| Sphinteract's published BIRD experiment | Original BIRD Dev, its own generation and feedback models, prompts and acceptance rule | Method design and qualified numerical context, rather than a direct Mini-Interact score comparison. |
| Full BIRD-INTERACT | 600 PostgreSQL tasks, broader operations, priority and follow-up questions, conversational or agent interaction | Benchmark design and interaction constraints. A local Mini score has no defensible percentile on this leaderboard. |

Sphinteract reports 58.93% for CQs and 53.00% for CQs with early stopping.
The local Mini reactive controller permits early stopping, so comparing it only
with the 58.93% row also mismatches the variant. Even the closer variant uses a
different dataset, scorer, model and simulator. The original-BIRD
studies provide a separate, more relevant comparison, with their scorer
differences stated in the [scoring note](scorer-choice-framing-check-2026-10-06.md).

The released agent-interaction design permits action choice; it does not require
every local controller to follow the reference agent. A structured controller
is a valid experimental adaptation when its added restrictions are disclosed.
Here, the reference port chooses freely among nine actions, while the SRA
controllers impose extra question, submission and stopping rules.

User questions, model calls, coins and normalised benchmark reward measure
different things. The local success and burden summaries should retain their
own labels rather than borrow a leaderboard metric or rank.

## Bounded retries and shared failures

Retry ends at acceptance, the relevant action/question/submission limit, or an
enabled early-stop decision. It is not an unlimited search. Bounded interaction
also appears in Sphinteract and BIRD-INTERACT: Sphinteract allows up to four
clarification rounds, while BIRD-INTERACT constrains conversational debugging
and charges for agent actions.

The final API comparison has **129 tasks failed by all four controllers**.
Shared failure and executable
rejected endpoints describe outcomes; they do not identify a single cause such
as intrinsic SQL difficulty or failure to incorporate answers.

Useful answers need not produce
correct SQL ([ABISS preprint, §6.5](https://arxiv.org/abs/2607.23340)). This
motivates a separate root-cause analysis; it does not diagnose the local failures.

## Sources and provenance

- [Sphinteract, §§6–7 and Table 2](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf).
- [BIRD-INTERACT paper](https://arxiv.org/abs/2510.05318).
- [Pinned Mini-Interact release](https://github.com/bird-bench/BIRD-Interact/tree/451fe2c3518ee1cf908d8139e2913483bd519381/mini_interact).
- [Released action definitions and prices](https://github.com/bird-bench/BIRD-Interact/blob/451fe2c3518ee1cf908d8139e2913483bd519381/mini_interact/knowledge_based/mini_interact_agent/experiments/utils/prompts.py).
- [Final direct-API freeze](../manifests/api-official-mini-interact-stress-comparison-v2-freeze-2026-08-30.json).
