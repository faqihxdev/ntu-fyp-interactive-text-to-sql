"""Verify local benchmark snapshots before running interaction experiments.

This command deliberately uses only the Python standard library. Dataset
snapshots live under the git-ignored ``experiment/datasets`` directory; the
fresh check is written under ``runs``. The original acquisition and final-run
records remain unchanged under ``manifests``.
"""

from __future__ import annotations

import argparse
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
DEFAULT_MANIFEST = EXPERIMENT_ROOT / "runs" / "dataset-readiness.json"
DEFAULT_MINI_PRIVATE_GT = (
    DEFAULT_DATASETS_ROOT
    / "private"
    / "mini-interact"
    / "mini_interact_GTs_20251112.jsonl"
)
DEFAULT_MINI_PRIVATE_MERGED = (
    DEFAULT_DATASETS_ROOT
    / "private"
    / "mini-interact"
    / "mini_interact_merged_20251112.jsonl"
)


MINI_INTERACT_SOURCE = "https://huggingface.co/datasets/birdsql/mini-interact"
BIRD_INTERACT_SOURCE = "https://github.com/bird-bench/BIRD-Interact"
MINI_INTERACT_PRIVATE_GT_SHA256 = (
    "76be864cb9bce1492daf20ff037463f1b37c5cc6dba593f7bd469bd2b1647824"
)
MINI_INTERACT_PRIVATE_MERGED_SHA256 = (
    "af60beaec040fbb5d0891141e97efb24d148c57aaa9dfe13fc4a97f2d70c5fcb"
)
MINI_INTERACT_GT_FIELDS = ("sol_sql", "external_knowledge", "test_cases")


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


def sqlite_logical_hash(path: Path) -> str:
    """Hash user-visible schema and complete table contents, ignoring page layout."""

    digest = hashlib.sha256()
    with closing(_connect_readonly(path)) as connection:
        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND sql IS NOT NULL "
            "ORDER BY type, name"
        ).fetchall()
        digest.update(
            json.dumps(objects, ensure_ascii=False, default=repr).encode("utf-8")
        )
        tables = [name for object_type, name, _ in objects if object_type == "table"]
        for table in tables:
            quoted = str(table).replace('"', '""')
            rows = [
                json.dumps(
                    [
                        {
                            "type": type(value).__name__,
                            "value": value.hex() if isinstance(value, bytes) else value,
                        }
                        for value in row
                    ],
                    ensure_ascii=False,
                    default=repr,
                    separators=(",", ":"),
                )
                for row in connection.execute(f'SELECT * FROM "{quoted}"')
            ]
            digest.update(str(table).encode("utf-8"))
            for row in sorted(rows):
                digest.update(row.encode("utf-8"))
                digest.update(b"\n")
    return digest.hexdigest()


def execute_readonly_sql(database: Path, sql: str, timeout_seconds: float) -> int:
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
    require(
        not missing_folders, f"Mini-Interact DB folders are missing: {missing_folders}"
    )

    database_tables: dict[str, dict[str, Any]] = {}
    exact_database_template_matches = 0
    logical_database_template_matches = 0
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
        database_hash = sha256_file(required_files["database"])
        template_hash = sha256_file(required_files["template"])
        exact_match = database_hash == template_hash
        if exact_match:
            exact_database_template_matches += 1
            logical_match = True
            logical_hash = None
        else:
            database_logical_hash = sqlite_logical_hash(required_files["database"])
            template_logical_hash = sqlite_logical_hash(required_files["template"])
            logical_match = database_logical_hash == template_logical_hash
            require(
                logical_match,
                f"Mini-Interact active/template contents differ for {database_id}",
            )
            logical_database_template_matches += 1
            logical_hash = database_logical_hash
        database_tables[database_id] = {
            "database": sqlite_quick_check(required_files["database"]),
            "template": sqlite_quick_check(required_files["template"]),
            "database_sha256": database_hash,
            "template_sha256": template_hash,
            "exact_file_match": exact_match,
            "logical_content_match": logical_match,
            "logical_sha256": logical_hash,
        }

    rows_with_empty_solution = sum(not row["sol_sql"] for row in rows)
    rows_with_empty_tests = sum(not row["test_cases"] for row in rows)
    rows_with_empty_knowledge = sum(not row["external_knowledge"] for row in rows)
    return {
        "status": "pass",
        "public_structure": "pass",
        "rows": len(rows),
        "selected_database_count": len(selected_databases),
        "database_folder_count": len(database_folders),
        "extra_database_folders": sorted(
            set(database_folders) - set(selected_databases)
        ),
        "database_tables": database_tables,
        "active_template_comparison": {
            "exact_file_matches": exact_database_template_matches,
            "byte_different_but_logically_identical": (
                logical_database_template_matches
            ),
            "logical_mismatches": 0,
            "interpretation": (
                "Policy probes use <db>.sqlite as in the released agent path; "
                "the scorer copies <db>_template.sqlite. Every pair has the "
                "same user-visible schema and complete table contents."
            ),
        },
        "ground_truth_payload": {
            "status": "omitted_from_public_release",
            "rows_with_empty_sol_sql": rows_with_empty_solution,
            "rows_with_empty_test_cases": rows_with_empty_tests,
            "rows_with_empty_external_knowledge": rows_with_empty_knowledge,
            "action": "Validate the separately stored private merge.",
        },
    }


