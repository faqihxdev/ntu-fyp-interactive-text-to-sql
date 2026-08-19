"""Verify local benchmark snapshots before running interaction experiments.

This command deliberately uses only the Python standard library. Dataset
snapshots live under the git-ignored ``experiment/datasets`` directory; the
generated manifest is tracked so the exact source revisions and local hashes
remain reviewable.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import sqlite3
import subprocess
import time
import warnings
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASETS_ROOT = EXPERIMENT_ROOT / "datasets"
DEFAULT_CLEAN_BIRD_PACKAGE = (
    DEFAULT_DATASETS_ROOT / "bird-sql-dev-20251106-databases.zip"
)
DEFAULT_CLEAN_BIRD_DB_ROOT = (
    DEFAULT_DATASETS_ROOT
    / "bird-sql-dev-20251106-databases"
    / "minidev"
    / "MINIDEV"
    / "dev_databases"
)
DEFAULT_LEGACY_BIRD_DB_ROOT = (
    DEFAULT_DATASETS_ROOT / "bird-dev" / "dev_databases" / "dev_databases"
)
DEFAULT_MANIFEST = EXPERIMENT_ROOT / "manifests" / "datasets-readiness.json"

CLEAN_BIRD_PACKAGE_FILE_ID = "13VLWIwpw5E3d5DUkMvzw7hvHE67a4XkG"
CLEAN_BIRD_PACKAGE_SHA256 = (
    "aeb211c0e39010bbdae3838bb5e8bd27dc446ed77495b1709f85ccc9bf67f2be"
)

CLEAN_BIRD_SOURCE = "https://huggingface.co/datasets/birdsql/bird_sql_dev_20251106"
MINI_INTERACT_SOURCE = "https://huggingface.co/datasets/birdsql/mini-interact"
BIRD_INTERACT_SOURCE = "https://github.com/bird-bench/BIRD-Interact"
CLARITY_PAPER = "https://aclanthology.org/2026.acl-industry.86/"


class ReadinessFailure(RuntimeError):
    """Raised when a required local artifact fails an integrity check."""


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReadinessFailure(
                    f"Invalid JSONL in {path} at line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ReadinessFailure(
                    f"Expected an object in {path} at line {line_number}"
                )
            rows.append(row)
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_snapshot_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or ".git" in path.relative_to(root).parts:
            continue
        yield path


def hash_snapshot(root: Path) -> dict[str, Any]:
    files: dict[str, dict[str, Any]] = {}
    total_bytes = 0
    for path in iter_snapshot_files(root):
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        total_bytes += size
        files[relative] = {"bytes": size, "sha256": sha256_file(path)}
    return {
        "file_count": len(files),
        "total_bytes": total_bytes,
        "files": files,
    }


def git_revision(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ReadinessFailure(f"SQLite database is missing: {path}")
    # ``immutable=1`` prevents SQLite from creating -wal/-shm sidecars while
    # auditing frozen benchmark snapshots. Acquisition checks must not mutate
    # the files or their directories.
    return sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro&immutable=1",
        uri=True,
    )


def sqlite_quick_check(path: Path, timeout_seconds: float = 10.0) -> int:
    deadline = time.monotonic() + timeout_seconds
    with closing(_connect_readonly(path)) as connection:
        connection.set_progress_handler(
            lambda: int(time.monotonic() > deadline), 10_000
        )
        result = connection.execute("PRAGMA quick_check").fetchone()
        if result != ("ok",):
            raise ReadinessFailure(f"SQLite quick_check failed for {path}: {result}")
        table_count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'"
        ).fetchone()[0]
        if table_count < 1:
            raise ReadinessFailure(f"SQLite database has no tables: {path}")
        return int(table_count)


def sqlite_table_counts(path: Path) -> dict[str, int]:
    """Return deterministic user-table row counts for provenance comparisons."""
    with closing(_connect_readonly(path)) as connection:
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        counts: dict[str, int] = {}
        for table in tables:
            quoted = table.replace('"', '""')
            counts[table] = int(
                connection.execute(f'SELECT COUNT(*) FROM "{quoted}"').fetchone()[0]
            )
        return counts


def check_clean_bird_package(
    archive: Path,
    database_root: Path,
    legacy_database_root: Path,
) -> dict[str, Any]:
    """Verify the database archive linked by the clean BIRD release card."""
    require(archive.is_file(), f"Clean BIRD database archive is missing: {archive}")
    archive_hash = sha256_file(archive)
    require(
        archive_hash == CLEAN_BIRD_PACKAGE_SHA256,
        "Clean BIRD database archive SHA-256 does not match the acquired snapshot",
    )
    require(
        database_root.is_dir(),
        f"Clean BIRD database archive is not extracted at: {database_root}",
    )

    database_files = sorted(database_root.glob("*/*.sqlite"))
    require(
        len(database_files) == 11,
        f"Expected 11 clean BIRD SQLite files, found {len(database_files)}",
    )

    sqlite_files: dict[str, dict[str, Any]] = {}
    logical_differences: dict[str, dict[str, Any]] = {}
    exact_matches = 0
    for database in database_files:
        database_id = database.parent.name
        require(
            database.name == f"{database_id}.sqlite",
            f"Unexpected clean BIRD SQLite path: {database}",
        )
        official_hash = sha256_file(database)
        sqlite_files[database_id] = {
            "bytes": database.stat().st_size,
            "sha256": official_hash,
        }

        legacy = legacy_database_root / database_id / database.name
        if not legacy.is_file():
            logical_differences[database_id] = {"legacy_status": "missing"}
            continue
        legacy_hash = sha256_file(legacy)
        if official_hash == legacy_hash:
            exact_matches += 1
            continue

        official_counts = sqlite_table_counts(database)
        legacy_counts = sqlite_table_counts(legacy)
        row_count_differences = {
            table: {
                "official": official_counts.get(table),
                "legacy": legacy_counts.get(table),
            }
            for table in sorted(set(official_counts) | set(legacy_counts))
            if official_counts.get(table) != legacy_counts.get(table)
        }
        logical_differences[database_id] = {
            "legacy_sha256": legacy_hash,
            "same_bytes": database.stat().st_size == legacy.stat().st_size,
            "row_count_differences": row_count_differences,
        }

    extracted_files = list(iter_snapshot_files(database_root.parents[2]))
    return {
        "status": "pass",
        "source": (
            "https://drive.google.com/file/d/"
            f"{CLEAN_BIRD_PACKAGE_FILE_ID}/view"
        ),
        "archive": {
            "path": str(archive),
            "bytes": archive.stat().st_size,
            "sha256": archive_hash,
        },
        "extracted": {
            "path": str(database_root.parents[2]),
            "file_count": len(extracted_files),
            "total_bytes": sum(path.stat().st_size for path in extracted_files),
        },
        "sqlite_files": sqlite_files,
        "legacy_comparison": {
            "legacy_root": str(legacy_database_root),
            "exact_file_matches": exact_matches,
            "different_files": sorted(logical_differences),
            "logical_differences": logical_differences,
            "interpretation": (
                "The linked clean-release package and legacy BIRD databases are "
                "distinct benchmark environments; results must pin one root."
            ),
        },
    }


def execute_readonly_sql(
    database: Path, sql: str, timeout_seconds: float
) -> int:
    deadline = time.monotonic() + timeout_seconds
    row_count = 0
    with closing(_connect_readonly(database)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.set_progress_handler(
            lambda: int(time.monotonic() > deadline), 10_000
        )
        cursor = connection.execute(sql)
        while True:
            rows = cursor.fetchmany(1_000)
            if not rows:
                break
            row_count += len(rows)
    return row_count


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReadinessFailure(message)


def check_clean_bird(
    dataset_root: Path,
    database_root: Path,
    execute_gold: bool,
    timeout_seconds: float,
    workers: int,
) -> dict[str, Any]:
    data_file = dataset_root / "data" / "dev_20251106-00000-of-00001.json"
    require(data_file.is_file(), f"Clean BIRD data file is missing: {data_file}")
    rows = load_json(data_file)
    require(isinstance(rows, list), "Clean BIRD payload must be a JSON array")
    require(len(rows) == 1_534, f"Expected 1,534 clean BIRD rows, found {len(rows)}")

    required = {"question_id", "db_id", "question", "evidence", "SQL", "difficulty"}
    for index, row in enumerate(rows):
        require(isinstance(row, dict), f"Clean BIRD row {index} is not an object")
        missing = sorted(required - row.keys())
        require(not missing, f"Clean BIRD row {index} is missing {missing}")
        require(bool(str(row["question"]).strip()), f"Row {index} has an empty question")
        require(bool(str(row["SQL"]).strip()), f"Row {index} has empty SQL")

    question_ids = [row["question_id"] for row in rows]
    require(
        len(set(question_ids)) == len(question_ids),
        "Clean BIRD question_id values are not unique",
    )
    database_ids = sorted({str(row["db_id"]) for row in rows})
    database_paths = {
        database_id: database_root / database_id / f"{database_id}.sqlite"
        for database_id in database_ids
    }
    missing_databases = [
        database_id
        for database_id, path in database_paths.items()
        if not path.is_file()
    ]
    require(
        not missing_databases,
        f"Clean BIRD databases are missing: {missing_databases}",
    )

    database_tables = {
        database_id: sqlite_quick_check(path)
        for database_id, path in database_paths.items()
    }

    execution_records: list[dict[str, Any]] = []
    if execute_gold:
        grouped_rows: dict[str, list[dict[str, Any]]] = {
            database_id: [] for database_id in database_ids
        }
        for row in rows:
            grouped_rows[str(row["db_id"])].append(row)

        def execute_database(
            database_id: str, database_rows: list[dict[str, Any]]
        ) -> list[dict[str, Any]]:
            records: list[dict[str, Any]] = []
            for row in database_rows:
                started = time.monotonic()
                try:
                    returned_rows = execute_readonly_sql(
                        database_paths[database_id],
                        str(row["SQL"]),
                        timeout_seconds,
                    )
                    records.append(
                        {
                            "question_id": row["question_id"],
                            "db_id": database_id,
                            "status": "pass",
                            "returned_rows": returned_rows,
                            "seconds": round(time.monotonic() - started, 3),
                        }
                    )
                except (sqlite3.Error, ReadinessFailure) as exc:
                    message = str(exc)
                    records.append(
                        {
                            "question_id": row["question_id"],
                            "db_id": database_id,
                            "status": (
                                "timeout"
                                if "interrupted" in message.lower()
                                else "error"
                            ),
                            "error": message,
                            "seconds": round(time.monotonic() - started, 3),
                        }
                    )
            return records

        with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
            futures = {
                executor.submit(
                    execute_database, database_id, database_rows
                ): database_id
                for database_id, database_rows in grouped_rows.items()
            }
            for future in as_completed(futures):
                database_id = futures[future]
                database_records = future.result()
                execution_records.extend(database_records)
                failures = sum(
                    record["status"] != "pass" for record in database_records
                )
                print(
                    f"Clean BIRD execution: {database_id} "
                    f"({len(database_records) - failures}/{len(database_records)} passed)",
                    flush=True,
                )

    execution_records.sort(key=lambda record: int(record["question_id"]))
    execution_failures = [
        record for record in execution_records if record["status"] != "pass"
    ]
    execution_timeouts = [
        record for record in execution_failures if record["status"] == "timeout"
    ]
    execution_errors = [
        record for record in execution_failures if record["status"] == "error"
    ]
    execution_passes = [
        record for record in execution_records if record["status"] == "pass"
    ]
    slowest_passes = sorted(
        execution_passes,
        key=lambda record: float(record["seconds"]),
        reverse=True,
    )[:10]

    if execution_errors:
        status = "fail"
    elif execution_timeouts:
        status = "conditional"
    else:
        status = "pass"

    difficulties: dict[str, int] = {}
    for row in rows:
        difficulty = str(row["difficulty"])
        difficulties[difficulty] = difficulties.get(difficulty, 0) + 1

    return {
        "status": status,
        "database_root": str(database_root),
        "rows": len(rows),
        "database_count": len(database_ids),
        "database_tables": database_tables,
        "difficulty_counts": dict(sorted(difficulties.items())),
        "gold_execution": {
            "requested": execute_gold,
            "workers": workers,
            "passed": len(execution_passes),
            "timed_out": len(execution_timeouts),
            "errors": len(execution_errors),
            "timeout_seconds_per_query": timeout_seconds,
            "failures": execution_failures,
            "slowest_passes": slowest_passes,
        },
    }


def _parse_json_file(path: Path) -> None:
    try:
        load_json(path)
    except json.JSONDecodeError as exc:
        raise ReadinessFailure(f"Invalid JSON file {path}: {exc}") from exc


def check_mini_interact(dataset_root: Path) -> dict[str, Any]:
    task_file = dataset_root / "mini_interact.jsonl"
    require(task_file.is_file(), f"Mini-Interact task file is missing: {task_file}")
    rows = load_jsonl(task_file)
    require(len(rows) == 300, f"Expected 300 Mini-Interact rows, found {len(rows)}")

    required = {
        "instance_id",
        "selected_database",
        "amb_user_query",
        "sol_sql",
        "test_cases",
        "external_knowledge",
        "user_query_ambiguity",
        "knowledge_ambiguity",
    }
    for index, row in enumerate(rows):
        missing = sorted(required - row.keys())
        require(not missing, f"Mini-Interact row {index} is missing {missing}")

    instance_ids = [str(row["instance_id"]) for row in rows]
    require(
        len(set(instance_ids)) == len(instance_ids),
        "Mini-Interact instance_id values are not unique",
    )
    selected_databases = sorted({str(row["selected_database"]) for row in rows})
    require(
        len(selected_databases) == 26,
        f"Expected 26 selected Mini-Interact databases, found {len(selected_databases)}",
    )

    database_folders = sorted(
        path.name
        for path in dataset_root.iterdir()
        if path.is_dir() and path.name != ".git"
    )
    missing_folders = sorted(set(selected_databases) - set(database_folders))
    require(not missing_folders, f"Mini-Interact DB folders are missing: {missing_folders}")

    database_tables: dict[str, dict[str, int]] = {}
    for database_id in database_folders:
        folder = dataset_root / database_id
        required_files = {
            "database": folder / f"{database_id}.sqlite",
            "template": folder / f"{database_id}_template.sqlite",
            "schema": folder / f"{database_id}_schema.txt",
            "knowledge": folder / f"{database_id}_kb.jsonl",
            "column_meanings": folder / f"{database_id}_column_meaning_base.json",
        }
        missing = [name for name, path in required_files.items() if not path.is_file()]
        require(not missing, f"Mini-Interact {database_id} is missing {missing}")
        require(
            required_files["schema"].stat().st_size > 0,
            f"Mini-Interact schema is empty for {database_id}",
        )
        load_jsonl(required_files["knowledge"])
        _parse_json_file(required_files["column_meanings"])
        database_tables[database_id] = {
            "database": sqlite_quick_check(required_files["database"]),
            "template": sqlite_quick_check(required_files["template"]),
        }

    rows_with_empty_solution = sum(not row["sol_sql"] for row in rows)
    rows_with_empty_tests = sum(not row["test_cases"] for row in rows)
    rows_with_empty_knowledge = sum(not row["external_knowledge"] for row in rows)
    public_payload_complete = (
        not rows_with_empty_solution
        and not rows_with_empty_tests
        and not rows_with_empty_knowledge
    )

    return {
        "status": "pass" if public_payload_complete else "conditional",
        "public_structure": "pass",
        "rows": len(rows),
        "selected_database_count": len(selected_databases),
        "database_folder_count": len(database_folders),
        "extra_database_folders": sorted(
            set(database_folders) - set(selected_databases)
        ),
        "database_tables": database_tables,
        "ground_truth_payload": {
            "status": "pass" if public_payload_complete else "blocked",
            "rows_with_empty_sol_sql": rows_with_empty_solution,
            "rows_with_empty_test_cases": rows_with_empty_tests,
            "rows_with_empty_external_knowledge": rows_with_empty_knowledge,
            "action": (
                "Request the official GT and test-case package, then merge it "
                "using the BIRD-Interact instructions."
                if not public_payload_complete
                else "none"
            ),
        },
    }


def check_evaluator(evaluator_root: Path) -> dict[str, Any]:
    required_files = [
        evaluator_root / "env" / "requirements.txt",
        evaluator_root
        / "mini_interact"
        / "knowledge_based"
        / "mini_interact_agent"
        / "batch_run_bird_interact"
        / "main.py",
        evaluator_root
        / "mini_interact"
        / "knowledge_based"
        / "mini_interact_agent"
        / "batch_run_bird_interact"
        / "action_handler_sqlite.py",
        evaluator_root
        / "mini_interact"
        / "knowledge_based"
        / "mini_interact_conv"
        / "evaluation"
        / "wrapper_evaluation_sqlite.py",
    ]
    missing = [str(path) for path in required_files if not path.is_file()]
    require(not missing, f"BIRD-Interact evaluator files are missing: {missing}")

    syntax_failures: list[dict[str, str]] = []
    syntax_warnings: list[dict[str, str]] = []
    compiled = 0
    mini_root = evaluator_root / "mini_interact"
    for path in sorted(mini_root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", SyntaxWarning)
                compile(source, str(path), "exec")
            for warning in caught:
                syntax_warnings.append(
                    {
                        "path": path.relative_to(evaluator_root).as_posix(),
                        "warning": str(warning.message),
                    }
                )
            compiled += 1
        except (SyntaxError, UnicodeDecodeError) as exc:
            syntax_failures.append(
                {"path": path.relative_to(evaluator_root).as_posix(), "error": str(exc)}
            )

    return {
        "status": "pass" if not syntax_failures else "fail",
        "revision": git_revision(evaluator_root),
        "python_files_compiled": compiled,
        "syntax_failures": syntax_failures,
        "syntax_warnings": syntax_warnings,
        "runtime_status": "conditional",
        "runtime_blocker": (
            "The public Mini-Interact rows contain empty sol_sql, test_cases, "
            "and external_knowledge; the official user simulator and scorer "
            "require the private payload."
        ),
    }


def check_clarity(dataset_root: Path) -> dict[str, Any]:
    if not dataset_root.exists():
        return {
            "status": "blocked",
            "reason": (
                "No official public CLARITY dataset repository was identified at "
                "acquisition time. Keep CLARITY as a planned secondary diagnostic "
                "until the authors release data or provide it directly."
            ),
            "paper": CLARITY_PAPER,
        }
    return {
        "status": "unverified",
        "reason": "A local CLARITY path exists but no release-specific validator is defined.",
        "path": str(dataset_root),
    }


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    clean_root = args.datasets_root / "bird-sql-dev-20251106"
    mini_root = args.datasets_root / "mini-interact"
    evaluator_root = args.datasets_root / "bird-interact-evaluator"

    clean_package = check_clean_bird_package(
        args.clean_bird_package,
        args.bird_database_root,
        args.legacy_bird_database_root,
    )
    clean = check_clean_bird(
        clean_root,
        args.bird_database_root,
        execute_gold=args.execute_clean_gold,
        timeout_seconds=args.clean_timeout,
        workers=args.clean_workers,
    )
    mini = check_mini_interact(mini_root)
    evaluator = check_evaluator(evaluator_root)
    clarity = check_clarity(args.datasets_root / "clarity")

    snapshots = {
        "bird_sql_dev_20251106": {
            "source": CLEAN_BIRD_SOURCE,
            "revision": git_revision(clean_root),
            **hash_snapshot(clean_root),
        },
        "bird_sql_dev_20251106_databases": clean_package,
        "mini_interact": {
            "source": MINI_INTERACT_SOURCE,
            "revision": git_revision(mini_root),
            **hash_snapshot(mini_root),
        },
        "bird_interact_evaluator": {
            "source": BIRD_INTERACT_SOURCE,
            "revision": git_revision(evaluator_root),
        },
    }

    hard_failures = [
        name
        for name, result in {
            "bird_sql_dev_20251106": clean,
            "bird_sql_dev_20251106_databases": clean_package,
            "mini_interact": mini,
            "bird_interact_evaluator": evaluator,
        }.items()
        if result["status"] == "fail"
    ]
    conditional = [
        name
        for name, result in {
            "bird_sql_dev_20251106": clean,
            "bird_sql_dev_20251106_databases": clean_package,
            "mini_interact": mini,
            "bird_interact_evaluator": evaluator,
            "clarity": clarity,
        }.items()
        if result["status"] in {"conditional", "blocked", "unverified"}
    ]
    overall = "fail" if hard_failures else ("conditional" if conditional else "pass")

    return {
        "schema_version": 2,
        "overall_status": overall,
        "hard_failures": hard_failures,
        "conditional_components": conditional,
        "snapshots": snapshots,
        "checks": {
            "bird_sql_dev_20251106": clean,
            "bird_sql_dev_20251106_databases": clean_package,
            "mini_interact": mini,
            "bird_interact_evaluator": evaluator,
            "clarity": clarity,
        },
    }


def preserve_completed_gold_sweep(
    current: dict[str, Any], previous: dict[str, Any]
) -> bool:
    """Carry forward an expensive sweep when the benchmark snapshot is unchanged."""

    try:
        previous_gold = previous["checks"]["bird_sql_dev_20251106"][
            "gold_execution"
        ]
        same_metadata = (
            current["snapshots"]["bird_sql_dev_20251106"]
            == previous["snapshots"]["bird_sql_dev_20251106"]
        )
        same_databases = (
            current["snapshots"]["bird_sql_dev_20251106_databases"]
            == previous["snapshots"]["bird_sql_dev_20251106_databases"]
        )
    except (KeyError, TypeError):
        return False
    if not previous_gold.get("requested") or not same_metadata or not same_databases:
        return False

    clean = current["checks"]["bird_sql_dev_20251106"]
    clean["gold_execution"] = previous_gold
    if int(previous_gold.get("errors", 0)):
        clean["status"] = "fail"
    elif int(previous_gold.get("timed_out", 0)):
        clean["status"] = "conditional"
    else:
        clean["status"] = "pass"

    required = {
        name: result
        for name, result in current["checks"].items()
        if name != "clarity"
    }
    current["hard_failures"] = sorted(
        name for name, result in required.items() if result["status"] == "fail"
    )
    current["conditional_components"] = sorted(
        name
        for name, result in current["checks"].items()
        if result["status"] in {"conditional", "blocked", "unverified"}
    )
    current["overall_status"] = (
        "fail"
        if current["hard_failures"]
        else ("conditional" if current["conditional_components"] else "pass")
    )
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--datasets-root",
        type=Path,
        default=DEFAULT_DATASETS_ROOT,
        help="Directory containing the downloaded dataset snapshots.",
    )
    parser.add_argument(
        "--bird-database-root",
        type=Path,
        default=DEFAULT_CLEAN_BIRD_DB_ROOT,
        help="SQLite root extracted from the package linked by clean BIRD Dev.",
    )
    parser.add_argument(
        "--clean-bird-package",
        type=Path,
        default=DEFAULT_CLEAN_BIRD_PACKAGE,
        help="Archive linked by the clean BIRD Dev release card.",
    )
    parser.add_argument(
        "--legacy-bird-database-root",
        type=Path,
        default=DEFAULT_LEGACY_BIRD_DB_ROOT,
        help="Original BIRD database root retained for provenance comparison.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Tracked JSON manifest to write.",
    )
    parser.add_argument(
        "--execute-clean-gold",
        action="store_true",
        help="Execute all 1,534 clean gold queries against the local SQLite DBs.",
    )
    parser.add_argument(
        "--clean-timeout",
        type=float,
        default=30.0,
        help="Per-query timeout in seconds for --execute-clean-gold.",
    )
    parser.add_argument(
        "--clean-workers",
        type=int,
        default=6,
        help="Number of databases to execute in parallel for the clean gold sweep.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Return a non-zero status when an external component is conditional.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    previous_manifest: dict[str, Any] | None = None
    if not args.execute_clean_gold and args.manifest.is_file():
        try:
            loaded = load_json(args.manifest)
            if isinstance(loaded, dict):
                previous_manifest = loaded
        except (OSError, json.JSONDecodeError):
            previous_manifest = None
    try:
        manifest = build_manifest(args)
    except (ReadinessFailure, sqlite3.Error, subprocess.CalledProcessError) as exc:
        print(f"READINESS FAIL: {exc}")
        return 1

    if previous_manifest is not None and preserve_completed_gold_sweep(
        manifest, previous_manifest
    ):
        print("Preserved completed clean-BIRD gold sweep (snapshots unchanged).")

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"Overall: {manifest['overall_status']}")
    for name, result in manifest["checks"].items():
        print(f"- {name}: {result['status']}")
    print(f"Manifest: {args.manifest}")

    if manifest["overall_status"] == "fail":
        return 1
    if args.require_complete and manifest["overall_status"] != "pass":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
