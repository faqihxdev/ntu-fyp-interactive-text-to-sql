"""Shared evaluator judgments and simulated-user answers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .sqlite_env import QueryResult
from .trajectory import BenchmarkTask, Usage


REVIEW_SOURCES = {"AmbQuestion", "AmbColumn", "AmbOutput", "AmbValue", "Other"}


@dataclass(frozen=True)
class SubmissionJudgement:
    accepted: bool
    feedback: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ClarificationAnswer:
    """Answer text, simulator metadata and recorded model usage."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None
    elapsed_ms: float | None = None


class InteractionOracle(Protocol):
    """Evaluate submissions and answer clarification questions."""

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
