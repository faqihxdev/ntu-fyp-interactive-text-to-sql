from __future__ import annotations

import unittest

from interaction.official_protocol import PROTOCOL_VERSION
from interaction.run_official_mini_interact import validate_existing_trajectories
from interaction.trajectory import BenchmarkTask, Trajectory


def _trajectory() -> Trajectory:
    return Trajectory.start(
        run_id="run",
        policy=f"{PROTOCOL_VERSION}:mini_react_port",
        task=BenchmarkTask(
            question_id="demo",
            db_id="db",
            question="Question",
            evidence=None,
            difficulty="interactive",
        ),
        config={
            "requested_model": "codex_subscription_proxy:gpt-5.6-luna",
            "reasoning_effort": "high",
            "user_patience_budget": 6.0,
            "max_action_turns": 60,
            "progress_guard": False,
            "guard_fingerprint": "bird_set",
            "max_guard_retries": 1,
        },
    )


class OfficialMiniInteractResumeValidationTest(unittest.TestCase):
    def test_matching_existing_trajectory_is_resumable(self) -> None:
        validate_existing_trajectories(
            [_trajectory()],
            selected_ids={"demo"},
            policy="mini_react_port",
            model="gpt-5.6-luna",
            reasoning_effort="high",
            user_patience_budget=6.0,
            max_action_turns=60,
            progress_guard=False,
            guard_fingerprint="bird_set",
            max_guard_retries=1,
        )

    def test_changed_model_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "requested_model"):
            validate_existing_trajectories(
                [_trajectory()],
                selected_ids={"demo"},
                policy="mini_react_port",
                model="different-model",
                reasoning_effort="high",
                user_patience_budget=6.0,
                max_action_turns=60,
                progress_guard=False,
                guard_fingerprint="bird_set",
                max_guard_retries=1,
            )


if __name__ == "__main__":
    unittest.main()
