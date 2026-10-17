"""Provider-neutral construction and provenance for structured model calls."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .openai_structured import OpenAIStructuredInvoker, SpendTracker


OPENAI_SURFACE = "openai_responses_api"
CODEX_SURFACE = "codex_subscription_proxy"
PROVIDERS = ("openai", "codex")


@dataclass(frozen=True)
class StructuredRuntime:
    policy_invoker: Any
    simulator_invoker: Any
    surface: str
    spend_tracker: SpendTracker | None

    def model_manifest(self, *, model: str, reasoning_effort: str) -> dict[str, Any]:
        if self.surface == OPENAI_SURFACE:
            assert self.spend_tracker is not None
            return {
                "surface": self.surface,
                "requested": model,
                "reasoning_effort": reasoning_effort,
                "verbosity": "low",
                "service_tier": "default",
                "store": False,
                "monetary_cost_available": True,
                "spend_guard": self.spend_tracker.snapshot(),
            }
        return {
            "surface": self.surface,
            "requested": model,
            "reasoning_effort": reasoning_effort,
            "verbosity": "low",
            "codex_cli_version": self.policy_invoker.codex_cli_version,
            "monetary_cost_available": False,
        }


def create_structured_runtime(
    *,
    provider: str,
    experiment_root: Path,
    model: str,
    reasoning_effort: str,
    policy_instructions: Path,
    simulator_instructions: Path,
    max_output_tokens: int,
    max_transport_retries: int,
    max_spend_usd: float | None,
    spend_ledger_path: Path | None = None,
    timeout_seconds: float = 600.0,
) -> StructuredRuntime:
    if provider not in PROVIDERS:
        raise ValueError(f"Unknown provider {provider!r}; choose from {PROVIDERS}")
    if provider == "codex":
        from codex_proxy.runner import CodexSubscriptionProxy

        policy = CodexSubscriptionProxy(
            model=model,
            reasoning_effort=reasoning_effort,
            verbosity="low",
            instructions_file=policy_instructions,
            timeout_seconds=timeout_seconds,
            max_transport_retries=max_transport_retries,
        )
        simulator = CodexSubscriptionProxy(
            model=model,
            reasoning_effort=reasoning_effort,
            verbosity="low",
            instructions_file=simulator_instructions,
            timeout_seconds=timeout_seconds,
            max_transport_retries=max_transport_retries,
        )
        return StructuredRuntime(policy, simulator, CODEX_SURFACE, None)

    if max_spend_usd is None or max_spend_usd <= 0:
        raise ValueError("OpenAI runs require a positive --max-spend-usd")
    load_dotenv(experiment_root / ".env", override=False)
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key.strip():
        raise ValueError(
            "OPENAI_API_KEY was not found in the environment or experiment .env"
        )
    tracker = SpendTracker(
        max_usd=max_spend_usd,
        journal_path=spend_ledger_path,
    )
    common = {
        "api_key": api_key,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "spend_tracker": tracker,
        "verbosity": "low",
        "max_output_tokens": max_output_tokens,
        "timeout_seconds": timeout_seconds,
        "max_transport_retries": max_transport_retries,
    }
    policy = OpenAIStructuredInvoker(
        instructions_file=policy_instructions,
        **common,
    )
    simulator = OpenAIStructuredInvoker(
        instructions_file=simulator_instructions,
        **common,
    )
    return StructuredRuntime(policy, simulator, OPENAI_SURFACE, tracker)
