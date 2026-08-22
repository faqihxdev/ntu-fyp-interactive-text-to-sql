"""Matched multi-round interaction controller with an optional progress guard.

The controller is the policy seam. It owns transitions, budgets, execution,
history, and trajectory logging; model and user/evaluator behavior are injected
as adapters. Hidden gold information stays behind the oracle interface.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .context import ContextBuilder
from .recovery import (
    RECOVERY_MODES,
    FeedbackRecovery,
    RecoveryActionView,
)
from .sqlite_env import QueryResult, SQLiteEnvironment
from .trajectory import BenchmarkTask, Trajectory, Usage

POLICIES = {"no_user", "proactive", "reactive", "hybrid"}
ACTION_KINDS = {"query_database", "submit_sql", "ask_user", "stop"}
REVIEW_SOURCES = {"AmbQuestion", "AmbColumn", "AmbOutput", "AmbValue", "Other"}
SQL_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class InteractionConfig:
    policy: str = "reactive"
    max_user_turns: int = 4
    max_model_calls: int = 20
    max_database_probes: int = 4
    max_submission_repairs: int = 3
    sql_timeout_seconds: float = 30.0
    max_result_rows: int = 500_000
    progress_guard: bool = False
    guard_fingerprint: str = "ordered_visible"
    max_guard_retries: int = 1
    guard_retry_fallback: str = "expose"
    recovery_mode: str = "baseline"
    max_recovery_protocol_retries: int = 2
    max_post_turn_recovery_submissions: int = 2

    def validate(self) -> None:
        if self.policy not in POLICIES:
            raise ValueError(f"Unknown interaction policy: {self.policy}")
        if self.max_user_turns < 0:
            raise ValueError("max_user_turns must be non-negative")
        if self.max_model_calls < 1:
            raise ValueError("max_model_calls must be positive")
        if self.max_database_probes < 0 or self.max_submission_repairs < 0:
            raise ValueError("probe and repair budgets must be non-negative")
        if self.max_guard_retries < 0:
            raise ValueError("max_guard_retries must be non-negative")
        if self.guard_fingerprint not in {
            "ordered_visible",
            "visible_multiset",
            "bird_set",
        }:
            raise ValueError(f"Unknown guard fingerprint: {self.guard_fingerprint}")
        if self.guard_retry_fallback not in {"expose", "stop"}:
            raise ValueError("guard_retry_fallback must be expose or stop")
        if self.recovery_mode not in RECOVERY_MODES:
            raise ValueError(f"Unknown recovery mode: {self.recovery_mode}")
        if self.max_recovery_protocol_retries < 0:
            raise ValueError("max_recovery_protocol_retries must be non-negative")
        if self.max_post_turn_recovery_submissions < 0:
            raise ValueError("max_post_turn_recovery_submissions must be non-negative")


@dataclass(frozen=True)
class AgentAction:
    kind: str
    sql: str | None = None
    question: str | None = None
    purpose: str | None = None
    reason: str | None = None
    review_source: str | None = None
    summary: str | None = None
    clarification_target: str | None = None
    grounded_user_turns: tuple[int, ...] | None = None
    change_summary: str | None = None
    request_contract: tuple[str, ...] | None = None

    @classmethod
    def query(cls, sql: str, purpose: str) -> AgentAction:
        return cls(kind="query_database", sql=sql, purpose=purpose)

    @classmethod
    def submit(cls, sql: str) -> AgentAction:
        return cls(kind="submit_sql", sql=sql)

    @classmethod
    def ask(
        cls,
        question: str,
        *,
        review_source: str = "Other",
        summary: str = "Clarification is needed before making progress.",
    ) -> AgentAction:
        return cls(
            kind="ask_user",
            question=question,
            review_source=review_source,
            summary=summary,
        )

    @classmethod
    def stop(cls, reason: str) -> AgentAction:
        return cls(kind="stop", reason=reason)

    def validate(self) -> None:
        if self.kind not in ACTION_KINDS:
            raise ValueError(f"Unknown action kind: {self.kind}")
        if (
            self.kind in {"query_database", "submit_sql"}
            and not (self.sql or "").strip()
        ):
            raise ValueError(f"{self.kind} requires non-empty SQL")
        if self.kind == "query_database" and not (self.purpose or "").strip():
            raise ValueError("query_database requires a purpose")
        if self.kind == "ask_user" and not (self.question or "").strip():
            raise ValueError("ask_user requires a non-empty question")
        if self.kind == "ask_user" and self.review_source not in REVIEW_SOURCES:
            raise ValueError(
                "ask_user requires review_source in "
                + ", ".join(sorted(REVIEW_SOURCES))
            )
        if self.kind == "ask_user" and not (self.summary or "").strip():
            raise ValueError("ask_user requires a non-empty SRA summary")

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


@dataclass(frozen=True)
class DecisionRequest:
    run_id: str
    task: BenchmarkTask
    policy: str
    phase: str
    context: str
    history: tuple[dict[str, Any], ...]
    allowed_actions: tuple[str, ...]
    budgets: dict[str, int]
    recovery: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "run_id": self.run_id,
            "task": asdict(self.task),
            "policy": self.policy,
            "phase": self.phase,
            "context": self.context,
            "history": list(self.history),
            "allowed_actions": list(self.allowed_actions),
            "budgets": self.budgets,
        }
        if self.recovery is not None:
            payload["recovery"] = self.recovery
        return payload


@dataclass(frozen=True)
class PolicyTurn:
    response_id: str
    action: AgentAction
    raw_output: str
    usage: Usage
    elapsed_ms: float
    transport_attempts: int = 1


class PolicyModel(Protocol):
    requested_model: str
    reasoning_effort: str

    def decide(self, request: DecisionRequest) -> PolicyTurn: ...


@dataclass(frozen=True)
class SubmissionJudgement:
    accepted: bool
    feedback: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClarificationAnswer:
    """Evaluator-side answer plus auditable, non-secret simulator metadata."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None
    elapsed_ms: float | None = None


