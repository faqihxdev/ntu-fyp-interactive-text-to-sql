# Oracle-information boundary for the 77.51% BIRD Dev result

**Date:** 2026-08-30
**Scope:** Primary-source literature review plus a read-only audit of the frozen
local OpenAI API run and its implementation. No benchmark or model calls were
made for this audit.

## Bottom line

If the 77.51% run used either (a) a correctness/failure signal obtained by comparing an intermediate SQL result with the reference result, or (b) clarification answers generated from the reference SQL, it is **oracle-assisted BIRD-dev EX**, not autonomous BIRD leaderboard-comparable EX. The safest description is an **oracle-assisted ceiling estimate**. “Upper bound” is acceptable informally, but it is not a mathematical upper bound unless the protocol guarantees that oracle interaction cannot hurt.

This is not inherently cheating when it is an explicitly disclosed experimental condition. It becomes a leakage/misrepresentation problem if the number is presented without qualification as ordinary BIRD Dev EX, as a leaderboard/SOTA result, or as deployable autonomous performance.

## 1. Ordinary BIRD EX boundary

At inference, BIRD supplies the question and database identifier, database contents, schema/value descriptions, and—when that condition is used—the expert-authored `evidence` field. The official repository calls this field **“oracle knowledge evidence”**; it is external/domain evidence, not the target SQL or target result ([official BIRD data description](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/README.md#L35-L44)). The current official leaderboard correspondingly records whether a system uses “Oracle Knowledge” ([official leaderboard](https://bird-bench.github.io/)). Comparisons should match that evidence condition.

The hidden-test boundary is unambiguous. The official submission instructions provide test databases, schema metadata/`column_meaning.json`, and `test.json`, but state that test examples do not contain target SQL and show `SQL: ""`; they expressly require methods not to rely on ground-truth SQL ([official BIRD test-submission guidelines](https://docs.google.com/document/d/1Rs6d_pcs2vfqW4Ymub7Wb1XtBNlrc-WfH3T7U1ktuBo/edit?usp=sharing)). Public dev data physically contains references, but a leaderboard-comparable **dev evaluation** likewise requires withholding each example’s reference from its generation, candidate selection, and stopping decisions.

EX is post-hoc evaluation, not an online oracle. BIRD defines EX by equality of the executed predicted and reference results ([BIRD paper, §5](https://arxiv.org/abs/2305.03111)). Its released evaluator loads predictions and references separately, executes both, and compares their result sets only in the evaluation stage ([evaluator comparison](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py#L17-L28), [post-hoc pairing](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py#L129-L144)).

By inference from BIRD supplying the database contents expressly for model exploration, inspecting/querying that database and observing the result or syntax/runtime error of the system’s **own** SQL is non-oracle database interaction. The boundary is crossed when an observation depends on the hidden target—for example, a target-result match bit, target test-case status, a gold-selected candidate, or feedback derived from gold SQL.

## 2. What Sphinteract simulates

Sphinteract contains three distinct feedback channels:

1. **Execution-error repair (non-oracle by itself).** If a generated query raises a database exception, the prompt supplies the invalid SQL, exception message, and schema so the model can repair it ([paper, §6.1](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf#page=5); [released prompt and loop](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb#L685-L697)). This feedback can be produced without a reference SQL.

2. **Semantic failure/success feedback (oracle).** The released `evalfunc` executes both candidate and target SQL and compares the results ([code](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb#L77-L134)). The interaction loops load BIRD’s `SQL` as `gold`, call `evalfunc` after each candidate, treat non-matching candidates as “incorrect sql answers,” and stop when a match is found ([code](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb#L874-L976)). Even if the generator never sees the raw reference, continuing/stopping exposes a target-conditioned correctness bit and can select a successful turn.

3. **Clarification answers (oracle).** The paper explicitly names a **Feedback Oracle**: it takes the clarification question and gold SQL and produces the answer ([Sphinteract paper, §7.2.3](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf#page=8)). The released prompt includes the “Gold Query,” instructs GPT-4o to answer based only on it, and formats each instance with `query = gold` ([prompt](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb#L497-L511), [call site](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb#L925-L941)). The SQL generator receives the oracle’s answer rather than the raw gold SQL, but that answer is still indirect target information.

## 3. Classification of the 77.51% result

Subject to the local audit, the classification rule is:

- **Autonomous BIRD Dev EX:** one final prediction per item is frozen without per-item reference SQL/results, reference-derived pass/fail, gold-conditioned clarification, or gold-based candidate selection; EX is then computed once, post hoc.
- **Oracle-assisted Sphinteract-style EX:** any intermediate generation, clarification, retry, stopping, or selection decision receives information derived from that item’s reference SQL/result. A final EX number remains numerically well-defined, but it measures performance under privileged target feedback and is not directly comparable with autonomous BIRD leaderboard entries.

BIRD-Interact reinforces rather than erases this distinction. It defines a **separate interactive benchmark** whose user simulator internally uses annotated ambiguity sources and ground-truth SQL, while noting that unconstrained simulators can leak ground-truth information. Its two-stage `AMB`/`LOC`/`UNA` design rejects requests for the answer and constrains what can be returned ([paper, §3.3](https://arxiv.org/pdf/2510.05318#page=5)). The released prompt keeps GT context on the simulator side and forbids returning reasoning or ground-truth SQL segments ([code](https://github.com/bird-bench/BIRD-Interact/blob/451fe2c3518ee1cf908d8139e2913483bd519381/BIRD-Interact-ADK/user_simulator/prompts.py#L145-L220)); its public data also omits `sol_sql` and `test_cases` ([official README](https://github.com/bird-bench/BIRD-Interact/blob/451fe2c3518ee1cf908d8139e2913483bd519381/BIRD-Interact-ADK/README.md#L143-L161)). Such scores are valid **within the declared BIRD-Interact protocol and metrics**, not interchangeable with ordinary single-turn BIRD EX.

## 4. Required wording and controls

Recommended wording, if the local audit confirms gold-conditioned interaction **and** verifies that raw reference SQL is not directly exposed to the generator:

> On the public BIRD development split, we obtain **77.51% execution accuracy under an oracle-assisted Sphinteract-style protocol**. The simulated user is conditioned on the reference SQL, and semantic success/retry decisions use reference-result equivalence. The SQL generator does not receive the raw reference SQL, but it does receive gold-derived clarification/correctness signals. We therefore report this as an oracle-assisted ceiling analysis, not as autonomous BIRD leaderboard/test EX.

Use **“gold-SQL-conditioned feedback oracle”** rather than merely “oracle knowledge,” because BIRD already uses “Oracle Knowledge” for the officially supplied `evidence` field.

Minimum controls for defensible reporting:

- Report autonomous and oracle-assisted results as separate rows; never merge them into one headline score.
- For the autonomous row, whitelist inputs (question, supplied DB/schema/metadata, declared BIRD evidence, and observations from executing the system’s own SQL). Freeze the final SQL before the evaluator can access gold SQL, gold results, EX bits, or test-case outcomes.
- Do not choose among turns/candidates using gold equivalence. Predefine a gold-independent selector or submit the final turn.
- Isolate the evaluator and, in the oracle arm, the simulator. Do not expose raw gold/test cases to the SQL agent; constrain answers to the asked ambiguity, reject solution-elicitation requests, and audit transcripts for SQL/target leakage, following BIRD-Interact’s function-driven separation.
- Predeclare the simulator model/prompt/version, temperature/seed, evidence condition, maximum turns, retry policy, stopping rule, and token/tool budget. Keep budgets equal in matched autonomous-vs-oracle ablations.
- Include at least: no-feedback baseline, database-exception-only feedback, semantic pass/fail oracle, and gold-conditioned clarification oracle. This identifies which privileged signal creates the gain.
- Reserve an unseen holdout or use the official hidden test for any leaderboard/deployment claim. Public-dev oracle experiments are useful mechanism/ceiling studies, not evidence of hidden-test autonomous generalization.

## 5. Local implementation and transcript audit

### What is isolated correctly

The frozen run uses separate one-shot policy and simulator invokers. API calls
set `store=False` and do not pass a previous-response identifier. The policy
receives the question, schema-only `CREATE TABLE` statements, rejected SQL
history, SQLite errors for invalid-query repair, and prior clarification
question-answer pairs. The task object excludes BIRD evidence and target SQL.
The simulator alone receives the target SQL.

The following read-only checks passed:

- all implementation hashes in the run contract still match the frozen files;
- the retained records independently reproduce 662/1,534 no-user successes,
  769/1,534 SQL-only successes, and 1,189/1,534 reactive successes;
- all 37 targeted controller, isolation, freeze, projection, and analysis tests
  pass;
- none of the 9,676 retained policy prompt files contains Sphinteract's explicit
  `Gold Query` block or generic gold/ground-truth wording;
- the only two task IDs whose policy prompts contain the target SQL as an exact
  or whitespace-normalised substring are benign overlaps with a rejected model
  query: task 883 embeds the target query as a subquery inside a longer wrong
  output, and task 1434 differs from the target only in the case of the value
  `PO BOX` versus `PO Box`;
- no retained simulator answer contains a complete `SELECT ... FROM ...` SQL
  statement; the runtime rejects such answers.

Therefore, no direct target-SQL injection into the SQL policy was found.

### What remains target-derived

The evaluator compares every intermediate candidate with the target result. A
failed candidate is then labelled as an incorrect SQL in the next prompt, and a
matching candidate ends the arm. This is a hidden semantic rejection and
selection oracle. It is required by the replicated Sphinteract-style protocol,
but is unavailable to an autonomous BIRD system.

The simulator prompt also reproduces Sphinteract's eight examples and instructs
the simulator to answer from the target SQL. The local guard removes explicit
phrases such as `gold query` and rejects a complete SQL statement, but it does
not prevent a natural-language answer from revealing an exact target column,
literal, predicate, or formula. This occurred in the retained run. For example:

- task 74 eventually received a reply specifying `EdOpsCode` and the value
  `SPECON` when those details were absent from the question and offered options;
- task 1119 received a reply specifying `away_player_9` after the listed options
  did not include that target column.

Across 872 initially failed tasks, the reactive arm retained 2,401 simulated
answers. Of these, 1,778 (74.1%) repeated a closed option, while 615 (25.6%)
selected `Other`; the remaining eight used non-exact wording around a closed
choice. A conservative lexical audit found 138 answers (5.7%) containing a
target-SQL identifier or literal that was not already present in the original
question or clarification question. These occurred in 134 of the 872 initial
failures (15.4%) and in 85 of the 437 reactive-only successes (19.5%). This is a
heuristic lower bound, not a semantic leakage classifier: selecting the correct
closed option is itself target-derived information even when no new SQL token is
copied.

### Score classification

The three local rows measure different information conditions:

| Row | Official BIRD-style EX | Target-derived information during generation |
|---|---:|---|
| No user | 43.16% | None beyond post-hoc scoring; one invalid-SQL repair may use its SQLite error |
| SQL-only recovery | 50.13% | Hidden target-result rejection and target-based stopping across up to five normal generations |
| Reactive clarification | 77.51% | The same hidden rejection/stopping plus target-SQL-conditioned simulated answers |

Using the separately retained Sphinteract-compatible scorer, the reactive arm
is 1,115/1,534 = 72.69%, rather than 77.51%. The difference comes from scorer
semantics, not model calls: the official BIRD-style scorer uses set equality,
whereas the released Sphinteract scorer additionally handles result length and
literal `ORDER BY` differently. The official 77.51% number is valid for the
declared local scorer, but the 72.69% diagnostic is the closer numerical
reference when discussing Sphinteract's 58.93% result.

Under that compatibility scorer, the local no-user, SQL-only, and reactive rows
are 40.35%, 46.94%, and 72.69%. The reactive-minus-SQL-only difference is 25.75
points, compared with Sphinteract's published CQ-minus-Simple difference of
22.55 points. The local absolute level is higher, but the within-stack effect is
not an obvious numerical outlier. A newer and different policy model, different
structured adapters, and a different hosted simulator still prevent a direct
replication claim.

The final verdict is therefore:

1. **No direct gold-SQL leak into the policy was found.**
2. **The 77.51% result is intentionally and materially oracle-assisted.** The
   simulator can provide strong target hints, and target-result equality controls
   retries and stopping.
3. **The paired 27.38-point result remains internally valid for the complete
   target-aware clarification bundle versus equally capped target-aware SQL
   retry.** It does not establish autonomous BIRD performance, human-user
   performance, or the causal value of question quality alone.
