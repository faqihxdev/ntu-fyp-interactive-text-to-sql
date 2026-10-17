# Relationship to Sphinteract

Sphinteract compares SQL-only retry, clarification questions and clarification
with early stopping. Its Simple method receives rejected SQL and generates
another query without asking a clarification question.
Sources are the [published paper](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf)
and [released notebook](https://github.com/ZhaoFuheng/Sphinteract/blob/21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b/zeroshot_experiments.ipynb),
checked on 6 October 2026.

## Inherited controls

The notebook reuses stored initial predictions across methods, permits four
recovery rounds after the initial SQL, and allows one immediate repair for an
invalid query. Repair calls are outside the normal-generation counter.
The clarification methods use Summarize-Review-Ask and a gold-aware simulated
user. The early-stopping variant can return `NO AMBIGUITY`.

Correctness feedback determines whether the methods continue.
SQL-only retry therefore means retry without clarification, not retry without
feedback. This comparison measures recovery when an external acceptance signal
is available; it does not establish a deployed system's ability to recognise
every executable-but-wrong query.

## Studies in this repository

| Study | Relationship to Sphinteract |
| --- | --- |
| Primary BIRD | Re-evaluates baseline, retry and clarification with Luna/high. It shares the initial SQL and any immediate repair, then compares task-paired outcomes under equal normal-generation caps. |
| Supporting BIRD | Adds proactive-only and hybrid configurations to baseline, reactive and early-stopping clarification. It compares complete configurations rather than isolating question timing. |
| Mini-Interact | Compares four controllers in a different environment with retrieval tools and action budgets. |

The [controller guide](../interaction/README.md) defines the methods and their
implementation. The [research summary](research-direction-lock.md) presents
the results and scope.

BIRD scoring controls acceptance in the two BIRD studies. Sphinteract's original
experiments use its own scorer. The
[scoring comparison](scorer-choice-framing-check-2026-10-06.md) explains why
alternative scores of saved queries do not reproduce those trajectories.

The contribution is an empirical adaptation and extension, including additional
controller comparisons and paired analysis. SRA, correctness-assisted retry,
shared initial predictions, equal round caps, invalid-SQL repair and
`NO AMBIGUITY` early stopping come from prior work.

## Published evaluation

Sphinteract evaluates KaggleDBQA and BIRD with GPT-3.5 Turbo and GPT-4 Turbo.
Table 2 reports GPT-4 Turbo BIRD accuracies of 32.07% for the initial baseline,
36.38% for Simple, 58.93% for clarification and 53.00% with early stopping.
It also studies interaction efficiency, cost, few-shot generation, supplied
hints and human use. The simulated-user studies here address a narrower scope.
