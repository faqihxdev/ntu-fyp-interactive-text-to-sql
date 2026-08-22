from __future__ import annotations

import unittest

from interaction.metrics import summarize_trajectories, trajectory_record
from interaction.trajectory import BenchmarkTask, Trajectory, Usage


def make_trajectory(question_id: int, success_turn: int | None) -> Trajectory:
    trajectory = Trajectory.start(
        run_id=f"run-{question_id}",
        policy="test",
        task=BenchmarkTask(question_id, "demo", "Question", None, "test"),
        config={"max_user_turns": 2},
    )
    for turn in range(success_turn or 0):
        trajectory.add_event(
            kind="user_clarification",
            actor="user_oracle",
            turn_index=turn + 1,
            elapsed_ms=1,
        )
    if success_turn is not None:
        trajectory.add_event(
            kind="submission_judgement",
            actor="user_oracle",
            turn_index=success_turn,
            elapsed_ms=2,
            payload={"accepted": True},
        )
    trajectory.finish(termination="done", total_elapsed_ms=3, final_sql="SELECT 1")
    return trajectory


class MetricsTest(unittest.TestCase):
    def test_ex_turn_curve_and_normalized_trapezoidal_auc(self) -> None:
        result = summarize_trajectories(
            [make_trajectory(1, 0), make_trajectory(2, 2), make_trajectory(3, None)],
            max_user_turns=2,
        )

        summary = result["summary"]
        self.assertEqual(
            [point["correct"] for point in summary["ex_by_user_turn"]],
            [1, 1, 2],
        )
        self.assertAlmostEqual(summary["normalized_ex_user_turn_auc"], 5 / 12, places=6)
        self.assertEqual(summary["final_execution_accuracy_percent"], 66.6667)

    def test_external_verdict_scores_no_user_at_turn_zero(self) -> None:
        trajectory = make_trajectory(7, None)
        record = trajectory_record(trajectory, externally_correct=True)

        self.assertTrue(record["correct"])
        self.assertEqual(record["success_turn"], 0)
        self.assertEqual(record["success_submission_attempt"], 1)

    def test_separates_policy_and_simulator_calls_and_ask_answerability(self) -> None:
        trajectory = Trajectory.start(
            run_id="run-mini",
            policy="test",
            task=BenchmarkTask("mini-1", "demo", "Question", None, "interactive"),
            config={"max_user_turns": 2},
        )
        trajectory.add_event(
            kind="policy_decision",
            actor="model",
            turn_index=0,
            elapsed_ms=1,
            payload={
                "action": {
                    "kind": "ask_user",
                    "review_source": "AmbQuestion",
                    "summary": "The intended definition is unclear.",
                }
            },
            usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12),
        )
        trajectory.add_event(
            kind="user_clarification",
            actor="user_oracle",
            turn_index=1,
            elapsed_ms=2,
            payload={
                "clarification_target": "cancelled rows",
                "simulator": {"encoder_action": "labeled", "calls": 2},
            },
            usage=Usage(input_tokens=20, output_tokens=3, total_tokens=23),
        )
        trajectory.add_event(
            kind="policy_decision",
            actor="model",
            turn_index=1,
            elapsed_ms=3,
            payload={
                "action": {
                    "kind": "submit_sql",
                    "grounded_user_turns": [1],
                }
            },
        )
        trajectory.add_event(
            kind="candidate_exposed",
            actor="system",
            turn_index=1,
            elapsed_ms=4,
        )
        trajectory.add_event(
            kind="submission_judgement",
            actor="user_oracle",
            turn_index=1,
            elapsed_ms=5,
            payload={"accepted": True},
        )
        trajectory.finish(
            termination="accepted", total_elapsed_ms=6, final_sql="SELECT 1"
        )

        result = summarize_trajectories([trajectory], max_user_turns=2)
        record = result["records"][0]
        summary = result["summary"]

        self.assertEqual(record["policy_model_calls"], 2)
        self.assertEqual(record["simulator_model_calls"], 2)
        self.assertEqual(record["total_inference_calls"], 4)
        self.assertEqual(record["success_submission_attempt"], 1)
        self.assertEqual(summary["clarification_answerability"]["labeled"], 1)
        self.assertEqual(summary["sra_review_sources"]["AmbQuestion"], 1)
        self.assertEqual(summary["feedback_grounding"]["targeted_clarifications"], 1)
        self.assertEqual(summary["feedback_grounding"]["compliance_rate"], 1.0)
        self.assertEqual(
            summary["clarification_answerability"]["answerable_precision"], 1.0
        )

    def test_official_reference_question_is_not_counted_as_sra(self) -> None:
        trajectory = Trajectory.start(
            run_id="run-reference",
            policy="mini-interact-stress-adaptation-v2:mini_react_port",
            task=BenchmarkTask("mini-2", "demo", "Question", None, "interactive"),
            config={"max_user_turns": 4},
        )
        trajectory.add_event(
            kind="policy_decision",
            actor="model",
            turn_index=0,
            elapsed_ms=1,
            payload={
                "phase": "mini_react",
                "action": {"kind": "ask", "review_source": "AmbValue"},
            },
        )
        trajectory.add_event(
            kind="policy_decision",
            actor="model",
            turn_index=1,
            elapsed_ms=1,
            payload={
                "phase": "reactive_sra",
                "action": {"kind": "ask", "review_source": "AmbColumn"},
            },
        )
        trajectory.finish(termination="done", total_elapsed_ms=2)

        summary = summarize_trajectories([trajectory], max_user_turns=4)["summary"]

        self.assertEqual(summary["sra_review_sources"]["AmbValue"], 0)
        self.assertEqual(summary["sra_review_sources"]["AmbColumn"], 1)


if __name__ == "__main__":
    unittest.main()
