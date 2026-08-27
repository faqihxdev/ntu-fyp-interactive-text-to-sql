from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from interaction.bird_dev_clarification_value import (
    ARMS,
    PROTOCOL_VERSION,
    BirdDevClarificationValueController,
    branch_execution_order,
)
from interaction.analyze_bird_dev_clarification_value import (
    _mechanical_audit,
    _paired_difference,
)
from interaction.analyze_bird_dev_clarification_value_public import (
    analyze_projection,
)
from interaction.export_bird_dev_clarification_value_public import (
    build_public_projection,
)
from interaction.bird_dev_matched import (
    BirdDevPrivateEvaluator,
    build_schema_only_context,
)
from interaction.sphinteract_source import SphinteractPrompts
from interaction.sphinteract_source import SPHINTERACT_NOTEBOOK_SHA256
from interaction.trajectory import BenchmarkTask, Usage
from interaction.run_bird_dev_clarification_value import (
    FREEZE_STATUS,
    FULL_FREEZE_STATUS,
    PILOT_FREEZE_STATUS,
    _record_requires_retry,
    _validate_record,
    run_tier,
    validate_freeze,
)
from interaction.make_bird_dev_clarification_value_selections import (
    build_full_selection,
    build_selections,
)


def _sra_payload() -> dict:
    return {
        "decision": "ask",
        "summary": "One distinction remains.",
        "reviews": {
            "AmbQuestion": "Reviewed.",
            "AmbTableColumn": "Reviewed.",
            "AmbOutput": "Reviewed.",
            "AmbValue": "Reviewed.",
        },
        "question": "Which value do you intend?",
        "options": ["One", "Two", "Both", "Other—please specify"],
    }


class QueueInvoker:
    model = "scripted"
    reasoning_effort = "none"

    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []

    def run(self, prompt, *, output_schema, artifact_directory=None, case_id="case"):
        self.prompts.append(prompt)
        return SimpleNamespace(
            output=dict(self.outputs.pop(0)),
            usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12),
            elapsed_ms=1.0,
            transport_attempts=1,
            served_model="scripted",
        )


def _prompts() -> SphinteractPrompts:
    return SphinteractPrompts(
        feedback_prefix="FEEDBACK PREFIX\n",
        feedback="NL={nlq}\nGOLD={query}\nCQ={question}",
        clarification_prefix="CQ PREFIX\n",
        clarification="Q={question}\nS={schema}\nSQL={sqls}\nCQS={cqs}\nASK",
        clarification_early_stop=(
            "Q={question}\nS={schema}\nSQL={sqls}\nCQS={cqs}\nES"
        ),
        sql_initial="S={schema}\nM={metadata}\nQ={question}\nSELECT ",
        sql_after_feedback=(
            "S={schema}\nSQL={sqls}\nCQ={cqas}\nM={metadata}\nQ={question}\nSELECT "
        ),
        sql_invalid_repair="S={schema}\nBAD={invalidSQL}\nERR={ex}\nSELECT ",
        sql_self_debug="S={schema}\nSQL={sqls}\nM={metadata}\nQ={question}\nSELECT ",
    )


class ClarificationValueControllerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        database_folder = self.root / "databases" / "demo"
        database_folder.mkdir(parents=True)
        self.database = database_folder / "demo.sqlite"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute("CREATE TABLE items (value INTEGER, label TEXT)")
            connection.executemany(
                "INSERT INTO items VALUES (?, ?)", [(1, "one"), (2, "two")]
            )
            connection.commit()
        self.task = BenchmarkTask(
            question_id=1,
            db_id="demo",
            question="Return the intended value.",
            evidence=None,
            difficulty="simple",
        )
        self.evaluator = BirdDevPrivateEvaluator(
            rows=[
                {
                    "question_id": 1,
                    "db_id": "demo",
                    "question": self.task.question,
                    "difficulty": "simple",
                    "SQL": "SELECT value FROM items WHERE value = 2",
                }
            ],
            database_root=self.root / "databases",
            timeout_seconds=1,
            max_rows=100,
        )
        self.schema = build_schema_only_context(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def controller(self, policy_outputs, simulator_outputs):
        return BirdDevClarificationValueController(
            policy_invoker=QueueInvoker(policy_outputs),
            simulator_invoker=QueueInvoker(simulator_outputs),
            evaluator=self.evaluator,
            prompts=_prompts(),
            database_root=self.root / "databases",
            artifact_root=self.root / "artifacts",
        )

    def test_sql_only_recovery_uses_no_questions_and_can_recover(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 1"},
                {"sql": "SELECT value FROM items WHERE value = 2"},
            ],
            [],
        )
        shared = controller.run_shared_initial(self.task, schema=self.schema)

        arm = controller.run_sql_only_recovery(
            self.task,
            schema=self.schema,
            shared=shared,
        )

        self.assertTrue(arm["official_success"])
        self.assertEqual(arm["normal_sql_attempts"], 2)
        self.assertEqual(arm["user_turns"], 0)
        self.assertEqual(arm["question_answers"], [])
        self.assertEqual(arm["protocol"], PROTOCOL_VERSION)
        self.assertIn("SQL=SELECT value FROM items", controller.policy_invoker.prompts[1])

    def test_reactive_and_sql_only_have_the_same_normal_attempt_cap(self) -> None:
        misses = [{"sql": "SELECT value FROM items WHERE value = 1"}] * 5
        sql_only = self.controller(misses, [])
        shared = sql_only.run_shared_initial(self.task, schema=self.schema)
        sql_only_arm = sql_only.run_sql_only_recovery(
            self.task,
            schema=self.schema,
            shared=shared,
        )

        reactive_outputs: list[dict] = [misses[0]]
        for _ in range(4):
            reactive_outputs.extend([_sra_payload(), misses[0]])
        reactive = self.controller(
            reactive_outputs,
            [{"answer": "One."}] * 4,
        )
        reactive_shared = reactive.run_shared_initial(self.task, schema=self.schema)
        reactive_arm = reactive.run_reactive_clarification(
            self.task,
            schema=self.schema,
            shared=reactive_shared,
        )

        self.assertEqual(sql_only_arm["normal_sql_attempts"], 5)
        self.assertEqual(reactive_arm["normal_sql_attempts"], 5)
        self.assertEqual(sql_only_arm["user_turns"], 0)
        self.assertEqual(reactive_arm["user_turns"], 4)

    def test_shared_initial_success_short_circuits_both_recovery_arms(self) -> None:
        controller = self.controller(
            [{"sql": "SELECT value FROM items WHERE value = 2"}],
            [],
        )

        record = controller.run_task(self.task)

        self.assertEqual(tuple(record["arms"]), ARMS)
        self.assertTrue(all(arm["official_success"] for arm in record["arms"].values()))
        self.assertTrue(
            all(arm["normal_sql_attempts"] == 1 for arm in record["arms"].values())
        )
        self.assertEqual(controller.policy_invoker.outputs, [])

    def test_branch_order_is_deterministic_and_balanced(self) -> None:
        first = [branch_execution_order(value) for value in range(200)]
        second = [branch_execution_order(value) for value in range(200)]

        self.assertEqual(first, second)
        sql_first = sum(order[0] == "sql_only_recovery" for order in first)
        self.assertGreater(sql_first, 75)
        self.assertLess(sql_first, 125)

    def test_runner_accepts_only_the_three_frozen_tier_sizes(self) -> None:
        self.assertEqual(run_tier(20), "smoke20")
        self.assertEqual(run_tier(100), "pilot100")
        self.assertEqual(run_tier(1534), "full1534")
        for invalid in (0, 1, 19, 21, 99, 101, 1533, 1535):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    run_tier(invalid)

    def test_freeze_binds_selection_model_effort_and_workers(self) -> None:
        selection = self.root / "selection.json"
        selection.write_text(
            json.dumps({"question_ids": list(range(20)), "tasks": 20}),
            encoding="utf-8",
        )
        digest = hashlib.sha256(selection.read_bytes()).hexdigest()
        freeze = self.root / "freeze.json"
        freeze.write_text(
            json.dumps(
                {
                    "protocol": PROTOCOL_VERSION,
                    "status": FREEZE_STATUS,
                    "selections": {
                        "smoke20": {
                            "path": str(selection.resolve()),
                            "sha256": digest,
                        }
                    },
                    "shared_stack": {
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "high",
                        "workers": 4,
                    },
                    "sphinteract_source": {
                        "notebook_sha256": SPHINTERACT_NOTEBOOK_SHA256,
                    },
                }
            ),
            encoding="utf-8",
        )

        validate_freeze(
            freeze,
            tier="smoke20",
            selection_path=selection,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            workers=4,
        )
        with self.assertRaisesRegex(ValueError, "Worker count"):
            validate_freeze(
                freeze,
                tier="smoke20",
                selection_path=selection,
                model="gpt-5.6-luna",
                reasoning_effort="high",
                workers=3,
            )

    def test_pilot_requires_the_post_smoke_freeze_status(self) -> None:
        selection = self.root / "pilot.json"
        selection.write_text(
            json.dumps({"question_ids": list(range(100)), "tasks": 100}),
            encoding="utf-8",
        )
        digest = hashlib.sha256(selection.read_bytes()).hexdigest()
        freeze = self.root / "freeze.json"
        freeze.write_text(
            json.dumps(
                {
                    "protocol": PROTOCOL_VERSION,
                    "status": PILOT_FREEZE_STATUS,
                    "selections": {
                        "pilot100": {
                            "path": str(selection.resolve()),
                            "sha256": digest,
                        }
                    },
                    "shared_stack": {
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "high",
                        "workers": 4,
                    },
                    "sphinteract_source": {
                        "notebook_sha256": SPHINTERACT_NOTEBOOK_SHA256,
                    },
                }
            ),
            encoding="utf-8",
        )

        validate_freeze(
            freeze,
            tier="pilot100",
            selection_path=selection,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            workers=4,
        )

    def test_full_run_requires_its_own_post_pilot_freeze_status(self) -> None:
        selection = self.root / "full.json"
        selection.write_text(
            json.dumps({"question_ids": list(range(1534)), "tasks": 1534}),
            encoding="utf-8",
        )
        digest = hashlib.sha256(selection.read_bytes()).hexdigest()
        freeze = self.root / "freeze.json"
        freeze.write_text(
            json.dumps(
                {
                    "protocol": PROTOCOL_VERSION,
                    "status": FULL_FREEZE_STATUS,
                    "selections": {
                        "full1534": {
                            "path": str(selection.resolve()),
                            "sha256": digest,
                        }
                    },
                    "shared_stack": {
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "high",
                        "workers": 4,
                    },
                    "sphinteract_source": {
                        "notebook_sha256": SPHINTERACT_NOTEBOOK_SHA256,
                    },
                }
            ),
            encoding="utf-8",
        )

        validate_freeze(
            freeze,
            tier="full1534",
            selection_path=selection,
            model="gpt-5.6-luna",
            reasoning_effort="high",
            workers=4,
        )

    def test_resume_validation_accepts_sorted_json_arm_keys(self) -> None:
        contract_digest = "contract"
        record = {
            "protocol": PROTOCOL_VERSION,
            "task": {"question_id": 1},
            "arms": {arm: {} for arm in sorted(ARMS)},
            "run_contract_sha256": contract_digest,
        }
        path = self.root / "record.json"
        path.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

        self.assertEqual(
            _validate_record(
                path,
                "1",
                expected_contract_sha256=contract_digest,
            ),
            record,
        )

    def test_resume_retries_only_protocol_errors(self) -> None:
        self.assertTrue(_record_requires_retry({"task_error": {"message": "retry"}}))
        self.assertTrue(
            _record_requires_retry(
                {"arms": {"reactive_clarification": {"termination": "arm_error"}}}
            )
        )
        self.assertFalse(
            _record_requires_retry(
                {"arms": {"reactive_clarification": {"termination": "accepted"}}}
            )
        )

    def test_analysis_audits_matched_prefix_and_primary_discordance(self) -> None:
        shared_candidate = {
            "sql": "SELECT value FROM items WHERE value = 1",
            "status": "ok",
            "official_ex": False,
            "sph_compatible_ex": False,
            "fingerprints": {"bird_set": "one"},
        }

        def arm(name: str, success: bool, turns: int, attempts: int) -> dict:
            candidates = [dict(shared_candidate)]
            if attempts > 1:
                candidates.append(
                    {
                        **shared_candidate,
                        "sql": "SELECT value FROM items WHERE value = 2",
                        "official_ex": success,
                        "sph_compatible_ex": success,
                        "fingerprints": {"bird_set": "two"},
                    }
                )
            return {
                "arm": name,
                "termination": "accepted" if success else "limit",
                "uses_shared_prefix": True,
                "candidates": candidates,
                "question_answers": (
                    [{"question": "Which?", "answer": "Two."}] * turns
                ),
                "normal_sql_attempts": attempts,
                "user_turns": turns,
                "official_success": success,
            }

        record = {
            "task": {"question_id": 1},
            "shared_initial": {
                "official_ex": False,
                "candidates": [shared_candidate],
            },
            "arms": {
                "no_user": arm("no_user", False, 0, 1),
                "sql_only_recovery": arm("sql_only_recovery", False, 0, 2),
                "reactive_clarification": arm(
                    "reactive_clarification", True, 1, 2
                ),
            },
        }

        self.assertTrue(_mechanical_audit([record])["passed"])
        comparison = _paired_difference(
            [record], "reactive_clarification", "sql_only_recovery"
        )
        self.assertEqual(comparison["difference_percentage_points"], 100.0)
        self.assertEqual(comparison["candidate_only_successes"], 1)

    def test_public_projection_replays_outcomes_without_private_fields(self) -> None:
        records = []
        outcomes = (
            (False, False, True),
            (True, True, True),
            (False, True, False),
        )
        for question_id, (no_user, sql_only, reactive) in enumerate(outcomes):
            records.append(
                {
                    "task": {
                        "question_id": question_id,
                        "question": "must not be exported",
                    },
                    "branch_execution_order": [
                        "sql_only_recovery",
                        "reactive_clarification",
                    ],
                    "shared_initial": {
                        "official_ex": no_user,
                        "sql": "must not be exported",
                    },
                    "arms": {
                        "no_user": {
                            "official_success": no_user,
                            "normal_sql_attempts": 1,
                            "user_turns": 0,
                            "termination": "accepted" if no_user else "rejected",
                        },
                        "sql_only_recovery": {
                            "official_success": sql_only,
                            "normal_sql_attempts": 2,
                            "user_turns": 0,
                            "termination": "accepted" if sql_only else "limit",
                        },
                        "reactive_clarification": {
                            "official_success": reactive,
                            "normal_sql_attempts": 2,
                            "user_turns": 1,
                            "termination": "accepted" if reactive else "limit",
                        },
                    },
                }
            )

        projection = build_public_projection(
            records=records,
            protocol_development_ids={0},
            source={"task_records_sha256": "example"},
        )

        summary = projection["summary_all_tasks"]
        self.assertEqual(summary["official_successes"]["no_user"], 1)
        self.assertEqual(summary["official_successes"]["sql_only_recovery"], 2)
        self.assertEqual(summary["official_successes"]["reactive_clarification"], 2)
        self.assertEqual(summary["reactive_only_successes"], 1)
        self.assertEqual(summary["sql_only_only_successes"], 1)
        self.assertEqual(
            projection["summary_excluding_clarification_value_smoke_or_pilot"][
                "tasks"
            ],
            2,
        )
        serialized = json.dumps(projection)
        self.assertNotIn("must not be exported", serialized)
        self.assertNotIn('"question"', serialized)
        self.assertNotIn('"sql"', serialized)

        replay = analyze_projection(projection)
        primary = replay["all_tasks"]["paired"]["reactive_minus_sql_only"]
        self.assertEqual(primary["difference_percentage_points"], 0.0)
        self.assertEqual(primary["candidate_only_successes"], 1)
        self.assertEqual(primary["baseline_only_successes"], 1)

    def test_selections_are_deterministic_disjoint_and_gold_independent(self) -> None:
        dataset = self.root / "dev.json"
        dataset.write_text("[]\n", encoding="utf-8")
        rows = [
            {
                "question_id": question_id,
                "db_id": f"db_{question_id % 11}",
                "difficulty": "simple",
                "question": f"private {question_id}",
                "SQL": f"SELECT {question_id}",
            }
            for question_id in range(1534)
        ]

        first = build_selections(rows=rows, dataset=dataset)
        second = build_selections(rows=rows, dataset=dataset)
        smoke, pilot = first

        self.assertEqual(first[0]["question_ids"], second[0]["question_ids"])
        self.assertEqual(first[1]["question_ids"], second[1]["question_ids"])
        self.assertEqual(len(smoke["question_ids"]), 20)
        self.assertEqual(len(pilot["question_ids"]), 100)
        self.assertFalse(set(smoke["question_ids"]) & set(pilot["question_ids"]))
        self.assertFalse(smoke["selection_uses_gold_sql_or_results"])

        full = build_full_selection(
            rows=rows,
            dataset=dataset,
            smoke=smoke,
            pilot=pilot,
        )
        self.assertEqual(full["question_ids"], list(range(1534)))
        self.assertEqual(full["tasks"], 1534)
        self.assertEqual(full["development_exposure"]["exposed_tasks"], 120)
        self.assertEqual(full["development_exposure"]["unexposed_tasks"], 1414)
        self.assertEqual(
            len(set(full["development_exposure"]["union_question_ids"])),
            120,
        )
        self.assertFalse(full["selection_uses_gold_sql_or_results"])


if __name__ == "__main__":
    unittest.main()
