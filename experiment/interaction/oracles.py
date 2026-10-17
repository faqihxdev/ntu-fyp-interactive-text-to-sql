"""Gold-isolated evaluator adapters for matched interaction experiments.

Policy models receive only :class:`BenchmarkTask`, public context, execution
observations, and the oracle's natural-language feedback. Gold SQL, labels, and
test cases remain inside these adapters and are never returned in metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from readiness.mini_interact_eval import (
    DEFAULT_SINGLE_INSTANCE_SCRIPT,
    evaluate_instance,
)
from readiness.mini_interact_live_eval import (
    DEFAULT_AGENT_ROOT,
    evaluate_live_instance,
)

from .oracle_types import ClarificationAnswer, SubmissionJudgement
from .sqlite_env import QueryResult, SQLiteEnvironment, bird_execution_equal
from .trajectory import BenchmarkTask


class PrivateAnswerer(Protocol):
    """Generate a user reply from evaluator-only task data."""

    def answer(
        self,
        task: BenchmarkTask,
        question: str,
        history: tuple[dict[str, Any], ...],
        private_row: dict[str, Any],
    ) -> str | ClarificationAnswer: ...


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(
        isinstance(row, dict) for row in payload
    ):
        raise ValueError(f"Expected a JSON array of objects: {path}")
    return payload


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


class CleanBirdExecutionOracle:
    """BIRD set-of-rows correctness plus a separate gold-grounded answerer."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        database_root: Path,
        answerer: PrivateAnswerer,
        timeout_seconds: float = 30.0,
        max_rows: int = 500_000,
    ) -> None:
        self.rows = {int(row["question_id"]): row for row in rows}
        if len(self.rows) != len(rows):
            raise ValueError("Clean BIRD question IDs must be unique")
        self.database_root = database_root.resolve()
        self.answerer = answerer
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self._gold_results: dict[int, QueryResult] = {}
        self._cache_lock = Lock()

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        database_root: Path,
        answerer: PrivateAnswerer,
        timeout_seconds: float = 30.0,
        max_rows: int = 500_000,
    ) -> CleanBirdExecutionOracle:
        return cls(
            rows=_load_json_array(path),
            database_root=database_root,
            answerer=answerer,
            timeout_seconds=timeout_seconds,
            max_rows=max_rows,
        )

    def _row(self, task: BenchmarkTask) -> tuple[int, dict[str, Any]]:
        try:
            question_id = int(task.question_id)
            row = self.rows[question_id]
        except (TypeError, ValueError, KeyError) as exc:
            raise KeyError(f"No clean-BIRD gold row for {task.question_id}") from exc
        if str(row["db_id"]) != task.db_id:
            raise ValueError(
                f"Database mismatch for clean-BIRD task {task.question_id}"
            )
        return question_id, row

    def _gold_result(self, question_id: int, row: dict[str, Any]) -> QueryResult:
        with self._cache_lock:
            cached = self._gold_results.get(question_id)
        if cached is not None:
            return cached
        database_id = str(row["db_id"])
        environment = SQLiteEnvironment(
            self.database_root / database_id / f"{database_id}.sqlite",
            timeout_seconds=self.timeout_seconds,
            max_rows=self.max_rows,
            preview_rows=0,
        )
        result = environment.execute(str(row["SQL"]))
        with self._cache_lock:
            self._gold_results.setdefault(question_id, result)
            return self._gold_results[question_id]

    def judge(
        self,
        task: BenchmarkTask,
        sql: str,
        result: QueryResult,
    ) -> SubmissionJudgement:
        question_id, row = self._row(task)
        gold_result = self._gold_result(question_id, row)
        accepted = bird_execution_equal(result, gold_result)
        return SubmissionJudgement(
            accepted=accepted,
            feedback=(
                "Yes, that result answers what I intended."
                if accepted
                else "No, that result does not answer what I intended."
            ),
            metadata={
                "benchmark": "clean_bird_dev",
                "evaluator": "bird_set_rows",
                "gold_execution_status": gold_result.observation.status,
            },
        )

    def answer(
        self,
        task: BenchmarkTask,
        question: str,
        history: tuple[dict[str, Any], ...],
    ) -> str | ClarificationAnswer:
        _, row = self._row(task)
        return self.answerer.answer(task, question, history, row)


