# Direct-API benchmark accounting

The authorised production replications used GPT-5.6 Luna with high reasoning
through the OpenAI Responses API.

| Study | Final scope | Final validity | Local spend ledger |
|---|---:|---|---:|
| Clarification value | 1,534 tasks x 3 controllers | Zero task/arm errors | US$12.64 |
| BIRD timing and stopping | 1,534 tasks x 5 controllers | Zero task/arm errors | US$33.34 |
| Mini-Interact | 240 tasks x 4 controllers | Zero system-error terminations | US$57.53 |
| **Production total** | 13,232 logical outcomes and trajectories | Valid final aggregates | **US$103.50** |

The ledger total is deliberately conservative. It includes discarded transport
attempts, score-blind system-error retries, and uncertain-timeout reservations.
Retained-record cost and token summaries are reported separately in each result
artifact. These are local estimates from recorded usage and published rates;
the provider invoice remains authoritative.
