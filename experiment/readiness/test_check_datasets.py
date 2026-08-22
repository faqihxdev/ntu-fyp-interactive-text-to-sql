from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from readiness.check_datasets import (
    check_mini_interact_private,
    effective_mini_interact_test_case_count,
    execute_readonly_sql,
    hash_snapshot,
    load_jsonl,
    preserve_completed_gold_sweep,
    sqlite_logical_hash,
    sqlite_quick_check,
    sqlite_table_counts,
)


class DatasetReadinessHelpersTest(unittest.TestCase):
    def test_jsonl_loader_rejects_non_object_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rows.jsonl"
            path.write_text(
                json.dumps(["not", "an", "object"]) + "\n", encoding="utf-8"
            )
            with self.assertRaises(RuntimeError):
                load_jsonl(path)

    def test_sqlite_helpers_validate_and_execute_readonly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.sqlite"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute(
                    "CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)"
                )
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

    def test_sqlite_logical_hash_ignores_file_metadata_but_detects_rows(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / name for name in ("a.sqlite", "b.sqlite", "c.sqlite")]
            for index, path in enumerate(paths):
                with closing(sqlite3.connect(path)) as connection:
                    connection.execute("CREATE TABLE items (id INTEGER, value TEXT)")
                    connection.executemany(
                        "INSERT INTO items VALUES (?, ?)",
                        [(1, "a"), (2, "b" if index < 2 else "changed")],
                    )
                    if index == 1:
                        connection.execute("PRAGMA user_version = 7")
                    connection.commit()

            self.assertEqual(
                sqlite_logical_hash(paths[0]), sqlite_logical_hash(paths[1])
            )
            self.assertNotEqual(
                sqlite_logical_hash(paths[0]), sqlite_logical_hash(paths[2])
            )

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
            current["checks"]["bird_sql_dev_20251106"]["gold_execution"]["requested"]
        )
        self.assertEqual(
            current["checks"]["bird_sql_dev_20251106"]["status"],
            "conditional",
        )

        changed = manifest("changed", False)
        self.assertFalse(preserve_completed_gold_sweep(changed, previous))

    def test_private_mini_interact_merge_preserves_public_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_file = root / "public.jsonl"
            private_file = root / "private.jsonl"
            merged_file = root / "merged.jsonl"
            public_rows = [
                {
                    "instance_id": "one",
                    "amb_user_query": "Show the records",
                    "sol_sql": [],
                    "external_knowledge": [],
                    "test_cases": [],
                },
                {
                    "instance_id": "two",
                    "amb_user_query": "Count the records",
                    "sol_sql": [],
                    "external_knowledge": [],
                    "test_cases": [],
                },
            ]
            private_rows = [
                {
                    "instance_id": "one",
                    "sol_sql": ["SELECT * FROM records"],
                    "external_knowledge": ["records means every row"],
                    "test_cases": [],
                },
                {
                    "instance_id": "two",
                    "sol_sql": ["SELECT COUNT(*) FROM records"],
                    "external_knowledge": ["count means row count"],
                    "test_cases": ["custom"],
                },
            ]
            merged_rows = [
                {**public_row, **private_row}
                for public_row, private_row in zip(
                    public_rows, private_rows, strict=True
                )
            ]
            for path, rows in (
                (public_file, public_rows),
                (private_file, private_rows),
                (merged_file, merged_rows),
            ):
                path.write_text(
                    "".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8",
                )

            result = check_mini_interact_private(
                public_file,
                private_file,
                merged_file,
                expected_rows=2,
                expected_private_sha256=None,
                expected_merged_sha256=None,
            )

            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["public_field_mismatches"], 0)
            self.assertEqual(
                result["canonical_merge"]["default_execution_fallback_rows"], 1
            )
            self.assertEqual(result["canonical_merge"]["effective_test_case_count"], 2)

    def test_private_mini_interact_merge_rejects_public_field_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_file = root / "public.jsonl"
            private_file = root / "private.jsonl"
            merged_file = root / "merged.jsonl"
            public_file.write_text(
                json.dumps({"instance_id": "one", "amb_user_query": "original"}) + "\n",
                encoding="utf-8",
            )
            private_file.write_text(
                json.dumps(
                    {
                        "instance_id": "one",
                        "sol_sql": ["SELECT 1"],
                        "external_knowledge": ["knowledge"],
                        "test_cases": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            merged_file.write_text(
                json.dumps(
                    {
                        "instance_id": "one",
                        "amb_user_query": "changed",
                        "sol_sql": ["SELECT 1"],
                        "external_knowledge": ["knowledge"],
                        "test_cases": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaises(RuntimeError):
                check_mini_interact_private(
                    public_file,
                    private_file,
                    merged_file,
                    expected_rows=1,
                    expected_private_sha256=None,
                    expected_merged_sha256=None,
                )

    def test_effective_test_case_count_includes_official_fallback(self) -> None:
        self.assertEqual(effective_mini_interact_test_case_count({"test_cases": []}), 1)
        self.assertEqual(
            effective_mini_interact_test_case_count({"test_cases": ["a", "b"]}),
            2,
        )


if __name__ == "__main__":
    unittest.main()
