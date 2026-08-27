"""Create deterministic, gold-independent clarification-value selections."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .bird_dev_clarification_value import branch_execution_order
from .run_bird_dev_clarification_value import DEFAULT_DATASET, EXPERIMENT_ROOT
from .run_support import sha256_file


SEED = "bird-dev-clarification-value-v1-20260826"
DEFAULT_SMOKE_OUTPUT = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-clarification-value-smoke20.json"
)
DEFAULT_PILOT_OUTPUT = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-clarification-value-pilot100.json"
)
DEFAULT_FULL_OUTPUT = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-clarification-value-full1534.json"
)


def _rank(question_id: int) -> str:
    return hashlib.sha256(f"{SEED}:{question_id}".encode("utf-8")).hexdigest()


def _manifest(
    *,
    rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    dataset: Path,
    tier: str,
    excluded_ids: list[int],
) -> dict[str, Any]:
    purposes = {
        "smoke20": "Protocol plumbing smoke; not method evidence.",
        "pilot100": (
            "Frozen 100-task pilot for the clarification-versus-retry control."
        ),
        "full1534": (
            "All original-BIRD Dev tasks for the clarification-versus-retry "
            "replication."
        ),
    }
    methods = {
        "smoke20": (
            "Deterministic SHA-256 rank over question_id, stratified only to "
            "place each recovery branch first on exactly half the tasks; no "
            "question text, gold SQL, execution result, or difficulty used."
        ),
        "pilot100": (
            "Deterministic SHA-256 rank over question_id, stratified only to "
            "place each recovery branch first on exactly half the tasks; no "
            "question text, gold SQL, execution result, or difficulty used."
        ),
        "full1534": (
            "All rows in the released original-BIRD Dev file, in released order; "
            "there is no task selection and no use of question text, difficulty, "
            "gold SQL, or execution results."
        ),
    }
    return {
        "schema_version": 1,
        "protocol": "bird-dev-clarification-value-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tier": tier,
        "purpose": purposes[tier],
        "method": methods[tier],
        "seed": SEED,
        "dataset": {
            "path": "codebase/experiment/datasets/bird-dev/dev.json",
            "sha256": sha256_file(dataset),
            "rows": len(rows),
        },
        "question_ids": [int(row["question_id"]) for row in selected],
        "tasks": len(selected),
        "distribution": {
            "database": dict(
                sorted(Counter(str(row["db_id"]) for row in selected).items())
            ),
            "difficulty": dict(
                sorted(Counter(str(row["difficulty"]) for row in selected).items())
            ),
        },
        "branch_execution_order": dict(
            sorted(
                Counter(
                    " -> ".join(branch_execution_order(int(row["question_id"])))
                    for row in selected
                ).items()
            )
        ),
        "excluded_question_ids": excluded_ids,
        "selection_uses_question_text": False,
        "selection_uses_gold_sql_or_results": False,
    }


def build_full_selection(
    *,
    rows: list[dict[str, Any]],
    dataset: Path,
    smoke: dict[str, Any],
    pilot: dict[str, Any],
) -> dict[str, Any]:
    if len(rows) != 1534:
        raise ValueError(f"Expected 1,534 original-BIRD rows, found {len(rows)}")
    question_ids = [int(row["question_id"]) for row in rows]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("BIRD question IDs are not unique")
    smoke_ids = [int(value) for value in smoke["question_ids"]]
    pilot_ids = [int(value) for value in pilot["question_ids"]]
    exposed_ids = smoke_ids + pilot_ids
    if len(exposed_ids) != 120 or len(exposed_ids) != len(set(exposed_ids)):
        raise ValueError("Smoke and pilot must expose exactly 120 disjoint IDs")
    if not set(exposed_ids).issubset(question_ids):
        raise ValueError("Development-exposure IDs are not a subset of BIRD Dev")

    manifest = _manifest(
        rows=rows,
        selected=rows,
        dataset=dataset,
        tier="full1534",
        excluded_ids=[],
    )
    manifest["development_exposure"] = {
        "purpose": (
            "The full benchmark includes the 120 IDs previously used for protocol "
            "development. Primary results use all 1,534 official Dev tasks; a "
            "prespecified sensitivity analysis excludes these IDs."
        ),
        "smoke20_question_ids": smoke_ids,
        "pilot100_question_ids": pilot_ids,
        "union_question_ids": exposed_ids,
        "exposed_tasks": len(exposed_ids),
        "unexposed_tasks": len(rows) - len(exposed_ids),
        "sensitivity_analysis": (
            "Repeat every arm summary and paired contrast on the 1,414 tasks not "
            "used in the smoke or pilot."
        ),
    }
    return manifest


def build_selections(
    *, rows: list[dict[str, Any]], dataset: Path
) -> tuple[dict[str, Any], dict[str, Any]]:
    if len(rows) != 1534:
        raise ValueError(f"Expected 1,534 original-BIRD rows, found {len(rows)}")
    question_ids = [int(row["question_id"]) for row in rows]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("BIRD question IDs are not unique")
    ranked = sorted(
        rows,
        key=lambda row: (_rank(int(row["question_id"])), int(row["question_id"])),
    )
    by_first_branch = {
        branch: [
            row
            for row in ranked
            if branch_execution_order(int(row["question_id"]))[0] == branch
        ]
        for branch in ("sql_only_recovery", "reactive_clarification")
    }
    smoke_rows = sorted(
        by_first_branch["sql_only_recovery"][:10]
        + by_first_branch["reactive_clarification"][:10],
        key=lambda row: (_rank(int(row["question_id"])), int(row["question_id"])),
    )
    pilot_rows = sorted(
        by_first_branch["sql_only_recovery"][10:60]
        + by_first_branch["reactive_clarification"][10:60],
        key=lambda row: (_rank(int(row["question_id"])), int(row["question_id"])),
    )
    smoke_ids = [int(row["question_id"]) for row in smoke_rows]
    pilot_ids = [int(row["question_id"]) for row in pilot_rows]
    if set(smoke_ids) & set(pilot_ids):
        raise AssertionError("Smoke and pilot selections overlap")
    return (
        _manifest(
            rows=rows,
            selected=smoke_rows,
            dataset=dataset,
            tier="smoke20",
            excluded_ids=[],
        ),
        _manifest(
            rows=rows,
            selected=pilot_rows,
            dataset=dataset,
            tier="pilot100",
            excluded_ids=smoke_ids,
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--smoke-output", type=Path, default=DEFAULT_SMOKE_OUTPUT)
    parser.add_argument("--pilot-output", type=Path, default=DEFAULT_PILOT_OUTPUT)
    parser.add_argument("--full-output", type=Path, default=DEFAULT_FULL_OUTPUT)
    parser.add_argument(
        "--full-only",
        action="store_true",
        help="Create only the all-1,534 manifest without rewriting historical tiers.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Expected the original-BIRD JSON array")
    smoke, pilot = build_selections(rows=rows, dataset=args.dataset)
    full = build_full_selection(
        rows=rows,
        dataset=args.dataset,
        smoke=smoke,
        pilot=pilot,
    )
    outputs = (
        ((args.full_output, full),)
        if args.full_only
        else (
            (args.smoke_output, smoke),
            (args.pilot_output, pilot),
            (args.full_output, full),
        )
    )
    for path, payload in outputs:
        if path.exists() and (args.full_only or path == args.full_output):
            raise FileExistsError(f"Refusing to overwrite existing manifest: {path}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"Wrote {path}: tasks={payload['tasks']}; "
            f"databases={len(payload['distribution']['database'])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
