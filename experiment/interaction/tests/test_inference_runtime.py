"""Provider defaults, optional imports and API spending guards."""

import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from interaction import inference_runtime as runtime
from interaction.oracle_types import ClarificationAnswer, SubmissionJudgement


class InferenceRuntimeTest(unittest.TestCase):
    def test_runner_defaults_and_explicit_codex_selection(self):
        runners = {
            "run_bird_dev_clarification_value": [
                "--selection-manifest", "selection.json", "--output-root", "run",
            ],
            "run_bird_dev_matched": [
                "--selection-manifest", "selection.json", "--output-root", "run",
            ],
            "run_official_mini_interact": [],
        }
        for name, arguments in runners.items():
            module = importlib.import_module(f"interaction.{name}")
            for extra, expected in (([], "openai"), (["--provider", "codex"], "codex")):
                with self.subTest(runner=name, provider=expected):
                    with patch.object(sys, "argv", [name, *arguments, *extra]):
                        self.assertEqual(module.parse_args().provider, expected)

    def test_api_runtime_does_not_import_codex_adapter(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
sys.modules['codex_proxy'] = None
sys.modules['codex_proxy.runner'] = None
from interaction import inference_runtime as runtime
with patch.object(runtime, 'load_dotenv'), \\
     patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}, clear=True), \\
     patch.object(runtime, 'OpenAIStructuredInvoker') as invoker:
    created = runtime.create_structured_runtime(
        provider='openai', experiment_root=Path('.'), model='fixture',
        reasoning_effort='high', policy_instructions=Path('policy.md'),
        simulator_instructions=Path('simulator.md'), max_output_tokens=100,
        max_transport_retries=0, max_spend_usd=1,
    )
    assert created.surface == runtime.OPENAI_SURFACE
    assert invoker.call_count == 2
    assert created.spend_tracker.max_usd == 1
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_api_requires_budget_and_key_before_creating_clients(self):
        with tempfile.TemporaryDirectory() as directory:
            arguments = dict(
                provider="openai", experiment_root=Path(directory), model="fixture",
                reasoning_effort="high", policy_instructions=Path("policy.md"),
                simulator_instructions=Path("simulator.md"), max_output_tokens=100,
                max_transport_retries=0,
            )
            with patch.object(runtime, "OpenAIStructuredInvoker") as invoker:
                for budget in (None, 0, -1):
                    with self.subTest(budget=budget):
                        with self.assertRaisesRegex(ValueError, "positive --max-spend-usd"):
                            runtime.create_structured_runtime(**arguments, max_spend_usd=budget)
                with patch.dict(os.environ, {}, clear=True):
                    with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
                        runtime.create_structured_runtime(**arguments, max_spend_usd=1)
                invoker.assert_not_called()

    def test_codex_remains_explicitly_available(self):
        with patch("codex_proxy.runner.CodexSubscriptionProxy") as proxy:
            created = runtime.create_structured_runtime(
                provider="codex", experiment_root=Path("."), model="fixture",
                reasoning_effort="high", policy_instructions=Path("policy.md"),
                simulator_instructions=Path("simulator.md"), max_output_tokens=100,
                max_transport_retries=0, max_spend_usd=None,
            )
        self.assertEqual(created.surface, runtime.CODEX_SURFACE)
        self.assertIsNone(created.spend_tracker)
        self.assertEqual(proxy.call_count, 2)


class OracleTypesTest(unittest.TestCase):
    def test_metadata_is_independent_per_answer_and_judgement(self):
        for factory in (
            lambda: ClarificationAnswer("Answer"),
            lambda: SubmissionJudgement(False, "Rejected"),
        ):
            first, second = factory(), factory()
            first.metadata["test"] = True
            self.assertEqual(second.metadata, {})


if __name__ == "__main__":
    unittest.main()
