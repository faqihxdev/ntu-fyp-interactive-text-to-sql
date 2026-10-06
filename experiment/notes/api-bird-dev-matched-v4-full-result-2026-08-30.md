# Direct-API timing and stopping result

All 1,534 BIRD Dev tasks and five controller configurations completed under the
pre-run freeze with zero task or arm errors. The direct OpenAI Responses API
run requested GPT-5.6 Luna with high reasoning.

| Controller | BIRD EX | Mean user turns | Mean model calls |
|---|---:|---:|---:|
| Initial-attempt baseline | 44.65% | 0.00 | 1.28 |
| Proactive-only | 58.02% | 0.55 | 5.38 |
| Reactive | 78.29% | 1.51 | 6.70 |
| Hybrid | 78.36% | 1.68 | 7.41 |
| Reactive with early stop | 64.99% | 0.71 | 4.28 |

Per-method call counts include the shared initial attempt for each method,
although the experiment makes that initial attempt only once per task.

Adding a proactive opportunity before the reactive loop changes EX by only
+0.07 points (95% task-bootstrap interval -1.76 to +1.89) while using more
turns and calls. Early stopping saves 0.80 turns per task but lowers EX by
13.30 points (interval -15.25 to -11.34). Proactive-only trails reactive by
20.27 points, although that whole-controller comparison does not isolate timing.

The retained records contain 32,698 model calls and 66.80 million tokens, with
a local estimated API cost of US$27.27. The provider invoice remains
authoritative.
