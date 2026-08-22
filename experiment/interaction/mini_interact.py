"""Public Mini-Interact task loading and a gold-isolated user simulator.

The policy-side task loader reads only the public release. The simulator is an
evaluator-side, structured-output adaptation of Mini-Interact's released
two-stage encoder/decoder: first classify what the clarification asks about,
then answer without exposing SQL or hidden labels.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from codex_proxy.runner import CodexSubscriptionProxy

try:
    from sqlglot import tokenize
    from sqlglot.tokens import TokenType
except ImportError:  # The shared lightweight experiment environment omits SQLGlot.
    tokenize = None
    TokenType = None

from .controller import ClarificationAnswer
from .trajectory import BenchmarkTask, Usage

SIMULATOR_ADAPTER_VERSION = "mini-interact-user-simulator-structured-v1"
SANITIZED_SIMULATOR_ADAPTER_VERSION = (
    "mini-interact-user-simulator-structured-sanitized-v2"
)
SIMULATOR_SANITIZER_VERSION = "mini-interact-answer-sanitizer-v1"
MAX_POLICY_VISIBLE_ANSWER_CHARS = 400


class SimulatorLeakageError(ValueError):
    """A simulator answer crossed the frozen evaluator/policy boundary."""


def validate_policy_visible_simulator_answer(answer: str) -> str:
    """Reject likely SQL, hidden labels, or private-prompt material."""

    clean = answer.strip()
    if not clean:
        raise SimulatorLeakageError("Mini-Interact simulator returned an empty answer")
    if len(clean) > MAX_POLICY_VISIBLE_ANSWER_CHARS:
        raise SimulatorLeakageError(
            "Mini-Interact simulator answer exceeded the 400-character limit"
        )
    lowered = clean.casefold()
    forbidden_literals = (
        "```",
        "<thought>",
        "</thought>",
        "sol_sql",
        "user_query_ambiguity",
        "knowledge_ambiguity",
        "test_cases",
        "create table",
    )
    if any(value in lowered for value in forbidden_literals):
        raise SimulatorLeakageError(
            "Mini-Interact simulator answer contained restricted material"
        )
    if re.search(r"\bselect\b[\s\S]{0,300}\bfrom\b", clean, re.IGNORECASE):
        raise SimulatorLeakageError(
            "Mini-Interact simulator answer contained SQL-like text"
        )
    return clean


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError(f"Expected an object at {path}:{line_number}")
            rows.append(row)
    return rows


def visible_external_knowledge(row: dict[str, Any], dataset_root: Path) -> str:
    """Return only knowledge that the official system prompt leaves unmasked."""

    database_id = str(row["selected_database"])
    knowledge_path = dataset_root / database_id / f"{database_id}_kb.jsonl"
    excluded = {
        int(item["deleted_knowledge"])
        for item in row.get("knowledge_ambiguity", [])
        if item.get("deleted_knowledge") is not None
    }
    visible = [
        item
        for item in load_jsonl(knowledge_path)
        if int(item.get("id", -1)) not in excluded
    ]
    if not visible:
        return "(No unmasked external knowledge was released for this task.)"
    return "\n".join(json.dumps(item, ensure_ascii=False) for item in visible)


def load_public_tasks(path: Path, dataset_root: Path) -> list[BenchmarkTask]:
    """Build model-visible tasks without reading the private merged artifact."""

    rows = load_jsonl(path)
    tasks = [
        BenchmarkTask(
            question_id=str(row["instance_id"]),
            db_id=str(row["selected_database"]),
            question=str(row["amb_user_query"]),
            evidence=(
                "UNMASKED EXTERNAL KNOWLEDGE\n"
                + visible_external_knowledge(row, dataset_root)
            ),
            difficulty="interactive",
        )
        for row in rows
    ]
    if len(tasks) != len({task.question_id for task in tasks}):
        raise ValueError("Mini-Interact public instance IDs must be unique")
    return tasks


_CLAUSE_NAMES = (
    {
        TokenType.SELECT: "SELECT",
        TokenType.FROM: "FROM",
        TokenType.WHERE: "WHERE",
        TokenType.HAVING: "HAVING",
        TokenType.GROUP_BY: "GROUP BY",
        TokenType.ORDER_BY: "ORDER BY",
        TokenType.LIMIT: "LIMIT",
        TokenType.OFFSET: "OFFSET",
        TokenType.JOIN: "JOIN",
        TokenType.STRAIGHT_JOIN: "STRAIGHT JOIN",
    }
    if TokenType is not None
    else {}
)


def segment_sql(sql: str) -> list[tuple[str, str]]:
    """Mirror the released Mini-Interact clause segmentation behavior."""

    if tokenize is None:
        return [("STATEMENT", sql.strip())]
    try:
        starts = [
            (token.start, _CLAUSE_NAMES[token.token_type])
            for token in tokenize(sql, read="sqlite")
            if token.token_type in _CLAUSE_NAMES
        ]
        if not starts:
            return [("STATEMENT", sql.strip())]
        starts.sort()
        return [
            (
                name,
                sql[
                    position : starts[index + 1][0]
                    if index + 1 < len(starts)
                    else len(sql)
                ].strip(),
            )
            for index, (position, name) in enumerate(starts)
        ]
    except Exception:  # noqa: BLE001 - mirror the released parser's fallback.
        return [("STATEMENT", part.strip()) for part in sql.split(";") if part.strip()]


def _sql_segments(row: dict[str, Any]) -> str:
    groups: list[str] = []
    for statement in row.get("sol_sql", []):
        groups.append(
            "\n\n".join(
                f"{clause}:\n{text}" for clause, text in segment_sql(str(statement))
            )
        )
    return "\n===\n".join(groups)


def _ambiguities(row: dict[str, Any]) -> str:
    payload = {
        "user_query_ambiguity": row.get("user_query_ambiguity", {}),
        "knowledge_ambiguity": row.get("knowledge_ambiguity", []),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _usage_sum(*records: Usage) -> Usage:
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


ENCODER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["labeled", "unlabeled", "unanswerable"],
        },
        "target": {"type": "string"},
    },
    "required": ["action", "target"],
    "additionalProperties": False,
}


DECODER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


class CodexMiniInteractUserSimulator:
    """Two-call evaluator-side simulator through the strict Luna proxy."""

    def __init__(
        self,
        *,
        proxy: CodexSubscriptionProxy,
        dataset_root: Path,
        artifact_directory: Path | None = None,
    ) -> None:
        self.proxy = proxy
        self.dataset_root = dataset_root.resolve()
        self.artifact_directory = artifact_directory

    def answer(
        self,
        task: BenchmarkTask,
        question: str,
        history: tuple[dict[str, Any], ...],
        private_row: dict[str, Any],
    ) -> ClarificationAnswer:
        if str(private_row["instance_id"]) != str(task.question_id):
            raise ValueError("Mini-Interact simulator task/row mismatch")
        turn = 1 + sum(item.get("kind") == "user_clarification" for item in history)
        case = f"{task.question_id}-user-turn-{turn}"
        ambiguities = _ambiguities(private_row)
        sql_segments = _sql_segments(private_row)
        encoder_prompt = f"""Classify one clarification question using Mini-Interact's released user-simulator action space.

