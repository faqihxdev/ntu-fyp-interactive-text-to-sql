from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from codex_proxy.runner import (
    audit_events,
    build_codex_command,
    is_retryable_transport_failure,
    resolve_codex_executable,
    served_model_from_events,
    sum_usage,
    usage_from_events,
)


class CodexProxyRunnerTest(unittest.TestCase):
    def test_command_is_ephemeral_schema_constrained_and_tool_disabled(self) -> None:
        command = build_codex_command(
            codex_executable="codex",
            working_directory=Path("workspace"),
            model="gpt-5.6-luna",
            reasoning_effort="max",
            verbosity="low",
            instructions_file=Path("instructions.md"),
            output_schema_file=Path("schema.json"),
            output_file=Path("result.json"),
        )

        self.assertIn("--ephemeral", command)
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--strict-config", command)
        self.assertIn("--output-schema", command)
        self.assertIn("features.shell_tool=false", command)
        self.assertEqual(command[-1], "-")

    def test_windows_cmd_launcher_resolves_to_packaged_native_binary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / "bin" / "codex.cmd"
            native = (
                root
                / "bin"
                / "node_modules"
                / "@openai"
                / "codex"
                / "node_modules"
                / "@openai"
                / "codex-win32-x64"
                / "vendor"
                / "x86_64-pc-windows-msvc"
                / "bin"
                / "codex.exe"
            )
            launcher.parent.mkdir(parents=True)
            launcher.touch()
            native.parent.mkdir(parents=True)
            native.touch()

            resolved = resolve_codex_executable(str(launcher), platform="nt")

            self.assertEqual(Path(resolved), native)

    def test_audit_rejects_any_tool_item(self) -> None:
        clean = (
            {"type": "item.completed", "item": {"type": "reasoning"}},
            {"type": "item.completed", "item": {"type": "agent_message"}},
            {"type": "turn.completed", "usage": {}},
        )
        dirty = clean + (
            {"type": "item.completed", "item": {"type": "command_execution"}},
        )

        self.assertEqual(audit_events(clean)["contaminated_item_types"], [])
        self.assertEqual(
            audit_events(dirty)["contaminated_item_types"], ["command_execution"]
        )

    def test_usage_is_read_from_last_completed_turn(self) -> None:
        usage = usage_from_events(
            (
                {
                    "type": "turn.completed",
                    "usage": {
                        "input_tokens": 100,
                        "cached_input_tokens": 25,
                        "output_tokens": 20,
                        "reasoning_output_tokens": 5,
                    },
                },
            )
        )

        self.assertEqual(usage.input_tokens, 100)
        self.assertEqual(usage.cached_input_tokens, 25)
        self.assertEqual(usage.output_tokens, 20)
        self.assertEqual(usage.reasoning_tokens, 5)
        self.assertEqual(usage.total_tokens, 120)

    def test_retries_network_error_items_but_not_tool_actions(self) -> None:
        network_events = (
            {
                "type": "item.completed",
                "item": {
                    "type": "error",
                    "message": "stream disconnected before completion",
                },
            },
            {"type": "turn.completed", "usage": {}},
        )
        network_audit = audit_events(network_events)
        self.assertTrue(
            is_retryable_transport_failure(
                returncode=0,
                stderr="No such host is known",
                events=network_events,
                audit=network_audit,
            )
        )

        tool_events = network_events + (
            {"type": "item.completed", "item": {"type": "command_execution"}},
        )
        self.assertFalse(
            is_retryable_transport_failure(
                returncode=0,
                stderr="No such host is known",
                events=tool_events,
                audit=audit_events(tool_events),
            )
        )

    def test_retry_usage_is_summed(self) -> None:
        first = usage_from_events(
            (
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 10, "output_tokens": 2},
                },
            )
        )
        second = usage_from_events(
            (
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 20, "output_tokens": 3},
                },
            )
        )

        total = sum_usage([first, second])

        self.assertEqual(total.input_tokens, 30)
        self.assertEqual(total.output_tokens, 5)
        self.assertEqual(total.total_tokens, 35)

    def test_served_model_is_recorded_only_when_explicitly_returned(self) -> None:
        self.assertIsNone(
            served_model_from_events(({"type": "turn.completed", "usage": {}},))
        )
        self.assertEqual(
            served_model_from_events(
                (
                    {"type": "turn.started", "model": "served-snapshot-123"},
                    {"type": "turn.completed", "usage": {}},
                )
            ),
            "served-snapshot-123",
        )


if __name__ == "__main__":
    unittest.main()
