from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from interaction.context import ContextBuilder
from interaction.evaluation import evaluate_file
from interaction.model import AgentTurn, FunctionCall
from interaction.no_user import NoUserBaseline, NoUserConfig
from interaction.sqlite_env import SQLiteEnvironment, bird_execution_equal
from interaction.trajectory import (
    BenchmarkTask,
    Trajectory,
    Usage,
    append_trajectory,
    load_trajectories,
)


class ScriptedAgent:
    requested_model = "scripted-test-model"
    reasoning_effort = "none"

    def __init__(self, calls: list[tuple[str, dict[str, str]]]) -> None:
        self.calls = list(calls)
        self.index = 0

    def _next(self) -> AgentTurn:
        name, arguments = self.calls[self.index]
        self.index += 1
        return AgentTurn(
            response_id=f"response-{self.index}",
            requested_model=self.requested_model,
            returned_model=self.requested_model,
            status="completed",
            function_calls=(
                FunctionCall(
                    call_id=f"call-{self.index}",
                    name=name,
                    arguments=arguments,
                    raw_arguments=json.dumps(arguments),
                ),
            ),
            output_text="",
            usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12),
            elapsed_ms=1.0,
        )

    def start(
        self, prompt: str, *, cache_key: str, metadata: dict[str, str]
    ) -> AgentTurn:
        self.last_prompt = prompt
        return self._next()

    def continue_after(
        self,
        previous_response_id: str,
        outputs: list[dict[str, str]],
        *,
        metadata: dict[str, str],
    ) -> AgentTurn:
        self.last_outputs = outputs
        return self._next()


def create_database(root: Path, db_id: str = "demo") -> Path:
    folder = root / db_id
    folder.mkdir(parents=True)
    path = folder / f"{db_id}.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT, category TEXT)"
        )
        connection.executemany(
            "INSERT INTO items(name, category) VALUES (?, ?)",
            [("Alpha", "x"), ("Alpha", "x"), ("Beta", "y")],
        )
        connection.commit()
    return path


class SQLiteEnvironmentTest(unittest.TestCase):
    def test_execution_is_read_only_and_bird_equality_ignores_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = create_database(Path(directory))
            environment = SQLiteEnvironment(database)
            duplicate = environment.execute(
                "SELECT name AS first_name FROM items WHERE category = 'x'"
            )
            distinct = environment.execute(
                "SELECT DISTINCT name AS other_name FROM items WHERE category = 'x'"
            )
            rejected = environment.execute("DELETE FROM items")

            self.assertTrue(bird_execution_equal(duplicate, distinct))
            self.assertNotEqual(
                duplicate.observation.fingerprints["visible_multiset"],
                distinct.observation.fingerprints["visible_multiset"],
            )
            self.assertEqual(rejected.observation.status, "rejected_non_read_query")
            self.assertEqual(
                environment.execute("SELECT COUNT(*) FROM items").rows,
                ((3,),),
            )


