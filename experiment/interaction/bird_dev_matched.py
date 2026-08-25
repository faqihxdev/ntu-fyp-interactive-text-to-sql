"""Matched original-BIRD controller bridge for clarification-timing comparisons.

This module deliberately does not reuse Mini-Interact coins, paid tools, or
private ambiguity labels.  It supplies every arm with the same schema-only
context and evaluator, shares the initial SQL across the Sphinteract arms, and
keeps gold SQL behind the evaluator-side simulator/scorer boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
from typing import Any, Protocol

from .sphinteract_source import SphinteractPrompts
from .sqlite_env import QueryResult, SQLiteEnvironment, bird_execution_equal
from .trajectory import BenchmarkTask, Usage


PROTOCOL_VERSION = "bird-dev-matched-v4"
ARMS = (
    "no_user",
    "proactive_only",
    "sph_cq",
    "project_hybrid",
    "sph_cq_es",
)
INTERACTIVE_ARMS = ARMS[1:]
SRA_REVIEW_SOURCES = (
    "AmbQuestion",
    "AmbTableColumn",
    "AmbOutput",
    "AmbValue",
)
SQL_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)
SIMULATOR_EVALUATOR_LABEL = re.compile(
    r"\b(?:the\s+)?(?:gold|ground[-\s]+truth|hidden|oracle)\s+"
    r"(?:sql|query|answer)\b",
    re.IGNORECASE,
)
SIMULATOR_SQL_SHAPED_REPLY = re.compile(
    r"(?is)```\s*sql\b|\bSELECT\b.+\bFROM\b|\bWITH\b.+\bSELECT\b"
)

SQL_OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
    "additionalProperties": False,
}
ANSWER_OUTPUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def sra_output_schema(*, allow_no_ambiguity: bool) -> dict[str, Any]:
    decisions = ["ask", "no_ambiguity"] if allow_no_ambiguity else ["ask"]
    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": decisions},
            "summary": {"type": "string"},
            "reviews": {
                "type": "object",
                "properties": {
                    source: {"type": "string"} for source in SRA_REVIEW_SOURCES
                },
                "required": list(SRA_REVIEW_SOURCES),
                "additionalProperties": False,
            },
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "minItems": 4,
                "maxItems": 4,
            },
        },
        "required": ["decision", "summary", "reviews", "question", "options"],
        "additionalProperties": False,
    }
    return schema


RECOMMENDED_SUFFIX = re.compile(r"\s*\(recommended\)\s*$", re.IGNORECASE)
OPEN_OPTION_PREFIX = re.compile(
    r"^\s*(?:[a-d][.)]\s*)?other\b",
    re.IGNORECASE,
)


def _is_open_option(value: str) -> bool:
    """Recognize an explicit leading Other choice, never the word 'another'."""

    return OPEN_OPTION_PREFIX.search(value) is not None


def normalize_sra_payload(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Enforce the predeclared three-closed-plus-open option presentation."""

    normalized = dict(payload)
    options = [str(value).strip() for value in payload.get("options", [])]
    changes: list[str] = []
    cleaned = [RECOMMENDED_SUFFIX.sub("", option).strip() for option in options]
    if cleaned != options:
        changes.append("removed_recommended_labels")
    if len(cleaned) == 3 and not any(_is_open_option(value) for value in cleaned):
        cleaned.append("Other—please specify")
        changes.append("appended_fixed_open_option")
    open_indexes = [
        index for index, value in enumerate(cleaned) if _is_open_option(value)
    ]
    if len(cleaned) == 4 and len(open_indexes) == 1 and open_indexes[0] != 3:
        open_option = cleaned.pop(open_indexes[0])
        cleaned.append(open_option)
        changes.append("moved_open_option_to_fourth")
    if len(cleaned) == 4 and not open_indexes:
        cleaned[-1] = "Other—please specify"
        changes.append("replaced_extra_fourth_with_fixed_open_option")
    elif (
        len(cleaned) == 4
        and open_indexes == [3]
        and cleaned[3] != "Other—please specify"
    ):
        cleaned[3] = "Other—please specify"
        changes.append("canonicalized_open_option")
    normalized["options"] = cleaned
    return normalized, tuple(changes)


def normalize_simulator_answer(value: str) -> tuple[str, tuple[str, ...]]:
    """Keep evaluator framing and complete SQL out of the visible user reply."""

    answer = value.strip()
    if not answer:
        raise ValueError("User simulator returned an empty answer")
    normalized, replacements = SIMULATOR_EVALUATOR_LABEL.subn(
        "the intended request",
        answer,
    )
    changes: list[str] = []
    if replacements:
        changes.append("neutralized_evaluator_label")
    if SIMULATOR_SQL_SHAPED_REPLY.search(normalized):
        raise ValueError("User simulator exposed a SQL-shaped answer")
    return normalized, tuple(changes)


class StructuredInvoker(Protocol):
    model: str
    reasoning_effort: str

    def run(
        self,
        prompt: str,
        *,
        output_schema: dict[str, Any],
        artifact_directory: Path | None = None,
        case_id: str = "case",
    ) -> Any: ...


