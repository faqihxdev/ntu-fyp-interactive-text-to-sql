# Controllers and scoring

The three studies use `gpt-5.6-luna` with high reasoning through the OpenAI
Responses API. Results, task selections and input hashes are in `../manifests/`.
See [results](../RESULTS.md) and [setup and commands](../README.md).

## BIRD Dev

Both studies use the 1,534-task original BIRD Dev split. SQL generation receives
the question and `CREATE TABLE` definitions. Recovery receives rejected SQL;
clarification also receives previous questions and answers. Invalid-SQL repair
receives the SQLite error.
Gold SQL, BIRD evidence, sampled values and result rows stay outside generation
prompts. The evaluator supplies acceptance feedback; the simulated user reads
gold SQL to answer clarification questions.

The initial-attempt baseline stops after the shared first query and any immediate
repair. Saved records call this method `no_user`.

| Study | Methods and rules | Main modules |
|---|---|---|
| Primary | Initial-attempt baseline, SQL-only recovery and reactive clarification share the same initial attempt and repair. Both recovery methods allow five normal generations; clarification allows four answers. Question-ID hashing determines which recovery method runs first. | `bird_dev_clarification_value.py`, `run_bird_dev_clarification_value.py` |
| Supporting | Initial-attempt baseline, proactive-only, reactive CQ, hybrid and reactive CQ with early stopping. Proactive/hybrid first decide whether to ask. They reuse shared SQL if they do not ask, or generate SQL after the answer. Hybrid then uses reactive clarification. | `bird_dev_matched.py`, `run_bird_dev_matched.py` |

Each normal generation permits one immediate invalid-SQL repair outside the
normal-generation count. Proactive-only allows one answer; other clarification
methods allow four, including any proactive answer. Reactive CQ with early
stopping can stop after a `NO AMBIGUITY` decision. These comparisons measure
complete methods, including their extra information and computation.

Clarification adapts [Sphinteract](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf)
and its [pinned notebook](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb).
Summarize-Review-Ask reviews question, schema, output and value ambiguity, then
asks one question with three options and an open alternative. The implementation
uses structured JSON, `AmbTableColumn` for schema ambiguity and removes trailing
recommendation labels. Simulator replies pass lexical checks for SQL fragments
and evaluator labels. These checks cannot rule out all indirect target hints.

Sphinteract already supplies SRA, gold-aware answers, correctness-assisted retry,
shared initial predictions, equal round limits, repair and optional early stopping.
This implementation also shares the initial repair, uses BIRD scoring and adds
proactive/hybrid comparisons. It evaluates oracle-assisted recovery, not autonomous
leaderboard performance or real-user effectiveness.

## Scoring

| Rule | Comparison |
|---|---|
| BIRD EX | Equal sets of result-row tuples. Ignores row order and duplicate rows; preserves column positions. Controls acceptance and stopping in both BIRD studies. |
| Sphinteract-compatible | Equal row counts, ignoring column order within each row. Preserves row sequence when gold SQL contains literal uppercase `ORDER BY`; otherwise compares normalised row sets. Applied afterwards to saved candidates. |
| Mini-Interact | Pinned single-instance SQLite evaluator checks private solution SQL and conditions on one database. The supplied bundle uses its default test, with order/column checks and floating-point handling. |

BIRD's rule comes from the [released evaluator](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py).
The [bird-bench fork](https://github.com/bird-bench/DAMO-ConvAI/blob/f9e562d05548d5d921bdb952d779398c0ffe9887/bird/llm/src/evaluation.py)
contains the same file. Scorers can produce different stopping decisions, so
rescoring saved candidates does not reproduce a run controlled by another scorer.

## Mini-Interact

`official_protocol.py` and `run_official_mini_interact.py` implement four controllers
on 240 tasks, separate from 10 pilot and 50 exploratory tasks.

| Controller | Action rules |
|---|---|
| Reference | Model chooses among the nine released actions, including retrieval, asking, submission and stopping. |
| Reactive | Retrieve schema, submit SQL, then use SRA after rejection. Permits early stopping. |
| Proactive | One SRA opportunity after schema retrieval, followed by SQL-only recovery. |
| Hybrid | Proactive opportunity followed by reactive SRA. Permits early stopping. |

All use Stress action prices, `12 + 2 * ambiguity_count` coins, six user-patience
coins and a 60-action cap. The three constrained controllers allow five normal
submissions and one immediate repair after each invalid submission, subject to
budgets. Reactive/hybrid allow four answers; proactive allows one.

The [reference prompt and actions](https://github.com/bird-bench/BIRD-Interact/blob/451fe2c3518ee1cf908d8139e2913483bd519381/mini_interact/knowledge_based/mini_interact_agent/experiments/utils/prompts.py)
are adapted to structured JSON. Malformed actions terminate as failures rather
than receiving the released small penalty. A normaliser supplies missing SRA
summary/review fields or the open option, while retaining raw output. The
two-stage simulator classifies and answers questions in separate calls and
sanitises replies. Gold data stays on the simulator/evaluator side.

The SQLite adapter checks read-only execution and invokes the pinned scorer on
a temporary database copy, with timeouts and private error handling. Controllers
differ in action choice, retrieval, question timing and stopping; their results
measure those combined differences.

## Analysis and shared code

BIRD reports the full split and recalculates results excluding development IDs:
120 primary and 110 supporting tasks. Mini-Interact's main comparison excludes
its 60 development tasks. Paired analysis uses 10,000 task-bootstrap samples,
exact two-sided McNemar tests and Holm adjustment for designated contrast families.
Database-cluster and leave-one-database-out checks are additional analyses.
These intervals describe task variation, not repeated model runs.

`inference_runtime.py` selects the API or optional Codex adapter;
`openai_structured.py` handles API calls and spend tracking. The `codex_` policy
modules and instruction filenames also serve API runs. `sqlite_env.py` implements
BIRD comparison. Trajectory, model, context, oracle and metric modules provide
shared records.
