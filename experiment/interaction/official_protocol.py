"""Official-protocol-compatible Mini-Interact controller.

This module deliberately does not modify the historical Freeze G controller.
It owns the released Mini-Interact action space, bird-coin accounting, paid
schema/knowledge retrieval, forced submission, and the sequencing rules for the
named comparison policies behind one controller interface.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from .controller import ClarificationAnswer, InteractionOracle
from .sqlite_env import QueryResult, SQLiteEnvironment
from .trajectory import BenchmarkTask, Trajectory, Usage

PROTOCOL_VERSION = "mini-interact-stress-adaptation-v3"
SRA_NORMALIZER_VERSION = "sra-metadata-normalizer-v1"
PROGRESS_GUARD_VERSION = "result-progress-guard-v1"
OFFICIAL_SOURCE_REVISION = "451fe2c3518ee1cf908d8139e2913483bd519381"

OFFICIAL_ACTION_COSTS: dict[str, float] = {
    "ask": 2.0,
    "submit": 3.0,
    "execute": 1.0,
    "get_schema": 1.0,
    "get_all_column_meanings": 1.0,
    "get_column_meaning": 0.5,
    "get_all_external_knowledge_names": 0.5,
    "get_knowledge_definition": 0.5,
    "get_all_knowledge_definitions": 1.0,
}
OFFICIAL_ACTIONS = tuple(OFFICIAL_ACTION_COSTS)
ENVIRONMENT_ACTIONS = tuple(
    action for action in OFFICIAL_ACTIONS if action not in {"ask", "submit"}
)
INTERNAL_ACTIONS = ("no_ambiguity",)
POLICIES = {"mini_react_port", "sphinteract_cq", "proactive", "hybrid"}
KNOWLEDGE_VISIBLE_FIELDS = ("id", "knowledge", "description", "definition")
SRA_REVIEW_SOURCES = ("AmbQuestion", "AmbColumn", "AmbOutput", "AmbValue")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise TypeError(f"Expected an object at {path}:{line_number}")
            rows.append(value)
    return rows


def load_official_public_tasks(path: Path) -> list[BenchmarkTask]:
    """Load only the fields visible at the start of the released protocol."""

    tasks = [
        BenchmarkTask(
            question_id=str(row["instance_id"]),
            db_id=str(row["selected_database"]),
            question=str(row["amb_user_query"]),
            evidence=None,
            difficulty="interactive",
        )
        for row in _load_jsonl(path)
    ]
    if len(tasks) != len({task.question_id for task in tasks}):
        raise ValueError("Mini-Interact public instance IDs must be unique")
    return tasks


@dataclass(frozen=True)
class OfficialMiniInteractConfig:
    policy: str = "mini_react_port"
    user_patience_budget: float = 6.0
    max_action_turns: int = 60
    max_user_questions: int = 4
    max_normal_submissions: int = 5
    sql_timeout_seconds: float = 30.0
    max_result_rows: int = 500_000
    progress_guard: bool = False
    guard_fingerprint: str = "bird_set"
    max_guard_retries: int = 1

    def validate(self) -> None:
        if self.policy not in POLICIES:
            raise ValueError(f"Unknown official comparison policy: {self.policy}")
        if self.user_patience_budget < 0:
            raise ValueError("user_patience_budget must be non-negative")
        if self.max_action_turns < 1:
            raise ValueError("max_action_turns must be positive")
        if self.max_user_questions < 1:
            raise ValueError("max_user_questions must be positive")
        if self.max_normal_submissions < 1:
            raise ValueError("max_normal_submissions must be positive")
        if self.guard_fingerprint not in {
            "bird_set",
            "visible_multiset",
            "ordered_visible",
        }:
            raise ValueError(f"Unknown guard fingerprint: {self.guard_fingerprint}")
        if self.max_guard_retries < 0:
            raise ValueError("max_guard_retries must be non-negative")


@dataclass(frozen=True)
class OfficialAction:
    kind: str
    sql: str | None = None
    question: str | None = None
    table_name: str | None = None
    column_name: str | None = None
    knowledge_name: str | None = None
    purpose: str | None = None
    review_source: str | None = None
    summary: str | None = None
    reviews: dict[str, str] | None = None
    options: tuple[str, ...] = ()

    def validate(self) -> None:
        if self.kind not in {*OFFICIAL_ACTION_COSTS, *INTERNAL_ACTIONS}:
            raise ValueError(f"Unknown Mini-Interact action: {self.kind}")
        if self.kind in {"execute", "submit"} and not (self.sql or "").strip():
            raise ValueError(f"{self.kind} requires non-empty SQL")
        if self.kind == "ask":
            if not (self.question or "").strip():
                raise ValueError("ask requires a non-empty question")
            if self.review_source is not None and self.review_source not in {
                *SRA_REVIEW_SOURCES,
                "Other",
            }:
                raise ValueError("ask review_source must be a valid SRA source")
        if self.kind == "get_column_meaning" and not (
            (self.table_name or "").strip() and (self.column_name or "").strip()
        ):
            raise ValueError("get_column_meaning requires table_name and column_name")
        if (
            self.kind == "get_knowledge_definition"
            and not (self.knowledge_name or "").strip()
        ):
            raise ValueError("get_knowledge_definition requires knowledge_name")

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def validate_sra_action(action: OfficialAction) -> None:
    """Enforce Sphinteract's summarize/review/3+1-question contract."""

    if action.kind not in {"ask", "no_ambiguity"}:
        raise ValueError("An SRA checkpoint must return ask or no_ambiguity")
    if not (action.summary or "").strip():
        raise ValueError("SRA requires a non-empty summary")
    reviews = action.reviews or {}
    if set(reviews) != set(SRA_REVIEW_SOURCES) or any(
        not str(reviews[source]).strip() for source in SRA_REVIEW_SOURCES
    ):
        raise ValueError("SRA must review all four ambiguity sources")
    if action.kind == "no_ambiguity":
        if action.options:
            raise ValueError("no_ambiguity must not include answer options")
        return
    if len(action.options) != 4 or any(not option.strip() for option in action.options):
        raise ValueError("SRA ask requires exactly four non-empty options")
    if len({option.casefold().strip() for option in action.options}) != 4:
        raise ValueError("SRA answer options must be distinct")
    open_option = action.options[-1].casefold()
    if "other" not in open_option and "specif" not in open_option:
        raise ValueError("The fourth SRA option must be open-ended")