class MiniInteractExecutionOracle:
    """Official Mini-Interact scorer plus an evaluator-side user simulator."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        database_root: Path,
        answerer: PrivateAnswerer,
        evaluator_script: Path = DEFAULT_SINGLE_INSTANCE_SCRIPT,
        timeout_seconds: float = 180.0,
    ) -> None:
        self.rows = {str(row["instance_id"]): row for row in rows}
        if len(self.rows) != len(rows):
            raise ValueError("Mini-Interact instance IDs must be unique")
        self.database_root = database_root.resolve()
        self.answerer = answerer
        self.evaluator_script = evaluator_script.resolve()
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        database_root: Path,
        answerer: PrivateAnswerer,
        evaluator_script: Path = DEFAULT_SINGLE_INSTANCE_SCRIPT,
        timeout_seconds: float = 180.0,
    ) -> MiniInteractExecutionOracle:
        return cls(
            rows=_load_jsonl(path),
            database_root=database_root,
            answerer=answerer,
            evaluator_script=evaluator_script,
            timeout_seconds=timeout_seconds,
        )

    def _row(self, task: BenchmarkTask) -> dict[str, Any]:
        try:
            row = self.rows[str(task.question_id)]
        except KeyError as exc:
            raise KeyError(f"No Mini-Interact row for {task.question_id}") from exc
        if str(row["selected_database"]) != task.db_id:
            raise ValueError(
                f"Database mismatch for Mini-Interact task {task.question_id}"
            )
        return row

    def judge(
        self,
        task: BenchmarkTask,
        sql: str,
        result: QueryResult,
    ) -> SubmissionJudgement:
        row = dict(self._row(task))
        row["pred_sqls"] = [sql]
        evaluation = evaluate_instance(
            row,
            database_root=self.database_root,
            evaluator_script=self.evaluator_script,
            mode="pred",
            timeout_seconds=self.timeout_seconds,
        )
        accepted = evaluation.get("status") == "success"
        return SubmissionJudgement(
            accepted=accepted,
            feedback=(
                "Yes, that result answers what I intended."
                if accepted
                else "No, that result does not answer what I intended."
            ),
            metadata={
                "benchmark": "mini_interact",
                "evaluator": "official_single_instance_sqlite",
                "status": evaluation.get("status"),
                "execution_error": evaluation.get("evaluation_phase_execution_error"),
                "timeout_error": evaluation.get("evaluation_phase_timeout_error"),
                "assertion_error": evaluation.get("evaluation_phase_assertion_error"),
                "effective_test_cases": evaluation.get("effective_total_test_cases"),
                "passed_test_cases": evaluation.get("passed_test_cases"),
                "source_template_unchanged": evaluation.get(
                    "source_template_unchanged"
                ),
            },
        )

    def answer(
        self,
        task: BenchmarkTask,
        question: str,
        history: tuple[dict[str, Any], ...],
    ) -> str | ClarificationAnswer:
        return self.answerer.answer(task, question, history, self._row(task))


class OfficialMiniInteractLiveOracle(MiniInteractExecutionOracle):
    """Pinned live-agent default scorer for the standardized comparison."""

    def __init__(
        self,
        *,
        rows: list[dict[str, Any]],
        database_root: Path,
        answerer: PrivateAnswerer,
        agent_root: Path = DEFAULT_AGENT_ROOT,
        timeout_seconds: float = 180.0,
    ) -> None:
        super().__init__(
            rows=rows,
            database_root=database_root,
            answerer=answerer,
            timeout_seconds=timeout_seconds,
        )
        self.agent_root = agent_root.resolve()

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        database_root: Path,
        answerer: PrivateAnswerer,
        agent_root: Path = DEFAULT_AGENT_ROOT,
        timeout_seconds: float = 180.0,
    ) -> OfficialMiniInteractLiveOracle:
        return cls(
            rows=_load_jsonl(path),
            database_root=database_root,
            answerer=answerer,
            agent_root=agent_root,
            timeout_seconds=timeout_seconds,
        )

    def judge(
        self,
        task: BenchmarkTask,
        sql: str,
        result: QueryResult,
    ) -> SubmissionJudgement:
        row = self._row(task)
        if result.observation.status != "ok":
            evaluation: dict[str, Any] = {
                "status": "failed",
                "execution_error": True,
                "timeout_error": result.observation.status == "timeout",
                "assertion_error": False,
                "passed_test_cases": 0,
                "effective_total_test_cases": 1,
                "evaluator": "official_live_agent_test_case_default",
                "runtime_adaptation": "read_only_precheck",
            }
        else:
            evaluation = evaluate_live_instance(
                row,
                sql,
                database_root=self.database_root,
                agent_root=self.agent_root,
                timeout_seconds=self.timeout_seconds,
            )
        accepted = evaluation.get("status") == "success"
        return SubmissionJudgement(
            accepted=accepted,
            feedback=(
                "SQL passed the Mini-Interact scorer."
                if accepted
                else "Submitted SQL failed the Mini-Interact scorer."
            ),
            metadata={
                "benchmark": "mini_interact",
                **evaluation,
            },
        )
