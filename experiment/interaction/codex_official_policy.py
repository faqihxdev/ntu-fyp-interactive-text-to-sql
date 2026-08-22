"""Codex adapter for the official-protocol-compatible Mini-Interact module."""

from __future__ import annotations

import importlib.util
import json
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

from codex_proxy.runner import CodexSubscriptionProxy

from .official_protocol import (
    OFFICIAL_ACTION_COSTS,
    SRA_REVIEW_SOURCES,
    OfficialAction,
    OfficialDecisionRequest,
    OfficialPolicyTurn,
)

POLICY_GUIDANCE = {
    "mini_react_port": (
        "Follow a structured port of the released Mini-Interact ReAct-style "
        "reference strategy: use paid "
        "environment actions to obtain missing schema, column, knowledge, or "
        "execution information; ask concise clarification when user intent is "
        "unresolved; submit when the SQL is ready."
    ),
    "sphinteract_cq": (
        "Follow the audited Sphinteract CQs-with-early-stopping adaptation. The "
        "controller enforces schema discovery, an initial SQL before any ask, "
        "reactive Summarize-Review-Ask checkpoints, and bounded regeneration."
    ),
    "proactive": (
        "Use exactly one proactive Summarize-Review-Ask checkpoint after paid "
        "schema discovery and before the first SQL. Never ask again after a "
        "rejection; regenerate from failed SQL and visible scorer feedback."
    ),
    "hybrid": (
        "Use one proactive Summarize-Review-Ask checkpoint after paid schema "
        "discovery, then use the same reactive checkpoint after wrong SQL. The "
        "controller caps the shared total at four user questions."
    ),
}

OFFICIAL_REFERENCE_PROMPT_SOURCE = (
    Path(__file__).resolve().parents[1]
    / "datasets"
    / "bird-interact-evaluator"
    / "mini_interact"
    / "knowledge_based"
    / "mini_interact_agent"
    / "experiments"
    / "utils"
    / "prompts.py"
)


@lru_cache(maxsize=1)
def official_reference_prompt_context() -> str:
    """Render the pinned reference instructions/demo for the structured port."""

    spec = importlib.util.spec_from_file_location(
        "_pinned_mini_reference_prompts", OFFICIAL_REFERENCE_PROMPT_SOURCE
    )
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load the pinned Mini-Interact reference prompt")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    template = module.TemplateReActUserBirdInteract("sqlite", "data analysis")
    return f"{template.get_init_msg()}\n\n{template.get_demos()}".strip()


def official_output_schema(allowed_actions: tuple[str, ...]) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(allowed_actions)},
            "sql": {"type": "string"},
            "question": {"type": "string"},
            "table_name": {"type": "string"},
            "column_name": {"type": "string"},
            "knowledge_name": {"type": "string"},
            "purpose": {"type": "string"},
            "review_source": {
                "type": "string",
                "enum": ["", *SRA_REVIEW_SOURCES, "Other"],
            },
            "summary": {"type": "string"},
            "reviews": {
                "type": "object",
                "properties": {
                    source: {"type": "string"} for source in SRA_REVIEW_SOURCES
                },
                "required": list(SRA_REVIEW_SOURCES),
                "additionalProperties": False,
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 4,
            },
        },
        "required": [
            "kind",
            "sql",
            "question",
            "table_name",
            "column_name",
            "knowledge_name",
            "purpose",
            "review_source",
            "summary",
            "reviews",
            "options",
        ],
        "additionalProperties": False,
    }