def _is_open_sra_option(option: str) -> bool:
    folded = option.casefold()
    return "other" in folded or "specif" in folded


def normalize_sra_action(
    action: OfficialAction,
) -> tuple[OfficialAction, tuple[str, ...]]:
    """Repair deterministic SRA metadata omissions without changing SQL intent.

    The raw model action remains in the ``policy_decision`` event. This helper
    only supplies boilerplate metadata and canonicalizes the open answer option;
    malformed questions, duplicate choices, and semantic content are left for
    strict validation to reject.
    """

    if action.kind not in {"ask", "no_ambiguity"}:
        return action, ()

    changes: list[str] = []
    summary = action.summary
    if not str(summary or "").strip():
        summary = (
            "A clarification-worthy ambiguity was identified."
            if action.kind == "ask"
            else "No actionable ambiguity was identified."
        )
        changes.append("defaulted_summary")

    reviews = dict(action.reviews or {})
    defaulted_review_sources: list[str] = []
    for source in SRA_REVIEW_SOURCES:
        if not str(reviews.get(source, "")).strip():
            reviews[source] = "No additional ambiguity was specified."
            defaulted_review_sources.append(source)
    if defaulted_review_sources:
        changes.append("defaulted_reviews:" + ",".join(defaulted_review_sources))

    options = tuple(action.options)
    if action.kind == "ask":
        nonempty_options = all(option.strip() for option in options)
        open_indices = [
            index for index, option in enumerate(options) if _is_open_sra_option(option)
        ]
        if len(options) == 3 and nonempty_options and not open_indices:
            options = (*options, "Other—please specify")
            changes.append("appended_open_option")
        elif (
            len(options) == 4
            and nonempty_options
            and len(open_indices) == 1
            and open_indices[0] != 3
        ):
            open_index = open_indices[0]
            open_option = options[open_index]
            options = tuple(
                option for index, option in enumerate(options) if index != open_index
            ) + (open_option,)
            changes.append("moved_open_option_to_fourth")

    if not changes:
        return action, ()
    return (
        replace(action, summary=summary, reviews=reviews, options=options),
        tuple(changes),
    )


def format_sra_question(action: OfficialAction) -> str:
    validate_sra_action(action)
    if action.kind != "ask":
        raise ValueError("Only an SRA ask can be rendered as a question")
    labels = ("A", "B", "C", "D")
    return "\n".join(
        [str(action.question).strip()]
        + [f"{label}. {option}" for label, option in zip(labels, action.options)]
    )


