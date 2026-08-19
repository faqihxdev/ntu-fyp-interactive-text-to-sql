from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from readiness.check_datasets import (
    execute_readonly_sql,
    hash_snapshot,
    load_jsonl,
    preserve_completed_gold_sweep,
    sqlite_quick_check,
    sqlite_table_counts,
)


class DatasetReadinessHelpersTest(unittest.TestCase):
    def test_jsonl_loader_rejects_non_object_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text(json.dumps(["not", "an", "object"]) + "\n", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                load_jsonl(path)

    def test_sqlite_helpers_validate_and_execute_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.sqlite"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
                connection.executemany(
                    "INSERT INTO items(value) VALUES (?)", [("a",), ("b",)]
                )
                connection.commit()

            self.assertEqual(sqlite_quick_check(path), 1)
            self.assertEqual(sqlite_table_counts(path), {"items": 2})
            self.assertEqual(
                execute_readonly_sql(path, "SELECT * FROM items", timeout_seconds=1.0),
                2,
            )

    def test_snapshot_hashes_are_deterministic_and_ignore_git(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data.txt").write_text("hello", encoding="utf-8")
            (root / ".git").mkdir()
            (root / ".git" / "ignored").write_text("metadata", encoding="utf-8")

            first = hash_snapshot(root)
            second = hash_snapshot(root)

            self.assertEqual(first, second)
            self.assertEqual(first["file_count"], 1)
            self.assertEqual(list(first["files"]), ["data.txt"])

    def test_completed_gold_sweep_is_preserved_only_for_same_snapshot(self) -> None:
        def manifest(metadata_hash: str, requested: bool) -> dict:
            return {
                "overall_status": "conditional",
                "hard_failures": [],
                "conditional_components": ["mini_interact", "clarity"],
                "snapshots": {
                    "bird_sql_dev_20251106": {"hash": metadata_hash},
                    "bird_sql_dev_20251106_databases": {"hash": "db"},
                },
                "checks": {
                    "bird_sql_dev_20251106": {
                        "status": "pass",
                        "gold_execution": {
                            "requested": requested,
                            "passed": 10 if requested else 0,
                            "timed_out": 1 if requested else 0,
                            "errors": 0,
                        },
                    },
                    "mini_interact": {"status": "conditional"},
                    "bird_interact_evaluator": {"status": "pass"},
                    "bird_sql_dev_20251106_databases": {"status": "pass"},
                    "clarity": {"status": "blocked"},
                },
            }

        current = manifest("same", False)
        previous = manifest("same", True)
        self.assertTrue(preserve_completed_gold_sweep(current, previous))
        self.assertTrue(
            current["checks"]["bird_sql_dev_20251106"]["gold_execution"][
                "requested"
            ]
        )
        self.assertEqual(
            current["checks"]["bird_sql_dev_20251106"]["status"],
            "conditional",
        )

        changed = manifest("changed", False)
        self.assertFalse(preserve_completed_gold_sweep(changed, previous))


if __name__ == "__main__":
    unittest.main()
