from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from interaction.bird_dev_matched import (
    ARMS,
    PROTOCOL_VERSION,
    BirdDevMatchedController,
    BirdDevPrivateEvaluator,
    build_schema_only_context,
    normalize_simulator_answer,
    normalize_sra_payload,
    render_clarification,
    sra_output_schema,
    sphinteract_execution_equal,
)
from interaction.make_bird_dev_full_selection import build_manifest
from interaction.analyze_bird_dev_matched import (
    _add_holm_adjustment,
    _arms_for_protocol,
    _exact_mcnemar_p_value,
    _load_records,
    _load_sensitivity_exclusions,
    _paired_specs,
)
from interaction.sphinteract_source import SphinteractPrompts
from interaction.run_bird_dev_matched import run_tier, validate_freeze
from interaction.sphinteract_source import SPHINTERACT_NOTEBOOK_SHA256
from interaction.sqlite_env import SQLiteEnvironment, bird_execution_equal
from interaction.trajectory import BenchmarkTask, Usage


def sra_payload(decision: str = "ask") -> dict:
    return {
        "decision": decision,
        "summary": "One distinction remains." if decision == "ask" else "Clear.",
        "reviews": {
            "AmbQuestion": "Reviewed.",
            "AmbTableColumn": "Reviewed.",
            "AmbOutput": "Reviewed.",
            "AmbValue": "Reviewed.",
        },
        "question": "Which value do you intend?" if decision == "ask" else "",
        "options": ["One", "Two", "Both", "Other—please specify"]
        if decision == "ask"
        else [],
    }


class QueueInvoker:
    model = "scripted"
    reasoning_effort = "none"

    def __init__(self, outputs: list[dict]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.output_schemas: list[dict] = []

    def run(self, prompt, *, output_schema, artifact_directory=None, case_id="case"):
        self.prompts.append(prompt)
        self.output_schemas.append(output_schema)
        output = dict(self.outputs.pop(0))
        transport_attempts = int(output.pop("_transport_attempts", 1))
        return SimpleNamespace(
            output=output,
            usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12),
            elapsed_ms=1.0,
            transport_attempts=transport_attempts,
            served_model="scripted",
        )


