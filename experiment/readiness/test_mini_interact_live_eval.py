from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from readiness.mini_interact_live_eval import evaluate_live_instance


class MiniInteractLiveEvaluatorTest(unittest.TestCase):
    def test_live_default_scorer_accepts_match_and_rejects_wrong_sql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "demo"
            database.mkdir()
            template = database / "demo_template.sqlite"
            with closing(sqlite3.connect(template)) as connection:
                connection.execute("CREATE TABLE items(value INTEGER)")
                connection.executemany("INSERT INTO items VALUES (?)", [(9,), (12,)])
                connection.commit()
            row = {
                "instance_id": "demo_1",
                "selected_database": "demo",
                "category": "Query",
                "sol_sql": ["SELECT value FROM items WHERE value = 9"],
                "test_cases": [],
            }

            accepted = evaluate_live_instance(
                row,
                "SELECT value FROM items WHERE value = 9",
                database_root=root,
            )
            rejected = evaluate_live_instance(
                row,
                "SELECT value FROM items WHERE value = 12",
                database_root=root,
            )

            self.assertEqual(accepted["status"], "success", json.dumps(accepted))
            self.assertEqual(rejected["status"], "failed", json.dumps(rejected))
            self.assertTrue(rejected["assertion_error"])
            self.assertTrue(accepted["source_template_unchanged"])

    def test_live_default_scorer_classifies_invalid_sql(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "demo"
            database.mkdir()
            template = database / "demo_template.sqlite"
            with closing(sqlite3.connect(template)) as connection:
                connection.execute("CREATE TABLE items(value INTEGER)")
                connection.commit()
            row = {
                "instance_id": "demo_1",
                "selected_database": "demo",
                "category": "Query",
                "sol_sql": ["SELECT value FROM items"],
            }

            result = evaluate_live_instance(
                row,
                "SELECT missing FROM items",
                database_root=root,
            )

            self.assertTrue(result["execution_error"], json.dumps(result))
            self.assertEqual(result["status"], "failed")

    def test_child_failure_does_not_expose_stderr(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "demo"
            database.mkdir()
            template = database / "demo_template.sqlite"
            with closing(sqlite3.connect(template)) as connection:
                connection.execute("CREATE TABLE items(value INTEGER)")
                connection.commit()
            row = {
                "instance_id": "demo_1",
                "selected_database": "demo",
                "category": "Query",
                "sol_sql": ["SELECT value FROM items"],
            }
            failed = subprocess.CompletedProcess(
                args=["worker"],
                returncode=1,
                stdout="",
                stderr="private SQL and scorer path must not escape",
            )

            with patch(
                "readiness.mini_interact_live_eval.subprocess.run",
                return_value=failed,
            ):
                result = evaluate_live_instance(
                    row,
                    "SELECT value FROM items",
                    database_root=root,
                )

        self.assertNotIn("stderr", result)
        self.assertTrue(result["stderr_present"])
        self.assertEqual(result["adapter_error_type"], "ChildProcessError")


if __name__ == "__main__":
    unittest.main()
