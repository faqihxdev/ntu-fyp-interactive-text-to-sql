"""Create the exact all-1,534 original-BIRD v4 selection manifest."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .run_bird_dev_matched import DEFAULT_DATASET, EXPERIMENT_ROOT
from .run_support import sha256_file


DEFAULT_SMOKE = EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-smoke10.json"
DEFAULT_PILOT = EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-pilot100.json"
DEFAULT_OUTPUT = EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-v4-full1534.json"


def _ids(path: Path) -> list[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = [int(value) for value in payload["question_ids"]]
    if not values or len(values) != len(set(values)):
        raise ValueError(f"Selection has missing or duplicate IDs: {path}")
    return values


def build_manifest(
    *,
    rows: list[dict[str, Any]],
    dataset: Path,
    smoke_ids: list[int],
    pilot_ids: list[int],
) -> dict[str, Any]:
    if len(rows) != 1534:
        raise ValueError(f"Expected 1,534 BIRD rows, found {len(rows)}")
    question_ids = [int(row["question_id"]) for row in rows]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("BIRD question IDs are not unique")
    available = set(question_ids)
    exposed = set(smoke_ids) | set(pilot_ids)
    if set(smoke_ids) & set(pilot_ids):
        raise ValueError("Smoke and pilot selections overlap")
    if not exposed <= available:
        raise ValueError("Development selection contains an unknown BIRD ID")
    return {
        "schema_version": 1,
        "protocol": "bird-dev-matched-v4",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Complete original-BIRD Dev evaluation after the v4 protocol and "
            "analysis contrasts were frozen."
        ),
        "method": "All rows in the released dev.json order; no task selection.",
        "dataset": {
            "path": "codebase/experiment/datasets/bird-dev/dev.json",
            "sha256": sha256_file(dataset),
            "rows": len(rows),
        },
        "question_ids": question_ids,
        "tasks": len(question_ids),
        "distribution": {
            "database": dict(
                sorted(Counter(str(row["db_id"]) for row in rows).items())
            ),
            "difficulty": dict(
                sorted(Counter(str(row["difficulty"]) for row in rows).items())
            ),
        },
        "development_exposure": {
            "smoke_ids": smoke_ids,
            "pilot_ids": pilot_ids,
            "union_ids": sorted(exposed),
            "union_tasks": len(exposed),
            "unexposed_sensitivity_tasks": len(question_ids) - len(exposed),
            "analysis_rule": (
                "Report the standard all-1,534 result as primary and repeat the "
                "paired arm analysis after excluding these 110 protocol-"
                "development IDs as a prespecified sensitivity."
            ),
        },
        "selection_uses_gold_sql_or_results": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--smoke-selection", type=Path, default=DEFAULT_SMOKE)
    parser.add_argument("--pilot-selection", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("Expected the original-BIRD JSON array")
    payload = build_manifest(
        rows=rows,
        dataset=args.dataset,
        smoke_ids=_ids(args.smoke_selection),
        pilot_ids=_ids(args.pilot_selection),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.output}: tasks={payload['tasks']}; "
        f"unexposed sensitivity={payload['development_exposure']['unexposed_sensitivity_tasks']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
