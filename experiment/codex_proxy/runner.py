"""Strict one-shot ChatGPT-authenticated Codex subscription proxy.

This is a provisional development surface, not an OpenAI API adapter. Every
call is ephemeral, runs in an empty directory, disables agent tools, requires a
JSON schema, and rejects non-message/reasoning events.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from interaction.trajectory import Usage


class CodexProxyError(RuntimeError):
    pass


@dataclass(frozen=True)
class CodexProxyResult:
    output_text: str
    output: dict[str, Any]
    usage: Usage
    elapsed_ms: float
    events: tuple[dict[str, Any], ...]
    codex_cli_version: str
    transport_attempts: int
    served_model: str | None = None


def _toml_string(value: str) -> str:
    return json.dumps(value)


def build_codex_command(
    *,
    codex_executable: str,
    working_directory: Path,
    model: str,
    reasoning_effort: str,
    verbosity: str,
    instructions_file: Path,
    output_schema_file: Path,
    output_file: Path,
) -> list[str]:
    configs = [
        'forced_login_method="chatgpt"',
        'service_tier="default"',
        f"model_instructions_file={_toml_string(str(instructions_file.resolve()))}",
        f"model_reasoning_effort={_toml_string(reasoning_effort)}",
        f"model_verbosity={_toml_string(verbosity)}",
        'personality="none"',
        'web_search="disabled"',
        "tools.web_search=false",
        "features.shell_tool=false",
        "features.unified_exec=false",
        "features.apps=false",
        "apps._default.enabled=false",
        "features.multi_agent=false",
        "features.hooks=false",
        "features.memories=false",
        "features.goals=false",
        "features.personality=false",
        "features.remote_plugin=false",
        "features.plugins=false",
        "features.tool_search=false",
        "features.tool_suggest=false",
        "skills.include_instructions=false",
        "skills.bundled.enabled=false",
        "include_apps_instructions=false",
        "include_collaboration_mode_instructions=false",
        "include_environment_context=false",
        "include_permissions_instructions=false",
    ]
    command = [
        codex_executable,
        "exec",
        "--cd",
        str(working_directory.resolve()),
        "--skip-git-repo-check",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--sandbox",
        "read-only",
        "--model",
        model,
        "--json",
        "--output-last-message",
        str(output_file.resolve()),
        "--output-schema",
        str(output_schema_file.resolve()),
    ]
    for config in configs:
        command.extend(["-c", config])
    command.append("-")
    return command


def parse_jsonl_events(text: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CodexProxyError(
                f"Invalid Codex JSONL event at line {line_number}: {exc}"
            ) from exc
        if not isinstance(event, dict):
            raise CodexProxyError(f"Codex event {line_number} is not an object")
        events.append(event)
    return tuple(events)


def audit_events(events: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    observed_items: list[str] = []
    contaminated: list[str] = []
    for event in events:
        event_type = str(event.get("type", ""))
        if not event_type.startswith("item."):
            continue
        item = event.get("item") or {}
        item_type = str(item.get("type", "unknown"))
        observed_items.append(item_type)
        if item_type not in {"agent_message", "reasoning"}:
            contaminated.append(item_type)
    turn_completed = any(event.get("type") == "turn.completed" for event in events)
    return {
        "observed_item_types": sorted(set(observed_items)),
        "contaminated_item_types": sorted(set(contaminated)),
        "turn_completed": turn_completed,
    }


_RETRYABLE_TRANSPORT_MARKERS = (
    "connection was forcibly closed",
    "no such host is known",
    "rate limit",
    "rate_limit",
    "service unavailable",
    "stream disconnected",
    "timed out",
    "timeout",
    "temporarily unavailable",
)


def is_retryable_transport_failure(
    *,
    returncode: int,
    stderr: str,
    events: tuple[dict[str, Any], ...],
    audit: dict[str, Any],
) -> bool:
    """Identify transient transport failures without retrying model behavior."""

    event_text = json.dumps(events, ensure_ascii=False).casefold()
    combined = f"{stderr}\n{event_text}".casefold()
    has_marker = any(marker in combined for marker in _RETRYABLE_TRANSPORT_MARKERS)
    only_error_contamination = set(audit["contaminated_item_types"]) <= {"error"}
    return has_marker and (returncode != 0 or only_error_contamination)


def usage_from_events(events: tuple[dict[str, Any], ...]) -> Usage:
    completed = [event for event in events if event.get("type") == "turn.completed"]
    if not completed:
        return Usage()
    raw = completed[-1].get("usage") or {}
    return Usage(
        input_tokens=int(raw.get("input_tokens", 0)),
        cached_input_tokens=int(raw.get("cached_input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        reasoning_tokens=int(raw.get("reasoning_output_tokens", 0)),
        total_tokens=(
            int(raw.get("input_tokens", 0)) + int(raw.get("output_tokens", 0))
        ),
        estimated_cost_usd=0.0,
    )


def served_model_from_events(events: tuple[dict[str, Any], ...]) -> str | None:
    """Extract a returned model identifier if the CLI explicitly emits one."""

    identifiers: list[str] = []
    for event in events:
        containers = [event]
        for key in ("item", "response", "turn"):
            value = event.get(key)
            if isinstance(value, dict):
                containers.append(value)
        for container in containers:
            for key in ("served_model", "model_id", "model_slug", "model"):
                value = container.get(key)
                if isinstance(value, str) and value.strip():
                    identifiers.append(value.strip())
    return identifiers[-1] if identifiers else None


def sum_usage(records: list[Usage]) -> Usage:
    return Usage(
        input_tokens=sum(record.input_tokens for record in records),
        cached_input_tokens=sum(record.cached_input_tokens for record in records),
        cache_write_input_tokens=sum(
            record.cache_write_input_tokens for record in records
        ),
        output_tokens=sum(record.output_tokens for record in records),
        reasoning_tokens=sum(record.reasoning_tokens for record in records),
        total_tokens=sum(record.total_tokens for record in records),
        estimated_cost_usd=round(
            sum(record.estimated_cost_usd for record in records), 8
        ),
    )


class CodexSubscriptionProxy:
    """One-shot, schema-constrained subscription inference adapter."""

    def __init__(
        self,
        *,
        model: str = "gpt-5.6-luna",
        reasoning_effort: str = "max",
        verbosity: str = "low",
        instructions_file: Path,
        timeout_seconds: float = 600.0,
        codex_executable: str | None = None,
        max_transport_retries: int = 1,
    ) -> None:
        executable = codex_executable or shutil.which("codex")
        if not executable:
            raise CodexProxyError("codex executable was not found on PATH")
        self.codex_executable = resolve_codex_executable(executable)
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.instructions_file = instructions_file.resolve()
        self.timeout_seconds = timeout_seconds
        if max_transport_retries < 0:
            raise CodexProxyError("max_transport_retries must be non-negative")
        self.max_transport_retries = max_transport_retries
        if not self.instructions_file.is_file():
            raise CodexProxyError(
                f"Proxy instructions file is missing: {self.instructions_file}"
            )
        version = subprocess.run(
            [self.codex_executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        ).stdout.strip()
        self.codex_cli_version = version.removeprefix("codex-cli ")

    def run(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        artifact_directory: Path | None = None,
        case_id: str = "case",
    ) -> CodexProxyResult:
        if not prompt.strip():
            raise CodexProxyError("Proxy prompt must not be empty")
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="fyp-codex-proxy-") as directory:
            working_directory = Path(directory)
            schema_file = working_directory / "output-schema.json"
            output_file = working_directory / "last-message.json"
            schema_file.write_text(
                json.dumps(output_schema, indent=2) + "\n", encoding="utf-8"
            )
            command = build_codex_command(
                codex_executable=self.codex_executable,
                working_directory=working_directory,
                model=self.model,
                reasoning_effort=self.reasoning_effort,
                verbosity=self.verbosity,
                instructions_file=self.instructions_file,
                output_schema_file=schema_file,
                output_file=output_file,
            )
            environment = os.environ.copy()
            environment.pop("OPENAI_API_KEY", None)
            environment.pop("CODEX_API_KEY", None)
            safe_id = "".join(
                character if character.isalnum() or character in "-_" else "_"
                for character in case_id
            )
            attempts = 0
            attempt_usages: list[Usage] = []
            while True:
                attempts += 1
                output_file.unlink(missing_ok=True)
                try:
                    completed = subprocess.run(
                        command,
                        input=prompt,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        env=environment,
                        timeout=self.timeout_seconds,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    attempt_id = f"{safe_id}-transport-attempt-{attempts}"
                    if artifact_directory is not None:
                        artifact_directory.mkdir(parents=True, exist_ok=True)
                        (artifact_directory / f"{attempt_id}.prompt.txt").write_text(
                            prompt, encoding="utf-8"
                        )
                        (artifact_directory / f"{attempt_id}.timeout.txt").write_text(
                            f"Timed out after {self.timeout_seconds} seconds.\n",
                            encoding="utf-8",
                        )
                    if attempts <= self.max_transport_retries:
                        time.sleep(min(2 ** (attempts - 1), 4))
                        continue
                    raise CodexProxyError(
                        "codex exec timed out after "
                        f"{self.timeout_seconds} seconds on {attempts} attempts"
                    ) from exc
                events = parse_jsonl_events(completed.stdout)
                attempt_usages.append(usage_from_events(events))
                audit = audit_events(events)
                attempt_id = f"{safe_id}-transport-attempt-{attempts}"
                if artifact_directory is not None:
                    artifact_directory.mkdir(parents=True, exist_ok=True)
                    (artifact_directory / f"{attempt_id}.prompt.txt").write_text(
                        prompt, encoding="utf-8"
                    )
                    (artifact_directory / f"{attempt_id}.events.jsonl").write_text(
                        completed.stdout, encoding="utf-8"
                    )
                    (artifact_directory / f"{attempt_id}.stderr.txt").write_text(
                        completed.stderr, encoding="utf-8"
                    )
                    (
                        artifact_directory / f"{attempt_id}.output-schema.json"
                    ).write_text(
                        json.dumps(output_schema, indent=2) + "\n", encoding="utf-8"
                    )
                    (artifact_directory / f"{attempt_id}.exit-code.txt").write_text(
                        f"{completed.returncode}\n", encoding="utf-8"
                    )
                    if output_file.is_file():
                        (artifact_directory / f"{attempt_id}.result.json").write_text(
                            output_file.read_text(encoding="utf-8"), encoding="utf-8"
                        )
                retryable = is_retryable_transport_failure(
                    returncode=completed.returncode,
                    stderr=completed.stderr,
                    events=events,
                    audit=audit,
                )
                if retryable and attempts <= self.max_transport_retries:
                    time.sleep(min(2 ** (attempts - 1), 4))
                    continue
                break
            if completed.returncode != 0:
                errors = [
                    event
                    for event in events
                    if event.get("type") in {"error", "turn.failed"}
                ]
                event_detail = (
                    json.dumps(errors[-3:], ensure_ascii=False) if errors else "none"
                )
                raise CodexProxyError(
                    f"codex exec exited {completed.returncode}; "
                    f"error events={event_detail}; stderr={completed.stderr[-1000:]}"
                )
            if audit["contaminated_item_types"]:
                raise CodexProxyError(
                    "Codex proxy attempted disallowed actions: "
                    f"{audit['contaminated_item_types']}"
                )
            if not audit["turn_completed"]:
                raise CodexProxyError("Codex proxy produced no turn.completed event")
            if (
                not output_file.is_file()
                or not output_file.read_text(encoding="utf-8").strip()
            ):
                raise CodexProxyError("Codex proxy final output is missing or empty")
            output_text = output_file.read_text(encoding="utf-8").strip()
            try:
                output = json.loads(output_text)
            except json.JSONDecodeError as exc:
                raise CodexProxyError(
                    f"Codex proxy output is not valid JSON: {exc}"
                ) from exc
            if not isinstance(output, dict):
                raise CodexProxyError("Codex proxy output is not a JSON object")

        return CodexProxyResult(
            output_text=output_text,
            output=output,
            usage=sum_usage(attempt_usages),
            elapsed_ms=round((time.monotonic() - started) * 1_000, 3),
            events=events,
            codex_cli_version=self.codex_cli_version,
            transport_attempts=attempts,
            served_model=served_model_from_events(events),
        )


def resolve_codex_executable(
    executable: str,
    *,
    platform: str | None = None,
) -> str:
    """Prefer the native Codex binary over the Windows CMD/Node launcher.

    Python can time out and kill a CMD launcher while its descendants retain
    captured stdout/stderr handles, causing ``subprocess.run`` to wait forever.
    The npm package ships the same-version native binary beside the launcher;
    invoking it directly makes the timeout apply to the actual process.
    """

    platform = platform or os.name
    path = Path(executable)
    if platform != "nt" or path.suffix.casefold() not in {".cmd", ".bat"}:
        return executable
    package_root = path.parent / "node_modules" / "@openai" / "codex"
    candidates = sorted(
        package_root.glob("node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe")
    )
    return str(candidates[0]) if candidates else executable