@dataclass(frozen=True)
class OfficialDecisionRequest:
    run_id: str
    task: BenchmarkTask
    policy: str
    phase: str
    history: tuple[dict[str, Any], ...]
    allowed_actions: tuple[str, ...]
    total_budget: float
    remaining_budget: float
    force_submit: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "task": {
                "question": self.task.question,
            },
            "policy": self.policy,
            "phase": self.phase,
            "history": list(self.history),
            "allowed_actions": list(self.allowed_actions),
            "action_costs": {
                action: OFFICIAL_ACTION_COSTS[action]
                for action in self.allowed_actions
                if action in OFFICIAL_ACTION_COSTS
            },
            "internal_zero_coin_actions": [
                action for action in self.allowed_actions if action in INTERNAL_ACTIONS
            ],
            "total_budget": self.total_budget,
            "remaining_budget": self.remaining_budget,
            "force_submit": self.force_submit,
        }


@dataclass(frozen=True)
class OfficialPolicyTurn:
    response_id: str
    action: OfficialAction
    raw_output: str
    usage: Usage
    elapsed_ms: float
    transport_attempts: int = 1
    served_model: str | None = None


class OfficialPolicyModel(Protocol):
    requested_model: str
    reasoning_effort: str

    def decide(self, request: OfficialDecisionRequest) -> OfficialPolicyTurn: ...


def _safe_error_payload(stage: str, exc: Exception) -> dict[str, str]:
    """Record failure class without persisting exception text or private data."""

    return {
        "stage": stage,
        "error": f"{stage} failed",
        "error_type": type(exc).__name__,
    }


class OfficialBudgetProvider(Protocol):
    def total_budget(
        self, task: BenchmarkTask, *, user_patience_budget: float
    ) -> float: ...


class MiniInteractPrivateBudgetProvider:
    """Evaluator-side implementation of the released per-task budget formula."""

    ENVIRONMENT_ALLOWANCE = 3.0
    SUBMISSION_ALLOWANCE = 3.0

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = {str(row["instance_id"]): row for row in rows}
        if len(self.rows) != len(rows):
            raise ValueError("Mini-Interact private instance IDs must be unique")

    @classmethod
    def from_file(cls, path: Path) -> MiniInteractPrivateBudgetProvider:
        return cls(_load_jsonl(path))

    def total_budget(
        self, task: BenchmarkTask, *, user_patience_budget: float
    ) -> float:
        try:
            row = self.rows[str(task.question_id)]
        except KeyError as exc:
            raise KeyError(
                f"No Mini-Interact budget row for {task.question_id}"
            ) from exc
        if str(row["selected_database"]) != task.db_id:
            raise ValueError(f"Database mismatch for task {task.question_id}")
        critical = (
            row.get("user_query_ambiguity", {}).get("critical_ambiguity", []) or []
        )
        knowledge = row.get("knowledge_ambiguity", []) or []
        ambiguity_allowance = (len(critical) + len(knowledge)) * OFFICIAL_ACTION_COSTS[
            "ask"
        ]
        return float(
            self.ENVIRONMENT_ALLOWANCE
            + self.SUBMISSION_ALLOWANCE
            + ambiguity_allowance
            + user_patience_budget
        )


@dataclass
class OfficialBudget:
    total: float
    remaining: float = field(init=False)
    force_submit: bool = False
    spent: float = 0.0

    def __post_init__(self) -> None:
        if self.total <= 0:
            raise ValueError("Official action budget must be positive")
        self.remaining = float(self.total)

    def charge(self, action_kind: str) -> dict[str, float | bool | str]:
        if action_kind not in OFFICIAL_ACTION_COSTS:
            raise ValueError(f"Unknown action cost: {action_kind}")
        forced_before = self.force_submit
        if forced_before and action_kind != "submit":
            raise ValueError("Budget is depleted; the next action must be submit")
        cost = OFFICIAL_ACTION_COSTS[action_kind]
        before = self.remaining
        self.remaining -= cost
        self.spent += cost
        if self.remaining <= 0:
            self.force_submit = True
        return {
            "action": action_kind,
            "cost": cost,
            "before": before,
            "after": self.remaining,
            "total": self.total,
            "spent": self.spent,
            "forced_before": forced_before,
            "force_submit": self.force_submit,
        }


@dataclass(frozen=True)
class OfficialToolObservation:
    text: str
    payload: dict[str, Any]
    result: QueryResult | None = None


