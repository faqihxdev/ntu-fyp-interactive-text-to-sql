"""Export a privacy-safe task-level projection of the primary BIRD study."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from .bird_dev_clarification_value import ARMS, PROTOCOL_VERSION
from .run_support import sha256_file


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EXPERIMENT_ROOT.parents[1]


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("protocol") != PROTOCOL_VERSION:
                raise ValueError(f"Protocol mismatch at line {line_number}")
            if "task_error" in record:
                raise ValueError(f"Task error at line {line_number}")
            if set(record.get("arms", {})) != set(ARMS):
                raise ValueError(f"Arm mismatch at line {line_number}")
            records.append(record)
    if not records:
        raise ValueError("No task records found")
    return records


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tasks = len(rows)
    successes = {
        arm: sum(bool(row["arms"][arm]["official_success"]) for row in rows)
        for arm in ARMS
    }
    reactive_only = sum(
        bool(row["arms"]["reactive_clarification"]["official_success"])
        and not bool(row["arms"]["sql_only_recovery"]["official_success"])
        for row in rows
    )
    sql_only_only = sum(
        bool(row["arms"]["sql_only_recovery"]["official_success"])
        and not bool(row["arms"]["reactive_clarification"]["official_success"])
        for row in rows
    )
    return {
        "tasks": tasks,
        "official_successes": successes,
        "reactive_minus_sql_only_percentage_points": round(
            100
            * (successes["reactive_clarification"] - successes["sql_only_recovery"])
            / tasks,
            4,
        ),
        "reactive_only_successes": reactive_only,
        "sql_only_only_successes": sql_only_only,
        "branch_execution_order_counts": dict(
            sorted(
                Counter(
                    " -> ".join(row["branch_execution_order"]) for row in rows
                ).items()
            )
        ),
    }


def build_public_projection(
    *,
    records: list[dict[str, Any]],
    protocol_development_ids: set[int],
    source: dict[str, str],
) -> dict[str, Any]:
    """Return only public IDs, outcome bits, burden counts, and branch order."""
    rows: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for record in records:
        question_id = int(record["task"]["question_id"])
        if question_id in seen_ids:
            raise ValueError(f"Duplicate question ID: {question_id}")
        seen_ids.add(question_id)
        rows.append(
            {
                "question_id": question_id,
                "clarification_value_smoke_or_pilot": (
                    question_id in protocol_development_ids
                ),
                "branch_execution_order": list(record["branch_execution_order"]),
                "shared_initial_official_success": bool(
                    record["shared_initial"]["official_ex"]
                ),
                "arms": {
                    arm: {
                        "official_success": bool(
                            record["arms"][arm]["official_success"]
                        ),
                        "normal_sql_attempts": int(
                            record["arms"][arm]["normal_sql_attempts"]
                        ),
                        "user_turns": int(record["arms"][arm]["user_turns"]),
                        "termination": str(record["arms"][arm]["termination"]),
                    }
                    for arm in ARMS
                },
            }
        )
    rows.sort(key=lambda row: row["question_id"])
    sensitivity_rows = [
        row for row in rows if not row["clarification_value_smoke_or_pilot"]
    ]
    return {
        "schema_version": 1,
        "artifact": "bird-dev-clarification-value-public-outcomes",
        "protocol": PROTOCOL_VERSION,
        "privacy_boundary": (
            "Contains public BIRD question IDs, success bits, attempt/turn counts, "
            "termination labels, branch order, and clarification-value smoke/pilot "
            "membership only. It excludes question text, SQL, prompts, answers, "
            "database results, and trajectories."
        ),
        "source": source,
        "summary_all_tasks": _summary(rows),
        "summary_excluding_clarification_value_smoke_or_pilot": _summary(
            sensitivity_rows
        ),
        "rows": rows,
    }


def export(run_root: Path, output: Path) -> dict[str, Any]:
    manifest_path = run_root / "run-manifest.json"
    records_path = run_root / "task-records.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Run manifest protocol mismatch")
    if manifest.get("task_records_sha256") != sha256_file(records_path):
        raise ValueError("Task-record hash does not match the run manifest")

    recorded_selection = Path(str(manifest["selection"]["path"]))
    selection_path = (
        recorded_selection
        if recorded_selection.is_absolute()
        else WORKSPACE_ROOT / recorded_selection
    )
    selection_sha256 = sha256_file(selection_path)
    if selection_sha256 != manifest["selection"]["sha256"]:
        raise ValueError("Selection hash does not match the run manifest")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    exposure = selection.get("development_exposure") or {}
    protocol_development_ids = {
        int(value) for value in exposure.get("union_question_ids", [])
    }

    records = _load_records(records_path)
    if len(records) != int(manifest["selection"]["tasks"]):
        raise ValueError("Task-record count does not match the run manifest")
    projection = build_public_projection(
        records=records,
        protocol_development_ids=protocol_development_ids,
        source={
            "run_manifest_sha256": sha256_file(manifest_path),
            "task_records_sha256": sha256_file(records_path),
            "selection_sha256": selection_sha256,
        },
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(projection, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return projection


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projection = export(args.run_root, args.output)
    summary = projection["summary_all_tasks"]
    print(
        f"Wrote {args.output}; tasks={summary['tasks']}; "
        "reactive-minus-SQL-only="
        f"{summary['reactive_minus_sql_only_percentage_points']:+.4f} pp"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
