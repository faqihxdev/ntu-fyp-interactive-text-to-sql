from __future__ import annotations

import unittest

from interaction.codex_official_policy import (
    build_official_policy_prompt,
    official_output_schema,
)
from interaction.official_protocol import OfficialDecisionRequest
from interaction.trajectory import BenchmarkTask


class CodexOfficialPolicyTest(unittest.TestCase):
    def request(
        self,
        *,
        policy="mini_react_port",
        phase="mini_react",
        allowed=("get_schema", "submit"),
    ):
        return OfficialDecisionRequest(
            run_id="hidden-run-id",
            task=BenchmarkTask(
                question_id="demo_1",
                db_id="demo",
                question="Return the intended value.",
                evidence="hidden from official initial context",
                difficulty="interactive",
            ),
            policy=policy,
            phase=phase,
            history=(),
            allowed_actions=allowed,
            total_budget=12,
            remaining_budget=12,
            force_submit=False,
        )

    def test_schema_exposes_only_allowed_official_actions(self) -> None:
        schema = official_output_schema(("ask",))

        self.assertEqual(schema["properties"]["kind"]["enum"], ["ask"])
        self.assertIn("review_source", schema["required"])
        self.assertIn("reviews", schema["required"])
        self.assertIn("options", schema["required"])
        self.assertFalse(schema["additionalProperties"])

    def test_prompt_contains_costs_and_not_gold_or_evidence(self) -> None:
        prompt = build_official_policy_prompt(self.request())

        self.assertIn("get_schema (cost 1)", prompt)
        self.assertIn("submit (cost 3)", prompt)
        self.assertIn("Return the intended value.", prompt)
        self.assertNotIn("hidden-run-id", prompt)
        self.assertNotIn("hidden from official initial context", prompt)
        self.assertIn("A TOY Example Interaction", prompt)
        self.assertIn("declared structured-output adaptation", prompt)

    def test_sphinteract_prompt_states_reactive_sequence(self) -> None:
        prompt = build_official_policy_prompt(
            self.request(
                policy="sphinteract_cq",
                phase="reactive_sra",
                allowed=("ask", "no_ambiguity"),
            )
        )

        self.assertIn("reactive Summarize-Review-Ask", prompt)
        self.assertIn("exactly three closed options", prompt)
        self.assertIn("no_ambiguity (internal; 0 bird coins)", prompt)

    def test_reference_question_does_not_claim_sra(self) -> None:
        prompt = build_official_policy_prompt(
            self.request(policy="mini_react_port", allowed=("ask",))
        )

        self.assertNotIn("Summarize-Review-Ask", prompt)


if __name__ == "__main__":
    unittest.main()
