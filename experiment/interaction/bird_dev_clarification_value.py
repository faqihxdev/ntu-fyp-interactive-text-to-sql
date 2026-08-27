"""Matched control for the value of clarification beyond extra SQL attempts.

This protocol is deliberately separate from the completed five-arm timing
study.  Every arm receives one physically shared initial SQL.  After a hidden
evaluator rejection, the two recovery arms receive the same normal-SQL attempt
cap; only the reactive arm receives clarification questions and answers.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import time
from typing import Any

from .bird_dev_matched import (
    BirdDevMatchedController,
    build_schema_only_context,
    database_path,
)
from .trajectory import BenchmarkTask


PROTOCOL_VERSION = "bird-dev-clarification-value-v1"
ARMS = ("no_user", "sql_only_recovery", "reactive_clarification")
RECOVERY_ARMS = ARMS[1:]


def branch_execution_order(question_id: int | str) -> tuple[str, str]:
    """Balance the two recovery branches against mutable-service time drift."""

    digest = hashlib.sha256(
        f"{PROTOCOL_VERSION}:{question_id}".encode("utf-8")
    ).digest()
    return RECOVERY_ARMS if digest[0] % 2 == 0 else RECOVERY_ARMS[::-1]


class BirdDevClarificationValueController(BirdDevMatchedController):
    """Execute the frozen three-arm clarification-value comparison."""

    def run_shared_initial(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
    ) -> dict[str, Any]:
        record = super().run_shared_initial(task, schema=schema)
        record["protocol"] = PROTOCOL_VERSION
        return record

    def _finish_arm(self, **kwargs: Any) -> dict[str, Any]:
        record = super()._finish_arm(**kwargs)
        arm = str(record["arm"])
        record["protocol"] = PROTOCOL_VERSION
        record["recovery_mode"] = {
            "no_user": "none",
            "sql_only_recovery": "evaluator_rejection_plus_sql_retry",
            "reactive_clarification": (
                "evaluator_rejection_plus_sra_question_and_simulated_answer"
            ),
        }[arm]
        record["normal_sql_attempt_cap"] = (
            1 if arm == "no_user" else self.config.max_normal_sql_attempts
        )
        record["user_turn_cap"] = (
            self.config.max_user_turns if arm == "reactive_clarification" else 0
        )
        return record

    def _arm_error(
        self,
        task: BenchmarkTask,
        arm: str,
        exc: Exception,
        *,
        uses_shared: bool,
    ) -> dict[str, Any]:
        record = super()._arm_error(
            task,
            arm,
            exc,
            uses_shared=uses_shared,
        )
        record["protocol"] = PROTOCOL_VERSION
        return record

    def run_sql_only_recovery(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        shared: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Retry rejected SQL without asking a user-facing question."""

        arm = "sql_only_recovery"
        started = time.monotonic()
        events: list[dict[str, Any]] = []
        candidates = [dict(value) for value in shared["candidates"]]
        incorrect_sqls: list[str] = []
        active_sql = str(shared["active_sql"])
        self._append_unique(incorrect_sqls, active_sql)
        normal_attempts = 1

        if any(candidate["official_ex"] for candidate in candidates):
            return self._finish_arm(
                task=task,
                arm=arm,
                started=started,
                events=events,
                candidates=candidates,
                question_answers=[],
                termination="accepted_initial",
                uses_shared_prefix=True,
                normal_sql_attempts=normal_attempts,
            )

        while normal_attempts < self.config.max_normal_sql_attempts:
            generation_events, generated, active_sql = self._generate_and_score(
                task=task,
                arm=arm,
                schema=schema,
                prompt=self._self_debug_sql_prompt(
                    schema=schema,
                    task=task,
                    incorrect_sqls=incorrect_sqls,
                    question_answers=(),
                ),
                call_kind=f"sql_self_debug_{normal_attempts + 1}",
                user_turn=0,
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
                    question_answers=[],
                    termination="accepted_after_sql_only_recovery",
                    uses_shared_prefix=True,
                    normal_sql_attempts=normal_attempts,
                )
            self._append_unique(incorrect_sqls, active_sql)

        return self._finish_arm(
            task=task,
            arm=arm,
            started=started,
            events=events,
            candidates=candidates,
            question_answers=[],
            termination="sql_attempt_limit_reached",
            uses_shared_prefix=True,
            normal_sql_attempts=normal_attempts,
        )

    def run_reactive_clarification(
        self,
        task: BenchmarkTask,
        *,
        schema: str,
        shared: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Run source-derived reactive clarification after a rejected SQL."""

        arm = "reactive_clarification"
        started = time.monotonic()
        candidates = [dict(value) for value in shared["candidates"]]
        normal_attempts = 1
        if any(candidate["official_ex"] for candidate in candidates):
            return self._finish_arm(
                task=task,
                arm=arm,
                started=started,
                events=[],
                candidates=candidates,
                question_answers=[],
                termination="accepted_initial",
                uses_shared_prefix=True,
                normal_sql_attempts=normal_attempts,
            )

        incorrect_sqls: list[str] = []
        self._append_unique(incorrect_sqls, str(shared["active_sql"]))
        return self._run_reactive_loop(
            task,
            schema=schema,
            arm=arm,
            started=started,
            events=[],
            candidates=candidates,
            question_answers=[],
            incorrect_sqls=incorrect_sqls,
            normal_attempts=normal_attempts,
            uses_shared_prefix=True,
            allow_no_ambiguity=False,
        )

    def run_task(self, task: BenchmarkTask) -> dict[str, Any]:
        started = time.monotonic()
        schema = build_schema_only_context(database_path(self.database_root, task))
        shared = self.run_shared_initial(task, schema=schema)
        executed: dict[str, dict[str, Any]] = {
            "no_user": self.no_user_record(task, shared)
        }
        order = branch_execution_order(task.question_id)
        for arm in order:
            try:
                if arm == "sql_only_recovery":
                    executed[arm] = self.run_sql_only_recovery(
                        task,
                        schema=schema,
                        shared=shared,
                    )
                else:
                    executed[arm] = self.run_reactive_clarification(
                        task,
                        schema=schema,
                        shared=shared,
                    )
            except Exception as exc:  # noqa: BLE001 - retain arm failures.
                executed[arm] = self._arm_error(
                    task,
                    arm,
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
            "branch_execution_order": list(order),
            "arms": {arm: executed[arm] for arm in ARMS},
            "total_elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
