"""Run the official Mini-Interact single-instance scorer reproducibly on Windows.

The upstream batch wrapper launches ``python3`` and addresses its child script
relative to the current directory. On this machine that selects a different
SQLite build without math functions. This adapter leaves the pinned evaluator
unchanged while using the active uv interpreter and absolute paths.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__:
    from .check_datasets import DEFAULT_DATASETS_ROOT, load_jsonl, sha256_file
else:
    from check_datasets import DEFAULT_DATASETS_ROOT, load_jsonl, sha256_file


DEFAULT_MERGED = (
    DEFAULT_DATASETS_ROOT
    / "private"
    / "mini-interact"
    / "mini_interact_merged_20251112.jsonl"
)
DEFAULT_DATABASE_ROOT = DEFAULT_DATASETS_ROOT / "mini-interact"
EVALUATION_DIR = (
    DEFAULT_DATASETS_ROOT
    / "bird-interact-evaluator"
    / "mini_interact"
    / "knowledge_based"
    / "mini_interact_conv"
    / "evaluation"
)
DEFAULT_SINGLE_INSTANCE_SCRIPT = EVALUATION_DIR / "single_instance_eval_sqlite.py"


def build_single_instance_command(
    evaluator_script: Path,
    input_file: Path,
    output_file: Path,
    *,
    mode: str,
    log_file: Path,
) -> list[str]:
    """Build the child command without depending on PATH or the caller's cwd."""

    return [
        sys.executable,
        str(evaluator_script.resolve()),
        "--jsonl_file",
        str(input_file.resolve()),
        "--output_file",
        str(output_file.resolve()),
        "--mode",
        mode,
        "--logging",
        "false",
        "--log_file",
        str(log_file.resolve()),
    ]


def effective_result(
    result: dict[str, Any], instance_row: dict[str, Any]
) -> dict[str, Any]:
    """Expose the upstream fallback-count quirk without changing its verdict."""

    normalized = dict(result)
    upstream_count = int(result.get("total_test_cases", 0))
    custom_tests = instance_row.get("test_cases", [])
    normalized["upstream_reported_total_test_cases"] = upstream_count
    normalized["effective_total_test_cases"] = len(custom_tests) if custom_tests else 1
    return normalized


def select_rows(
    rows: list[dict[str, Any]],
    *,
    instance_ids: list[str],
    skip: int,
    limit: int | None,
    run_all: bool,
) -> list[dict[str, Any]]:
    """Require an explicit bounded selection unless ``--all`` is supplied."""

    if instance_ids:
        by_id = {str(row.get("instance_id")): row for row in rows}
        missing = [
            instance_id for instance_id in instance_ids if instance_id not in by_id
        ]
        if missing:
            raise ValueError(f"Unknown Mini-Interact instance IDs: {missing}")
        return [by_id[instance_id] for instance_id in instance_ids]
    if run_all:
        return rows
    if limit is None:
        raise ValueError("Select --instance-id/--limit, or use --all explicitly.")
    if skip < 0 or limit < 1:
        raise ValueError("--skip must be non-negative and --limit must be positive.")
    return rows[skip : skip + limit]


def require_sqlite_math_functions() -> dict[str, Any]:
    """Fail early if the active interpreter cannot execute benchmark math SQL."""

    with sqlite3.connect(":memory:") as connection:
        value = connection.execute("SELECT POWER(2, 3), LOG(10, 100)").fetchone()
    if value != (8.0, 2.0):
        raise RuntimeError(f"Unexpected SQLite math-function result: {value}")
    return {
        "python": sys.executable,
        "sqlite_version": sqlite3.sqlite_version,
        "power_and_log_probe": list(value),
    }


def evaluate_instance(
    row: dict[str, Any],
    *,
    database_root: Path,
    evaluator_script: Path,
    mode: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    instance_id = str(row.get("instance_id", "unknown"))
    database_id = str(row.get("selected_database", ""))
    template = database_root / database_id / f"{database_id}_template.sqlite"
    if not template.is_file():
        raise FileNotFoundError(f"Template database is missing: {template}")

    template_hash_before = sha256_file(template)
    with tempfile.TemporaryDirectory(
        prefix=f"mini-interact-{database_id}-"
    ) as directory:
        temporary_root = Path(directory)
        temporary_template = temporary_root / f"{database_id}_template.sqlite"
        ephemeral_database = temporary_root / f"{database_id}_ephemeral_1.sqlite"
        input_file = temporary_root / "instance.jsonl"
        output_file = temporary_root / "result.json"
        log_file = temporary_root / "instance.log"
        shutil.copy2(template, temporary_template)
        shutil.copy2(template, ephemeral_database)
        input_file.write_text(
            json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8"
        )

        environment = os.environ.copy()
        environment["EPHEMERAL_DB_PATH"] = str(ephemeral_database)
        command = build_single_instance_command(
            evaluator_script,
            input_file,
            output_file,
            mode=mode,
            log_file=log_file,
        )
        completed = subprocess.run(
            command,
            cwd=evaluator_script.resolve().parent,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
        )
        if completed.returncode != 0 or not output_file.is_file():
            return {
                "instance_id": instance_id,
                "database_id": database_id,
                "status": "failed",
                "adapter_error": "official single-instance evaluator process failed",
                "adapter_error_type": "ChildProcessError",
                "returncode": completed.returncode,
                "stderr_present": bool(completed.stderr.strip()),
            }
        raw_result = json.loads(output_file.read_text(encoding="utf-8"))

    template_hash_after = sha256_file(template)
    if template_hash_before != template_hash_after:
        raise RuntimeError(f"Source template changed while evaluating {instance_id}")
    normalized = effective_result(raw_result, row)
    normalized["instance_id"] = instance_id
    normalized["database_id"] = database_id
    normalized["source_template_unchanged"] = True
    normalized["source_template_sha256"] = template_hash_before
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jsonl-file", type=Path, default=DEFAULT_MERGED)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument(
        "--single-instance-script", type=Path, default=DEFAULT_SINGLE_INSTANCE_SCRIPT
    )
    parser.add_argument("--mode", choices=["gold", "pred"], default="gold")
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Evaluate every row. Treat this as a large run requiring approval.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON summary path; keep private when gold data is involved.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        environment = require_sqlite_math_functions()
        rows = load_jsonl(args.jsonl_file)
        selected = select_rows(
            rows,
            instance_ids=args.instance_id,
            skip=args.skip,
            limit=args.limit,
            run_all=args.all,
        )
        results = [
            evaluate_instance(
                row,
                database_root=args.db_path,
                evaluator_script=args.single_instance_script,
                mode=args.mode,
                timeout_seconds=args.timeout,
            )
            for row in selected
        ]
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"MINI-INTERACT EVALUATION FAIL: {exc}")
        return 1

    passed = sum(result.get("status") == "success" for result in results)
    summary = {
        "mode": args.mode,
        "environment": environment,
        "selected_instances": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    print(
        f"Mini-Interact {args.mode}: {passed}/{len(results)} passed "
        f"with {environment['sqlite_version']} via {environment['python']}"
    )
    for result in results:
        print(
            f"- {result['instance_id']} ({result['database_id']}): "
            f"{result.get('status')}"
        )
    if args.output:
        print(f"Summary: {args.output}")
    return 0 if passed == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