@dataclass(frozen=True)
class BirdDevMatchedConfig:
    max_user_turns: int = 4
    max_normal_sql_attempts: int = 5
    repair_invalid_sql_once: bool = True
    sql_timeout_seconds: float = 120.0
    max_result_rows: int = 500_000

    def validate(self) -> None:
        if self.max_user_turns != 4:
            raise ValueError("The matched protocol is frozen at four user turns")
        if self.max_normal_sql_attempts != 5:
            raise ValueError("The matched protocol is frozen at five normal SQLs")
        if self.sql_timeout_seconds <= 0 or self.max_result_rows < 1:
            raise ValueError("SQL limits must be positive")


@dataclass
class _ProactiveCheckpoint:
    """State shared by proactive-only and hybrid after the pre-SQL decision."""

    decision: str
    events: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    question_answers: list[tuple[str, str]]
    active_sql: str
    normal_sql_attempts: int
    uses_shared_prefix: bool


def load_original_bird_rows(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError(f"Expected a JSON array of BIRD rows: {path}")
    required = {"question_id", "db_id", "question", "difficulty", "SQL"}
    for index, row in enumerate(payload):
        missing = sorted(required - set(row))
        if missing:
            raise ValueError(f"BIRD row {index} is missing {missing}")
    ids = [int(row["question_id"]) for row in payload]
    if len(ids) != len(set(ids)):
        raise ValueError("BIRD question IDs are not unique")
    return payload


def load_original_bird_tasks(path: Path) -> list[BenchmarkTask]:
    return [
        BenchmarkTask(
            question_id=int(row["question_id"]),
            db_id=str(row["db_id"]),
            question=str(row["question"]),
            evidence=None,
            difficulty=str(row["difficulty"]),
        )
        for row in load_original_bird_rows(path)
    ]


def database_path(database_root: Path, task: BenchmarkTask) -> Path:
    return database_root.resolve() / task.db_id / f"{task.db_id}.sqlite"


def build_schema_only_context(path: Path) -> str:
    """Build deterministic CREATE TABLE context without values or descriptions."""

    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    with closing(
        sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
    ) as connection:
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        ).fetchall()
    statements = [str(sql).strip() for _, sql in rows if sql]
    if not statements:
        raise ValueError(f"No user tables found in {path}")
    return "\n\n".join(statements)


def clean_generated_sql(value: str) -> str:
    sql = SQL_FENCE.sub("", value.strip()).strip()
    if sql.casefold().startswith("sql:"):
        sql = sql[4:].strip()
    match = re.search(r"\b(SELECT|WITH)\b", sql, flags=re.IGNORECASE)
    if match and match.start() > 0:
        sql = sql[match.start() :]
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise ValueError("Model returned empty SQL")
    return sql


def _sph_row(row: tuple[Any, ...]) -> tuple[Any, ...]:
    """Mirror Sphinteract's column-order-independent row normalization."""

    return tuple(sorted(list(row), key=lambda value: hash(value)))


def sphinteract_execution_equal(
    predicted: QueryResult,
    gold: QueryResult,
    *,
    gold_sql: str,
) -> bool:
    """Compatibility implementation of the released notebook's ``evalfunc``."""

    if predicted.observation.status != "ok" or gold.observation.status != "ok":
        return False
    if predicted.observation.truncated or gold.observation.truncated:
        return False
    if len(predicted.rows) != len(gold.rows):
        return False
    predicted_rows = [_sph_row(row) for row in predicted.rows]
    gold_rows = [_sph_row(row) for row in gold.rows]
    if "ORDER BY" in gold_sql:
        return predicted_rows == gold_rows
    return set(predicted_rows) == set(gold_rows)


def render_clarification(payload: Mapping[str, Any]) -> str:
    question = str(payload.get("question", "")).strip()
    options = [str(value).strip() for value in payload.get("options", [])]
    if not question:
        raise ValueError("Clarification action has an empty question")
    if len(options) != 4 or any(not option for option in options):
        raise ValueError("Clarification must contain exactly four non-empty options")
    open_indexes = [
        index for index, option in enumerate(options) if _is_open_option(option)
    ]
    if open_indexes != [3]:
        raise ValueError("Clarification must contain one open Other option in fourth")
    labels = "ABCD"
    return (
        question
        + "\n"
        + "\n".join(
            f"{labels[index]}. {option}" for index, option in enumerate(options)
        )
    )


def _format_previous_answers(
    question_answers: Sequence[tuple[str, str]],
    *,
    title_case: bool,
) -> str:
    if not question_answers:
        return "no previous clarification questions.\n"
    if title_case:
        return "".join(
            f"Multiple Choice Clarification Question: {question}\nAnswer: {answer}\n"
            for question, answer in question_answers
        )
    return "".join(
        f"multiple choice clarification question: {question}\nuser: {answer}\n"
        for question, answer in question_answers
    )


