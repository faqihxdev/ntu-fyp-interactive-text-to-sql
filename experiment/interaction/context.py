"""Deterministic schema, description, and targeted value context for BIRD."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from contextlib import closing
from threading import Lock
from typing import Any

from .trajectory import BenchmarkTask


QUOTED = re.compile(r"['\"`]([^'\"`]{2,80})['\"`]")
CAPITALIZED = re.compile(
    r"\b(?:[A-Z][A-Za-z0-9&'_-]+(?:\s+[A-Z][A-Za-z0-9&'_-]+){0,3})\b"
)
UPPER_CODE = re.compile(r"\b[A-Z][A-Z0-9_-]{1,12}\b")
TOKEN = re.compile(r"[A-Za-z0-9]+")
QUESTION_STARTERS = {
    "what",
    "which",
    "who",
    "when",
    "where",
    "why",
    "how",
    "find",
    "give",
    "list",
    "show",
    "calculate",
    "compute",
    "among",
    "for",
    "is",
    "are",
    "does",
    "do",
}


def _quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _normal_tokens(text: str) -> set[str]:
    return {token.lower() for token in TOKEN.findall(text) if len(token) > 1}


@dataclass(frozen=True)
class RetrievedValue:
    term: str
    table: str
    column: str
    value: Any
    match_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term,
            "table": self.table,
            "column": self.column,
            "value": self.value,
            "match_type": self.match_type,
        }


@dataclass(frozen=True)
class ContextBundle:
    prompt: str
    prompt_sha256: str
    schema_sha256: str
    description_sha256: str
    retrieved_values: tuple[RetrievedValue, ...]
    retrieval_queries: int
    retrieval_elapsed_ms: float
    retrieval_cache_hit: bool


class ContextBuilder:
    """Build complete BIRD context while hiding every gold/evaluator field."""

    PROMPT_VERSION = "bird-agent-v3"

    def __init__(
        self,
        *,
        retrieval_query_budget: int = 16,
        retrieval_vm_step_budget: int = 5_000_000,
        retrieval_match_limit: int = 10,
    ) -> None:
        self.retrieval_query_budget = retrieval_query_budget
        self.retrieval_vm_step_budget = retrieval_vm_step_budget
        self.retrieval_match_limit = retrieval_match_limit
        self._value_cache: dict[
            tuple[str, int, str], tuple[RetrievedValue, ...]
        ] = {}
        self._cache_lock = Lock()

    @staticmethod
    def _connect(database_path: Path) -> sqlite3.Connection:
        return sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro&immutable=1",
            uri=True,
        )

    def _schema(self, database_path: Path) -> tuple[str, list[dict[str, str]]]:
        with closing(self._connect(database_path)) as connection:
            rows = connection.execute(
                "SELECT name, sql FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            schema = "\n\n".join(str(sql) for _, sql in rows if sql)
            columns: list[dict[str, str]] = []
            for table, _ in rows:
                quoted = _quote_identifier(str(table))
                for column in connection.execute(f"PRAGMA table_info({quoted})"):
                    columns.append(
                        {
                            "table": str(table),
                            "column": str(column[1]),
                            "type": str(column[2] or ""),
                        }
                    )
        return schema, columns

    @staticmethod
    def _descriptions(database_path: Path) -> str:
        description_root = database_path.parent / "database_description"
        sections: list[str] = []
        if not description_root.is_dir():
            return "(No separate column descriptions were released.)"
        for path in sorted(description_root.glob("*.csv")):
            lines: list[str] = []
            try:
                with path.open(encoding="utf-8-sig", errors="replace", newline="") as handle:
                    for row in csv.DictReader(handle):
                        original = str(row.get("original_column_name") or "").strip()
                        cleaned = str(row.get("column_name") or "").strip()
                        description = str(row.get("column_description") or "").strip()
                        data_format = str(row.get("data_format") or "").strip()
                        value_description = str(row.get("value_description") or "").strip()
                        name = cleaned or original
                        detail = description or original
                        extras = "; ".join(
                            value
                            for value in (data_format, value_description)
                            if value
                        )
                        line = f"- {name}: {detail}"
                        if extras:
                            line += f" ({extras})"
                        lines.append(line)
            except (csv.Error, OSError):
                continue
            if lines:
                sections.append(f"TABLE {path.stem}\n" + "\n".join(lines))
        return "\n\n".join(sections) or "(No readable descriptions.)"

    @staticmethod
    def _terms(task: BenchmarkTask) -> list[str]:
        text = "\n".join(value for value in (task.question, task.evidence) if value)
        candidates: list[str] = []
        candidates.extend(match.strip() for match in QUOTED.findall(text))
        candidates.extend(match.strip() for match in UPPER_CODE.findall(text))
        candidates.extend(match.strip() for match in CAPITALIZED.findall(text))
        seen: set[str] = set()
        terms: list[str] = []
        for candidate in candidates:
            normalized = " ".join(candidate.split())
            lowered = normalized.lower()
            if (
                lowered in seen
                or lowered in QUESTION_STARTERS
                or len(normalized) < 2
                or normalized.isdigit()
            ):
                continue
            seen.add(lowered)
            terms.append(normalized)
            if len(terms) == 4:
                break
        return terms

    @staticmethod
    def _rank_columns(
        columns: list[dict[str, str]], task: BenchmarkTask
    ) -> list[dict[str, str]]:
        question_tokens = _normal_tokens(
            " ".join(value for value in (task.question, task.evidence) if value)
        )

        def rank(column: dict[str, str]) -> tuple[int, int, str, str]:
            name_tokens = _normal_tokens(column["table"] + " " + column["column"])
            overlap = len(question_tokens & name_tokens)
            text_type = int(
                any(token in column["type"].upper() for token in ("CHAR", "TEXT", "CLOB"))
                or not column["type"]
            )
            return (-overlap, -text_type, column["table"], column["column"])

        return sorted(columns, key=rank)

    def _retrieve_values(
        self,
        task: BenchmarkTask,
        database_path: Path,
        columns: list[dict[str, str]],
    ) -> tuple[tuple[RetrievedValue, ...], int, float, bool]:
        started = time.monotonic()
        terms = self._terms(task)
        if not terms or not self.retrieval_query_budget:
            return (), 0, 0.0, False

        # Ranking depends on the full task text, so cross-task caching by term
        # alone would make context depend on evaluation order.
        cache_key = (
            str(database_path.resolve()),
            task.question_id,
            "\u241f".join(terms).lower(),
        )
        with self._cache_lock:
            cached = self._value_cache.get(cache_key)
        if cached is not None:
            return cached, 0, round((time.monotonic() - started) * 1_000, 3), True

        ranked_columns = self._rank_columns(columns, task)
        matches: list[RetrievedValue] = []
        query_count = 0
        columns_per_term = max(
            1, self.retrieval_query_budget // max(1, len(terms) * 2)
        )
        with closing(self._connect(database_path)) as connection:
            connection.execute("PRAGMA query_only = ON")
            for term in terms:
                term_columns = ranked_columns[:columns_per_term]
                for column in term_columns:
                    if query_count >= self.retrieval_query_budget:
                        break
                    table = _quote_identifier(column["table"])
                    name = _quote_identifier(column["column"])
                    exact_statement = (
                        f"SELECT DISTINCT {name} FROM {table} "
                        f"WHERE {name} = ? COLLATE NOCASE LIMIT 2"
                    )
                    query_count += 1
                    try:
                        values = self._execute_with_step_budget(
                            connection, exact_statement, term
                        )
                    except sqlite3.Error:
                        values = []
                    match_type = "exact"
                    allow_contains = len(term) >= 5 and not term.isupper()
                    if (
                        not values
                        and allow_contains
                        and query_count < self.retrieval_query_budget
                    ):
                        contains_statement = (
                            f"SELECT DISTINCT {name} FROM {table} "
                            f"WHERE instr(lower(CAST({name} AS TEXT)), lower(?)) > 0 "
                            "LIMIT 2"
                        )
                        query_count += 1
                        try:
                            values = self._execute_with_step_budget(
                                connection, contains_statement, term
                            )
                            match_type = "contains"
                        except sqlite3.Error:
                            values = []
                    for (value,) in values:
                        if value is None or len(str(value)) > 160:
                            continue
                        matches.append(
                            RetrievedValue(
                                term=term,
                                table=column["table"],
                                column=column["column"],
                                value=value,
                                match_type=match_type,
                            )
                        )
                        if len(matches) >= self.retrieval_match_limit:
                            break
                    if len(matches) >= self.retrieval_match_limit:
                        break
                if len(matches) >= self.retrieval_match_limit:
                    break

        result = tuple(matches)
        with self._cache_lock:
            self._value_cache[cache_key] = result
        return (
            result,
            query_count,
            round((time.monotonic() - started) * 1_000, 3),
            False,
        )

    def _execute_with_step_budget(
        self,
        connection: sqlite3.Connection,
        statement: str,
        term: str,
    ) -> list[tuple[Any, ...]]:
        """Execute retrieval with a deterministic VM-instruction ceiling.

        A wall-clock timeout made model-visible values depend on machine load.
        SQLite's progress callback instead bounds the same query plan by a fixed
        amount of work, so repeated runs over the immutable database are stable.
        """

        progress_interval = 5_000
        callbacks_remaining = max(
            1, self.retrieval_vm_step_budget // progress_interval
        )

        def stop_after_budget() -> int:
            nonlocal callbacks_remaining
            callbacks_remaining -= 1
            return int(callbacks_remaining < 0)

        connection.set_progress_handler(stop_after_budget, progress_interval)
        try:
            return connection.execute(statement, (term,)).fetchall()
        finally:
            connection.set_progress_handler(None, 0)

    def build(self, task: BenchmarkTask, database_path: Path) -> ContextBundle:
        schema, columns = self._schema(database_path)
        descriptions = self._descriptions(database_path)
        (
            values,
            retrieval_queries,
            retrieval_elapsed_ms,
            retrieval_cache_hit,
        ) = self._retrieve_values(task, database_path, columns)
        value_text = (
            "\n".join(
                f"- {value.table}.{value.column} = {json.dumps(value.value, ensure_ascii=False)} "
                f"({value.match_type} match for {json.dumps(value.term, ensure_ascii=False)})"
                for value in values
            )
            or "(No targeted value matches were found within the fixed retrieval budget.)"
        )
        evidence = task.evidence.strip() if task.evidence else "(None supplied.)"
        prompt = f"""DATABASE: {task.db_id}
SQL DIALECT: SQLite

SCHEMA (complete; includes released PK/FK constraints)
{schema}

COLUMN DESCRIPTIONS
{descriptions}

DETERMINISTIC VALUE MATCHES
{value_text}

QUESTION
{task.question}

BENCHMARK EVIDENCE
{evidence}
""".strip()
        return ContextBundle(
            prompt=prompt,
            prompt_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            schema_sha256=hashlib.sha256(schema.encode("utf-8")).hexdigest(),
            description_sha256=hashlib.sha256(
                descriptions.encode("utf-8")
            ).hexdigest(),
            retrieved_values=values,
            retrieval_queries=retrieval_queries,
            retrieval_elapsed_ms=retrieval_elapsed_ms,
            retrieval_cache_hit=retrieval_cache_hit,
        )