class OfficialMiniInteractTools:
    """Paid SQLite/schema/knowledge actions from the released Mini protocol."""

    def __init__(
        self, *, dataset_root: Path, public_rows: list[dict[str, Any]]
    ) -> None:
        self.dataset_root = dataset_root.resolve()
        self.rows = {str(row["instance_id"]): row for row in public_rows}
        if len(self.rows) != len(public_rows):
            raise ValueError("Mini-Interact public instance IDs must be unique")
        self._asset_cache: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}

    @classmethod
    def from_file(
        cls, *, dataset_root: Path, public_data: Path
    ) -> OfficialMiniInteractTools:
        return cls(dataset_root=dataset_root, public_rows=_load_jsonl(public_data))

    def _row(self, task: BenchmarkTask) -> dict[str, Any]:
        try:
            row = self.rows[str(task.question_id)]
        except KeyError as exc:
            raise KeyError(
                f"No public Mini-Interact row for {task.question_id}"
            ) from exc
        if str(row["selected_database"]) != task.db_id:
            raise ValueError(f"Database mismatch for task {task.question_id}")
        return row

    def _assets(
        self, task: BenchmarkTask
    ) -> tuple[str, dict[str, Any], dict[str, Any]]:
        cached = self._asset_cache.get(str(task.question_id))
        if cached is not None:
            return cached
        row = self._row(task)
        root = self.dataset_root / task.db_id
        schema = (root / f"{task.db_id}_schema.txt").read_text(encoding="utf-8")
        column_meanings = json.loads(
            (root / f"{task.db_id}_column_meaning_base.json").read_text(
                encoding="utf-8"
            )
        )
        column_meanings = {
            str(key).casefold(): value for key, value in column_meanings.items()
        }
        knowledge = {
            str(item["knowledge"]): item
            for item in _load_jsonl(root / f"{task.db_id}_kb.jsonl")
        }
        deleted = {
            int(item["deleted_knowledge"])
            for item in row.get("knowledge_ambiguity", [])
            if item.get("deleted_knowledge") is not None
        }
        visible_knowledge = {
            name: item
            for name, item in knowledge.items()
            if int(item.get("id", -1)) not in deleted
        }
        assets = (schema, column_meanings, visible_knowledge)
        self._asset_cache[str(task.question_id)] = assets
        return assets

    @staticmethod
    def _visible_knowledge(item: dict[str, Any]) -> dict[str, Any]:
        return {key: item[key] for key in KNOWLEDGE_VISIBLE_FIELDS if key in item}

    @staticmethod
    def _format_query_result(result: QueryResult) -> str:
        observation = result.observation
        if observation.status != "ok":
            return f"SQL execution error: {observation.error or observation.status}"
        if observation.truncated:
            return "SQL execution result exceeded the evaluation safety limit."
        if not result.rows:
            return "Query executed, empty result set."
        lines = [" | ".join(str(value)[:100] for value in row) for row in result.rows]
        text = "\n".join(lines)
        words = text.split()
        return " ".join(words[:500]) + ("..." if len(words) > 500 else "")

    def perform(
        self,
        task: BenchmarkTask,
        action: OfficialAction,
        *,
        timeout_seconds: float,
        max_result_rows: int,
    ) -> OfficialToolObservation:
        if action.kind not in ENVIRONMENT_ACTIONS:
            raise ValueError(f"Not an environment action: {action.kind}")
        schema, column_meanings, knowledge = self._assets(task)
        if action.kind == "execute":
            environment = SQLiteEnvironment(
                self.dataset_root / task.db_id / f"{task.db_id}.sqlite",
                timeout_seconds=timeout_seconds,
                max_rows=max_result_rows,
            )
            result = environment.execute(str(action.sql))
            return OfficialToolObservation(
                text=self._format_query_result(result),
                payload={"execution": result.observation.to_dict()},
                result=result,
            )
        if action.kind == "get_schema":
            return OfficialToolObservation(text=schema, payload={"chars": len(schema)})
        if action.kind == "get_all_column_meanings":
            text = json.dumps(column_meanings, ensure_ascii=False, indent=2)
            return OfficialToolObservation(
                text=text, payload={"items": len(column_meanings)}
            )
        if action.kind == "get_column_meaning":
            key = (
                f"{task.db_id}|{str(action.table_name).strip()}|"
                f"{str(action.column_name).strip()}"
            ).casefold()
            text = str(column_meanings.get(key, "Column meaning not found"))
            return OfficialToolObservation(
                text=text, payload={"found": key in column_meanings}
            )
        if action.kind == "get_all_external_knowledge_names":
            names = list(knowledge)
            return OfficialToolObservation(
                text=json.dumps(names, ensure_ascii=False),
                payload={"items": len(names)},
            )
        if action.kind == "get_knowledge_definition":
            item = knowledge.get(str(action.knowledge_name))
            visible = self._visible_knowledge(item) if item else None
            return OfficialToolObservation(
                text=(
                    json.dumps(visible, ensure_ascii=False, indent=2)
                    if visible
                    else "Knowledge not found or not accessible."
                ),
                payload={"found": item is not None},
            )
        visible = [self._visible_knowledge(item) for item in knowledge.values()]
        return OfficialToolObservation(
            text=json.dumps(visible, ensure_ascii=False, indent=2),
            payload={"items": len(visible)},
        )


