# Interactive Text-to-SQL comparator map

**Date:** 24 August 2026
**Scope:** the FYP's matched multi-round controller studies on original BIRD Dev and Mini-Interact. Only primary papers and official repositories are used.

## Bottom line

Sphinteract is **not the only related work**, but it is the **best direct method comparator for the original-BIRD experiment**: it uses BIRD Dev, iterative SQL repair, a gold-conditioned user oracle, at most four feedback rounds/five SQL attempts, and execution accuracy. Its CQs and CQs-with-early-stopping methods should therefore be rerun locally under the same model, inputs, oracle, limits, and scorer as the project controller.

For the Mini-Interact experiment, the closest direct benchmark comparator is instead the **released Mini-Interact reference scaffold** under the same local harness. Published BIRD-Interact leaderboard scores are not directly comparable to the project's 240-task local Stress adaptation.

Here, **direct** means that task IDs, model, input context, simulated user, turn/action budget, database and scorer are held fixed so that the controller is the main changed factor. A paper can be highly relevant yet remain **context-only** if it changes the task or interaction contract.

## Comparator map

| Work | Dataset and protocol | Intervention | User or simulator feedback | Metrics | Local run/comparability | Verdict |
|---|---|---|---|---|---|---|
| [Sphinteract](https://www.vldb.org/pvldb/vol18/p1145-zhao.pdf) ([official repository](https://github.com/ZhaoFuheng/Sphinteract)) | Original BIRD Dev (1,534 questions) and KaggleDBQA. Generate an initial SQL, then allow at most four feedback rounds and five SQLs. | Simple correctness feedback; SRA multiple-choice clarification questions (CQs); CQs with model-decided early stopping (CQs&ES). | A GPT-4o oracle receives the clarification question and gold SQL; a separate human study is also reported. | Execution accuracy by round, SQL component precision/recall, interactions and cost. On zero-shot GPT-4 Turbo BIRD, the paper reports 32.07% without interaction, 58.93% for CQs and 53.00% for CQs&ES. | Code is public and the two closest arms are implemented locally. The published percentages are historical system anchors, not direct head-to-head scores when the model, prompt stack, oracle or evaluator differs. | **Direct method baseline on original BIRD when rerun locally. Best comparator for that track.** |
| [BIRD-Interact](https://arxiv.org/html/2510.05318) ([official repository](https://github.com/bird-bench/BIRD-Interact)) | Full has 600 PostgreSQL tasks with priority and follow-up subtasks. `c-Interact` follows a prescribed conversational/debugging flow; `a-Interact` exposes nine priced actions under a task budget. | Dynamic clarification, knowledge/schema/database exploration, SQL execution and repair. | A function-driven simulator first selects a constrained semantic action and then generates a grounded response from annotations and hidden task information. | Executable-test-case success rate, normalized reward, action cost and interaction analyses. | Official code exists, but Full/Lite tasks, PostgreSQL, follow-ups, budgets and scoring differ from both original BIRD EX and the local Mini adaptation. | **Direct benchmark family, but published Full/Lite scores are context-only for this FYP.** |
| [Mini-Interact](https://github.com/bird-bench/BIRD-Interact/tree/main/mini_interact) | Portable release of 300 SQLite, SELECT-only, knowledge-ambiguity priority tasks; follow-ups are currently decoupled and unreleased in Mini. | Agentic tool use and clarification under the released action meanings, task budgets and test-case scorer. | Released BIRD-Interact-style simulator plus private ground truth/test cases; the required private bundle is available locally. | Priority-task test-case success and interaction/action cost. | Runnable locally. A translated reference scaffold is comparable only inside the same frozen local IDs, model, simulator, action prices, budget and scorer. The project's 240-task Stress adaptation is not an official leaderboard submission. | **Direct reference baseline for the Mini-Interact track.** |
| [“I Need Help!”](https://aclanthology.org/2024.emnlp-main.131/) ([official repository](https://github.com/appier-research/i-need-help)) | BIRD, but as a one-shot support-seeking decision rather than a dialogue. | Direct Ask, Write-then-Ask and Execute-then-Ask estimate whether to reveal BIRD's annotated external knowledge before regenerating SQL. | No generated clarification question or conversational simulator; “support” is the fixed human-authored BIRD evidence. | EX, ask rate/user burden, Delta-Burden Curve and AUDBC, asking precision/recall and flip behaviour. | Public and runnable, but it tests calibrated evidence requesting, not multi-round clarification and answer incorporation. Its burden metrics are reusable. | **Context-only; optional gate ablation only if the thesis makes “when to request evidence” a main claim.** |
| [Somov & Tutubalina, *Confidence Estimation for Error Detection in Text-to-SQL Systems*](https://arxiv.org/html/2501.09527) ([official repository](https://github.com/runnerup96/error-detection-in-text2sql)) | Spider-derived PAUQ distribution-shift splits and EHRSQL, not BIRD. | Entropy/confidence-based selective prediction chooses whether to emit SQL or abstain. | None: abstention does not start a clarification or repair dialogue. | Coverage-risk, precision/recall, false discovery, AUC and calibration measures. | Analysis code is public but tied to different datasets and model stacks. | **Context-only for uncertainty, abstention and stopping.** |
| [AmbiSQL](https://arxiv.org/html/2508.15276v1) ([official repository](https://github.com/JustinzjDing/AmbiSQL)) | A curated demonstration set of 40 ambiguous BIRD/TAG questions over five BIRD databases, not full BIRD Dev. | Before SQL generation, detect/classify ambiguous phrases, ask multiple-choice questions, rewrite the NL question and recheck it before invoking XiYan-SQL. | Curated intended clarifications; no standardized multi-round oracle, shared turn cap or full-BIRD paired protocol is reported. | Exact match for downstream SQL and precision/recall/F1 for ambiguity detection. The v1 paper reports 42.5% to 92.5% exact match on its 40-query set. | Code/demo is public but depends on its own XiYan/API stack and curated data. It is not a drop-in matched arm. | **Context-only proactive-design comparator.** |
| [CLARITY](https://aclanthology.org/2026.acl-industry.86/) ([paper](https://arxiv.org/html/2604.22313)) | Synthetic Spider/BIRD single- and multi-turn cases with uni-/multi-facet column/value ambiguity or unanswerability and helpful, partial or unhelpful continuations. | Diagnose ambiguity, localize pivot terms/schema targets and predict SQL from a generated conversation. | Conversations are generated before evaluation; the tested model consumes the history rather than choosing clarification questions online. | Execution accuracy, loose/strict interpretation matching, ambiguity-detection accuracy and schema match accuracy. | No official public code/data artifact was identified in the inspected primary source, and the requested data are not local. Its static-history task also differs from online controller evaluation. | **Context-only diagnostic benchmark.** |
| [ABISS](https://arxiv.org/html/2607.23340) ([official repository](https://github.com/giosullutrone/ABISS-Evaluating-Text-to-SQL-Systems-Through-Agent-Interaction)) | Generated ABISS-BIRD/Spider questions covering answerable, ambiguous and unanswerable categories. Dynamic dialogue allows three clarification turns plus one final SQL/feedback turn. | Optional category prediction followed by unrestricted choice among SQL, clarification or unsolvability feedback. | A three-model council judges question relevance and generates natural-language answers grounded in hidden knowledge or gold-SQL fragments. | Recognition, category classification, EX, feedback accuracy, Answer Incorporation Rate and dialogue statistics. | Conceptually very close, but it uses newly generated tasks and a different oracle/scorer; the official repository currently says “COMING SOON,” so it is not locally runnable. | **Closest additional conceptual peer, but context-only for the present study.** |
| [MISP-SQL](https://aclanthology.org/D19-1547/) ([official repository](https://github.com/sunlab-osu/MISP)) | WikiSQL and Spider with parser-internal, incremental SQL construction. | An uncertainty detector selects a semantic unit; template questions obtain yes/no feedback; the world model constrains subsequent parser search. | Simulation answers from gold SQL; the paper also reports a small real-user study. | Query/execution accuracy and average questions per query. | Code is public but coupled to legacy SQLNet/SQLova/SyntaxSQLNet internals rather than an LLM/BIRD controller. | **Historical context only.** It establishes that detector–question–update loops predate current LLM systems. |

### Bibliographic correction

“I Need Help!” is **Wu et al., EMNLP 2024**, not Somov & Tutubalina. Somov & Tutubalina's 2025 paper is the confidence/abstention study listed separately above.

## Minimum defensible comparison set

### Primary: matched original-BIRD controller comparison

Use the same original BIRD Dev rows and hold the model, schema-only context, gold-conditioned simulator, four-user-turn/five-SQL cap and evaluator fixed across:

1. **No user interaction** — measures the recovery gain.
2. **Proactive-only** — one pre-SQL clarification opportunity, then SQL-only retries after rejection.
3. **Sphinteract CQs** — the strongest directly relevant reactive method.
4. **Project hybrid** — the same proactive checkpoint as proactive-only plus the same non-early-stop reactive loop as CQs.
5. **Sphinteract CQs&ES** — a secondary stopping ablation, not a timing arm.

This is the strongest direct comparison because each competing method is rerun in one shared protocol. The primary timing comparisons are proactive-only versus reactive CQ and hybrid versus each component. CQ&ES versus CQ isolates the stopping decision. Report final EX, EX by turn, user turns/SQL attempts and paired task-level differences. Sphinteract's published numbers should be shown only as labelled historical anchors.

Sphinteract's Simple binary-feedback arm is optional, not required: include it only if the thesis asks whether clarification content adds value beyond a bare rejection signal.

### Secondary: Mini-Interact controller/scaffold comparison

Retain the released-reference port as the direct Mini baseline and compare it with the local controller bundles only under the same frozen Mini IDs, model, simulator, budgets and scorer. Describe this as a **local Mini-Interact adaptation**, not an official reproduction or leaderboard comparison. It measures behaviour in a richer agentic environment; it does not replace the cleaner original-BIRD Sphinteract comparison.

### Contextual citations, not required implementations

- Use **“I Need Help!”** for user-burden/gating metrics.
- Use **AmbiSQL** for proactive pre-SQL clarification.
- Use **CLARITY** and **ABISS** for ambiguity localization, answer incorporation and modern interactive benchmark limitations.
- Use **Somov & Tutubalina** for confidence/abstention.
- Use **MISP-SQL** as the historical interactive-controller precursor.

## Why every related paper need not be implemented

A baseline should test the nearest competing explanation for the thesis claim. Implementing all related systems would mix different datasets, support types, models, SQL engines, user simulators, budgets and metrics; their score differences would not identify a controller effect. Related-work coverage and experimental baselines serve different purposes.

The defensible claim is therefore narrow:

> Sphinteract is the best direct method comparator for the matched original-BIRD study, while the released Mini-Interact scaffold is the direct reference for the Mini track. Other works position the study and contribute useful diagnostics, but do not require implementation because they test different interaction contracts.