def _format_sql_feedback(question_answers: Sequence[tuple[str, str]]) -> str:
    if not question_answers:
        return "no previous clarification questions are asked.\n"
    return "".join(
        f"multiple choice clarification question: {question}\nuser: {answer}\n"
        for question, answer in question_answers
    )


def _usage_dict(usage: Usage) -> dict[str, Any]:
    return asdict(usage)


def _sum_usage(events: Sequence[Mapping[str, Any]]) -> dict[str, int | float]:
    keys = tuple(Usage.__dataclass_fields__)
    totals: dict[str, int | float] = {key: 0 for key in keys}
    for event in events:
        usage = event.get("usage")
        if not isinstance(usage, Mapping):
            continue
        for key in keys:
            totals[key] += usage.get(key, 0)
    totals["estimated_cost_usd"] = round(float(totals["estimated_cost_usd"]), 8)
    return totals


class BirdDevPrivateEvaluator:
    """Gold-private official-BIRD and Sph-compatible scorer."""

    def __init__(
        self,
        *,
        rows: Sequence[dict[str, Any]],
        database_root: Path,
        timeout_seconds: float,
        max_rows: int,
    ) -> None:
        self.rows = {int(row["question_id"]): row for row in rows}
        if len(self.rows) != len(rows):
            raise ValueError("BIRD question IDs are not unique")
        self.database_root = database_root.resolve()
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self._gold_cache: dict[int, QueryResult] = {}
        self._lock = threading.Lock()

    def private_row(self, task: BenchmarkTask) -> dict[str, Any]:
        try:
            row = self.rows[int(task.question_id)]
        except (KeyError, TypeError, ValueError) as exc:
            raise KeyError(f"No private BIRD row for {task.question_id}") from exc
        if str(row["db_id"]) != task.db_id:
            raise ValueError(f"Database mismatch for BIRD task {task.question_id}")
        return row

    def _environment(self, task: BenchmarkTask) -> SQLiteEnvironment:
        return SQLiteEnvironment(
            database_path(self.database_root, task),
            timeout_seconds=self.timeout_seconds,
            max_rows=self.max_rows,
            preview_rows=0,
        )

    def gold_result(self, task: BenchmarkTask) -> QueryResult:
        question_id = int(task.question_id)
        with self._lock:
            cached = self._gold_cache.get(question_id)
        if cached is not None:
            return cached
        row = self.private_row(task)
        result = self._environment(task).execute(str(row["SQL"]))
        with self._lock:
            self._gold_cache.setdefault(question_id, result)
            return self._gold_cache[question_id]

    def evaluate(
        self, task: BenchmarkTask, sql: str
    ) -> tuple[dict[str, Any], QueryResult]:
        result = self._environment(task).execute(sql)
        row = self.private_row(task)
        gold = self.gold_result(task)
        official = bird_execution_equal(result, gold)
        sph_compatible = sphinteract_execution_equal(
            result,
            gold,
            gold_sql=str(row["SQL"]),
        )
        observation = result.observation
        return (
            {
                "sql": sql,
                "status": observation.status,
                "error": observation.error,
                "row_count": observation.row_count,
                "truncated": observation.truncated,
                "elapsed_ms": observation.elapsed_ms,
                "fingerprints": dict(observation.fingerprints),
                "official_ex": official,
                "sph_compatible_ex": sph_compatible,
                "scorers_disagree": official != sph_compatible,
            },
            result,
        )


