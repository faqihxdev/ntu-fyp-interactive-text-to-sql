from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from interaction.official_comparison import (
    COMPARISON_POLICIES,
    analyze_comparison,
    balanced_launch_order,
    balanced_shards,
    exact_mcnemar_two_sided,
    merge_policy_outputs,
)
from interaction.official_protocol import PROTOCOL_VERSION
from interaction.trajectory import BenchmarkTask, Trajectory, stable_json


def trajectory(question_id: str, policy: str) -> Trajectory:
    result = Trajectory.start(
        run_id=f"{policy}:{question_id}",
        policy=f"{PROTOCOL_VERSION}:{policy}",
        task=BenchmarkTask(question_id, "db", "Question", None, "interactive"),
        config={},
    )
    result.finish(termination="done", total_elapsed_ms=1, final_sql="SELECT 1")
    return result


def metrics(path: Path, policy: str, successes: dict[str, bool]) -> None:
    official_records = []
    secondary_records = []
    for index, (question_id, success) in enumerate(successes.items()):
        official_records.append(
            {
                "question_id": question_id,
                "success": success,
                "bird_coins": 10 + index,
                "user_questions": 1,
                "submissions": 2,
                "total_tokens": 100,
                "termination": "accepted" if success else "budget_exhausted",
            }
        )
        secondary_records.append(
            {
                "question_id": question_id,
                "success_turn": 1 if success else None,
            }
        )
    path.write_text(
        json.dumps(
            {
                "official_protocol": {"records": official_records},
                "secondary_policy_analysis": {"records": secondary_records},
                "policy": policy,
            }
        ),
        encoding="utf-8",
    )


class OfficialComparisonTest(unittest.TestCase):
    def test_balanced_shards_cover_ids_once(self) -> None:
        ids = [f"id-{index}" for index in range(10)]

        shards = balanced_shards(ids, 2)

        self.assertEqual([len(shard) for shard in shards], [5, 5])
        self.assertEqual(set(shards[0]) | set(shards[1]), set(ids))
        self.assertFalse(set(shards[0]) & set(shards[1]))

    def test_launch_order_rotates_by_shard(self) -> None:
        first = balanced_launch_order(COMPARISON_POLICIES, 0)
        second = balanced_launch_order(COMPARISON_POLICIES, 1)

        self.assertEqual(first[0], "mini_react_port")
        self.assertEqual(second[0], "sphinteract_cq")
        self.assertEqual(set(first), set(second))

    def test_merge_orders_exact_ids_and_rejects_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shard_a = root / "a.jsonl"
            shard_b = root / "b.jsonl"
            shard_a.write_text(
                stable_json(trajectory("b", "hybrid").to_dict()) + "\n",
                encoding="utf-8",
            )
            shard_b.write_text(
                stable_json(trajectory("a", "hybrid").to_dict()) + "\n",
                encoding="utf-8",
            )
            destination = root / "combined.jsonl"

            merged = merge_policy_outputs(
                policy="hybrid",
                shard_outputs=[shard_a, shard_b],
                expected_ids=["a", "b"],
                destination=destination,
            )

            self.assertEqual([item.task.question_id for item in merged], ["a", "b"])
            with self.assertRaisesRegex(ValueError, "missing"):
                merge_policy_outputs(
                    policy="hybrid",
                    shard_outputs=[shard_a],
                    expected_ids=["a", "b"],
                    destination=destination,
                )

    def test_analysis_is_paired_and_reports_predeclared_flags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {}
            outcomes = {
                "mini_react_port": {"a": False, "b": False},
                "sphinteract_cq": {"a": False, "b": True},
                "proactive": {"a": True, "b": False},
                "hybrid": {"a": True, "b": True},
            }
            for policy, policy_outcomes in outcomes.items():
                path = root / f"{policy}.json"
                metrics(path, policy, policy_outcomes)
                paths[policy] = path

            result = analyze_comparison(
                paths,
                expected_ids=["a", "b"],
                seed=7,
                bootstrap_samples=100,
                noninferiority_margin=0.05,
            )

        self.assertEqual(result["tasks"], 2)
        self.assertEqual(result["policy_summaries"]["hybrid"]["successes"], 2)
        comparison = result["paired_comparisons"]["hybrid_minus_mini_react_port"]
        self.assertEqual(comparison["task_success"]["paired_difference"], 1.0)
        self.assertTrue(comparison["predeclared_interpretation"]["success_superiority"])

    def test_exact_mcnemar(self) -> None:
        self.assertEqual(exact_mcnemar_two_sided(0, 0), 1.0)
        self.assertEqual(exact_mcnemar_two_sided(0, 4), 0.125)


if __name__ == "__main__":
    unittest.main()
