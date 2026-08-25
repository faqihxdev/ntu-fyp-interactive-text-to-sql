"""Isolated adapter for Mini-Interact's live agent ``submit`` scorer.

The older project adapter invokes the separate conversational evaluator. This
module instead imports the pinned live-agent ``test_case_default`` in a child
process, runs it against an ephemeral database copy, and enforces a real parent
wall-clock timeout. Hidden rows never enter policy-side objects or logs.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import types
from contextlib import closing
from pathlib import Path
from typing import Any

if __package__:
    from .check_datasets import DEFAULT_DATASETS_ROOT, sha256_file
else:
    from check_datasets import DEFAULT_DATASETS_ROOT, sha256_file

DEFAULT_DATABASE_ROOT = DEFAULT_DATASETS_ROOT / "mini-interact"
DEFAULT_AGENT_ROOT = (
    DEFAULT_DATASETS_ROOT
    / "bird-interact-evaluator"
    / "mini_interact"
    / "knowledge_based"
    / "mini_interact_agent"
)


def _load_live_test_case(agent_root: Path):
    scorer_root = (
        agent_root / "src" / "envs" / "bird_interact_env" / "test_case_utils_sqlite"
    )
    package_name = "_pinned_mini_live_scorer"
    package = types.ModuleType(package_name)
    package.__path__ = [str(scorer_root)]
    sys.modules[package_name] = package
    for child in ("logger", "db_utils", "test_utils"):
        module_name = f"{package_name}.{child}"
        spec = importlib.util.spec_from_file_location(
            module_name, scorer_root / f"{child}.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load pinned live scorer module: {child}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return sys.modules[f"{package_name}.test_utils"].test_case_default


def _worker(payload_path: Path, output_path: Path) -> int:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    database_path = Path(payload["database_path"])
    result: dict[str, Any] = {
        "status": "failed",
        "execution_error": False,
        "timeout_error": False,
        "assertion_error": False,
        "passed_test_cases": 0,
        "effective_total_test_cases": 1,
    }
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            connection.execute("PRAGMA query_only = ON")
            try:
                connection.execute(str(payload["predicted_sql"])).fetchall()
            except sqlite3.Error:
                result["execution_error"] = True
            if not result["execution_error"]:
                test_case_default = _load_live_test_case(Path(payload["agent_root"]))
                try:
                    test_case_default(
                        [str(payload["predicted_sql"])],
                        [str(value) for value in payload["solution_sqls"]],
                        str(database_path),
                        connection,
                        payload.get("conditions"),
                    )
                except AssertionError:
                    result["assertion_error"] = True
                else:
                    result["status"] = "success"
                    result["passed_test_cases"] = 1
    except Exception as exc:  # noqa: BLE001 - child reports adapter failures.
        # Never return exception text from the private scorer process.  Some
        # exception messages can contain SQL, paths, or benchmark internals.
        result["adapter_error"] = "live scorer adapter failure"
        result["adapter_error_type"] = type(exc).__name__
    output_path.write_text(
        json.dumps(result, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


def evaluate_live_instance(
    row: dict[str, Any],
    predicted_sql: str,
    *,
    database_root: Path = DEFAULT_DATABASE_ROOT,
    agent_root: Path = DEFAULT_AGENT_ROOT,
    timeout_seconds: float = 180.0,
) -> dict[str, Any]:
    """Score one query with the pinned live Mini default test-case function."""

    instance_id = str(row.get("instance_id", "unknown"))
    database_id = str(row.get("selected_database", ""))
    solution_sqls = row.get("sol_sql") or []
    if row.get("category", "Query") != "Query":
        raise ValueError("The live Mini adapter currently supports Query tasks only")
    if not solution_sqls:
        raise ValueError(f"Missing solution SQL for {instance_id}")
    template = database_root / database_id / f"{database_id}_template.sqlite"
    if not template.is_file():
        raise FileNotFoundError(template)
    if not (agent_root / "src").is_dir():
        raise FileNotFoundError(agent_root)

    template_hash_before = sha256_file(template)
    with tempfile.TemporaryDirectory(prefix=f"mini-live-{database_id}-") as directory:
        temporary_root = Path(directory)
        database_path = temporary_root / f"{database_id}.sqlite"
        payload_path = temporary_root / "private-payload.json"
        output_path = temporary_root / "result.json"
        shutil.copy2(template, database_path)
        payload_path.write_text(
            json.dumps(
                {
                    "database_path": str(database_path),
                    "agent_root": str(agent_root.resolve()),
                    "predicted_sql": predicted_sql,
                    "solution_sqls": solution_sqls,
                    "conditions": row.get("conditions"),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve()),
                    "--worker",
                    "--payload",
                    str(payload_path),
                    "--output",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            result = {
                "status": "failed",
                "execution_error": False,
                "timeout_error": True,
                "assertion_error": False,
                "passed_test_cases": 0,
                "effective_total_test_cases": 1,
            }
        else:
            if completed.returncode != 0 or not output_path.is_file():
                result = {
                    "status": "failed",
                    "adapter_error": "live scorer child process failed",
                    "adapter_error_type": "ChildProcessError",
                    "execution_error": False,
                    "timeout_error": False,
                    "assertion_error": False,
                    "passed_test_cases": 0,
                    "effective_total_test_cases": 1,
                    "returncode": completed.returncode,
                    # Preserve only whether diagnostics existed.  The raw
                    # stream may contain private scorer details.
                    "stderr_present": bool(completed.stderr.strip()),
                }
            else:
                result = json.loads(output_path.read_text(encoding="utf-8"))

    template_hash_after = sha256_file(template)
    if template_hash_before != template_hash_after:
        raise RuntimeError(f"Source template changed while evaluating {instance_id}")
    return {
        **result,
        "instance_id": instance_id,
        "database_id": database_id,
        "evaluator": "official_live_agent_test_case_default",
        "official_source_revision": "451fe2c3518ee1cf908d8139e2913483bd519381",
        "source_template_unchanged": True,
        "source_template_sha256": template_hash_before,
        "wall_clock_timeout_seconds": timeout_seconds,
        "runtime_adaptation": "parent_process_timeout_and_read_only_precheck",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--payload", type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.worker or args.payload is None or args.output is None:
        raise SystemExit(
            "This module is an internal worker; use evaluate_live_instance()."
        )
    return _worker(args.payload, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
