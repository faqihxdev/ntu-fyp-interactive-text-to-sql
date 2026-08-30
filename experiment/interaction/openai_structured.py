"""Direct OpenAI Responses adapter for schema-constrained experiment calls.

The adapter intentionally matches the small ``StructuredInvoker`` seam used by
the BIRD and Mini-Interact controllers.  It adds bounded transport retries,
thread-safe spend accounting, and private per-call artifacts without exposing
the API key to prompts, logs, or manifests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import random
import threading
import time
from pathlib import Path
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)

from .model import _usage_from_response
from .trajectory import Usage


class OpenAIStructuredError(RuntimeError):
    """Raised when a direct API call cannot produce the required JSON object."""


class SpendLimitExceeded(OpenAIStructuredError):
    """Raised before a request when the configured spend ceiling is exhausted."""


@dataclass(frozen=True)
class OpenAIStructuredResult:
    output_text: str
    output: dict[str, Any]
    usage: Usage
    elapsed_ms: float
    events: tuple[dict[str, Any], ...]
    transport_attempts: int
    served_model: str | None


class SpendTracker:
    """Thread-safe, conservative API spend guard shared by all invokers."""

    def __init__(
        self,
        *,
        max_usd: float,
        reservation_usd: float = 0.05,
        journal_path: Path | None = None,
    ) -> None:
        if max_usd <= 0:
            raise ValueError("max_usd must be positive")
        if reservation_usd <= 0 or reservation_usd > max_usd:
            raise ValueError("reservation_usd must be positive and within max_usd")
        self.max_usd = float(max_usd)
        self.reservation_usd = float(reservation_usd)
        self.journal_path = journal_path.resolve() if journal_path else None
        self._spent_usd = self._load_journal_total()
        self._reserved_usd = 0.0
        self._lock = threading.Lock()

    def _load_journal_total(self) -> float:
        if self.journal_path is None or not self.journal_path.is_file():
            return 0.0
        total = 0.0
        with self.journal_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    total += float(record["charged_usd"])
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise ValueError(
                        f"Invalid spend journal line {line_number}: "
                        f"{self.journal_path}"
                    ) from exc
        return total

    def _append_charge(self, *, charged_usd: float, reason: str) -> None:
        if self.journal_path is None:
            return
        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "charged_usd": round(charged_usd, 8),
            "reason": reason,
        }
        with self.journal_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return round(self._spent_usd, 8)

    @property
    def reserved_usd(self) -> float:
        with self._lock:
            return round(self._reserved_usd, 8)

    @property
    def remaining_usd(self) -> float:
        with self._lock:
            return round(
                max(0.0, self.max_usd - self._spent_usd - self._reserved_usd), 8
            )

    def reserve(self) -> None:
        with self._lock:
            projected = (
                self._spent_usd + self._reserved_usd + self.reservation_usd
            )
            if projected > self.max_usd + 1e-12:
                raise SpendLimitExceeded(
                    "OpenAI spend limit reached before request: "
                    f"spent=${self._spent_usd:.4f}, "
                    f"reserved=${self._reserved_usd:.4f}, "
                    f"limit=${self.max_usd:.2f}"
                )
            self._reserved_usd += self.reservation_usd

    def settle(self, actual_usd: float) -> None:
        if actual_usd < 0:
            raise ValueError("actual_usd cannot be negative")
        with self._lock:
            self._reserved_usd = max(
                0.0, self._reserved_usd - self.reservation_usd
            )
            self._spent_usd += float(actual_usd)
            self._append_charge(charged_usd=float(actual_usd), reason="reported_usage")

    def abandon(self, *, charge_reservation: bool) -> None:
        """Release a failed attempt, conservatively charging uncertain timeouts."""

        with self._lock:
            self._reserved_usd = max(
                0.0, self._reserved_usd - self.reservation_usd
            )
            if charge_reservation:
                self._spent_usd += self.reservation_usd
                self._append_charge(
                    charged_usd=self.reservation_usd,
                    reason="uncertain_timeout_reservation",
                )

    def snapshot(self) -> dict[str, float]:
        return {
            "max_usd": self.max_usd,
            "reservation_usd": self.reservation_usd,
            "spent_usd": self.spent_usd,
            "reserved_usd": self.reserved_usd,
            "remaining_usd": self.remaining_usd,
            "journal_path": str(self.journal_path) if self.journal_path else None,
        }


def _safe_case_id(value: str) -> str:
    return "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in value
    )


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(exc, APIStatusError):
        return int(exc.status_code) in {408, 409, 429} or exc.status_code >= 500
    return False


def _timeout_may_have_been_charged(exc: Exception) -> bool:
    return isinstance(exc, APITimeoutError)


class OpenAIStructuredInvoker:
    """One-shot strict-JSON adapter backed by the OpenAI Responses API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reasoning_effort: str,
        instructions_file: Path,
        spend_tracker: SpendTracker,
        verbosity: str = "low",
        max_output_tokens: int = 8_192,
        timeout_seconds: float = 600.0,
        max_transport_retries: int = 3,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise OpenAIStructuredError("OPENAI_API_KEY is missing or empty")
        if max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if max_transport_retries < 0:
            raise ValueError("max_transport_retries must be non-negative")
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.max_transport_retries = max_transport_retries
        self.instructions_file = instructions_file.resolve()
        if not self.instructions_file.is_file():
            raise OpenAIStructuredError(
                f"Instructions file is missing: {self.instructions_file}"
            )
        self.instructions = self.instructions_file.read_text(encoding="utf-8")
        self.prompt_cache_key = "fyp-structured-" + hashlib.sha256(
            self.instructions.encode("utf-8")
        ).hexdigest()[:24]
        self.spend_tracker = spend_tracker
        self.client = client or OpenAI(
            api_key=api_key,
            max_retries=0,
            timeout=timeout_seconds,
        )

    def _write_artifacts(
        self,
        directory: Path | None,
        *,
        attempt_id: str,
        prompt: str,
        output_schema: dict[str, Any],
        response: Any | None = None,
        error: Exception | None = None,
    ) -> None:
        if directory is None:
            return
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{attempt_id}.prompt.txt").write_text(
            prompt, encoding="utf-8"
        )
        (directory / f"{attempt_id}.output-schema.json").write_text(
            json.dumps(output_schema, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if response is not None:
            raw = (
                response.model_dump(mode="json")
                if hasattr(response, "model_dump")
                else {"output_text": getattr(response, "output_text", "")}
            )
            (directory / f"{attempt_id}.response.json").write_text(
                json.dumps(raw, indent=2, ensure_ascii=False, default=str) + "\n",
                encoding="utf-8",
            )
        if error is not None:
            (directory / f"{attempt_id}.error.txt").write_text(
                f"{type(error).__name__}: {error}\n", encoding="utf-8"
            )

    def run(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        artifact_directory: Path | None = None,
        case_id: str = "case",
    ) -> OpenAIStructuredResult:
        if not prompt.strip():
            raise OpenAIStructuredError("Prompt must not be empty")
        started = time.monotonic()
        safe_id = _safe_case_id(case_id)
        attempts = 0
        attempt_events: list[dict[str, Any]] = []

        while True:
            attempts += 1
            self.spend_tracker.reserve()
            attempt_id = f"{safe_id}-transport-attempt-{attempts}"
            try:
                response = self.client.responses.create(
                    model=self.model,
                    instructions=self.instructions,
                    input=prompt,
                    reasoning={"effort": self.reasoning_effort},
                    text={
                        "verbosity": self.verbosity,
                        "format": {
                            "type": "json_schema",
                            "name": "structured_output",
                            "schema": output_schema,
                            "strict": True,
                        },
                    },
                    max_output_tokens=self.max_output_tokens,
                    prompt_cache_key=self.prompt_cache_key,
                    service_tier="default",
                    store=False,
                )
                usage = _usage_from_response(response)
                self.spend_tracker.settle(usage.estimated_cost_usd)
                self._write_artifacts(
                    artifact_directory,
                    attempt_id=attempt_id,
                    prompt=prompt,
                    output_schema=output_schema,
                    response=response,
                )
            except Exception as exc:
                self.spend_tracker.abandon(
                    charge_reservation=_timeout_may_have_been_charged(exc)
                )
                self._write_artifacts(
                    artifact_directory,
                    attempt_id=attempt_id,
                    prompt=prompt,
                    output_schema=output_schema,
                    error=exc,
                )
                retryable = _retryable(exc)
                attempt_events.append(
                    {
                        "attempt": attempts,
                        "status": "transport_error",
                        "error_type": type(exc).__name__,
                        "retryable": retryable,
                    }
                )
                if retryable and attempts <= self.max_transport_retries:
                    delay = min(20.0, 0.75 * (2 ** (attempts - 1)))
                    time.sleep(delay + random.random() * 0.25)
                    continue
                raise OpenAIStructuredError(
                    f"Responses API failed after {attempts} attempt(s): "
                    f"{type(exc).__name__}: {exc}"
                ) from exc

            status = str(getattr(response, "status", ""))
            if status != "completed":
                raise OpenAIStructuredError(
                    f"Responses API returned incomplete status {status!r}"
                )
            output_text = str(getattr(response, "output_text", "") or "").strip()
            if not output_text:
                raise OpenAIStructuredError("Responses API output is empty")
            try:
                output = json.loads(output_text)
            except json.JSONDecodeError as exc:
                raise OpenAIStructuredError(
                    f"Responses API output is not valid JSON: {exc}"
                ) from exc
            if not isinstance(output, dict):
                raise OpenAIStructuredError(
                    "Responses API output is not a JSON object"
                )
            attempt_events.append(
                {
                    "attempt": attempts,
                    "status": "completed",
                    "response_id": str(getattr(response, "id", "")),
                    "served_model": str(getattr(response, "model", "")),
                }
            )
            return OpenAIStructuredResult(
                output_text=output_text,
                output=output,
                usage=usage,
                elapsed_ms=round((time.monotonic() - started) * 1_000, 3),
                events=tuple(attempt_events),
                transport_attempts=attempts,
                served_model=str(getattr(response, "model", "")) or None,
            )