LABELED AMBIGUITY POINTS (evaluator-only)
{ambiguities}

GROUND-TRUTH SQL SEGMENTS (evaluator-only)
{sql_segments}

AI COLLABORATOR QUESTION
{question}

Choose labeled when it asks about a labeled point, unlabeled when it asks an important unlabeled SQL distinction, or unanswerable when it is irrelevant/unnecessary. Put the matching term or segment in target; use an empty target for unanswerable. Do not answer the question and do not reveal SQL.
""".strip()
        encoded = self.proxy.run(
            encoder_prompt,
            output_schema=ENCODER_SCHEMA,
            artifact_directory=self.artifact_directory,
            case_id=f"{case}-encoder",
        )
        action = str(encoded.output["action"])
        target = str(encoded.output["target"])

        database_id = str(private_row["selected_database"])
        schema_path = self.dataset_root / database_id / f"{database_id}_schema.txt"
        schema = schema_path.read_text(encoding="utf-8")
        gold_sql = "\n\n".join(str(value) for value in private_row.get("sol_sql", []))
        decoder_prompt = f"""Role-play the Mini-Interact user and answer one clarification naturally and concisely.

The evaluated Text-to-SQL agent must not see evaluator-only labels or SQL. Answer only what was asked, do not include SQL/code, do not describe hidden reasoning, do not ask another question, and do not change the intended task. If ACTION is unanswerable, say that the question cannot help clarify the request.

DATABASE SCHEMA
{schema}

AMBIGUITY POINTS (evaluator-only)
{ambiguities}

INTENDED SQL (evaluator-only)
{gold_sql}

INTENDED SQL SEGMENTS (evaluator-only)
{sql_segments}

AMBIGUOUS USER REQUEST
{private_row.get("amb_user_query", "")}

AI COLLABORATOR QUESTION
{question}

ACTION
{action}({json.dumps(target, ensure_ascii=False)})
""".strip()
        decoded = self.proxy.run(
            decoder_prompt,
            output_schema=DECODER_SCHEMA,
            artifact_directory=self.artifact_directory,
            case_id=f"{case}-decoder",
        )
        answer = str(decoded.output["answer"]).strip()
        if not answer:
            raise ValueError("Mini-Interact user simulator returned an empty answer")
        return ClarificationAnswer(
            text=answer,
            metadata={
                "adapter": SIMULATOR_ADAPTER_VERSION,
                "surface": "codex_subscription_proxy",
                "model": self.proxy.model,
                "reasoning_effort": self.proxy.reasoning_effort,
                "encoder_action": action,
                "calls": 2,
                "transport_attempts": int(getattr(encoded, "transport_attempts", 1))
                + int(getattr(decoded, "transport_attempts", 1)),
            },
            usage=_usage_sum(encoded.usage, decoded.usage),
            elapsed_ms=round(encoded.elapsed_ms + decoded.elapsed_ms, 3),
        )


class SanitizedCodexMiniInteractUserSimulator(CodexMiniInteractUserSimulator):
    """Official-comparison adapter with deterministic leakage containment."""

    def answer(
        self,
        task: BenchmarkTask,
        question: str,
        history: tuple[dict[str, Any], ...],
        private_row: dict[str, Any],
    ) -> ClarificationAnswer:
        raw = super().answer(task, question, history, private_row)
        clean = validate_policy_visible_simulator_answer(raw.text)
        return ClarificationAnswer(
            text=clean,
            metadata={
                **raw.metadata,
                "adapter": SANITIZED_SIMULATOR_ADAPTER_VERSION,
                "sanitizer": SIMULATOR_SANITIZER_VERSION,
                "max_visible_answer_chars": MAX_POLICY_VISIBLE_ANSWER_CHARS,
                "clear_query_source": "ambiguous_query_fallback",
            },
            usage=raw.usage,
            elapsed_ms=raw.elapsed_ms,
        )
