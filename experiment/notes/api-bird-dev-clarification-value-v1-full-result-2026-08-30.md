# Direct-API clarification-value result

All 1,534 BIRD Dev tasks completed under the pre-run freeze with zero task or
arm errors. The direct OpenAI Responses API run requested GPT-5.6 Luna with
high reasoning.

| Controller | BIRD EX | Recovery after 872 initial failures | Mean user turns |
|---|---:|---:|---:|
| Initial-attempt baseline | 662/1,534 (43.16%) | 0 | 0.00 |
| SQL-only recovery | 769/1,534 (50.13%) | 107 (12.3%) | 0.00 |
| Reactive clarification | 1,189/1,534 (77.51%) | 527 (60.4%) | 1.57 |

Reactive clarification exceeds equally capped SQL-only recovery by **27.38
percentage points** (95% task-bootstrap interval **+25.03 to +29.73**). It
succeeds alone on 437 tasks; SQL-only recovery succeeds alone on 17. Excluding
the 120 smoke and pilot IDs gives a similar +28.08-point difference. This
removes the primary protocol's development tasks, but the project had already
evaluated full BIRD Dev in an earlier study.

This establishes the value of the complete target-aware clarification process
over retry under the tested hidden rejection oracle. It does not isolate
question wording, represent a human-user study, or provide an autonomous BIRD
leaderboard score.

The retained records contain 12,071 model calls and 27.29 million tokens, with
a local estimated API cost of US$12.61. The provider invoice remains
authoritative.