def _without_mini_interact_gt_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Return only fields that must remain identical to the public release."""

    visible = dict(row)
    for field in MINI_INTERACT_GT_FIELDS:
        visible.pop(field, None)
    if isinstance(visible.get("follow_up"), dict):
        follow_up = dict(visible["follow_up"])
        for field in MINI_INTERACT_GT_FIELDS:
            follow_up.pop(field, None)
        visible["follow_up"] = follow_up
    return visible


def effective_mini_interact_test_case_count(row: dict[str, Any]) -> int:
    """Count custom tests, or the official default execution fallback."""

    test_cases = row.get("test_cases", [])
    return len(test_cases) if test_cases else 1


def check_mini_interact_private(
    public_file: Path,
    private_gt_file: Path,
    merged_file: Path,
    *,
    expected_rows: int = 300,
    expected_private_sha256: str | None = MINI_INTERACT_PRIVATE_GT_SHA256,
    expected_merged_sha256: str | None = MINI_INTERACT_PRIVATE_MERGED_SHA256,
) -> dict[str, Any]:
    """Validate the private payload and merge without exposing hidden values."""

    missing = [
        str(path) for path in (private_gt_file, merged_file) if not path.is_file()
    ]
    if missing:
        return {
            "status": "blocked",
            "reason": "Private Mini-Interact payload or canonical merge is missing.",
            "missing": missing,
        }

    require(
        public_file.is_file(), f"Mini-Interact public file is missing: {public_file}"
    )
    private_hash = sha256_file(private_gt_file)
    merged_hash = sha256_file(merged_file)
    if expected_private_sha256 is not None:
        require(
            private_hash == expected_private_sha256,
            "Mini-Interact private payload SHA-256 differs from the received artifact",
        )
    if expected_merged_sha256 is not None:
        require(
            merged_hash == expected_merged_sha256,
            "Mini-Interact canonical merge SHA-256 differs from the validated artifact",
        )

    public_rows = load_jsonl(public_file)
    private_rows = load_jsonl(private_gt_file)
    merged_rows = load_jsonl(merged_file)
    for label, rows in (
        ("public", public_rows),
        ("private", private_rows),
        ("merged", merged_rows),
    ):
        require(
            len(rows) == expected_rows,
            f"Expected {expected_rows} Mini-Interact {label} rows, found {len(rows)}",
        )

    def index(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
        indexed = {str(row.get("instance_id", "")): row for row in rows}
        require(
            "" not in indexed, f"Mini-Interact {label} contains an empty instance_id"
        )
        require(
            len(indexed) == len(rows),
            f"Mini-Interact {label} instance_id values are not unique",
        )
        return indexed

    public_by_id = index(public_rows, "public")
    private_by_id = index(private_rows, "private")
    merged_by_id = index(merged_rows, "merged")
    require(
        public_by_id.keys() == private_by_id.keys() == merged_by_id.keys(),
        "Mini-Interact public, private, and merged instance_id sets differ",
    )

    for instance_id, public_row in public_by_id.items():
        private_row = private_by_id[instance_id]
        merged_row = merged_by_id[instance_id]
        require(
            _without_mini_interact_gt_fields(public_row)
            == _without_mini_interact_gt_fields(merged_row),
            f"Mini-Interact merge changed a public field for {instance_id}",
        )
        for field in MINI_INTERACT_GT_FIELDS:
            require(
                merged_row.get(field, []) == private_row.get(field, []),
                f"Mini-Interact merge mismatched hidden field {field} for {instance_id}",
            )
        if isinstance(private_row.get("follow_up"), dict):
            merged_follow_up = merged_row.get("follow_up", {})
            require(
                isinstance(merged_follow_up, dict),
                f"Mini-Interact merged follow_up is invalid for {instance_id}",
            )
            for field in MINI_INTERACT_GT_FIELDS:
                require(
                    merged_follow_up.get(field, [])
                    == private_row["follow_up"].get(field, []),
                    f"Mini-Interact follow_up mismatched {field} for {instance_id}",
                )

    nonempty_solution = sum(bool(row.get("sol_sql")) for row in merged_rows)
    nonempty_knowledge = sum(bool(row.get("external_knowledge")) for row in merged_rows)
    custom_test_rows = sum(bool(row.get("test_cases")) for row in merged_rows)
    fallback_rows = sum(not row.get("test_cases") for row in merged_rows)
    require(
        nonempty_solution == expected_rows,
        "Mini-Interact canonical merge contains empty solution SQL",
    )
    require(
        nonempty_knowledge == expected_rows,
        "Mini-Interact canonical merge contains empty oracle knowledge",
    )

    return {
        "status": "pass",
        "privacy": "hidden values omitted from this manifest",
        "rows": expected_rows,
        "matching_instance_ids": expected_rows,
        "public_field_mismatches": 0,
        "private_payload": {
            "path": str(private_gt_file),
            "bytes": private_gt_file.stat().st_size,
            "sha256": private_hash,
        },
        "canonical_merge": {
            "path": str(merged_file),
            "bytes": merged_file.stat().st_size,
            "sha256": merged_hash,
            "nonempty_solution_rows": nonempty_solution,
            "nonempty_oracle_knowledge_rows": nonempty_knowledge,
            "custom_test_case_rows": custom_test_rows,
            "default_execution_fallback_rows": fallback_rows,
            "effective_test_case_count": sum(
                effective_mini_interact_test_case_count(row) for row in merged_rows
            ),
        },
    }


def check_evaluator(
    evaluator_root: Path, *, private_payload_ready: bool = False
) -> dict[str, Any]:
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
        "runtime_status": (
            "three_database_gold_smoke_passed" if private_payload_ready else "blocked"
        ),
        "runtime_note": (
            "The private merge is available and a three-database gold/default smoke "
            "has passed; no full run has been started."
            if private_payload_ready
            else "The official simulator and scorer require the private payload."
        ),
    }


def check_original_bird(dataset_root: Path, freeze_path: Path) -> dict[str, Any]:
    """Verify original BIRD against the final run's recorded input hashes."""
    from interaction.artifact_paths import resolve_recorded_path

    freeze = load_json(freeze_path)
    local_root = (EXPERIMENT_ROOT / "datasets/bird-dev").resolve()
    hashes = {}
    for name, digest in freeze["artifact_hashes"].items():
        path = resolve_recorded_path(name, EXPERIMENT_ROOT)
        if path.is_relative_to(local_root):
            relative = path.relative_to(local_root).as_posix()
            require(relative not in hashes, f"Duplicate original-BIRD input: {relative}")
            hashes[relative] = digest
    require("dev.json" in hashes, "The freeze must identify original BIRD Dev.")
    require(
        sum(name.endswith(".sqlite") for name in hashes) == 11,
        "The original-BIRD freeze must identify 11 databases.",
    )
    for relative, expected in hashes.items():
        target = dataset_root / relative
        require(target.is_file(), f"Missing original-BIRD input: {target}")
        require(
            sha256_file(target) == expected, f"Original-BIRD hash mismatch: {target}"
        )
    rows = load_json(dataset_root / "dev.json")
    require(len(rows) == 1534, "Original BIRD Dev must contain 1,534 tasks.")
    return {"status": "pass", "tasks": len(rows), "verified_files": len(hashes)}


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    mini_root = args.datasets_root / "mini-interact"
    evaluator_root = args.datasets_root / "bird-interact-evaluator"
    bird = check_original_bird(args.datasets_root / "bird-dev", args.bird_freeze)
    mini = check_mini_interact(mini_root)
    private = check_mini_interact_private(
        mini_root / "mini_interact.jsonl",
        args.mini_private_gt,
        args.mini_private_merged,
    )
    evaluator = check_evaluator(
        evaluator_root, private_payload_ready=private["status"] == "pass"
    )
    checks = {
        "original_bird_dev": bird,
        "mini_interact": mini,
        "mini_interact_private": private,
        "bird_interact_evaluator": evaluator,
    }
    failures = [name for name, result in checks.items() if result["status"] == "fail"]
    conditional = [
        name
        for name, result in checks.items()
        if result["status"] in {"conditional", "blocked", "unverified"}
    ]
    return {
        "schema_version": 4,
        "scope": "Datasets used in the three final thesis studies; no model calls or gold sweep.",
        "overall_status": "fail"
        if failures
        else ("conditional" if conditional else "pass"),
        "hard_failures": failures,
        "conditional_components": conditional,
        "snapshots": {
            "original_bird_freeze": {
                "path": str(args.bird_freeze),
                "sha256": sha256_file(args.bird_freeze),
            },
            "mini_interact": {
                "source": MINI_INTERACT_SOURCE,
                "revision": git_revision(mini_root),
                **hash_snapshot(mini_root),
            },
            "bird_interact_evaluator": {
                "source": BIRD_INTERACT_SOURCE,
                "revision": git_revision(evaluator_root),
            },
        },
        "checks": checks,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--datasets-root", type=Path, default=DEFAULT_DATASETS_ROOT)
    parser.add_argument(
        "--bird-freeze",
        type=Path,
        default=EXPERIMENT_ROOT
        / "manifests"
        / "api-bird-dev-clarification-value-v1-full1534-freeze-2026-08-30.json",
        help="Recorded original-BIRD input hashes from the final study.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Write a fresh local check without replacing historical acquisition records.",
    )
    parser.add_argument("--mini-private-gt", type=Path, default=DEFAULT_MINI_PRIVATE_GT)
    parser.add_argument(
        "--mini-private-merged", type=Path, default=DEFAULT_MINI_PRIVATE_MERGED
    )
    parser.add_argument("--require-complete", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = build_manifest(args)
    except (
        ReadinessFailure,
        OSError,
        ValueError,
        KeyError,
        sqlite3.Error,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"READINESS FAIL: {exc}")
        return 1
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