def allowed_actions(policy: str, phase: str, *, force_submit: bool) -> tuple[str, ...]:
    """Return one audited state-machine slice of the shared action space."""

    if policy not in POLICIES:
        raise ValueError(f"Unknown policy: {policy}")
    if force_submit:
        return ("submit",)
    if policy == "mini_react_port" and phase == "guard_retry":
        return (*ENVIRONMENT_ACTIONS, "submit")
    if policy == "mini_react_port":
        return OFFICIAL_ACTIONS
    if phase == "discovery":
        return ("get_schema",)
    if phase in {"proactive_sra", "reactive_sra"}:
        return ("ask", "no_ambiguity")
    if phase == "repair_submission":
        return ("submit",)
    if phase in {"generate_initial", "generate_revision"}:
        return (*ENVIRONMENT_ACTIONS, "submit")
    raise ValueError(f"Unsupported {policy} phase: {phase}")


class OfficialMiniInteractController:
    """Run every named arm through one released-action/budget interface."""

    def __init__(
        self,
        *,
        model: OfficialPolicyModel,
        oracle: InteractionOracle,
        tools: OfficialMiniInteractTools,
        budget_provider: OfficialBudgetProvider,
        config: OfficialMiniInteractConfig | None = None,
    ) -> None:
        self.model = model
        self.oracle = oracle
        self.tools = tools
        self.budget_provider = budget_provider
        self.config = config or OfficialMiniInteractConfig()
        self.config.validate()

    def run(self, task: BenchmarkTask, *, run_id: str | None = None) -> Trajectory:
        started = time.monotonic()
        run_id = run_id or uuid4().hex
        total_budget = self.budget_provider.total_budget(
            task, user_patience_budget=self.config.user_patience_budget
        )
        budget = OfficialBudget(total_budget)
        trajectory = Trajectory.start(
            run_id=run_id,
            policy=f"{PROTOCOL_VERSION}:{self.config.policy}",
            task=task,
            config={
                **asdict(self.config),
                "protocol_version": PROTOCOL_VERSION,
                "official_source_revision": OFFICIAL_SOURCE_REVISION,
                "requested_model": self.model.requested_model,
                "reasoning_effort": self.model.reasoning_effort,
                "total_bird_coins": total_budget,
                "stress_mode": True,
                "paper_lambda_pat": 3,
                "gold_visible_to_policy": False,
                "initial_context": "ambiguous_question_and_budget_only",
                "identity": (
                    "mini_react_structured_port"
                    if self.config.policy == "mini_react_port"
                    else "sphinteract_derived_mini_adaptation"
                ),
            },
        )
        trajectory.add_event(
            kind="official_protocol_started",
            actor="system",
            elapsed_ms=(time.monotonic() - started) * 1_000,
            payload={
                "protocol": PROTOCOL_VERSION,
                "total_bird_coins": total_budget,
                "initial_context": "question_only",
                "stress_mode": True,
            },
        )

        phase = "mini_react" if self.config.policy == "mini_react_port" else "discovery"
        history: list[dict[str, Any]] = []
        user_turns = 0
        normal_submissions = 0
        repair_submissions = 0
        guard_retries = 0
        rejected_fingerprints: set[str] = set()
        last_sql: str | None = None

        def finish(termination: str) -> Trajectory:
            trajectory.finish(
                termination=termination,
                total_elapsed_ms=(time.monotonic() - started) * 1_000,
                final_sql=last_sql,
            )
            return trajectory

        for action_turn in range(1, self.config.max_action_turns + 1):
            permitted = allowed_actions(
                self.config.policy, phase, force_submit=budget.force_submit
            )
            request = OfficialDecisionRequest(
                run_id=run_id,
                task=task,
                policy=self.config.policy,
                phase=phase,
                history=tuple(history),
                allowed_actions=permitted,
                total_budget=budget.total,
                remaining_budget=budget.remaining,
                force_submit=budget.force_submit,
            )
            try:
                turn = self.model.decide(request)
            except Exception as exc:  # noqa: BLE001 - external model adapter.
                trajectory.add_event(
                    kind="system_error",
                    actor="model",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload=_safe_error_payload("decision", exc),
                )
                return finish("model_error")

            action = turn.action
            trajectory.add_event(
                kind="policy_decision",
                actor="model",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "response_id": turn.response_id,
                    "action_turn": action_turn,
                    "phase": phase,
                    "allowed_actions": list(permitted),
                    "action": action.to_dict(),
                    "raw_output": turn.raw_output,
                    "transport_attempts": turn.transport_attempts,
                    "served_model": turn.served_model,
                },
                usage=turn.usage,
            )
            if phase in {"proactive_sra", "reactive_sra"}:
                action, normalization_changes = normalize_sra_action(action)
                if normalization_changes:
                    trajectory.add_event(
                        kind="sra_normalization",
                        actor="system",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload={
                            "phase": phase,
                            "normalizer_version": SRA_NORMALIZER_VERSION,
                            "changes": list(normalization_changes),
                        },
                    )
            try:
                action.validate()
                if action.kind not in permitted:
                    raise ValueError(f"{action.kind} is not allowed during {phase}")
                if phase in {"proactive_sra", "reactive_sra"}:
                    validate_sra_action(action)
                elif action.kind in INTERNAL_ACTIONS:
                    raise ValueError("Internal SRA action outside an SRA checkpoint")
            except ValueError as exc:
                trajectory.add_event(
                    kind="model_protocol_error",
                    actor="system",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={"phase": phase, "error": str(exc)},
                )
                return finish("model_protocol_error")

            if action.kind == "no_ambiguity":
                record = {
                    "kind": "sra_no_ambiguity",
                    "phase": phase,
                    "summary": action.summary,
                    "reviews": action.reviews,
                    "remaining_bird_coins": budget.remaining,
                }
                history.append(record)
                trajectory.add_event(
                    kind="sra_no_ambiguity",
                    actor="model",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload=record,
                )
                if phase == "proactive_sra":
                    phase = "generate_initial"
                    continue
                return finish("sra_no_ambiguity_after_rejection")

            forced_before = budget.force_submit
            charge = budget.charge(action.kind)
            trajectory.add_event(
                kind="bird_coin_charge",
                actor="system",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload=charge,
            )

            if action.kind in ENVIRONMENT_ACTIONS:
                try:
                    observation = self.tools.perform(
                        task,
                        action,
                        timeout_seconds=self.config.sql_timeout_seconds,
                        max_result_rows=self.config.max_result_rows,
                    )
                except Exception as exc:  # noqa: BLE001 - local adapter errors.
                    observation = OfficialToolObservation(
                        text="Environment action failed.",
                        payload=_safe_error_payload("environment_action", exc),
                    )
                record = {
                    "kind": "official_environment_action",
                    "action": action.to_dict(),
                    "observation": observation.text,
                    "remaining_bird_coins": budget.remaining,
                }
                history.append(record)
                trajectory.add_event(
                    kind=(
                        "database_probe"
                        if action.kind == "execute"
                        else "environment_retrieval"
                    ),
                    actor="environment",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={**record, **observation.payload},
                )
                if phase == "discovery":
                    phase = (
                        "generate_initial"
                        if self.config.policy == "sphinteract_cq"
                        else "proactive_sra"
                    )
                continue

            if action.kind == "ask":
                question = (
                    format_sra_question(action)
                    if phase in {"proactive_sra", "reactive_sra"}
                    else str(action.question).strip()
                )
                try:
                    raw_answer = self.oracle.answer(task, question, tuple(history))
                except Exception as exc:  # noqa: BLE001 - simulator adapter.
                    trajectory.add_event(
                        kind="system_error",
                        actor="oracle",
                        turn_index=user_turns,
                        elapsed_ms=(time.monotonic() - started) * 1_000,
                        payload=_safe_error_payload("answer", exc),
                    )
                    return finish("oracle_error")
                if isinstance(raw_answer, ClarificationAnswer):
                    answer = raw_answer.text
                    metadata = raw_answer.metadata
                    usage = raw_answer.usage
                    simulator_elapsed = raw_answer.elapsed_ms
                else:
                    answer = str(raw_answer)
                    metadata = {}
                    usage = None
                    simulator_elapsed = None
                user_turns += 1
                record = {
                    "kind": "user_clarification",
                    "question": question,
                    "options": list(action.options),
                    "answer": answer.strip(),
                    "user_turn": user_turns,
                    "review_source": action.review_source,
                    "summary": action.summary,
                    "reviews": action.reviews,
                    "remaining_bird_coins": budget.remaining,
                }
                history.append(record)
                payload = dict(record)
                if metadata:
                    payload["simulator"] = metadata
                if simulator_elapsed is not None:
                    payload["simulator_elapsed_ms"] = simulator_elapsed
                trajectory.add_event(
                    kind="user_clarification",
                    actor="user_oracle",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload=payload,
                    usage=usage,
                )
                phase = (
                    "generate_initial"
                    if phase == "proactive_sra"
                    else "generate_revision"
                )
                continue

            submission_kind = (
                "invalid_sql_repair" if phase == "repair_submission" else "normal"
            )
            if self.config.policy != "mini_react_port":
                if submission_kind == "normal":
                    normal_submissions += 1
                else:
                    repair_submissions += 1
            last_sql = str(action.sql).strip()
            database = SQLiteEnvironment(
                self.tools.dataset_root / task.db_id / f"{task.db_id}.sqlite",
                timeout_seconds=self.config.sql_timeout_seconds,
                max_rows=self.config.max_result_rows,
            )
            result = database.execute(last_sql)
            fingerprint = result.observation.fingerprints.get(
                self.config.guard_fingerprint
            )
            repeated_rejected_result = bool(
                fingerprint and fingerprint in rejected_fingerprints
            )
            trajectory.add_event(
                kind="sql_execution",
                actor="environment",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "sql": last_sql,
                    "observation": result.observation.to_dict(),
                    "submission_kind": submission_kind,
                    "user_visible": False,
                },
            )
            trajectory.add_event(
                kind="candidate_exposed",
                actor="system",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "sql": last_sql,
                    "submission_kind": submission_kind,
                    "remaining_bird_coins": budget.remaining,
                },
            )
            try:
                judgement = self.oracle.judge(task, last_sql, result)
            except Exception as exc:  # noqa: BLE001 - evaluator adapter.
                trajectory.add_event(
                    kind="system_error",
                    actor="oracle",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload=_safe_error_payload("judge", exc),
                )
                return finish("oracle_error")

            invalid = bool(
                judgement.metadata.get("execution_error")
                or judgement.metadata.get("timeout_error")
                or result.observation.status != "ok"
            )
            if judgement.accepted:
                policy_feedback = "SQL passed the Mini-Interact scorer."
            elif invalid:
                policy_feedback = (
                    "Submitted SQL failed because it was not executable. "
                    "The official interface exposes no detailed exception."
                )
            else:
                policy_feedback = (
                    "Submitted SQL failed the Mini-Interact default test. "
                    "Please try again."
                )
            trajectory.add_event(
                kind="submission_judgement",
                actor="user_oracle",
                turn_index=user_turns,
                elapsed_ms=(time.monotonic() - started) * 1_000,
                payload={
                    "accepted": judgement.accepted,
                    "feedback": policy_feedback,
                    "evaluator": judgement.metadata,
                    "submission_kind": submission_kind,
                    "forced_submission": forced_before or budget.force_submit,
                },
            )
            if judgement.accepted:
                return finish("accepted_submission")
            if forced_before or budget.force_submit:
                return finish("forced_submission_rejected")

            history.append(
                {
                    "kind": "submission_rejected",
                    "sql": last_sql,
                    "feedback": policy_feedback,
                    "invalid_sql": invalid,
                    "submission_kind": submission_kind,
                    "remaining_bird_coins": budget.remaining,
                }
            )
            if fingerprint:
                rejected_fingerprints.add(fingerprint)
            if repeated_rejected_result:
                guard_activates = bool(
                    self.config.progress_guard
                    and guard_retries < self.config.max_guard_retries
                    and self.config.policy == "mini_react_port"
                )
                trajectory.add_event(
                    kind="repeated_rejected_result",
                    actor="progress_guard",
                    turn_index=user_turns,
                    elapsed_ms=(time.monotonic() - started) * 1_000,
                    payload={
                        "guard_version": PROGRESS_GUARD_VERSION,
                        "fingerprint_kind": self.config.guard_fingerprint,
                        "guard_action": (
                            "internal_retry" if guard_activates else "observe_only"
                        ),
                        "retry": guard_retries + 1
                        if guard_activates
                        else guard_retries,
                        "maximum_retries": self.config.max_guard_retries,
                    },
                )
                if guard_activates:
                    guard_retries += 1
                    history.append(
                        {
                            "kind": "guard_feedback",
                            "guard_version": PROGRESS_GUARD_VERSION,
                            "fingerprint_kind": self.config.guard_fingerprint,
                            "instruction": (
                                "This SQL produced the same configured result "
                                "fingerprint as an earlier rejected submission. "
                                "Before asking the user again, make one internal "
                                "attempt that changes that fingerprint."
                            ),
                            "remaining_bird_coins": budget.remaining,
                        }
                    )
                    phase = "guard_retry"
                    continue
            if self.config.policy == "mini_react_port":
                phase = "mini_react"
                continue
            if invalid and submission_kind == "normal":
                phase = "repair_submission"
                continue
            if normal_submissions >= self.config.max_normal_submissions:
                return finish("normal_submission_limit_exhausted")
            if self.config.policy == "proactive":
                phase = "generate_revision"
                continue
            if user_turns >= self.config.max_user_questions:
                return finish("user_question_limit_exhausted")
            phase = "reactive_sra"

        return finish("action_turn_budget_exhausted")