def prompts() -> SphinteractPrompts:
    return SphinteractPrompts(
        feedback_prefix="FEEDBACK PREFIX\n",
        feedback="NL={nlq}\nGOLD={query}\nCQ={question}",
        clarification_prefix="CQ PREFIX\n",
        clarification=("Q={question}\nS={schema}\nSQL={sqls}\nCQS={cqs}\nASK"),
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


class BirdDevMatchedTest(unittest.TestCase):
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
        self.rows = [
            {
                "question_id": 1,
                "db_id": "demo",
                "question": self.task.question,
                "difficulty": "simple",
                "SQL": "SELECT value FROM items WHERE value = 2",
            }
        ]
        self.evaluator = BirdDevPrivateEvaluator(
            rows=self.rows,
            database_root=self.root / "databases",
            timeout_seconds=1,
            max_rows=100,
        )
        self.schema = build_schema_only_context(self.database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def controller(self, policy_outputs, simulator_outputs):
        return BirdDevMatchedController(
            policy_invoker=QueueInvoker(policy_outputs),
            simulator_invoker=QueueInvoker(simulator_outputs),
            evaluator=self.evaluator,
            prompts=prompts(),
            database_root=self.root / "databases",
            artifact_root=self.root / "artifacts",
        )

    def test_schema_context_has_schema_but_no_values(self) -> None:
        self.assertIn("CREATE TABLE items", self.schema)
        self.assertNotIn("one", self.schema)
        self.assertNotIn("two", self.schema)

    def test_sph_scorer_ignores_column_order_but_official_does_not(self) -> None:
        environment = SQLiteEnvironment(self.database, timeout_seconds=1)
        left = environment.execute("SELECT value, label FROM items ORDER BY value")
        right = environment.execute("SELECT label, value FROM items ORDER BY value")

        self.assertFalse(bird_execution_equal(left, right))
        self.assertTrue(
            sphinteract_execution_equal(
                left,
                right,
                gold_sql="SELECT label, value FROM items ORDER BY value",
            )
        )

    def test_invalid_initial_sql_gets_one_repair(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT missing FROM items"},
                {"sql": "SELECT value FROM items WHERE value = 2"},
            ],
            [],
        )

        shared = controller.run_shared_initial(self.task, schema=self.schema)
        no_user = controller.no_user_record(self.task, shared)

        self.assertEqual(shared["model_calls"], 2)
        self.assertEqual(len(shared["candidates"]), 2)
        self.assertEqual(no_user["termination"], "accepted_initial")

    def test_transport_retries_count_as_physical_model_calls(self) -> None:
        controller = self.controller(
            [
                {
                    "sql": "SELECT value FROM items WHERE value = 2",
                    "_transport_attempts": 2,
                }
            ],
            [],
        )

        shared = controller.run_shared_initial(self.task, schema=self.schema)

        self.assertEqual(shared["model_calls"], 2)

    def test_cqs_asks_then_recovers(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 1"},
                sra_payload(),
                {"sql": "SELECT value FROM items WHERE value = 2"},
            ],
            [{"answer": "Two."}],
        )
        shared = controller.run_shared_initial(self.task, schema=self.schema)

        arm = controller.run_reactive_arm(
            self.task,
            schema=self.schema,
            shared=shared,
            arm="sph_cq",
        )

        self.assertTrue(arm["official_success"])
        self.assertEqual(arm["official_first_success_turn"], 1)
        self.assertEqual(arm["user_turns"], 1)

    def test_cqs_early_stop_can_terminate_without_asking(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 1"},
                sra_payload("no_ambiguity"),
            ],
            [],
        )
        shared = controller.run_shared_initial(self.task, schema=self.schema)

        arm = controller.run_reactive_arm(
            self.task,
            schema=self.schema,
            shared=shared,
            arm="sph_cq_es",
        )

        self.assertEqual(arm["termination"], "no_ambiguity")
        self.assertEqual(arm["user_turns"], 0)

    def test_proactive_only_asks_once_then_uses_sql_only_retries(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 1"},
                sra_payload(),
                {"sql": "SELECT value FROM items WHERE value = 1"},
                {"sql": "SELECT value FROM items WHERE value = 2"},
            ],
            [{"answer": "Two."}],
        )
        shared = controller.run_shared_initial(self.task, schema=self.schema)

        arm = controller.run_proactive_only(
            self.task,
            schema=self.schema,
            shared=shared,
        )

        self.assertTrue(arm["official_success"])
        self.assertEqual(arm["user_turns"], 1)
        self.assertEqual(arm["normal_sql_attempts"], 2)
        self.assertEqual(arm["termination"], "accepted_after_self_debug")
        self.assertIn("Two.", controller.policy_invoker.prompts[-1])

    def test_proactive_no_ambiguity_reuses_shared_initial(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 1"},
                sra_payload("no_ambiguity"),
                {"sql": "SELECT value FROM items WHERE value = 2"},
            ],
            [],
        )
        shared = controller.run_shared_initial(self.task, schema=self.schema)

        arm = controller.run_proactive_only(
            self.task,
            schema=self.schema,
            shared=shared,
        )

        self.assertTrue(arm["official_success"])
        self.assertTrue(arm["uses_shared_prefix"])
        self.assertEqual(arm["user_turns"], 0)
        self.assertEqual(arm["official_first_success_turn"], 0)

    def test_hybrid_uses_proactive_then_reactive_answer(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 1"},
                sra_payload(),
                {"sql": "SELECT value FROM items WHERE value = 1"},
                sra_payload(),
                {"sql": "SELECT value FROM items WHERE value = 2"},
            ],
            [{"answer": "Two."}, {"answer": "Use exactly two."}],
        )
        shared = controller.run_shared_initial(self.task, schema=self.schema)

        arm = controller.run_project_hybrid(
            self.task,
            schema=self.schema,
            shared=shared,
        )

        self.assertTrue(arm["official_success"])
        self.assertEqual(arm["official_first_success_turn"], 2)
        self.assertEqual(arm["user_turns"], 2)
        self.assertFalse(arm["uses_shared_prefix"])
        reactive_schema = controller.policy_invoker.output_schemas[3]
        self.assertEqual(
            reactive_schema["properties"]["decision"]["enum"],
            ["ask"],
        )

    def test_run_task_emits_the_five_v4_arms(self) -> None:
        controller = self.controller(
            [
                {"sql": "SELECT value FROM items WHERE value = 2"},
                sra_payload("no_ambiguity"),
                sra_payload("no_ambiguity"),
            ],
            [],
        )

        record = controller.run_task(self.task)

        self.assertEqual(tuple(record["arms"]), ARMS)
        self.assertTrue(all(arm["official_success"] for arm in record["arms"].values()))

    def test_render_clarification_requires_open_fourth_choice_set(self) -> None:
        rendered = render_clarification(sra_payload())
        self.assertIn("A. One", rendered)
        self.assertIn("D. Other", rendered)
        invalid = sra_payload()
        invalid["options"] = ["One", "Two"]
        with self.assertRaisesRegex(ValueError, "exactly four"):
            render_clarification(invalid)

    def test_sra_schema_and_normalizer_remove_bias_without_inventing_choice(
        self,
    ) -> None:
        schema = sra_output_schema(allow_no_ambiguity=False)
        self.assertEqual(schema["properties"]["options"]["minItems"], 4)
        raw = sra_payload()
        raw["options"] = ["One (Recommended)", "Two", "Both"]

        normalized, changes = normalize_sra_payload(raw)

        self.assertEqual(
            normalized["options"],
            ["One", "Two", "Both", "Other—please specify"],
        )
        self.assertEqual(
            changes,
            ("removed_recommended_labels", "appended_fixed_open_option"),
        )

    def test_open_option_detection_does_not_treat_another_as_other(self) -> None:
        raw = sra_payload()
        raw["options"] = [
            "One",
            "Two",
            "Use another available field",
            "D. Other (please specify)",
        ]

        normalized, changes = normalize_sra_payload(raw)

        self.assertEqual(normalized["options"][-1], "Other—please specify")
        self.assertEqual(changes, ("canonicalized_open_option",))
        self.assertIn("D. Other—please specify", render_clarification(normalized))

    def test_simulator_answer_neutralizes_evaluator_label(self) -> None:
        answer, changes = normalize_simulator_answer(
            "D. The Gold Query defines abnormal as T-BIL >= 2.0 mg/dL."
        )

        self.assertEqual(
            answer,
            "D. the intended request defines abnormal as T-BIL >= 2.0 mg/dL.",
        )
        self.assertEqual(changes, ("neutralized_evaluator_label",))

    def test_simulator_answer_rejects_complete_sql(self) -> None:
        with self.assertRaisesRegex(ValueError, "SQL-shaped"):
            normalize_simulator_answer("SELECT value FROM items WHERE value = 2")

    def test_analyzer_can_still_read_frozen_v2_records(self) -> None:
        records_path = self.root / "v2-records.jsonl"
        records_path.write_text(
            json.dumps({"protocol": "bird-dev-matched-v2"}) + "\n",
            encoding="utf-8",
        )

        records, protocol = _load_records(records_path)

        self.assertEqual(records, [{"protocol": "bird-dev-matched-v2"}])
        self.assertEqual(protocol, "bird-dev-matched-v2")
        self.assertEqual(
            _arms_for_protocol(protocol),
            ("no_user", "sph_cq", "sph_cq_es", "project_hybrid"),
        )

    def test_v4_analysis_includes_direct_timing_and_stopping_contrasts(self) -> None:
        specs = set(_paired_specs(PROTOCOL_VERSION))

        self.assertIn(
            ("proactive_minus_sph_cq", "proactive_only", "sph_cq"),
            specs,
        )
        self.assertIn(
            ("sph_cq_es_minus_sph_cq", "sph_cq_es", "sph_cq"),
            specs,
        )

    def test_exact_mcnemar_and_holm_adjustment_are_deterministic(self) -> None:
        self.assertEqual(_exact_mcnemar_p_value(0, 0), 1.0)
        self.assertEqual(_exact_mcnemar_p_value(0, 5), 0.0625)
        comparisons = {
            "a": {"exact_mcnemar_p_value": 0.01},
            "b": {"exact_mcnemar_p_value": 0.04},
            "c": {"exact_mcnemar_p_value": 0.03},
            "secondary": {"exact_mcnemar_p_value": 0.001},
        }

        _add_holm_adjustment(comparisons, ("a", "b", "c"))

        self.assertEqual(comparisons["a"]["holm_adjusted_p_value"], 0.03)
        self.assertEqual(comparisons["b"]["holm_adjusted_p_value"], 0.06)
        self.assertEqual(comparisons["c"]["holm_adjusted_p_value"], 0.06)
        self.assertIsNone(comparisons["secondary"]["holm_adjusted_p_value"])

    def test_freeze_rejects_changed_input_artifact(self) -> None:
        selection = self.root / "selection.json"
        selection.write_text('{"question_ids":[1]}\n', encoding="utf-8")
        artifact = self.root / "artifact.txt"
        artifact.write_text("frozen", encoding="utf-8")

        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        freeze = self.root / "freeze.json"
        freeze.write_text(
            json.dumps(
                {
                    "protocol": PROTOCOL_VERSION,
                    "status": "frozen_before_pilot_model_calls",
                    "selection": {"sha256": digest(selection)},
                    "shared_stack": {
                        "model": "test-model",
                        "reasoning_effort": "high",
                    },
                    "sphinteract_source": {
                        "notebook_sha256": SPHINTERACT_NOTEBOOK_SHA256,
                    },
                    "artifact_hashes": {str(artifact): digest(artifact)},
                }
            ),
            encoding="utf-8",
        )

        validate_freeze(
            freeze,
            selection_path=selection,
            model="test-model",
            reasoning_effort="high",
        )
        artifact.write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Input artifact changed"):
            validate_freeze(
                freeze,
                selection_path=selection,
                model="test-model",
                reasoning_effort="high",
            )

    def test_run_tier_allows_only_smoke_pilot_or_exact_full_size(self) -> None:
        self.assertEqual(run_tier(10), "smoke")
        self.assertEqual(run_tier(100), "pilot")
        self.assertEqual(run_tier(1534), "full")
        for invalid in (0, 101, 1533, 1535):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    run_tier(invalid)

    def test_full_selection_records_prespecified_unexposed_sensitivity(self) -> None:
        dataset = self.root / "dev.json"
        dataset.write_text("[]\n", encoding="utf-8")
        rows = [
            {
                "question_id": question_id,
                "db_id": f"db_{question_id % 11}",
                "difficulty": "simple",
            }
            for question_id in range(1534)
        ]

        manifest = build_manifest(
            rows=rows,
            dataset=dataset,
            smoke_ids=list(range(10)),
            pilot_ids=list(range(10, 110)),
        )

        self.assertEqual(manifest["tasks"], 1534)
        self.assertEqual(manifest["development_exposure"]["union_tasks"], 110)
        self.assertEqual(
            manifest["development_exposure"]["unexposed_sensitivity_tasks"],
            1424,
        )

        selection = self.root / "full-selection.json"
        selection.write_text(json.dumps(manifest), encoding="utf-8")
        self.assertEqual(
            _load_sensitivity_exclusions(selection),
            {str(value) for value in range(110)},
        )


if __name__ == "__main__":
    unittest.main()
