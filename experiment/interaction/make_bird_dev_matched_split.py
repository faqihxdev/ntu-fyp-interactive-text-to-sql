"""Create deterministic disjoint 10-task smoke and 100-task BIRD pilot sets."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .run_bird_dev_matched import DEFAULT_DATASET, EXPERIMENT_ROOT
from .run_support import sha256_file


DEFAULT_SMOKE = EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-smoke10.json"
DEFAULT_PILOT = EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-pilot100.json"
DEFAULT_SEED = "bird-dev-matched-v1-20260824"


def _rank(seed: str, question_id: int) -> str:
    return hashlib.sha256(f"{seed}:{question_id}".encode()).hexdigest()


def _distribution(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    return {
        "database": dict(sorted(Counter(str(row["db_id"]) for row in rows).items())),
        "difficulty": dict(
            sorted(Counter(str(row["difficulty"]) for row in rows).items())
        ),
    }


def choose_smoke(rows: list[dict[str, Any]], *, seed: str, count: int) -> list[dict[str, Any]]:
    remaining = list(rows)
    selected: list[dict[str, Any]] = []
    databases: set[str] = set()
    difficulties: set[str] = set()
    while len(selected) < count:
        remaining.sort(
            key=lambda row: (
                -(str(row["db_id"]) not in databases),
                -(str(row["difficulty"]) not in difficulties),
                _rank(seed, int(row["question_id"])),
            )
        )
        chosen = remaining.pop(0)
        selected.append(chosen)
        databases.add(str(chosen["db_id"]))
        difficulties.add(str(chosen["difficulty"]))
    return selected


def _payload(
    *,
    rows: list[dict[str, Any]],
    dataset: Path,
    seed: str,
    purpose: str,
    method: str,
    excluded_ids: list[int],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": purpose,
        "method": method,
        "seed": seed,
        "dataset": {
            "path": "codebase/experiment/datasets/bird-dev/dev.json",
            "sha256": sha256_file(dataset),
            "rows": 1534,
        },
        "question_ids": [int(row["question_id"]) for row in rows],
        "tasks": len(rows),
        "distribution": _distribution(rows),
        "excluded_question_ids": excluded_ids,
        "selection_uses_gold_sql_or_results": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--smoke-output", type=Path, default=DEFAULT_SMOKE)
    parser.add_argument("--pilot-output", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--seed", default=DEFAULT_SEED)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = json.loads(args.dataset.read_text(encoding="utf-8"))
    if not isinstance(rows, list) or len(rows) != 1534:
        raise ValueError("Expected the 1,534-row original BIRD Dev snapshot")
    smoke = choose_smoke(rows, seed=args.seed + ":smoke", count=10)
    smoke_ids = {int(row["question_id"]) for row in smoke}
    pilot_candidates = [
        row for row in rows if int(row["question_id"]) not in smoke_ids
    ]
    pilot_candidates.sort(
        key=lambda row: _rank(args.seed + ":pilot", int(row["question_id"]))
    )
    pilot = pilot_candidates[:100]
    if smoke_ids & {int(row["question_id"]) for row in pilot}:
        raise AssertionError("Smoke and pilot selections overlap")

    smoke_payload = _payload(
        rows=smoke,
        dataset=args.dataset,
        seed=args.seed + ":smoke",
        purpose="Protocol-development smoke test; excluded from the cost pilot.",
        method=(
            "Greedy deterministic coverage of unseen databases and difficulties, "
            "with SHA-256 rank as the tie-breaker."
        ),
        excluded_ids=[],
    )
    pilot_payload = _payload(
        rows=pilot,
        dataset=args.dataset,
        seed=args.seed + ":pilot",
        purpose=(
            "Frozen cost/protocol-health pilot; not the 1,534-task final result."
        ),
        method=(
            "First 100 remaining rows under deterministic SHA-256 rank after "
            "excluding the development smoke IDs."
        ),
        excluded_ids=sorted(smoke_ids),
    )
    for path, payload in (
        (args.smoke_output, smoke_payload),
        (args.pilot_output, pilot_payload),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {path} ({payload['tasks']} tasks)")
        print(json.dumps(payload["distribution"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