class BirdDevMatchedController:
    """Execute one BIRD task through the five frozen matched arms."""

    def __init__(
        self,
        *,
        policy_invoker: StructuredInvoker,
        simulator_invoker: StructuredInvoker,
        evaluator: BirdDevPrivateEvaluator,
        prompts: SphinteractPrompts,
        database_root: Path,
        artifact_root: Path,
        config: BirdDevMatchedConfig | None = None,
    ) -> None:
        self.policy_invoker = policy_invoker
        self.simulator_invoker = simulator_invoker
        self.evaluator = evaluator
        self.prompts = prompts
        self.database_root = database_root.resolve()
        self.artifact_root = artifact_root.resolve()
        self.config = config or BirdDevMatchedConfig()
        self.config.validate()
        self._call_index = 0
        self._event_ledger: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._candidate_ledger: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._answer_ledger: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def _invoke(
        self,
        *,
        task: BenchmarkTask,
        arm: str,
        role: str,
        call_kind: str,
        prompt: str,
        output_schema: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        self._call_index += 1
        invoker = self.policy_invoker if role == "policy" else self.simulator_invoker
        private_segment = "simulator-private" if role == "simulator" else "policy"
        result = invoker.run(
            prompt,
            output_schema=output_schema,
            artifact_directory=(
                self.artifact_root / private_segment / str(task.question_id) / arm
            ),
            case_id=(f"bird-{task.question_id}-{arm}-{call_kind}-{self._call_index}"),
        )
        if not isinstance(result.output, dict):
            raise TypeError(f"{call_kind} output is not an object")
        event = {
            "kind": "model_call",
            "role": role,
            "call_kind": call_kind,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "output": dict(result.output),
            "usage": _usage_dict(result.usage),
            "elapsed_ms": result.elapsed_ms,
            "transport_attempts": result.transport_attempts,
            "served_model": result.served_model,
        }
        key = (str(task.question_id), arm)
        self._event_ledger.setdefault(key, []).append(event)
        return dict(result.output), event

    @staticmethod
    def _sql_adapter_suffix() -> str:
        return (
            "\n\n/* Structured adapter: return one JSON object with the complete "
            "SQLite query, including SELECT or WITH, in the `sql` field. */"
        )

    @staticmethod
    def _sra_adapter_suffix(*, allow_no_ambiguity: bool) -> str:
        stop = (
            " Return decision `no_ambiguity` only when the source prompt permits "
            "NO AMBIGUITY; for schema compatibility, fill its four unused option "
            "slots with the text `Not used—NO AMBIGUITY`."
            if allow_no_ambiguity
            else " Decision must be `ask`; early stopping is not permitted."
        )
        return (
            "\n\n/* Structured adapter: return summary; separate reviews for "
            "AmbQuestion, AmbTableColumn, AmbOutput, and AmbValue; and either "
            "one concise question with exactly three closed options plus one "
            "open Other option, or the permitted stop decision. Never mark, "
            "recommend, rank, or hint that any option is preferred." + stop + " */"
        )

    def _initial_sql_prompt(self, *, schema: str, task: BenchmarkTask) -> str:
        return (
            self.prompts.sql_initial.format(
                schema=schema,
                metadata="",
                question=task.question,
            )
            + self._sql_adapter_suffix()
        )

    def _repair_sql_prompt(
        self,
        *,
        schema: str,
        sql: str,
        error: str | None,
    ) -> str:
        return (
            self.prompts.sql_invalid_repair.format(
                schema=schema,
                invalidSQL=sql,
                ex=error or "Query was not an executable read-only SQLite query.",
            )
            + self._sql_adapter_suffix()
        )

    def _sql_after_feedback_prompt(
        self,
        *,
        schema: str,
        task: BenchmarkTask,
        incorrect_sqls: Sequence[str],
        question_answers: Sequence[tuple[str, str]],
    ) -> str:
        return (
            self.prompts.sql_after_feedback.format(
                schema=schema,
                sqls="\n".join(incorrect_sqls) or "(No previous SQL.)",
                cqas=_format_sql_feedback(question_answers),
                metadata="",
                question=task.question,
            )
            + self._sql_adapter_suffix()
        )

    def _self_debug_sql_prompt(
        self,
        *,
        schema: str,
        task: BenchmarkTask,
        incorrect_sqls: Sequence[str],
        question_answers: Sequence[tuple[str, str]],
    ) -> str:
        clarification_context = (
            "/* Clarification already received: */\n"
            + _format_sql_feedback(question_answers)
            if question_answers
            else ""
        )
        return (
            self.prompts.sql_self_debug.format(
                schema=schema,
                sqls=",\n".join(incorrect_sqls),
                metadata=clarification_context,
                question=task.question,
            )
            + self._sql_adapter_suffix()
        )

    def _reactive_sra_prompt(
        self,
        *,
        schema: str,
        task: BenchmarkTask,
        incorrect_sqls: Sequence[str],
        question_answers: Sequence[tuple[str, str]],
        allow_no_ambiguity: bool,
    ) -> str:
        template = (
            self.prompts.clarification_early_stop
            if allow_no_ambiguity
            else self.prompts.clarification
        )
        return (
            self.prompts.clarification_prefix
            + template.format(
                schema=schema,
                question=task.question,
                sqls=",\n".join(incorrect_sqls),
                cqs=_format_previous_answers(
                    question_answers,
                    title_case=allow_no_ambiguity,
                ),
            )
            + self._sra_adapter_suffix(allow_no_ambiguity=allow_no_ambiguity)
        )

    def _proactive_sra_prompt(self, *, schema: str, task: BenchmarkTask) -> str:
        return (
            self.prompts.clarification_prefix
            + f"""/* Before writing SQL, decide whether one clarification would resolve a material ambiguity in this question: */
{task.question}
/* Given the following database schema: */
{schema}

/* Review AmbQuestion, AmbTableColumn, AmbOutput, and AmbValue separately. If no material user-intent ambiguity remains, choose NO AMBIGUITY. Otherwise ask one easy-to-understand multiple-choice clarification with three closed options and one open Other option. Do not ask about facts that the database itself can answer. */
"""
            + self._sra_adapter_suffix(allow_no_ambiguity=True)
        )

    def _invoke_sra(
        self,
        *,
        task: BenchmarkTask,
        arm: str,
        call_kind: str,
        prompt: str,
        allow_no_ambiguity: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        raw, event = self._invoke(
            task=task,
            arm=arm,
            role="policy",
            call_kind=call_kind,
            prompt=prompt,
            output_schema=sra_output_schema(allow_no_ambiguity=allow_no_ambiguity),
        )
        normalized, changes = normalize_sra_payload(raw)
        event["normalized_output"] = normalized
        event["normalization_changes"] = list(changes)
        return normalized, event

    def _answer_question(
        self,
        *,
        task: BenchmarkTask,
        arm: str,
        rendered_question: str,
        question_answers: Sequence[tuple[str, str]],
    ) -> tuple[str, dict[str, Any]]:
        row = self.evaluator.private_row(task)
        prompt = (
            self.prompts.feedback_prefix
            + self.prompts.feedback.format(
                query=str(row["SQL"]),
                question=rendered_question,
                nlq=task.question,
            )
            + "\n\n/* Structured adapter: return only the intended natural-language "
            "reply in the `answer` field. Never expose the gold SQL. */"
        )
        payload, event = self._invoke(
            task=task,
            arm=arm,
            role="simulator",
            call_kind=f"user_answer_{len(question_answers) + 1}",
            prompt=prompt,
            output_schema=ANSWER_OUTPUT_SCHEMA,
        )
        answer, changes = normalize_simulator_answer(str(payload.get("answer", "")))
        if changes:
            event["answer_normalization_changes"] = list(changes)
        event["output"] = {"answer": answer}
        key = (str(task.question_id), arm)
        self._answer_ledger.setdefault(key, []).append((rendered_question, answer))
        return answer, event

    def _generate_and_score(
        self,
        *,
        task: BenchmarkTask,
        arm: str,
        schema: str,
        prompt: str,
        call_kind: str,
        user_turn: int,
        allow_repair: bool,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
        payload, call_event = self._invoke(
            task=task,
            arm=arm,
            role="policy",
            call_kind=call_kind,
            prompt=prompt,
            output_schema=SQL_OUTPUT_SCHEMA,
        )
        sql = clean_generated_sql(str(payload.get("sql", "")))
        call_event["output"] = {"sql": sql}
        candidate, _ = self.evaluator.evaluate(task, sql)
        candidate.update(
            {
                "user_turn": user_turn,
                "candidate_kind": call_kind,
            }
        )
        events = [call_event]
        candidates = [candidate]
        key = (str(task.question_id), arm)
        self._candidate_ledger.setdefault(key, []).append(candidate)
        active_sql = sql
        if (
            allow_repair
            and self.config.repair_invalid_sql_once
            and candidate["status"] != "ok"
        ):
            repair_payload, repair_event = self._invoke(
                task=task,
                arm=arm,
                role="policy",
                call_kind=f"{call_kind}_invalid_repair",
                prompt=self._repair_sql_prompt(
                    schema=schema,
                    sql=sql,
                    error=candidate.get("error"),
                ),
                output_schema=SQL_OUTPUT_SCHEMA,
            )
            repaired_sql = clean_generated_sql(str(repair_payload.get("sql", "")))
            repair_event["output"] = {"sql": repaired_sql}
            repaired, _ = self.evaluator.evaluate(task, repaired_sql)
            repaired.update(
                {
                    "user_turn": user_turn,
                    "candidate_kind": f"{call_kind}_invalid_repair",
                }
            )
            events.append(repair_event)
            candidates.append(repaired)
            self._candidate_ledger.setdefault(key, []).append(repaired)
            active_sql = repaired_sql
        return events, candidates, active_sql

    def run_shared_initial(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        events, candidates, active_sql = self._generate_and_score(
            task=task,
            arm="shared_initial",
            schema=schema,
            prompt=self._initial_sql_prompt(schema=schema, task=task),
            call_kind="initial_sql",
            user_turn=0,
            allow_repair=True,
        )
        active = candidates[-1]
        return {
            "protocol": PROTOCOL_VERSION,
            "task_id": task.question_id,
            "events": events,
            "candidates": candidates,
            "active_sql": active_sql,
            "official_ex": bool(active["official_ex"]),
            "sph_compatible_ex": bool(active["sph_compatible_ex"]),
            "usage": _sum_usage(events),
            "model_calls": sum(
                int(event.get("transport_attempts", 1))
                for event in events
                if event.get("kind") == "model_call"
            ),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }

    @staticmethod
    def _success_turn(
        candidates: Sequence[Mapping[str, Any]], scorer: str
    ) -> int | None:
        turns = [
            int(candidate["user_turn"])
            for candidate in candidates
            if bool(candidate[scorer])
        ]
        return min(turns) if turns else None

    def _finish_arm(
        self,
        *,
        task: BenchmarkTask,
        arm: str,
        started: float,
        events: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        question_answers: list[tuple[str, str]],
        termination: str,
        uses_shared_prefix: bool,
        normal_sql_attempts: int,
    ) -> dict[str, Any]:
        final_sql = candidates[-1]["sql"] if candidates else None
        official_turn = self._success_turn(candidates, "official_ex")
        sph_turn = self._success_turn(candidates, "sph_compatible_ex")
        return {
            "protocol": PROTOCOL_VERSION,
            "task_id": task.question_id,
            "arm": arm,
            "uses_shared_prefix": uses_shared_prefix,
            "events": events,
            "candidates": candidates,
            "question_answers": [
                {"question": question, "answer": answer}
                for question, answer in question_answers
            ],
            "termination": termination,
            "final_sql": final_sql,
            "normal_sql_attempts": normal_sql_attempts,
            "user_turns": len(question_answers),
            "official_success": official_turn is not None,
            "official_first_success_turn": official_turn,
            "sph_compatible_success": sph_turn is not None,
            "sph_compatible_first_success_turn": sph_turn,
            "arm_specific_usage": _sum_usage(events),
            "arm_specific_model_calls": sum(
                int(event.get("transport_attempts", 1))
                for event in events
                if event.get("kind") == "model_call"
            ),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }

    def no_user_record(
        self,
        task: BenchmarkTask,
        shared: Mapping[str, Any],
    ) -> dict[str, Any]:
        candidates = [dict(value) for value in shared["candidates"]]
        termination = (
            "accepted_initial"
            if any(candidate["official_ex"] for candidate in candidates)
            else "initial_rejected"
        )
        return self._finish_arm(
            task=task,
            arm="no_user",
            started=time.monotonic(),
            events=[],
            candidates=candidates,
            question_answers=[],
            termination=termination,
            uses_shared_prefix=True,
            normal_sql_attempts=1,
        )

    @staticmethod
    def _append_unique(values: list[str], value: str) -> None:
        if value not in values:
            values.append(value)

    def _run_proactive_checkpoint(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        shared: Mapping[str, Any],
        arm: str,
    ) -> _ProactiveCheckpoint:
        """Run the identical pre-SQL checkpoint used by both proactive arms."""

        events: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        question_answers: list[tuple[str, str]] = []
        payload, proactive_event = self._invoke_sra(
            task=task,
            arm=arm,
            call_kind="proactive_sra",
            prompt=self._proactive_sra_prompt(schema=schema, task=task),
            allow_no_ambiguity=True,
        )
        events.append(proactive_event)
        decision = str(payload.get("decision", ""))
        if decision == "no_ambiguity":
            candidates.extend(dict(value) for value in shared["candidates"])
            return _ProactiveCheckpoint(
                decision=decision,
                events=events,
                candidates=candidates,
                question_answers=question_answers,
                active_sql=str(shared["active_sql"]),
                normal_sql_attempts=1,
                uses_shared_prefix=True,
            )
        if decision != "ask":
            raise ValueError(f"Invalid proactive SRA decision: {decision}")

        rendered = render_clarification(payload)
        answer, answer_event = self._answer_question(
            task=task,
            arm=arm,
            rendered_question=rendered,
            question_answers=question_answers,
        )
        events.append(answer_event)
        question_answers.append((rendered, answer))
        generation_events, generated, active_sql = self._generate_and_score(
            task=task,
            arm=arm,
            schema=schema,
            prompt=self._sql_after_feedback_prompt(
                schema=schema,
                task=task,
                incorrect_sqls=(),
                question_answers=question_answers,
            ),
            call_kind="sql_after_proactive_answer",
            user_turn=1,
            allow_repair=True,
        )
        events.extend(generation_events)
        candidates.extend(generated)
        return _ProactiveCheckpoint(
            decision=decision,
            events=events,
            candidates=candidates,
            question_answers=question_answers,
            active_sql=active_sql,
            normal_sql_attempts=1,
            uses_shared_prefix=False,
        )

    def _run_reactive_loop(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        arm: str,
        started: float,
        events: list[dict[str, Any]],
        candidates: list[dict[str, Any]],
        question_answers: list[tuple[str, str]],
        incorrect_sqls: list[str],
        normal_attempts: int,
        uses_shared_prefix: bool,
        allow_no_ambiguity: bool,
    ) -> dict[str, Any]:
        """Apply one shared post-rejection CQ loop to reactive and hybrid arms."""

        while (
            len(question_answers) < self.config.max_user_turns
            and normal_attempts < self.config.max_normal_sql_attempts
        ):
            payload, sra_event = self._invoke_sra(
                task=task,
                arm=arm,
                call_kind=f"reactive_sra_{len(question_answers) + 1}",
                prompt=self._reactive_sra_prompt(
                    schema=schema,
                    task=task,
                    incorrect_sqls=incorrect_sqls,
                    question_answers=question_answers,
                    allow_no_ambiguity=allow_no_ambiguity,
                ),
                allow_no_ambiguity=allow_no_ambiguity,
            )
            events.append(sra_event)
            decision = str(payload.get("decision", ""))
            if decision == "no_ambiguity":
                if not allow_no_ambiguity:
                    raise ValueError("CQs cannot emit no_ambiguity")
                return self._finish_arm(
                    task=task,
                    arm=arm,
                    started=started,
                    events=events,
                    candidates=candidates,
                    question_answers=question_answers,
                    termination="no_ambiguity",
                    uses_shared_prefix=uses_shared_prefix,
                    normal_sql_attempts=normal_attempts,
                )
            if decision != "ask":
                raise ValueError(f"Invalid SRA decision: {decision}")
            rendered = render_clarification(payload)
            answer, answer_event = self._answer_question(
                task=task,
                arm=arm,
                rendered_question=rendered,
                question_answers=question_answers,
            )
            events.append(answer_event)
            question_answers.append((rendered, answer))
            generation_events, generated, active_sql = self._generate_and_score(
                task=task,
                arm=arm,
                schema=schema,
                prompt=self._sql_after_feedback_prompt(
                    schema=schema,
                    task=task,
                    incorrect_sqls=incorrect_sqls,
                    question_answers=question_answers,
                ),
                call_kind=f"sql_after_answer_{len(question_answers)}",
                user_turn=len(question_answers),
                allow_repair=True,
            )
            normal_attempts += 1
            events.extend(generation_events)
            candidates.extend(generated)
            if any(candidate["official_ex"] for candidate in generated):
                return self._finish_arm(
                    task=task,
                    arm=arm,
                    started=started,
                    events=events,
                    candidates=candidates,
                    question_answers=question_answers,
                    termination="accepted_after_clarification",
                    uses_shared_prefix=uses_shared_prefix,
                    normal_sql_attempts=normal_attempts,
                )
            self._append_unique(incorrect_sqls, active_sql)

        return self._finish_arm(
            task=task,
            arm=arm,
            started=started,
            events=events,
            candidates=candidates,
            question_answers=question_answers,
            termination="interaction_limit_reached",
            uses_shared_prefix=uses_shared_prefix,
            normal_sql_attempts=normal_attempts,
        )

    def run_reactive_arm(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        shared: Mapping[str, Any],
        arm: str,
    ) -> dict[str, Any]:
        if arm not in {"sph_cq", "sph_cq_es"}:
            raise ValueError(f"Not a reactive arm: {arm}")
        started = time.monotonic()
        events: list[dict[str, Any]] = []
        candidates = [dict(value) for value in shared["candidates"]]
        question_answers: list[tuple[str, str]] = []
        active_sql = str(shared["active_sql"])
        incorrect_sqls: list[str] = []
        self._append_unique(incorrect_sqls, active_sql)
        normal_attempts = 1
        if any(candidate["official_ex"] for candidate in candidates):
            return self._finish_arm(
                task=task,
                arm=arm,
                started=started,
                events=events,
                candidates=candidates,
                question_answers=question_answers,
                termination="accepted_initial",
                uses_shared_prefix=True,
                normal_sql_attempts=normal_attempts,
            )

        return self._run_reactive_loop(
            task,
            schema=schema,
            arm=arm,
            started=started,
            events=events,
            candidates=candidates,
            question_answers=question_answers,
            incorrect_sqls=incorrect_sqls,
            normal_attempts=normal_attempts,
            uses_shared_prefix=True,
            allow_no_ambiguity=arm == "sph_cq_es",
        )

    def run_proactive_only(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        shared: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Ask at most once before SQL, then use SQL-only self-debug retries."""

        arm = "proactive_only"
        started = time.monotonic()
        state = self._run_proactive_checkpoint(
            task,
            schema=schema,
            shared=shared,
            arm=arm,
        )
        if any(candidate["official_ex"] for candidate in state.candidates):
            termination = (
                "accepted_initial"
                if state.decision == "no_ambiguity"
                else "accepted_after_proactive_clarification"
            )
            return self._finish_arm(
                task=task,
                arm=arm,
                started=started,
                events=state.events,
                candidates=state.candidates,
                question_answers=state.question_answers,
                termination=termination,
                uses_shared_prefix=state.uses_shared_prefix,
                normal_sql_attempts=state.normal_sql_attempts,
            )

        incorrect_sqls: list[str] = []
        self._append_unique(incorrect_sqls, state.active_sql)
        while state.normal_sql_attempts < self.config.max_normal_sql_attempts:
            generation_events, generated, active_sql = self._generate_and_score(
                task=task,
                arm=arm,
                schema=schema,
                prompt=self._self_debug_sql_prompt(
                    schema=schema,
                    task=task,
                    incorrect_sqls=incorrect_sqls,
                    question_answers=state.question_answers,
                ),
                call_kind=f"sql_self_debug_{state.normal_sql_attempts + 1}",
                user_turn=len(state.question_answers),
                allow_repair=True,
            )
            state.normal_sql_attempts += 1
            state.events.extend(generation_events)
            state.candidates.extend(generated)
            if any(candidate["official_ex"] for candidate in generated):
                return self._finish_arm(
                    task=task,
                    arm=arm,
                    started=started,
                    events=state.events,
                    candidates=state.candidates,
                    question_answers=state.question_answers,
                    termination="accepted_after_self_debug",
                    uses_shared_prefix=state.uses_shared_prefix,
                    normal_sql_attempts=state.normal_sql_attempts,
                )
            self._append_unique(incorrect_sqls, active_sql)

        return self._finish_arm(
            task=task,
            arm=arm,
            started=started,
            events=state.events,
            candidates=state.candidates,
            question_answers=state.question_answers,
            termination="sql_attempt_limit_reached",
            uses_shared_prefix=state.uses_shared_prefix,
            normal_sql_attempts=state.normal_sql_attempts,
        )

    def run_project_hybrid(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        shared: Mapping[str, Any],
    ) -> dict[str, Any]:
        arm = "project_hybrid"
        started = time.monotonic()
        state = self._run_proactive_checkpoint(
            task,
            schema=schema,
            shared=shared,
            arm=arm,
        )
        if any(candidate["official_ex"] for candidate in state.candidates):
            return self._finish_arm(
                task=task,
                arm=arm,
                started=started,
                events=state.events,
                candidates=state.candidates,
                question_answers=state.question_answers,
                termination="accepted_after_proactive_checkpoint",
                uses_shared_prefix=state.uses_shared_prefix,
                normal_sql_attempts=state.normal_sql_attempts,
            )
        incorrect_sqls: list[str] = []
        self._append_unique(incorrect_sqls, state.active_sql)
        return self._run_reactive_loop(
            task,
            schema=schema,
            arm=arm,
            started=started,
            events=state.events,
            candidates=state.candidates,
            question_answers=state.question_answers,
            incorrect_sqls=incorrect_sqls,
            normal_attempts=state.normal_sql_attempts,
            uses_shared_prefix=state.uses_shared_prefix,
            allow_no_ambiguity=False,
        )

    def run_task(self, task: BenchmarkTask) -> dict[str, Any]:
        started = time.monotonic()
        schema = build_schema_only_context(database_path(self.database_root, task))
        shared = self.run_shared_initial(task, schema=schema)
        arms: dict[str, dict[str, Any]] = {"no_user": self.no_user_record(task, shared)}
        try:
            arms["proactive_only"] = self.run_proactive_only(
                task,
                schema=schema,
                shared=shared,
            )
        except Exception as exc:  # noqa: BLE001 - retain arm failures.
            arms["proactive_only"] = self._arm_error(
                task,
                "proactive_only",
                exc,
                uses_shared=False,
            )
        for arm in ("sph_cq",):
            try:
                arms[arm] = self.run_reactive_arm(
                    task,
                    schema=schema,
                    shared=shared,
                    arm=arm,
                )
            except Exception as exc:  # noqa: BLE001 - retain arm failures.
                arms[arm] = self._arm_error(task, arm, exc, uses_shared=True)
        try:
            arms["project_hybrid"] = self.run_project_hybrid(
                task,
                schema=schema,
                shared=shared,
            )
        except Exception as exc:  # noqa: BLE001 - retain arm failures.
            arms["project_hybrid"] = self._arm_error(
                task,
                "project_hybrid",
                exc,
                uses_shared=False,
            )
        try:
            arms["sph_cq_es"] = self.run_reactive_arm(
                task,
                schema=schema,
                shared=shared,
                arm="sph_cq_es",
            )
        except Exception as exc:  # noqa: BLE001 - retain arm failures.
            arms["sph_cq_es"] = self._arm_error(
                task,
                "sph_cq_es",
                exc,
                uses_shared=True,
            )
        return {
            "schema_version": 1,
            "protocol": PROTOCOL_VERSION,
            "task": {
                "question_id": task.question_id,
                "db_id": task.db_id,
                "question": task.question,
                "difficulty": task.difficulty,
                "evidence_visible": False,
            },
            "schema_sha256": hashlib.sha256(schema.encode("utf-8")).hexdigest(),
            "shared_initial": shared,
            "arms": arms,
            "total_elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }

    def _arm_error(
        self,
        task: BenchmarkTask,
        arm: str,
        exc: Exception,
        *,
        uses_shared: bool,
    ) -> dict[str, Any]:
        key = (str(task.question_id), arm)
        events = list(self._event_ledger.get(key, []))
        candidates = list(self._candidate_ledger.get(key, []))
        question_answers = list(self._answer_ledger.get(key, []))
        return {
            "protocol": PROTOCOL_VERSION,
            "task_id": task.question_id,
            "arm": arm,
            "uses_shared_prefix": uses_shared,
            "events": events,
            "candidates": candidates,
            "question_answers": [
                {"question": question, "answer": answer}
                for question, answer in question_answers
            ],
            "termination": "arm_error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "final_sql": candidates[-1]["sql"] if candidates else None,
            "normal_sql_attempts": 0,
            "user_turns": len(question_answers),
            "official_success": False,
            "official_first_success_turn": None,
            "sph_compatible_success": False,
            "sph_compatible_first_success_turn": None,
            "arm_specific_usage": _sum_usage(events),
            "arm_specific_model_calls": sum(
                int(event.get("transport_attempts", 1))
                for event in events
                if event.get("kind") == "model_call"
            ),
            "elapsed_ms": 0.0,
        }
