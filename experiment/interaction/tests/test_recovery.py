from __future__ import annotations

import unittest

from interaction.recovery import (
    FeedbackRecovery,
    RecoveryActionView,
)


class FeedbackRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = FeedbackRecovery("grounded")
        self.history = (
            {
                "kind": "user_clarification",
                "user_turn": 1,
                "clarification_target": "truly different funds",
                "question": "What do you mean by truly different funds?",
                "answer": "Use the domain-specific differentiator rule.",
            },
            {
                "kind": "submission_rejected",
                "sql": "SELECT COUNT(*) FROM funds",
                "change_summary": "Applied the supplied fund definition.",
            },
        )

    def test_snapshot_compiles_binding_requirements(self) -> None:
        snapshot = self.protocol.snapshot(self.history)

        self.assertEqual(snapshot.required_user_turns, (1,))
        self.assertEqual(snapshot.asked_targets, ("truly different funds",))
        self.assertEqual(snapshot.rejected_attempts, 1)

    def test_submission_requires_all_turns_a_change_and_new_sql(self) -> None:
        snapshot = self.protocol.snapshot(self.history)
        assessment = self.protocol.assess(
            RecoveryActionView(
                kind="submit_sql",
                sql="SELECT COUNT(*) FROM funds",
                clarification_target=None,
                grounded_user_turns=(),
                change_summary=None,
                request_contract=None,
            ),
            snapshot,
            rejected_sql={"select count(*) from funds"},
        )

        self.assertFalse(assessment.valid)
        self.assertEqual(len(assessment.violations), 4)

    def test_definition_question_names_its_target(self) -> None:
        snapshot = self.protocol.snapshot(())
        assessment = self.protocol.assess(
            RecoveryActionView(
                kind="ask_user",
                sql=None,
                clarification_target="truly different funds",
                grounded_user_turns=(),
                change_summary=None,
                request_contract=(),
            ),
            snapshot,
            rejected_sql=set(),
        )

        self.assertTrue(assessment.valid)

    def test_request_contract_cannot_drop_prior_elements(self) -> None:
        snapshot = self.protocol.snapshot(
            (
                {
                    "kind": "database_probe",
                    "request_contract": [
                        "output: customer ID",
                        "ratio: mortgage divided by property value",
                    ],
                },
            )
        )
        assessment = self.protocol.assess(
            RecoveryActionView(
                kind="submit_sql",
                sql="SELECT customer_id FROM records",
                clarification_target=None,
                grounded_user_turns=(),
                change_summary="Submit the requested customer output.",
                request_contract=("output: customer ID",),
            ),
            snapshot,
            rejected_sql=set(),
        )

        self.assertFalse(assessment.valid)
        self.assertIn("must not drop", " ".join(assessment.violations))


if __name__ == "__main__":
    unittest.main()
