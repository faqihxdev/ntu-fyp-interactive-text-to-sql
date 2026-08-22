"""Versioned, gold-free trajectory records shared by every policy arm."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BenchmarkTask:
    """Model-visible benchmark fields; deliberately excludes gold SQL."""

    question_id: int | str
    db_id: str
    question: str
    evidence: str | None
    difficulty: str


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


@dataclass(frozen=True)
class TrajectoryEvent:
    sequence: int
    kind: str
    actor: str
    turn_index: int
    elapsed_ms: float
    payload: dict[str, Any] = field(default_factory=dict)
    usage: Usage | None = None


@dataclass
class Trajectory:
    schema_version: int
    run_id: str
    policy: str
    task: BenchmarkTask
    config: dict[str, Any]
    started_at: str
    events: list[TrajectoryEvent] = field(default_factory=list)
    final_sql: str | None = None
    termination: str | None = None
    total_elapsed_ms: float | None = None

    @classmethod
    def start(
        cls,
        *,
        run_id: str,
        policy: str,
        task: BenchmarkTask,
        config: dict[str, Any],
    ) -> Trajectory:
        return cls(
            schema_version=SCHEMA_VERSION,
            run_id=run_id,
            policy=policy,
            task=task,
            config=config,
            started_at=utc_now(),
        )

    def add_event(
        self,
        *,
        kind: str,
        actor: str,
        elapsed_ms: float,
        payload: dict[str, Any] | None = None,
        usage: Usage | None = None,
        turn_index: int = 0,
    ) -> None:
        self.events.append(
            TrajectoryEvent(
                sequence=len(self.events),
                kind=kind,
                actor=actor,
                turn_index=turn_index,
                elapsed_ms=round(elapsed_ms, 3),
                payload=payload or {},
                usage=usage,
            )
        )

    def finish(
        self,
        *,
        termination: str,
        total_elapsed_ms: float,
        final_sql: str | None = None,
    ) -> None:
        self.termination = termination
        self.total_elapsed_ms = round(total_elapsed_ms, 3)
        self.final_sql = final_sql
        self.validate()

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported trajectory schema: {self.schema_version}")
        if not self.run_id or not self.policy:
            raise ValueError("Trajectory run_id and policy are required")
        if [event.sequence for event in self.events] != list(range(len(self.events))):
            raise ValueError("Trajectory event sequences must be contiguous")
        if self.termination is not None and self.total_elapsed_ms is None:
            raise ValueError("A finished trajectory requires total_elapsed_ms")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Trajectory:
        trajectory = cls(
            schema_version=int(payload["schema_version"]),
            run_id=str(payload["run_id"]),
            policy=str(payload["policy"]),
            task=BenchmarkTask(**payload["task"]),
            config=dict(payload["config"]),
            started_at=str(payload["started_at"]),
            events=[
                TrajectoryEvent(
                    sequence=int(event["sequence"]),
                    kind=str(event["kind"]),
                    actor=str(event["actor"]),
                    turn_index=int(event["turn_index"]),
                    elapsed_ms=float(event["elapsed_ms"]),
                    payload=dict(event.get("payload", {})),
                    usage=(Usage(**event["usage"]) if event.get("usage") else None),
                )
                for event in payload.get("events", [])
            ],
            final_sql=payload.get("final_sql"),
            termination=payload.get("termination"),
            total_elapsed_ms=payload.get("total_elapsed_ms"),
        )
        trajectory.validate()
        return trajectory

    @property
    def total_usage(self) -> Usage:
        records = [event.usage for event in self.events if event.usage is not None]
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


def append_trajectory(path: Path, trajectory: Trajectory) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(stable_json(trajectory.to_dict()) + "\n")


def load_trajectories(path: Path) -> list[Trajectory]:
    trajectories: list[Trajectory] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                trajectories.append(Trajectory.from_dict(json.loads(line)))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Invalid trajectory JSONL at {path}:{line_number}: {exc}"
                ) from exc
    return trajectories
