from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from interaction.analyze_standardized_results import (
    _clopper_pearson,
    _four_arm_overlap,
    _load_raw_trajectory_audit,
    _sensitivity,
)


class StandardizedResultAuditTest(unittest.TestCase):
    def test_exact_discordant_interval_contains_observed_share(self) -> None:
        lower, upper = _clopper_pearson(19, 27)

        self.assertLess(lower, 19 / 27)
        self.assertGreater(upper, 19 / 27)
        self.assertGreater(lower, 0.45)
        self.assertLess(upper, 0.9)

    def test_raw_audit_separates_normalized_and_unrepaired_sra(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hybrid.jsonl"
            rows = [
                {
                    "termination": "accepted_submission",
                    "events": [
                        {
                            "kind": "policy_decision",
                            "payload": {
                                "phase": "proactive_sra",
                                "action": {
                                    "kind": "ask",
                                    "question": "Choose one",
                                    "options": ["A", "B", "C"],
                                },
                            },
                        },
                        {
                            "kind": "sra_normalization",
                            "payload": {
                                "changes": [
                                    "defaulted_summary",
                                    "defaulted_reviews:AmbQuestion,AmbColumn,AmbOutput,AmbValue",
                                    "appended_open_option",
                                ]
                            },
                        },
                        {"kind": "user_clarification", "payload": {}},
                        {
                            "kind": "candidate_exposed",
                            "payload": {"sql": "SELECT 1"},
                        },
                        {
                            "kind": "submission_judgement",
                            "payload": {"accepted": False},
                        },
                        {
                            "kind": "candidate_exposed",
                            "payload": {"sql": "SELECT 1"},
                        },
                        {
                            "kind": "submission_judgement",
                            "payload": {"accepted": True},
                        },
                    ],
                },
                {
                    "termination": "forced_submission_rejected",
                    "events": [
                        {
                            "kind": "policy_decision",
                            "payload": {
                                "phase": "reactive_sra",
                                "action": {
                                    "kind": "ask",
                                    "question": "Choose one",
                                    "summary": "Summary",
                                    "reviews": {
                                        "AmbQuestion": "x",
                                        "AmbColumn": "x",
                                        "AmbOutput": "x",
                                        "AmbValue": "x",
                                    },
                                    "options": ["A", "A", "C", "Other"],
                                },
                            },
                        },
                        {
                            "kind": "model_protocol_error",
                            "payload": {
                                "error": "SRA answer options must be distinct"
                            },
                        },
                        {
                            "kind": "sql_execution",
                            "payload": {"observation": {"status": "ok"}},
                        },
                    ],
                },
            ]
            path.write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            audit = _load_raw_trajectory_audit(path)

        self.assertEqual(audit["trajectories"], 2)
        self.assertEqual(audit["successes"], 1)
        self.assertEqual(audit["sra"]["raw_decisions"], 2)
        self.assertEqual(audit["sra"]["raw_compliant"], 0)
        self.assertEqual(audit["sra"]["post_normalization_compliant"], 1)
        self.assertEqual(audit["sra"]["normalization_events"], 1)
        self.assertEqual(
            audit["protocol_error_categories"]["malformed_sra_action"], 1
        )
        self.assertEqual(
            audit["rejected_sql_repeat_observation"]["exact_repeat_events"], 1
        )
        self.assertEqual(
            audit["rejected_sql_repeat_observation"][
                "trajectories_with_exact_repeat"
            ],
            1,
        )
        self.assertEqual(
            audit["forced_rejected_endpoint_execution"]["trajectories"], 1
        )
        self.assertEqual(
            audit["forced_rejected_endpoint_execution"]["executable"], 1
        )
        self.assertEqual(
            audit["forced_rejected_endpoint_execution"]["status_counts"],
            {"ok": 1},
        )

    def test_overlap_exports_every_cell_without_ids(self) -> None:
        outcomes = {
            "mini_react_port": [True, True, False],
            "sphinteract_cq": [True, False, False],
            "proactive": [False, False, False],
            "hybrid": [True, False, False],
        }
        arms = {
            arm: {
                "records": {
                    str(index): {"correct": value}
                    for index, value in enumerate(values)
                }
            }
            for arm, values in outcomes.items()
        }

        overlap = _four_arm_overlap(arms)

        self.assertEqual(overlap["tasks"], 3)
        self.assertEqual(overlap["cells"]["1101"], 1)
        self.assertEqual(overlap["cells"]["1000"], 1)
        self.assertEqual(overlap["cells"]["0000"], 1)
        self.assertEqual(len(overlap["cells"]), 16)

    def test_exclusion_sensitivity_recomputes_costs_calls_and_auc(self) -> None:
        arms = {}
        for arm in (
            "mini_react_port",
            "sphinteract_cq",
            "proactive",
            "hybrid",
        ):
            arms[arm] = {
                "records": {
                    "keep": {
                        "correct": True,
                        "success_turn": 1,
                        "user_turns": 1,
                        "bird_coins": 2,
                        "submissions": 1,
                        "policy_model_calls": 3,
                        "simulator_model_calls": 1,
                        "total_tokens": 10,
                    },
                    "drop": {
                        "correct": False,
                        "success_turn": None,
                        "user_turns": 4,
                        "bird_coins": 20,
                        "submissions": 5,
                        "policy_model_calls": 9,
                        "simulator_model_calls": 4,
                        "total_tokens": 100,
                    },
                }
            }

        sensitivity = _sensitivity(arms, {"drop"})

        assert sensitivity is not None
        values = sensitivity["all_reported_arm_metrics"]["hybrid"]
        self.assertEqual(values["mean_bird_coins"], 2)
        self.assertEqual(values["mean_total_inference_calls"], 4)
        self.assertEqual(values["normalized_ex_user_turn_auc"], 0.875)


if __name__ == "__main__":
    unittest.main()
