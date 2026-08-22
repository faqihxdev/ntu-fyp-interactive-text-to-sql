from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from interaction.controller import ClarificationAnswer
from interaction.oracles import (
    CleanBirdExecutionOracle,
    MiniInteractExecutionOracle,
    OfficialMiniInteractLiveOracle,
)
from interaction.sqlite_env import SQLiteEnvironment
from interaction.trajectory import BenchmarkTask


class RecordingAnswerer:
    def __init__(self) -> None:
        self.private_rows: list[dict] = []

    def answer(self, task, question, history, private_row):
        self.private_rows.append(private_row)
        return ClarificationAnswer(
            text="Use the intended narrow definition.",
            metadata={"simulator": "test"},
        )


def create_database(root: Path, db_id: str = "demo") -> Path:
    folder = root / db_id
    folder.mkdir(parents=True)
    path = folder / f"{db_id}.sqlite"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE items(value INTEGER)")
        connection.executemany("INSERT INTO items VALUES (?)", [(9,), (12,)])
        connection.commit()
    return path


class OracleTest(unittest.TestCase):
    def test_clean_bird_judges_execution_and_keeps_gold_in_answerer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = create_database(root)
            answerer = RecordingAnswerer()
            oracle = CleanBirdExecutionOracle(
                rows=[
                    {
                        "question_id": 7,
                        "db_id": "demo",
                        "SQL": "SELECT value FROM items WHERE value = 9",
                    }
                ],
                database_root=root,
                answerer=answerer,
            )
            task = BenchmarkTask(7, "demo", "Which value?", None, "simple")
            environment = SQLiteEnvironment(database)

            wrong = oracle.judge(
                task,
                "SELECT value FROM items WHERE value = 12",
                environment.execute("SELECT value FROM items WHERE value = 12"),
            )
            correct = oracle.judge(
                task,
                "SELECT value FROM items WHERE value = 9",
                environment.execute("SELECT value FROM items WHERE value = 9"),
            )
            answer = oracle.answer(task, "Which one?", ())

            self.assertFalse(wrong.accepted)
            self.assertTrue(correct.accepted)
            self.assertNotIn("SQL", wrong.metadata)
            self.assertIsInstance(answer, ClarificationAnswer)
            self.assertEqual(
                answerer.private_rows[0]["SQL"],
                "SELECT value FROM items WHERE value = 9",
            )

    @patch("interaction.oracles.evaluate_instance")
    def test_mini_interact_uses_official_pred_mode(self, evaluate) -> None:
        evaluate.return_value = {
            "status": "success",
            "evaluation_phase_execution_error": False,
            "evaluation_phase_timeout_error": False,
            "evaluation_phase_assertion_error": False,
            "effective_total_test_cases": 1,
            "passed_test_cases": 1,
            "source_template_unchanged": True,
        }
        answerer = RecordingAnswerer()
        row = {
            "instance_id": "demo_1",
            "selected_database": "demo",
            "sol_sql": ["SELECT 9"],
            "test_cases": [],
        }
        oracle = MiniInteractExecutionOracle(
            rows=[row],
            database_root=Path("unused"),
            answerer=answerer,
            evaluator_script=Path(__file__),
        )
        task = BenchmarkTask("demo_1", "demo", "Which value?", None, "interactive")

        judgement = oracle.judge(task, "SELECT 9", unittest.mock.Mock())
        answer = oracle.answer(task, "Which one?", ())

        self.assertTrue(judgement.accepted)
        submitted = evaluate.call_args.args[0]
        self.assertEqual(submitted["pred_sqls"], ["SELECT 9"])
        self.assertEqual(evaluate.call_args.kwargs["mode"], "pred")
        self.assertNotIn("sol_sql", judgement.metadata)
        self.assertIsInstance(answer, ClarificationAnswer)
        self.assertEqual(answerer.private_rows[0]["sol_sql"], ["SELECT 9"])

    @patch("interaction.oracles.evaluate_live_instance")
    def test_standardized_oracle_uses_live_agent_scorer(self, evaluate) -> None:
        evaluate.return_value = {
            "status": "success",
            "execution_error": False,
            "timeout_error": False,
            "assertion_error": False,
            "evaluator": "official_live_agent_test_case_default",
        }
        row = {
            "instance_id": "demo_1",
            "selected_database": "demo",
            "sol_sql": ["SELECT 9"],
        }
        oracle = OfficialMiniInteractLiveOracle(
            rows=[row],
            database_root=Path("unused"),
            answerer=RecordingAnswerer(),
        )
        task = BenchmarkTask("demo_1", "demo", "Which value?", None, "interactive")
        result = unittest.mock.Mock()
        result.observation.status = "ok"

        judgement = oracle.judge(task, "SELECT 9", result)

        self.assertTrue(judgement.accepted)
        evaluate.assert_called_once()
        self.assertEqual(
            judgement.metadata["evaluator"],
            "official_live_agent_test_case_default",
        )
        self.assertNotIn("sol_sql", judgement.metadata)


if __name__ == "__main__":
    unittest.main()
