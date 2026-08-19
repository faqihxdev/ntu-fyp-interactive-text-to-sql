"""Model seam: OpenAI Responses adapter plus a small testable interface."""

from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any, Protocol

from openai import OpenAI

from .trajectory import Usage


AGENT_PROMPT_VERSION = "bird-sql-agent-v2"


AGENT_INSTRUCTIONS = """You are a careful SQLite text-to-SQL agent.

Your goal is to submit one SQL query that answers the supplied question. The
database schema, column descriptions, benchmark evidence, and any deterministic
value matches are provided in the input. You may inspect the database with
query_database before committing. Never ask the user and never invent a second
candidate for voting.

Rules:
1. Use only SQLite SELECT/WITH queries.
2. Follow benchmark evidence exactly when it defines a term or formula.
3. Match the requested result shape exactly. Return only requested values: do
   not add explanations, labels, IDs, diagnostic columns, or alternative forms.
   A count question normally returns one scalar unless a breakdown is explicitly
   requested. Keep requested source fields as separate output columns; do not
   concatenate them unless a formatted string is explicitly requested. When
   evidence maps a phrase to named columns, prefer those exact columns.
4. Treat percentage as a 0-100 value unless the question/evidence explicitly
   requests a fraction. Do not add DISTINCT merely because rows share a person
   or entity; preserve the database grain implied by the requested record type.
5. Check joins, filters, aggregation, projection, DISTINCT, ordering, and LIMIT.
6. Use query_database for targeted value, join, or intermediate-result checks;
   probes are limited, so make each one purposeful.
7. Call submit_sql only when the SQL is your final answer. If submission returns
   an execution error, repair that SQL using the error; do not repeat it.
8. The hidden gold query/result is unavailable. Do not request it.

You must respond by calling one of the supplied tools, not with prose.
"""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "query_database",
        "description": (
            "Execute one read-only SQLite probe to inspect values, joins, or an "
            "intermediate/final-looking result before submission."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "One SELECT/WITH query."},
                "purpose": {
                    "type": "string",
                    "description": "Briefly state what uncertainty this probe checks.",
                },
            },
            "required": ["sql", "purpose"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "type": "function",
        "name": "submit_sql",
        "description": "Submit the single final SQLite query for evaluation.",
        "parameters": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "The final SELECT/WITH query."}
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


MODEL_PRICES_PER_MILLION = {
    "gpt-5.6-luna": {"input": 0.20, "cached": 0.02, "output": 1.20},
    "gpt-5.6-sol": {"input": 5.00, "cached": 0.50, "output": 30.00},
    "gpt-5.6": {"input": 5.00, "cached": 0.50, "output": 30.00},
}


@dataclass(frozen=True)
class FunctionCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str
    parse_error: str | None = None


@dataclass(frozen=True)
class AgentTurn:
    response_id: str
    requested_model: str
    returned_model: str
    status: str
    function_calls: tuple[FunctionCall, ...]
    output_text: str
    usage: Usage
    elapsed_ms: float


class AgentModel(Protocol):
    requested_model: str
    reasoning_effort: str

    def start(
        self,
        prompt: str,
        *,
        cache_key: str,
        metadata: dict[str, str],
    ) -> AgentTurn: ...

    def continue_after(
        self,
        previous_response_id: str,
        outputs: list[dict[str, str]],
        *,
        metadata: dict[str, str],
    ) -> AgentTurn: ...


def estimate_usage_cost(model: str, usage: dict[str, Any]) -> float:
    prices = MODEL_PRICES_PER_MILLION.get(model)
    if prices is None:
        return 0.0
    input_tokens = int(usage.get("input_tokens", 0))
    input_details = usage.get("input_tokens_details") or {}
    cached = int(input_details.get("cached_tokens", 0))
    cache_write = int(input_details.get("cache_write_tokens", 0))
    uncached = max(0, input_tokens - cached - cache_write)
    output_tokens = int(usage.get("output_tokens", 0))
    dollars = (
        uncached * prices["input"]
        + cached * prices["cached"]
        + cache_write * prices["input"] * 1.25
        + output_tokens * prices["output"]
    ) / 1_000_000
    return round(dollars, 8)


def _usage_from_response(response: Any) -> Usage:
    if response.usage is None:
        return Usage()
    raw = response.usage.model_dump()
    input_details = raw.get("input_tokens_details") or {}
    output_details = raw.get("output_tokens_details") or {}
    returned_model = str(response.model)
    return Usage(
        input_tokens=int(raw.get("input_tokens", 0)),
        cached_input_tokens=int(input_details.get("cached_tokens", 0)),
        cache_write_input_tokens=int(input_details.get("cache_write_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
        reasoning_tokens=int(output_details.get("reasoning_tokens", 0)),
        total_tokens=int(raw.get("total_tokens", 0)),
        estimated_cost_usd=estimate_usage_cost(returned_model, raw),
    )


def _turn_from_response(response: Any, requested_model: str, elapsed_ms: float) -> AgentTurn:
    calls: list[FunctionCall] = []
    for item in response.output:
        if getattr(item, "type", None) != "function_call":
            continue
        raw_arguments = str(item.arguments)
        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, dict):
                raise TypeError("function arguments must decode to an object")
            parse_error = None
        except (json.JSONDecodeError, TypeError) as exc:
            arguments = {}
            parse_error = str(exc)
        calls.append(
            FunctionCall(
                call_id=str(item.call_id),
                name=str(item.name),
                arguments=arguments,
                raw_arguments=raw_arguments,
                parse_error=parse_error,
            )
        )
    return AgentTurn(
        response_id=str(response.id),
        requested_model=requested_model,
        returned_model=str(response.model),
        status=str(response.status),
        function_calls=tuple(calls),
        output_text=str(response.output_text or ""),
        usage=_usage_from_response(response),
        elapsed_ms=round(elapsed_ms, 3),
    )


class OpenAIResponsesAgent:
    """Bounded function-calling adapter for the current OpenAI Responses API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-5.6-luna",
        reasoning_effort: str = "medium",
        max_output_tokens: int = 4_096,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.requested_model = model
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.timeout_seconds = timeout_seconds
        self.client = OpenAI(api_key=api_key, max_retries=5, timeout=timeout_seconds)

    def _create(self, **kwargs: Any) -> AgentTurn:
        started = time.monotonic()
        response = self.client.responses.create(
            model=self.requested_model,
            instructions=AGENT_INSTRUCTIONS,
            tools=TOOLS,
            tool_choice="required",
            parallel_tool_calls=False,
            reasoning={"effort": self.reasoning_effort},
            max_output_tokens=self.max_output_tokens,
            store=True,
            **kwargs,
        )
        return _turn_from_response(
            response,
            requested_model=self.requested_model,
            elapsed_ms=(time.monotonic() - started) * 1_000,
        )

    def start(
        self,
        prompt: str,
        *,
        cache_key: str,
        metadata: dict[str, str],
    ) -> AgentTurn:
        return self._create(
            input=prompt,
            prompt_cache_key=cache_key,
            metadata=metadata,
        )

    def continue_after(
        self,
        previous_response_id: str,
        outputs: list[dict[str, str]],
        *,
        metadata: dict[str, str],
    ) -> AgentTurn:
        return self._create(
            previous_response_id=previous_response_id,
            input=outputs,
            metadata=metadata,
        )
