from __future__ import annotations

import sys
import unittest
from pathlib import Path

from readiness.mini_interact_eval import (
    build_single_instance_command,
    effective_result,
    select_rows,
)


class MiniInteractEvaluatorAdapterTest(unittest.TestCase):
    def test_child_command_uses_current_interpreter_and_absolute_script(self) -> None:
        command = build_single_instance_command(
            Path("relative/single_instance_eval_sqlite.py"),
            Path("input.jsonl"),
            Path("output.json"),
            mode="gold",
            log_file=Path("instance.log"),
        )

        self.assertEqual(command[0], sys.executable)
        self.assertTrue(Path(command[1]).is_absolute())

    def test_default_fallback_is_counted_without_hiding_upstream_value(self) -> None:
        normalized = effective_result(
            {"status": "success", "total_test_cases": 0, "passed_test_cases": 1},
            {"test_cases": []},
        )

        self.assertEqual(normalized["upstream_reported_total_test_cases"], 0)
        self.assertEqual(normalized["effective_total_test_cases"], 1)
        self.assertEqual(normalized["passed_test_cases"], 1)

    def test_selection_must_be_explicit(self) -> None:
        rows = [
            {"instance_id": "one"},
            {"instance_id": "two"},
            {"instance_id": "three"},
        ]

        with self.assertRaises(ValueError):
            select_rows(rows, instance_ids=[], skip=0, limit=None, run_all=False)
        self.assertEqual(
            select_rows(
                rows,
                instance_ids=["three", "one"],
                skip=0,
                limit=None,
                run_all=False,
            ),
            [rows[2], rows[0]],
        )


if __name__ == "__main__":
    unittest.main()
