from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from interaction.openai_structured import (
    OpenAIStructuredInvoker,
    SpendLimitExceeded,
    SpendTracker,
)


class _Usage:
    def model_dump(self):
        return {
            "input_tokens": 1_000,
            "input_tokens_details": {"cached_tokens": 200},
            "output_tokens": 100,
            "output_tokens_details": {"reasoning_tokens": 80},
            "total_tokens": 1_100,
        }


class _Response:
    id = "resp_test"
    model = "gpt-5.6-luna"
    status = "completed"
    output_text = json.dumps({"sql": "SELECT 1"})
    usage = _Usage()

    def model_dump(self, mode="json"):
        return {
            "id": self.id,
            "model": self.model,
            "status": self.status,
            "output_text": self.output_text,
        }


class OpenAIStructuredInvokerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.instructions = self.root / "instructions.md"
        self.instructions.write_text("Return only JSON.", encoding="utf-8")
        self.client = SimpleNamespace(
            responses=SimpleNamespace(create=Mock(return_value=_Response()))
        )
        self.tracker = SpendTracker(max_usd=1.0)
        self.invoker = OpenAIStructuredInvoker(
            api_key="test-key",
            model="gpt-5.6-luna",
            reasoning_effort="high",
            instructions_file=self.instructions,
            spend_tracker=self.tracker,
            client=self.client,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_strict_schema_request_and_usage_are_recorded(self) -> None:
        schema = {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
            "additionalProperties": False,
        }
        result = self.invoker.run(
            "Write SQL",
            output_schema=schema,
            artifact_directory=self.root / "artifacts",
            case_id="case-1",
        )
        self.assertEqual(result.output, {"sql": "SELECT 1"})
        self.assertEqual(result.served_model, "gpt-5.6-luna")
        self.assertEqual(result.transport_attempts, 1)
        self.assertEqual(result.usage.total_tokens, 1_100)
        self.assertGreater(result.usage.estimated_cost_usd, 0)
        self.assertEqual(self.tracker.spent_usd, result.usage.estimated_cost_usd)
        request = self.client.responses.create.call_args.kwargs
        self.assertEqual(request["reasoning"], {"effort": "high"})
        self.assertEqual(request["text"]["verbosity"], "low")
        self.assertEqual(request["text"]["format"]["schema"], schema)
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])
        self.assertEqual(request["service_tier"], "default")
        self.assertTrue(
            list((self.root / "artifacts").glob("*.response.json"))
        )

    def test_spend_tracker_refuses_before_crossing_limit(self) -> None:
        tracker = SpendTracker(max_usd=0.1, reservation_usd=0.1)
        tracker.reserve()
        with self.assertRaises(SpendLimitExceeded):
            tracker.reserve()
        tracker.settle(0.02)
        with self.assertRaises(SpendLimitExceeded):
            tracker.reserve()

    def test_spend_journal_survives_restart(self) -> None:
        journal = self.root / "spend.jsonl"
        first = SpendTracker(max_usd=1.0, journal_path=journal)
        first.reserve()
        first.settle(0.12)
        second = SpendTracker(max_usd=1.0, journal_path=journal)
        self.assertEqual(second.spent_usd, 0.12)
        self.assertEqual(len(journal.read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
