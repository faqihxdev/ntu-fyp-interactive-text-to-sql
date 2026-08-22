"""Feedback-grounded recovery protocol for interactive Text-to-SQL.

The controller owns execution and budgets; this module owns the smaller
recovery contract presented to and checked against the policy model.  It turns
an otherwise loose event history into binding clarification requirements and
deterministic progress checks without reading evaluator-only gold data.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

RECOVERY_MODES = {"baseline", "grounded"}
_SQL_SPACE = re.compile(r"\s+")


def normalize_recovery_sql(sql: str) -> str:
    """Normalize SQL only enough to detect an exact repeated submission."""

    return _SQL_SPACE.sub(" ", sql.strip().rstrip(";")).casefold()


@dataclass(frozen=True)
class ClarificationRequirement:
    user_turn: int
    target: str
    question: str
    answer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_turn": self.user_turn,
            "target": self.target,
            "question": self.question,
            "answer": self.answer,
        }


@dataclass(frozen=True)
class RecoverySnapshot:
    """Compact, model-visible recovery state derived from public history."""

    requirements: tuple[ClarificationRequirement, ...]
    rejected_attempts: int
    rejected_change_summaries: tuple[str, ...]
    asked_targets: tuple[str, ...]
    latest_request_contract: tuple[str, ...]

    @property
    def required_user_turns(self) -> tuple[int, ...]:
        return tuple(item.user_turn for item in self.requirements)

    def to_prompt_dict(self) -> dict[str, Any]:
        return {
            "binding_clarifications": [item.to_dict() for item in self.requirements],
            "required_grounded_user_turns": list(self.required_user_turns),
            "rejected_attempts": self.rejected_attempts,
            "previous_change_summaries": list(self.rejected_change_summaries),
            "previous_clarification_targets": list(self.asked_targets),
            "latest_request_contract": list(self.latest_request_contract),
        }


@dataclass(frozen=True)
class RecoveryActionView:
    kind: str
    sql: str | None
    clarification_target: str | None
    grounded_user_turns: tuple[int, ...] | None
    change_summary: str | None
    request_contract: tuple[str, ...] | None


@dataclass(frozen=True)
class RecoveryAssessment:
    valid: bool
    violations: tuple[str, ...] = ()


class FeedbackRecovery:
    """Compile and enforce the gold-free answer-incorporation contract."""

    VERSION = "feedback-grounded-recovery-v2"

    def __init__(self, mode: str) -> None:
        if mode not in RECOVERY_MODES:
            raise ValueError(f"Unknown recovery mode: {mode}")
        self.mode = mode

    @property
    def enabled(self) -> bool:
        return self.mode == "grounded"

    def snapshot(self, history: tuple[dict[str, Any], ...]) -> RecoverySnapshot:
        requirements: list[ClarificationRequirement] = []
        rejected_change_summaries: list[str] = []
        asked_targets: list[str] = []
        latest_request_contract: tuple[str, ...] = ()
        rejected_attempts = 0
        for record in history:
            raw_contract = record.get("request_contract") or []
            contract = tuple(
                str(item).strip() for item in raw_contract if str(item).strip()
            )
            if contract:
                latest_request_contract = contract
            kind = record.get("kind")
            if kind == "user_clarification":
                target = str(record.get("clarification_target") or "").strip()
                requirements.append(
                    ClarificationRequirement(
                        user_turn=int(record["user_turn"]),
                        target=target or "unspecified request detail",
                        question=str(record.get("question") or "").strip(),
                        answer=str(record.get("answer") or "").strip(),
                    )
                )
                if target:
                    asked_targets.append(target)
            elif kind == "submission_rejected":
                rejected_attempts += 1
                summary = str(record.get("change_summary") or "").strip()
                if summary:
                    rejected_change_summaries.append(summary)
        return RecoverySnapshot(
            requirements=tuple(requirements),
            rejected_attempts=rejected_attempts,
            rejected_change_summaries=tuple(rejected_change_summaries),
            asked_targets=tuple(asked_targets),
            latest_request_contract=latest_request_contract,
        )

    def assess(
        self,
        action: RecoveryActionView,
        snapshot: RecoverySnapshot,
        *,
        rejected_sql: set[str],
    ) -> RecoveryAssessment:
        """Check only properties the controller can verify without gold data."""

        if not self.enabled:
            return RecoveryAssessment(valid=True)

        violations: list[str] = []
        request_contract = tuple(
            str(item).strip() for item in (action.request_contract or ())
        )
        if any(not item for item in request_contract):
            violations.append("request_contract entries must be non-empty")
        normalized_contract = [item.casefold() for item in request_contract]
        if len(normalized_contract) != len(set(normalized_contract)):
            violations.append("request_contract contains duplicate entries")
        if action.kind in {"query_database", "submit_sql"} and not request_contract:
            violations.append(
                "a probe/submission must include the current request_contract"
            )
        if (
            action.kind in {"query_database", "submit_sql"}
            and snapshot.latest_request_contract
            and len(request_contract) < len(snapshot.latest_request_contract)
        ):
            violations.append(
                "request_contract may refine prior entries but must not drop them"
            )
        expected_turns = snapshot.required_user_turns
        actual_turns = tuple(action.grounded_user_turns or ())
        if len(actual_turns) != len(set(actual_turns)):
            violations.append("grounded_user_turns contains duplicates")
        if set(actual_turns) != set(expected_turns):
            violations.append(
                "grounded_user_turns must contain every prior clarification turn "
                f"exactly once; expected {list(expected_turns)}"
            )

        if (
            action.kind == "ask_user"
            and not (action.clarification_target or "").strip()
        ):
            violations.append(
                "ask_user must name the exact phrase or request detail being clarified"
            )

        if (
            action.kind in {"query_database", "submit_sql"}
            and (expected_turns or snapshot.rejected_attempts)
            and not (action.change_summary or "").strip()
        ):
            violations.append(
                "a probe/submission after feedback must state its concrete change"
            )

        if (
            action.kind == "submit_sql"
            and action.sql
            and normalize_recovery_sql(action.sql) in rejected_sql
        ):
            violations.append("the submitted SQL exactly repeats rejected SQL")

        return RecoveryAssessment(valid=not violations, violations=tuple(violations))
