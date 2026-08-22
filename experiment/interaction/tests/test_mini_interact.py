from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from interaction.mini_interact import (
    CodexMiniInteractUserSimulator,
    SanitizedCodexMiniInteractUserSimulator,
    SimulatorLeakageError,
    load_public_tasks,
    validate_policy_visible_simulator_answer,
)
from interaction.trajectory import Usage


class FakeProxy:
    model = "gpt-5.6-luna"
    reasoning_effort = "max"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, prompt, **kwargs):
        self.prompts.append(prompt)
        output = (
            {"action": "labeled", "target": "metric"}
            if len(self.prompts) == 1
            else {"answer": "Use the quality score defined for this task."}
        )
        return SimpleNamespace(
            output=output,
            usage=Usage(input_tokens=5, output_tokens=2, total_tokens=7),
            elapsed_ms=10.0,
        )


class MiniInteractTest(unittest.TestCase):
    def test_public_task_excludes_masked_knowledge_and_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "demo"
            database.mkdir()
            (database / "demo_kb.jsonl").write_text(
                json.dumps({"id": 0, "knowledge": "visible"})
                + "\n"
                + json.dumps({"id": 1, "knowledge": "masked-secret"})
                + "\n",
                encoding="utf-8",
            )
            public_path = root / "mini.jsonl"
            public_path.write_text(
                json.dumps(
                    {
                        "instance_id": "demo_1",
                        "selected_database": "demo",
                        "amb_user_query": "Show the intended metric.",
                        "user_query_ambiguity": {
                            "critical_ambiguity": [
                                {"term": "metric", "sql_snippet": "SECRET_SQL"}
                            ]
                        },
                        "knowledge_ambiguity": [{"deleted_knowledge": 1}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            task = load_public_tasks(public_path, root)[0]
            serialized = json.dumps(asdict(task))

            self.assertIn("visible", task.evidence or "")
            self.assertNotIn("masked-secret", task.evidence or "")
            self.assertNotIn("SECRET_SQL", serialized)

    def test_structured_simulator_uses_two_calls_without_logging_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "demo"
            database.mkdir()
            (database / "demo_schema.txt").write_text(
                "CREATE TABLE items(value INTEGER);", encoding="utf-8"
            )
            proxy = FakeProxy()
            simulator = CodexMiniInteractUserSimulator(
                proxy=proxy,
                dataset_root=root,
            )
            task = load_task()
            row = {
                "instance_id": "demo_1",
                "selected_database": "demo",
                "amb_user_query": task.question,
                "user_query_ambiguity": {
                    "critical_ambiguity": [
                        {"term": "metric", "sql_snippet": "value * 2"}
                    ]
                },
                "knowledge_ambiguity": [],
                "sol_sql": ["SELECT value * 2 FROM items"],
            }

            answer = simulator.answer(task, "Which metric?", (), row)

            self.assertEqual(len(proxy.prompts), 2)
            self.assertEqual(answer.metadata["encoder_action"], "labeled")
            self.assertNotIn("metric", json.dumps(answer.metadata))
            self.assertEqual(answer.usage.total_tokens, 14)
            self.assertNotIn("SELECT", answer.text.upper())

    def test_sanitized_simulator_records_boundary_and_accepts_plain_answer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "demo"
            database.mkdir()
            (database / "demo_schema.txt").write_text(
                "CREATE TABLE items(value INTEGER);", encoding="utf-8"
            )
            simulator = SanitizedCodexMiniInteractUserSimulator(
                proxy=FakeProxy(), dataset_root=root
            )
            task = load_task()
            row = {
                "instance_id": "demo_1",
                "selected_database": "demo",
                "amb_user_query": task.question,
                "user_query_ambiguity": {},
                "knowledge_ambiguity": [],
                "sol_sql": ["SELECT value FROM items"],
            }

            answer = simulator.answer(task, "Which metric?", (), row)

            self.assertEqual(
                answer.metadata["sanitizer"], "mini-interact-answer-sanitizer-v1"
            )
            self.assertEqual(
                answer.metadata["clear_query_source"], "ambiguous_query_fallback"
            )

    def test_simulator_sanitizer_rejects_sql_and_private_keys(self) -> None:
        with self.assertRaises(SimulatorLeakageError):
            validate_policy_visible_simulator_answer("SELECT value FROM items")
        with self.assertRaises(SimulatorLeakageError):
            validate_policy_visible_simulator_answer("The sol_sql field says nine.")


def load_task():
    from interaction.trajectory import BenchmarkTask

    return BenchmarkTask(
        question_id="demo_1",
        db_id="demo",
        question="Show the intended metric.",
        evidence=None,
        difficulty="interactive",
    )


if __name__ == "__main__":
    unittest.main()