class NoUserBaselineTest(unittest.TestCase):
    def test_value_retrieval_cache_is_task_specific(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = create_database(root)
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("ALTER TABLE items ADD COLUMN alpha_name TEXT")
                connection.execute("ALTER TABLE items ADD COLUMN beta_name TEXT")
                connection.execute(
                    "UPDATE items SET alpha_name = 'Shared', beta_name = 'Different'"
                )
                connection.execute("UPDATE items SET beta_name = 'Shared' WHERE id = 3")
                connection.commit()
            builder = ContextBuilder(
                retrieval_query_budget=2,
                retrieval_match_limit=1,
            )
            alpha = builder.build(
                BenchmarkTask(
                    question_id=101,
                    db_id="demo",
                    question="Find alpha_name equal to 'Shared'.",
                    evidence=None,
                    difficulty="simple",
                ),
                database,
            )
            beta = builder.build(
                BenchmarkTask(
                    question_id=102,
                    db_id="demo",
                    question="Find beta_name equal to 'Shared'.",
                    evidence=None,
                    difficulty="simple",
                ),
                database,
            )
            self.assertGreater(alpha.retrieval_queries, 0)
            self.assertGreater(beta.retrieval_queries, 0)
            self.assertEqual(alpha.retrieved_values[0].column, "alpha_name")
            self.assertEqual(beta.retrieved_values[0].column, "beta_name")

            alpha_cached = builder.build(
                BenchmarkTask(
                    question_id=101,
                    db_id="demo",
                    question="Find alpha_name equal to 'Shared'.",
                    evidence=None,
                    difficulty="simple",
                ),
                database,
            )
            self.assertTrue(alpha_cached.retrieval_cache_hit)
            self.assertEqual(alpha_cached.retrieval_queries, 0)
            self.assertEqual(alpha_cached.retrieved_values, alpha.retrieved_values)

    def test_probe_error_repair_submission_and_gold_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            task = BenchmarkTask(
                question_id=7,
                db_id="demo",
                question="List the Alpha items.",
                evidence="Alpha means category x.",
                difficulty="simple",
            )
            agent = ScriptedAgent(
                [
                    (
                        "query_database",
                        {
                            "sql": "SELECT DISTINCT category FROM items",
                            "purpose": "Inspect category values",
                        },
                    ),
                    ("submit_sql", {"sql": "SELECT missing FROM items"}),
                    (
                        "submit_sql",
                        {"sql": "SELECT name FROM items WHERE category = 'x'"},
                    ),
                ]
            )
            runner = NoUserBaseline(
                model=agent,
                database_root=root,
                context_builder=ContextBuilder(retrieval_query_budget=0),
                config=NoUserConfig(max_model_calls=5),
            )
            trajectory = runner.run(task, run_id="test-run")

            self.assertEqual(trajectory.termination, "submitted_executable_sql")
            self.assertEqual(
                trajectory.final_sql,
                "SELECT name FROM items WHERE category = 'x'",
            )
            kinds = [event.kind for event in trajectory.events]
            self.assertEqual(kinds.count("database_probe"), 1)
            self.assertEqual(kinds.count("execution_repair_feedback"), 1)
            self.assertEqual(kinds.count("final_submission"), 2)
            self.assertNotIn("gold", agent.last_prompt.lower())
            self.assertFalse(trajectory.config["gold_visible_to_policy"])

    def test_trajectory_round_trip_and_separate_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            trajectory = Trajectory.start(
                run_id="round-trip",
                policy="test",
                task=BenchmarkTask(
                    question_id=1,
                    db_id="demo",
                    question="List x names",
                    evidence=None,
                    difficulty="simple",
                ),
                config={"gold_visible_to_policy": False},
            )
            trajectory.finish(
                termination="submitted_executable_sql",
                total_elapsed_ms=1.0,
                final_sql="SELECT name FROM items WHERE category = 'x'",
            )
            trajectory_path = root / "trajectories.jsonl"
            append_trajectory(trajectory_path, trajectory)
            loaded = load_trajectories(trajectory_path)
            self.assertEqual(loaded[0].to_dict(), trajectory.to_dict())

            dataset_path = root / "dataset.json"
            dataset_path.write_text(
                json.dumps(
                    [
                        {
                            "question_id": 1,
                            "db_id": "demo",
                            "question": "List x names",
                            "evidence": None,
                            "difficulty": "simple",
                            "SQL": (
                                "SELECT DISTINCT name FROM items WHERE category = 'x'"
                            ),
                        }
                    ]
                ),
                encoding="utf-8",
            )
            report = evaluate_file(
                trajectory_path,
                dataset_path,
                root,
                workers=1,
            )
            self.assertEqual(report["summary"]["execution_accuracy_percent"], 100.0)
            self.assertTrue(report["records"][0]["correct"])


if __name__ == "__main__":
    unittest.main()