def summarize_official_protocol(trajectories: list[Trajectory]) -> dict[str, Any]:
    """Protocol-level SR and bird-coin usage without inventing a new reward."""

    records: list[dict[str, Any]] = []
    for trajectory in trajectories:
        charges = [
            event for event in trajectory.events if event.kind == "bird_coin_charge"
        ]
        accepted = any(
            event.kind == "submission_judgement"
            and event.payload.get("accepted") is True
            for event in trajectory.events
        )
        submissions = [
            event for event in trajectory.events if event.kind == "submission_judgement"
        ]
        clarifications = [
            event for event in trajectory.events if event.kind == "user_clarification"
        ]
        first_question_sequence = (
            min(event.sequence for event in clarifications) if clarifications else None
        )
        usage = trajectory.total_usage
        records.append(
            {
                "question_id": trajectory.task.question_id,
                "success": accepted,
                "bird_coins": round(
                    sum(float(event.payload.get("cost", 0.0)) for event in charges), 3
                ),
                "user_questions": len(clarifications),
                "submissions": len(submissions),
                "normal_submissions": sum(
                    event.payload.get("submission_kind") == "normal"
                    for event in submissions
                ),
                "invalid_sql_repairs": sum(
                    event.payload.get("submission_kind") == "invalid_sql_repair"
                    for event in submissions
                ),
                "invalid_submissions": sum(
                    bool((event.payload.get("evaluator") or {}).get("execution_error"))
                    or bool((event.payload.get("evaluator") or {}).get("timeout_error"))
                    for event in submissions
                ),
                "submissions_before_first_question": (
                    sum(
                        event.sequence < first_question_sequence
                        for event in submissions
                    )
                    if first_question_sequence is not None
                    else len(submissions)
                ),
                "sra_no_ambiguity": sum(
                    event.kind == "sra_no_ambiguity" for event in trajectory.events
                ),
                "policy_model_calls": sum(
                    event.kind == "policy_decision" for event in trajectory.events
                ),
                "simulator_model_calls": sum(
                    int((event.payload.get("simulator") or {}).get("calls", 0))
                    for event in clarifications
                ),
                "total_tokens": usage.total_tokens,
                "actions": len(charges),
                "termination": trajectory.termination,
            }
        )
    tasks = len(records)

    def mean_field(name: str) -> float:
        return (
            round(sum(float(record[name]) for record in records) / tasks, 4)
            if tasks
            else 0.0
        )

    return {
        "protocol_version": PROTOCOL_VERSION,
        "tasks": tasks,
        "successes": sum(record["success"] for record in records),
        "success_rate_percent": (
            round(100 * sum(record["success"] for record in records) / tasks, 4)
            if tasks
            else 0.0
        ),
        "mean_bird_coins": mean_field("bird_coins"),
        "mean_user_questions": mean_field("user_questions"),
        "mean_submissions": mean_field("submissions"),
        "mean_normal_submissions": mean_field("normal_submissions"),
        "mean_invalid_sql_repairs": mean_field("invalid_sql_repairs"),
        "mean_invalid_submissions": mean_field("invalid_submissions"),
        "mean_submissions_before_first_question": mean_field(
            "submissions_before_first_question"
        ),
        "mean_policy_model_calls": mean_field("policy_model_calls"),
        "mean_simulator_model_calls": mean_field("simulator_model_calls"),
        "mean_total_tokens": mean_field("total_tokens"),
        "records": records,
    }
