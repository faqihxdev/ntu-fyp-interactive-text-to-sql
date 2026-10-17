# BIRD evaluator repository provenance and EX rule

Source verification dated 6 October 2026. This note compares the released BIRD and Mini-Dev execution-accuracy rules and records the provenance of their repositories.

## Official provenance and dataset identity

The official BIRD homepage's `Mini-Dev (500)` button links directly to `https://github.com/bird-bench/mini_dev`. Both the hyperlink target in the page HTML and its destination were checked. This establishes an official repository link; the button does not pin a commit. The homepage also records the Mini-Dev release on 27 June 2024. [BIRD homepage](https://bird-bench.github.io/), [button destination](https://github.com/bird-bench/mini_dev).

At the pinned Mini-Dev commit, the README describes 500 text-to-SQL pairs from 11 databases. Mini-Interact is a separate 300-task SQLite interactive benchmark with its own environment and evaluator. [Pinned Mini-Dev overview](https://github.com/bird-bench/mini_dev/blob/abd11b6db92a1c9f809b32f7564c7c71b34d67f0/README.md#overview), [BIRD-Interact dataset descriptions](https://bird-interact.github.io/).

## Pinned EX evidence

Mini-Dev commit `abd11b6db92a1c9f809b32f7564c7c71b34d67f0` defines `calculate_ex` in `evaluation/evaluation_ex.py`, lines 18-22:

```python
res = 0
if set(predicted_res) == set(ground_truth_res):
    res = 1
return res
```

This is the binary decision rule, with 1 for equal sets and 0 otherwise. [Mini-Dev comparator](https://github.com/bird-bench/mini_dev/blob/abd11b6db92a1c9f809b32f7564c7c71b34d67f0/evaluation/evaluation_ex.py#L18-L22).

The caller passes `calculate_ex` to `execute_sql`. At the same commit, `evaluation/evaluation_utils.py`, lines 57-67, executes predicted SQL and then ground-truth SQL on the same connection, fetches both results, and passes them to that callback. The EX caller assigns 0 on timeout or another caught execution exception. [Callback and error handling](https://github.com/bird-bench/mini_dev/blob/abd11b6db92a1c9f809b32f7564c7c71b34d67f0/evaluation/evaluation_ex.py#L25-L43), [execution helper](https://github.com/bird-bench/mini_dev/blob/abd11b6db92a1c9f809b32f7564c7c71b34d67f0/evaluation/evaluation_utils.py#L57-L67).

AlibabaResearch/DAMO-ConvAI commit `483554eae102996f5ec1f4feab4e78ef29c2a394` uses the same set-equality condition and 0/1 outcome inside `bird/llm/src/evaluation.py`, lines 17-28, after fetching both query results. Its caller also assigns 0 for timeout or another caught execution exception. [AlibabaResearch decision rule](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py#L17-L28), [error handling](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py#L32-L48).

## Interpretation and limits

Reading these comparators implies that row order and duplicate-row multiplicity do not affect the comparison. Values retain their positions within each returned row tuple; neither comparator searches column permutations or branches on `ORDER BY`. This is result-set equality on the evaluated database, not proof of SQL equivalence across all possible databases. [Mini-Dev rule](https://github.com/bird-bench/mini_dev/blob/abd11b6db92a1c9f809b32f7564c7c71b34d67f0/evaluation/evaluation_ex.py#L18-L22), [AlibabaResearch rule](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py#L17-L28).

The supported conclusion is that the pinned Mini-Dev and AlibabaResearch files share the core EX decision rule. That does not mean their entire wrappers are identical or interchangeable. This comparison does not establish equivalence of input parsing, database selection, dialect handling, configuration, aggregation, or reporting, nor does it establish identical scores across datasets or execution environments.

## BIRD organisation fork

On 6 October 2026, the GitHub repository API identified `bird-bench/DAMO-ConvAI` as a fork of `AlibabaResearch/DAMO-ConvAI`. Both `parent.full_name` and `source.full_name` named that upstream repository. The fork's main commit was `f9e562d05548d5d921bdb952d779398c0ffe9887`. [Repository metadata API](https://api.github.com/repos/bird-bench/DAMO-ConvAI).

For `bird/llm/src/evaluation.py`, the upstream commit `483554eae102996f5ec1f4feab4e78ef29c2a394` and that fork commit contain the same Git blob, `0459b75caed8d53a9436851cbcb051d02d1fba4e`, of 6,214 bytes. The decoded GitHub Contents API responses were also byte-identical. This check applies to that evaluator file. [Pinned upstream file](https://github.com/AlibabaResearch/DAMO-ConvAI/blob/483554eae102996f5ec1f4feab4e78ef29c2a394/bird/llm/src/evaluation.py), [pinned fork file](https://github.com/bird-bench/DAMO-ConvAI/blob/f9e562d05548d5d921bdb952d779398c0ffe9887/bird/llm/src/evaluation.py).

At the verification date, the official site's `Code` link targeted `AlibabaResearch/DAMO-ConvAI/tree/main/bird`, while `Mini-Dev` targeted `bird-bench/mini_dev`. The experiments use the pinned AlibabaResearch evaluator. [Official BIRD site](https://bird-bench.github.io/).