class InteractionOracle(Protocol):
    """Evaluator-side adapter; hidden labels and gold stay behind this seam."""

    def judge(
        self,
        task: BenchmarkTask,
        sql: str,
        result: QueryResult,
    ) -> SubmissionJudgement: ...

    def answer(
        self,
        task: BenchmarkTask,
        question: str,
        history: tuple[dict[str, Any], ...],
    ) -> str | ClarificationAnswer: ...


class TaskContextBuilder(Protocol):
    def build(self, task: BenchmarkTask, database_path: Path) -> Any: ...


def normalize_sql(sql: str) -> str:
    return SQL_SPACE.sub(" ", sql.strip().rstrip(";")).casefold()


def _sparse_result_kind(result: QueryResult) -> str | None:
    if result.observation.row_count == 0:
        return "empty"
    if len(result.rows) != 1 or len(result.rows[0]) != 1:
        return None
    value = result.rows[0][0]
    if value is None:
        return "null"
    if isinstance(value, (int, float)) and value == 0:
        return "zero"
    return None


class InteractionController:
    """Run no-user, proactive, reactive, or hybrid policy through one interface."""

    POLICY_NAME = "matched_interaction_controller_v4"

    def __init__(
        self,
        *,
        model: PolicyModel,
        oracle: InteractionOracle,
        database_root: Path,
        context_builder: TaskContextBuilder | None = None,
        config: InteractionConfig | None = None,
    ) -> None:
        self.model = model
        self.oracle = oracle
        self.database_root = database_root.resolve()
        self.context_builder = context_builder or ContextBuilder()
        self.config = config or InteractionConfig()
        self.config.validate()
        self.recovery = FeedbackRecovery(self.config.recovery_mode)

    def _database_path(self, task: BenchmarkTask) -> Path:
        return self.database_root / task.db_id / f"{task.db_id}.sqlite"

    def _allowed_actions(self, phase: str) -> tuple[str, ...]:
        if phase == "clarify_after_rejection":
            return ("ask_user",)
        if phase in {
            "generate_after_clarification",
            "generate_after_guard",
            "final_recovery_after_rejection",
        }:
            # A user answer or internal repeat warning must be acted on before
            # another interruption. Database probes remain available, but the
            # phase persists until an executable SQL submission is exposed.
            if self.recovery.enabled:
                return ("query_database", "submit_sql")
            return ("query_database", "submit_sql", "stop")
        if self.config.policy == "no_user":
            return ("query_database", "submit_sql", "stop")
        if self.config.policy == "reactive":
            return ("query_database", "submit_sql", "stop")
        if self.config.policy == "proactive" and phase == "initial":
            return ("ask_user",)
        if self.config.policy == "proactive":
            return ("query_database", "submit_sql", "stop")
        if self.recovery.enabled:
            return ("ask_user", "query_database", "submit_sql")
        return ("ask_user", "query_database", "submit_sql", "stop")

    def run(self, task: BenchmarkTask, *, run_id: str | None = None) -> Trajectory:
        started = time.monotonic()
        run_id = run_id or uuid4().hex
        trajectory = Trajectory.start(
            run_id=run_id,
            policy=f"{self.POLICY_NAME}:{self.config.policy}",
            task=task,
            config={
                **asdict(self.config),
                "requested_model": self.model.requested_model,
                "reasoning_effort": self.model.reasoning_effort,
                "gold_visible_to_policy": False,
                "controller_version": self.POLICY_NAME,
            },
        )
        database_path = self._database_path(task)
        try:
            context = self.context_builder.build(task, database_path)
        except Exception as exc:  # noqa: BLE001 - record adapter failures in-trajectory.
            trajectory.add_event(
                kind="system_error",
                actor="system",
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={"stage": "context", "error": repr(exc)},
            )
            trajectory.finish(
                termination="context_error",
                total_elapsed_ms=(time.monotonic() - started) * 1_000,
            )
            return trajectory

        trajectory.add_event(
            kind="context_built",
            actor="system",
            elapsed_ms=(time.monotonic() - started) * 1_000,
            payload={
                "prompt_sha256": str(context.prompt_sha256),
                "schema_sha256": str(context.schema_sha256),
                "description_sha256": str(context.description_sha256),
                "prompt_chars": len(str(context.prompt)),
            },
        )
        environment = SQLiteEnvironment(
            database_path,
            timeout_seconds=self.config.sql_timeout_seconds,
            max_rows=self.config.max_result_rows,
        )

        phase = "initial"
        history: list[dict[str, Any]] = []
        rejected_fingerprints: set[str] = set()
        rejected_sql: set[str] = set()
        model_calls = 0
        user_turns = 0
        database_probes = 0
        submission_repairs = 0
        guard_retries = 0
        recovery_protocol_retries = 0
        post_turn_recovery_submissions = 0
        last_exposed_sql: str | None = None

        while model_calls < self.config.max_model_calls:
            allowed = self._allowed_actions(phase)
            recovery_snapshot = self.recovery.snapshot(tuple(history))
            budgets = {
                "model_calls_remaining": self.config.max_model_calls - model_calls,
                "user_turns_remaining": self.config.max_user_turns - user_turns,
                "database_probes_remaining": (
                    self.config.max_database_probes - database_probes
                ),
                "submission_repairs_remaining": (
                    self.config.max_submission_repairs - submission_repairs
                ),
                "guard_retries_remaining": (
                    self.config.max_guard_retries - guard_retries
                ),
            }
            if self.recovery.enabled:
                budgets.update(
                    {
                        "recovery_protocol_retries_remaining": (
                            self.config.max_recovery_protocol_retries
                            - recovery_protocol_retries
                        ),
                        "post_turn_recovery_submissions_remaining": (
                            self.config.max_post_turn_recovery_submissions
                            - post_turn_recovery_submissions
                        ),
                    }
                )
            request = DecisionRequest(
                run_id=run_id,
                task=task,
                policy=self.config.policy,
                phase=phase,
                context=str(context.prompt),
                history=tuple(history),
                allowed_actions=allowed,
                budgets=budgets,
                recovery=(
                    {
                        "protocol": self.recovery.VERSION,
                        **recovery_snapshot.to_prompt_dict(),
                    }
                    if self.recovery.enabled
                    else None
                ),
            )
            try:
                turn = self.model.decide(request)
                turn.action.validate()
            except Exception as exc:  # noqa: BLE001 - model adapters are external boundaries.
                trajectory.add_event(
                    kind="system_error",
                    actor="model",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={"stage": "decision", "error": repr(exc)},
                )
                trajectory.finish(
                    termination="model_error",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=last_exposed_sql,
                )
                return trajectory

            model_calls += 1
            action = turn.action
            trajectory.add_event(
                kind="policy_decision",
                actor="model",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "response_id": turn.response_id,
                    "phase": phase,
                    "allowed_actions": list(allowed),
                    "action": action.to_dict(),
                    "raw_output": turn.raw_output,
                    "transport_attempts": turn.transport_attempts,
                },
                usage=turn.usage,
            )
            if action.kind not in allowed:
                trajectory.add_event(
                    kind="protocol_error",
                    actor="system",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={"action": action.kind, "allowed_actions": list(allowed)},
                )
                trajectory.finish(
                    termination="model_protocol_error",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=last_exposed_sql,
                )
                return trajectory

            assessment = self.recovery.assess(
                RecoveryActionView(
                    kind=action.kind,
                    sql=action.sql,
                    clarification_target=action.clarification_target,
                    grounded_user_turns=action.grounded_user_turns,
                    change_summary=action.change_summary,
                    request_contract=action.request_contract,
                ),
                recovery_snapshot,
                rejected_sql=rejected_sql,
            )
            if not assessment.valid:
                if (
                    recovery_protocol_retries
                    >= self.config.max_recovery_protocol_retries
                ):
                    trajectory.add_event(
                        kind="recovery_protocol_error",
                        actor="system",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={"violations": list(assessment.violations)},
                    )
                    trajectory.finish(
                        termination="recovery_protocol_retry_budget_exhausted",
                        total_elapsed_ms=(time.monotonic() - started) * 1_000,
                        final_sql=last_exposed_sql,
                    )
                    return trajectory
                recovery_protocol_retries += 1
                record = {
                    "kind": "recovery_protocol_feedback",
                    "violations": list(assessment.violations),
                    "instruction": (
                        "Correct the structured recovery fields and choose the next "
                        "action again. This retry does not consume a user turn."
                    ),
                }
                history.append(record)
                trajectory.add_event(
                    kind="recovery_protocol_retry",
                    actor="system",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={**record, "retry": recovery_protocol_retries},
                )
                continue
            recovery_protocol_retries = 0

            if action.kind == "stop":
                trajectory.finish(
                    termination="model_stop",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=last_exposed_sql,
                )
                return trajectory

            if action.kind == "ask_user":
                if user_turns >= self.config.max_user_turns:
                    trajectory.finish(
                        termination="user_turn_budget_exhausted",
                        total_elapsed_ms=(time.monotonic() - started) * 1_000,
                        final_sql=last_exposed_sql,
                    )
                    return trajectory
                question = str(action.question).strip()
                try:
                    raw_answer = self.oracle.answer(task, question, tuple(history))
                except Exception as exc:  # noqa: BLE001 - simulator adapters vary.
                    trajectory.add_event(
                        kind="system_error",
                        actor="oracle",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={"stage": "answer", "error": repr(exc)},
                    )
                    trajectory.finish(
                        termination="oracle_error",
                        total_elapsed_ms=(time.monotonic() - started) * 1_000,
                        final_sql=last_exposed_sql,
                    )
                    return trajectory
                if isinstance(raw_answer, ClarificationAnswer):
                    answer = raw_answer.text
                    answer_metadata = raw_answer.metadata
                    answer_usage = raw_answer.usage
                    answer_elapsed_ms = raw_answer.elapsed_ms
                else:
                    answer = str(raw_answer)
                    answer_metadata = {}
                    answer_usage = None
                    answer_elapsed_ms = None
                if not answer.strip():
                    trajectory.add_event(
                        kind="system_error",
                        actor="oracle",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={
                            "stage": "answer",
                            "error": "empty clarification answer",
                        },
                    )
                    trajectory.finish(
                        termination="oracle_error",
                        total_elapsed_ms=(time.monotonic() - started) * 1_000,
                        final_sql=last_exposed_sql,
                    )
                    return trajectory
                user_turns += 1
                record = {
                    "kind": "user_clarification",
                    "question": question,
                    "answer": answer.strip(),
                    "user_turn": user_turns,
                    "clarification_target": str(
                        action.clarification_target or ""
                    ).strip(),
                    "request_contract": list(action.request_contract or ()),
                }
                history.append(record)
                event_payload = dict(record)
                if answer_metadata:
                    event_payload["simulator"] = answer_metadata
                if answer_elapsed_ms is not None:
                    event_payload["simulator_elapsed_ms"] = answer_elapsed_ms
                trajectory.add_event(
                    kind="user_clarification",
                    actor="user_oracle",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload=event_payload,
                    usage=answer_usage,
                )
                phase = "generate_after_clarification"
                continue

            sql = str(action.sql).strip()
            if action.kind == "query_database":
                if database_probes >= self.config.max_database_probes:
                    record = {
                        "kind": "probe_budget_exhausted",
                        "instruction": "Submit the best final SQL now.",
                    }
                    history.append(record)
                    trajectory.add_event(
                        kind="probe_rejected",
                        actor="environment",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={"sql": sql, "reason": "budget_exhausted"},
                    )
                    continue
                database_probes += 1
                result = environment.execute(sql)
                observation = result.observation.to_dict()
                record = {
                    "kind": "database_probe",
                    "sql": sql,
                    "purpose": str(action.purpose or ""),
                    "observation": json.loads(result.observation.tool_output()),
                    "grounded_user_turns": list(action.grounded_user_turns or ()),
                    "change_summary": str(action.change_summary or "").strip(),
                    "request_contract": list(action.request_contract or ()),
                }
                history.append(record)
                trajectory.add_event(
                    kind="database_probe",
                    actor="environment",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={**record, "observation": observation},
                )
                continue

            result = environment.execute(sql)
            if phase == "final_recovery_after_rejection":
                post_turn_recovery_submissions += 1
                trajectory.add_event(
                    kind="post_turn_recovery_submission",
                    actor="system",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={
                        "attempt": post_turn_recovery_submissions,
                        "maximum": self.config.max_post_turn_recovery_submissions,
                    },
                )
            trajectory.add_event(
                kind="sql_execution",
                actor="environment",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "sql": sql,
                    "observation": result.observation.to_dict(),
                    "user_visible": False,
                },
            )
            if result.observation.status != "ok" or result.observation.truncated:
                if submission_repairs >= self.config.max_submission_repairs:
                    trajectory.finish(
                        termination="submission_repair_budget_exhausted",
                        total_elapsed_ms=(time.monotonic() - started) * 1_000,
                        final_sql=last_exposed_sql or sql,
                    )
                    return trajectory
                submission_repairs += 1
                record = {
                    "kind": "execution_repair_feedback",
                    "sql": sql,
                    "status": result.observation.status,
                    "error": result.observation.error,
                    "instruction": "Repair the SQL and submit a changed executable query.",
                }
                history.append(record)
                trajectory.add_event(
                    kind="execution_repair_feedback",
                    actor="environment",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload=record,
                )
                continue

            fingerprint = result.observation.fingerprints.get(
                self.config.guard_fingerprint
            )
            normalized_sql = normalize_sql(sql)
            repeated_result = bool(fingerprint and fingerprint in rejected_fingerprints)
            if repeated_result:
                if not self.config.progress_guard:
                    trajectory.add_event(
                        kind="repeated_rejected_result",
                        actor="progress_guard",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={
                            "sql": sql,
                            "fingerprint_kind": self.config.guard_fingerprint,
                            "guard_action": "observe_only",
                            "same_normalized_sql": normalized_sql in rejected_sql,
                            "sparse_result_kind": _sparse_result_kind(result),
                        },
                    )
                elif guard_retries < self.config.max_guard_retries:
                    guard_retries += 1
                    record = {
                        "kind": "guard_feedback",
                        "repeat_type": "rejected_visible_result",
                        "same_normalized_sql": normalized_sql in rejected_sql,
                        "sparse_result_kind": _sparse_result_kind(result),
                        "instruction": (
                            "This visible result was already rejected under the "
                            "accumulated feedback. Produce a materially different SQL result."
                        ),
                    }
                    history.append(record)
                    trajectory.add_event(
                        kind="repeated_rejected_result",
                        actor="progress_guard",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={
                            "sql": sql,
                            "fingerprint_kind": self.config.guard_fingerprint,
                            "guard_action": "internal_retry",
                            "retry": guard_retries,
                            **{
                                key: value
                                for key, value in record.items()
                                if key not in {"kind", "instruction"}
                            },
                        },
                    )
                    phase = "generate_after_guard"
                    continue
                elif self.config.guard_retry_fallback == "stop":
                    trajectory.add_event(
                        kind="repeated_rejected_result",
                        actor="progress_guard",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={
                            "sql": sql,
                            "fingerprint_kind": self.config.guard_fingerprint,
                            "guard_action": "stop_after_retry_cap",
                        },
                    )
                    trajectory.finish(
                        termination="guard_retry_budget_exhausted",
                        total_elapsed_ms=(time.monotonic() - started) * 1_000,
                        final_sql=last_exposed_sql,
                    )
                    return trajectory
                elif self.config.progress_guard:
                    trajectory.add_event(
                        kind="repeated_rejected_result",
                        actor="progress_guard",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={
                            "sql": sql,
                            "fingerprint_kind": self.config.guard_fingerprint,
                            "guard_action": "expose_after_retry_cap",
                        },
                    )

            last_exposed_sql = sql
            trajectory.add_event(
                kind="candidate_exposed",
                actor="system",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={"sql": sql, "observation": result.observation.to_dict()},
            )
            if self.config.policy == "no_user":
                trajectory.finish(
                    termination="submitted_executable_sql",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=sql,
                )
                return trajectory

            try:
                judgement = self.oracle.judge(task, sql, result)
            except Exception as exc:  # noqa: BLE001 - evaluator adapters vary.
                trajectory.add_event(
                    kind="system_error",
                    actor="oracle",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={"stage": "judge", "error": repr(exc)},
                )
                trajectory.finish(
                    termination="oracle_error",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=sql,
                )
                return trajectory

            trajectory.add_event(
                kind="submission_judgement",
                actor="user_oracle",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "accepted": judgement.accepted,
                    "feedback": judgement.feedback,
                    "evaluator": judgement.metadata,
                },
            )
            if judgement.accepted:
                trajectory.finish(
                    termination="accepted_submission",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=sql,
                )
                return trajectory

            if fingerprint:
                rejected_fingerprints.add(fingerprint)
            rejected_sql.add(normalized_sql)
            guard_retries = 0
            history.append(
                {
                    "kind": "submission_rejected",
                    "sql": sql,
                    "observation": json.loads(result.observation.tool_output()),
                    "feedback": judgement.feedback,
                    "grounded_user_turns": list(action.grounded_user_turns or ()),
                    "change_summary": str(action.change_summary or "").strip(),
                    "request_contract": list(action.request_contract or ()),
                }
            )
            if user_turns >= self.config.max_user_turns:
                if (
                    self.recovery.enabled
                    and post_turn_recovery_submissions
                    < self.config.max_post_turn_recovery_submissions
                ):
                    phase = "final_recovery_after_rejection"
                    continue
                trajectory.finish(
                    termination="user_turn_budget_exhausted",
                    total_elapsed_ms=(time.monotonic() - started) * 1_000,
                    final_sql=sql,
                )
                return trajectory
            phase = (
                "clarify_after_rejection"
                if self.config.policy in {"reactive", "proactive"}
                else "hybrid_after_rejection"
            )

        trajectory.finish(
            termination="model_call_budget_exhausted",
            total_elapsed_ms=(time.monotonic() - started) * 1_000,
            final_sql=last_exposed_sql,
        )
        return trajectory
