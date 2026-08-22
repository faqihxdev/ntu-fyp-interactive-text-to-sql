from __future__ import annotations

import unittest

from interaction.compare_policy_metrics import compare, per_task_auc


def payload(records: list[dict], max_turns: int = 2) -> dict:
    return {
        "summary": {
            "ex_by_user_turn": [
                {"user_turn": turn, "correct": 0} for turn in range(max_turns + 1)
            ]
        },
        "records": records,
    }


class ComparePolicyMetricsTest(unittest.TestCase):
    def test_per_task_auc_uses_cumulative_trapezoid(self) -> None:
        self.assertEqual(per_task_auc({"success_turn": 0}, 2), 1.0)
        self.assertEqual(per_task_auc({"success_turn": 1}, 2), 0.75)
        self.assertEqual(per_task_auc({"success_turn": 2}, 2), 0.25)
        self.assertEqual(per_task_auc({"success_turn": None}, 2), 0.0)

    def test_paired_comparison_reports_accuracy_and_repeat_change(self) -> None:
        baseline = payload(
            [
                {
                    "question_id": "a",
                    "correct": True,
                    "success_turn": 1,
                    "repeated_rejected_results": 2,
                },
                {
                    "question_id": "b",
                    "correct": False,
                    "success_turn": None,
                    "repeated_rejected_results": 2,
                },
            ]
        )
        candidate = payload(
            [
                {
                    "question_id": "a",
                    "correct": True,
                    "success_turn": 1,
                    "repeated_rejected_results": 1,
                },
                {
                    "question_id": "b",
                    "correct": True,
                    "success_turn": 2,
                    "repeated_rejected_results": 1,
                },
            ]
        )

        result = compare(baseline, candidate, bootstrap_samples=100, seed=7)

        self.assertEqual(
            result["comparisons"]["execution_accuracy"]["paired_difference"],
            0.5,
        )
        self.assertEqual(result["discordant_correctness"]["candidate_only_correct"], 1)
        self.assertEqual(result["predeclared_checks"]["repeat_reduction_relative"], 0.5)

    def test_task_mismatch_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "identical task IDs"):
            compare(
                payload([{"question_id": "a", "correct": False}]),
                payload([{"question_id": "b", "correct": False}]),
                bootstrap_samples=10,
            )


if __name__ == "__main__":
    unittest.main()