def build_official_policy_prompt(request: OfficialDecisionRequest) -> str:
    if request.policy not in POLICY_GUIDANCE:
        raise ValueError(f"No official policy guidance for {request.policy}")
    rules = {
        "execute": "Run one read-only SQLite SELECT/WITH diagnostic query.",
        "get_schema": "Retrieve the released schema and example rows.",
        "get_all_column_meanings": "Retrieve every released column meaning.",
        "get_column_meaning": "Set table_name and column_name for one column.",
        "get_all_external_knowledge_names": "List accessible knowledge names.",
        "get_knowledge_definition": "Set knowledge_name for one definition.",
        "get_all_knowledge_definitions": "Retrieve every accessible definition.",
        "ask": "Ask one concise non-technical clarification question.",
        "submit": "Submit one final read-only SQLite SELECT/WITH query.",
        "no_ambiguity": (
            "Internal zero-coin SRA sentinel: no useful unresolved ambiguity remains."
        ),
    }
    actions = "\n".join(
        (
            f"- {action} (cost {OFFICIAL_ACTION_COSTS[action]:g}): {rules[action]}"
            if action in OFFICIAL_ACTION_COSTS
            else f"- {action} (internal; 0 bird coins): {rules[action]}"
        )
        for action in request.allowed_actions
    )
    if request.phase in {"proactive_sra", "reactive_sra"}:
        actions += (
            "\n\nSRA CHECKPOINT\n"
            "1. Summarize what prior answers and failed SQL have established.\n"
            "2. Review AmbQuestion, AmbColumn, AmbOutput, and AmbValue separately; "
            "fill every reviews field.\n"
            "3. If one useful ambiguity remains, return ask with one question, "
            "exactly three closed options, and a fourth open 'Other—please specify' "
            "option. Set review_source to the strongest category.\n"
            "4. Otherwise return no_ambiguity with an empty question/options."
        )
    state = request.to_dict()
    state.pop("run_id", None)
    reference_context = ""
    if request.policy == "mini_react_port":
        reference_context = f"""

PINNED OFFICIAL REFERENCE INSTRUCTIONS AND TOY TRAJECTORY
Use this as behavioral guidance. The surrounding adapter replaces its XML and
visible-thought grammar with the JSON action schema supplied to you; that is a
declared structured-output adaptation.

{official_reference_prompt_context()}
"""
    return f"""Choose exactly one action in a Mini-Interact agentic trajectory.

POLICY
{POLICY_GUIDANCE[request.policy]}
{reference_context}

PROTOCOL
- The initial model-visible task contains only the ambiguous question and budget.
- Schema, column meanings, external knowledge, execution, questions, and submissions are paid actions.
- Never assume hidden gold SQL, labels, test cases, or unretrieved database information.
- A rejected submission is feedback, not permission to repeat it.
- If only submit is allowed, submit the best SQL now.
- Fill unused string fields with an empty string, options with [], and every unused reviews value with an empty string.

ALLOWED ACTIONS
{actions}

SQL CHECK
- Match requested output columns, filters, joins, grain, aggregation, ordering, and LIMIT.
- Use clarification answers and retrieved definitions exactly.
- Execute only when its diagnostic value justifies the extra coin.

STATE
{json.dumps(state, ensure_ascii=False, indent=2)}
""".strip()


class CodexOfficialPolicyModel:
    """Structured action decisions through the strict subscription proxy."""

    def __init__(
        self,
        *,
        proxy: CodexSubscriptionProxy,
        artifact_directory: Path | None = None,
    ) -> None:
        self.proxy = proxy
        self.artifact_directory = artifact_directory
        self.requested_model = f"codex_subscription_proxy:{proxy.model}"
        self.reasoning_effort = proxy.reasoning_effort
        self._call_index = 0

    def decide(self, request: OfficialDecisionRequest) -> OfficialPolicyTurn:
        self._call_index += 1
        result = self.proxy.run(
            build_official_policy_prompt(request),
            output_schema=official_output_schema(request.allowed_actions),
            artifact_directory=self.artifact_directory,
            case_id=f"{request.run_id}-official-decision-{self._call_index}",
        )
        payload = result.output
        action = OfficialAction(
            kind=str(payload.get("kind", "")),
            sql=str(payload.get("sql", "")).strip() or None,
            question=str(payload.get("question", "")).strip() or None,
            table_name=str(payload.get("table_name", "")).strip() or None,
            column_name=str(payload.get("column_name", "")).strip() or None,
            knowledge_name=str(payload.get("knowledge_name", "")).strip() or None,
            purpose=str(payload.get("purpose", "")).strip() or None,
            review_source=str(payload.get("review_source", "")).strip() or None,
            summary=str(payload.get("summary", "")).strip() or None,
            reviews={
                str(key): str(value)
                for key, value in (payload.get("reviews") or {}).items()
            },
            options=tuple(str(value).strip() for value in payload.get("options") or []),
        )
        return OfficialPolicyTurn(
            response_id=f"codex-proxy-{uuid4().hex}",
            action=action,
            raw_output=result.output_text,
            usage=result.usage,
            elapsed_ms=result.elapsed_ms,
            transport_attempts=int(getattr(result, "transport_attempts", 1)),
        )
