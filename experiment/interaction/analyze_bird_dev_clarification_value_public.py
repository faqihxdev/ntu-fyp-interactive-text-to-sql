"""Reproduce primary BIRD statistics from the privacy-safe outcome projection."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

from .analyze_bird_dev_clarification_value import (
    _bootstrap_mean_ci,
    _exact_mcnemar_p_value,
)
from .bird_dev_clarification_value import ARMS, PROTOCOL_VERSION
from .run_support import sha256_file


def _arm_summary(rows: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    successes = [float(bool(row["arms"][arm]["official_success"])) for row in rows]
    return {
        "tasks": len(rows),
        "official_successes": int(sum(successes)),
        "official_ex": round(mean(successes), 6),
        "task_bootstrap_95_ci": _bootstrap_mean_ci(successes),
        "mean_normal_sql_attempts": round(
            mean(float(row["arms"][arm]["normal_sql_attempts"]) for row in rows),
            4,
        ),
        "mean_user_turns": round(
            mean(float(row["arms"][arm]["user_turns"]) for row in rows), 4
        ),
    }


def _paired_summary(
    rows: list[dict[str, Any]], candidate: str, baseline: str
) -> dict[str, Any]:
    differences: list[float] = []
    candidate_only = 0
    baseline_only = 0
    for row in rows:
        candidate_success = bool(row["arms"][candidate]["official_success"])
        baseline_success = bool(row["arms"][baseline]["official_success"])
        differences.append(float(candidate_success) - float(baseline_success))
        candidate_only += int(candidate_success and not baseline_success)
        baseline_only += int(baseline_success and not candidate_success)
    return {
        "candidate": candidate,
        "baseline": baseline,
        "difference_percentage_points": round(100 * mean(differences), 4),
        "task_bootstrap_95_ci_percentage_points": [
            round(100 * value, 4) for value in _bootstrap_mean_ci(differences)
        ],
        "candidate_only_successes": candidate_only,
        "baseline_only_successes": baseline_only,
        "exact_two_sided_mcnemar_p_value": _exact_mcnemar_p_value(
            baseline_only, candidate_only
        ),
    }


def _subset(rows: list[dict[str, Any]]) -> dict[str, Any]:
    initial_failures = [
        row for row in rows if not row["shared_initial_official_success"]
    ]
    return {
        "tasks": len(rows),
        "initial_failures": len(initial_failures),
        "arms": {arm: _arm_summary(rows, arm) for arm in ARMS},
        "paired": {
            "sql_only_minus_no_user": _paired_summary(
                rows, "sql_only_recovery", "no_user"
            ),
            "reactive_minus_no_user": _paired_summary(
                rows, "reactive_clarification", "no_user"
            ),
            "reactive_minus_sql_only": _paired_summary(
                rows, "reactive_clarification", "sql_only_recovery"
            ),
        },
        "reactive_minus_sql_only_among_initial_failures": _paired_summary(
            initial_failures, "reactive_clarification", "sql_only_recovery"
        ),
    }


def analyze_projection(projection: dict[str, Any]) -> dict[str, Any]:
    if projection.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Projection protocol mismatch")
    rows = projection.get("rows") or []
    if not rows:
        raise ValueError("Projection has no rows")
    question_ids = [int(row["question_id"]) for row in rows]
    if len(question_ids) != len(set(question_ids)):
        raise ValueError("Projection contains duplicate question IDs")
    sensitivity_rows = [
        row for row in rows if not row["clarification_value_smoke_or_pilot"]
    ]
    return {
        "schema_version": 1,
        "artifact": "bird-dev-clarification-value-public-analysis",
        "protocol": PROTOCOL_VERSION,
        "scope": (
            "Outcome-only replay; no question text, SQL, prompts, answers, "
            "database results, or trajectories are used."
        ),
        "all_tasks": _subset(rows),
        "excluding_clarification_value_smoke_or_pilot": _subset(
            sensitivity_rows
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    projection = json.loads(args.projection.read_text(encoding="utf-8"))
    result = analyze_projection(projection)
    result["projection_sha256"] = sha256_file(args.projection)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    primary = result["all_tasks"]["paired"]["reactive_minus_sql_only"]
    print(
        f"Wrote {args.output}; tasks={result['all_tasks']['tasks']}; "
        f"reactive-minus-SQL-only={primary['difference_percentage_points']:+.4f} pp"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
