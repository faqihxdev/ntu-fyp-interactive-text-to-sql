# Read-only audit of the standardised Mini-Interact result

**Date:** 30 August 2026
**Boundary:** existing trajectories and metrics only; no model, simulator, or evaluator calls.

## Verified arm summaries

| Local controller bundle | Success | Coins/task | Questions/task | Total calls/task | Total tokens/task |
|---|---:|---:|---:|---:|---:|
| Local reference port | 92/240 (38.33%) | 16.756 | 1.321 | 12.833 | 164971 |
| Reactive | 53/240 (22.08%) | 16.548 | 1.546 | 14.175 | 151366 |
| Proactive-only | 55/240 (22.92%) | 17.719 | 0.988 | 12.092 | 126030 |
| Hybrid | 75/240 (31.25%) | 15.867 | 2.325 | 14.217 | 125239 |

## Paired statistical checks

| Hybrid minus comparator | Success difference (95% task bootstrap) | Discordants | Exact McNemar | Holm-adjusted primary p |
|---|---:|---:|---:|---:|
| Local reference port | -7.08 pp [-12.50, -2.08] | 30 / 13 | 0.013718 | 0.013718 |
| Reactive | +9.17 pp [+3.75, +14.58] | 13 / 35 | 0.002088 | 0.004176 |
| Proactive-only | +8.33 pp [+3.33, +13.33] | 9 / 29 | 0.001658 | n/a |

The bootstrap resamples the 240 realised task differences. It does not include new model or simulator draws. The exact test conditions on discordant task pairs; disagreement at a threshold is therefore reported rather than resolved in favour of one method.

## Raw versus normalised SRA compliance

| Bundle | Raw SRA decisions | Raw compliant | Post-normalisation compliant | Normaliser events | Success when activated |
|---|---:|---:|---:|---:|---:|
| Local reference port | 0 | 0 | 0 | 0 | n/a |
| Reactive | 468 | 453 | 468 | 15 | 6.67% |
| Proactive-only | 240 | 237 | 240 | 3 | 66.67% |
| Hybrid | 625 | 611 | 625 | 14 | 28.57% |

Normaliser outcome strata are observational: activation was not randomised and may identify harder or less format-compliant trajectories.
The normaliser is part of the SRA bundles; this table does not establish that it is causally neutral.

## Forced rejected endpoints

| Bundle | Executable final endpoint | Forced rejected endpoints |
|---|---:|---:|
| Local reference port | 145 | 148 |
| Reactive | 90 | 90 |
| Proactive-only | 176 | 177 |
| Hybrid | 96 | 96 |

The machine-readable audit also exports all 16 four-arm success/failure cells. Non-zero cells are listed below in reference/reactive/proactive/hybrid bit order:

`0000`=129, `0001`=8, `0010`=2, `0011`=3, `0100`=3, `0101`=1, `0110`=1, `0111`=1, `1000`=19, `1001`=15, `1010`=2, `1011`=9, `1100`=5, `1101`=5, `1110`=4, `1111`=33

## Robustness and interpretation boundaries

- Hybrid minus Local reference port: database-cluster bootstrap 95% interval [-12.95, -1.25] pp; leave-one-database-out direction reversals: 0.
- Hybrid minus Reactive: database-cluster bootstrap 95% interval [+2.83, +15.29] pp; leave-one-database-out direction reversals: 0.
- Hybrid minus Proactive-only: database-cluster bootstrap 95% interval [+2.76, +13.87] pp; leave-one-database-out direction reversals: 0.
- Shared failure and terminal labels are descriptive endpoints, not semantic diagnoses of SQL reasoning or answer incorporation.
- The model alias is mutable, policy and simulator use independent stochastic calls, and the gold-conditioned simulator is protected by lexical rather than semantic leakage checks.

## Rejected-SQL repetition observation

The final standardised controller did not structurally block repeated rejected SQL. The counts below are observations from retained history, not enforced invariants:

| Bundle | Exact repeat events after rejection | Trajectories affected |
|---|---:|---:|
| Local reference port | 17 | 17 |
| Reactive | 13 | 13 |
| Proactive-only | 18 | 15 |
| Hybrid | 13 | 13 |
