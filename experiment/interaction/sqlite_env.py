"""Immutable SQLite execution and EX-compatible result fingerprints."""

from __future__ import annotations

import base64
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any


READ_QUERY = re.compile(r"^\s*(?:(?:--[^\n]*\n)\s*)*(SELECT|WITH)\b", re.IGNORECASE)


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bytes):
        return {"bytes_base64": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (str, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return {"float": repr(value)}
        return value
    return str(value)


def _equivalence_value(value: Any) -> list[str]:
    """Encode values so Python/official-evaluator numeric equality is retained."""
    if value is None:
        return ["null", ""]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and not math.isfinite(value):
            return ["number", repr(value)]
        number = Decimal(str(value)).normalize()
        return ["number", format(number, "f")]
    return [type(value).__name__, str(value)]


def _row_key(row: tuple[Any, ...]) -> str:
    return json.dumps(
        [_equivalence_value(value) for value in row],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _hash_strings(values: list[str]) -> str:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def result_fingerprints(
    columns: tuple[str, ...], rows: tuple[tuple[Any, ...], ...]
) -> dict[str, str]:
    row_keys = [_row_key(row) for row in rows]
    bird_set = sorted(set(row_keys))
    visible_multiset = sorted(Counter(row_keys).elements())
    column_key = json.dumps(columns, ensure_ascii=False, separators=(",", ":"))
    return {
        "bird_set": _hash_strings(bird_set),
        "visible_multiset": _hash_strings([column_key, *visible_multiset]),
        "ordered_visible": _hash_strings([column_key, *row_keys]),
    }


@dataclass(frozen=True)
class ExecutionObservation:
    status: str
    columns: tuple[str, ...]
    preview: tuple[tuple[Any, ...], ...]
    row_count: int
    truncated: bool
    elapsed_ms: float
    fingerprints: dict[str, str]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["columns"] = list(self.columns)
        payload["preview"] = [
            [_json_value(value) for value in row] for row in self.preview
        ]
        return payload

    def tool_output(self) -> str:
        payload = self.to_dict()
        payload.pop("fingerprints", None)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class QueryResult:
    observation: ExecutionObservation
    rows: tuple[tuple[Any, ...], ...]


class SQLiteEnvironment:
    """Read-only local database module used by probes, submissions, and scoring."""

    def __init__(
        self,
        database_path: Path,
        *,
        timeout_seconds: float = 30.0,
        max_rows: int = 500_000,
        preview_rows: int = 12,
    ) -> None:
        self.database_path = database_path.resolve()
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.preview_rows = preview_rows

    def _connect(self) -> sqlite3.Connection:
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        return sqlite3.connect(
            f"{self.database_path.as_uri()}?mode=ro&immutable=1",
            uri=True,
        )

    def execute(self, sql: str) -> QueryResult:
        started = time.monotonic()
        clean_sql = sql.strip()
        if not READ_QUERY.match(clean_sql):
            observation = ExecutionObservation(
                status="rejected_non_read_query",
                columns=(),
                preview=(),
                row_count=0,
                truncated=False,
                elapsed_ms=round((time.monotonic() - started) * 1_000, 3),
                fingerprints={},
                error="Only one read-only SELECT or WITH statement is allowed.",
            )
            return QueryResult(observation=observation, rows=())

        deadline = time.monotonic() + self.timeout_seconds
        rows: list[tuple[Any, ...]] = []
        columns: tuple[str, ...] = ()
        truncated = False
        try:
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA query_only = ON")
                connection.set_progress_handler(
                    lambda: int(time.monotonic() > deadline), 10_000
                )
                cursor = connection.execute(clean_sql)
                columns = tuple(
                    str(description[0]) for description in (cursor.description or ())
                )
                while True:
                    batch = cursor.fetchmany(1_000)
                    if not batch:
                        break
                    remaining = self.max_rows - len(rows)
                    rows.extend(tuple(row) for row in batch[:remaining])
                    if len(batch) > remaining or len(rows) >= self.max_rows:
                        truncated = True
                        break
        except sqlite3.Error as exc:
            message = str(exc)
            status = "timeout" if "interrupted" in message.lower() else "sql_error"
            observation = ExecutionObservation(
                status=status,
                columns=columns,
                preview=tuple(rows[: self.preview_rows]),
                row_count=len(rows),
                truncated=truncated,
                elapsed_ms=round((time.monotonic() - started) * 1_000, 3),
                fingerprints={},
                error=message,
            )
            return QueryResult(observation=observation, rows=tuple(rows))

        row_tuple = tuple(rows)
        fingerprints = (
            {} if truncated else result_fingerprints(columns, row_tuple)
        )
        observation = ExecutionObservation(
            status="ok",
            columns=columns,
            preview=row_tuple[: self.preview_rows],
            row_count=len(row_tuple),
            truncated=truncated,
            elapsed_ms=round((time.monotonic() - started) * 1_000, 3),
            fingerprints=fingerprints,
            error=(
                f"Result exceeded the {self.max_rows}-row safety limit."
                if truncated
                else None
            ),
        )
        return QueryResult(observation=observation, rows=row_tuple)


def bird_execution_equal(left: QueryResult, right: QueryResult) -> bool:
    """Match the released BIRD SQLite evaluator's set-of-row semantics."""
    if left.observation.status != "ok" or right.observation.status != "ok":
        return False
    if left.observation.truncated or right.observation.truncated:
        return False
    return set(left.rows) == set(right.rows)
