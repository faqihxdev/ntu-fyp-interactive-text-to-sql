"""Paired comparison of two frozen interaction-policy metric files."""

from __future__ import annotations

import argparse
import json
import random
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .run_support import sha256_file

DEFAULT_SEED = 20260820
DEFAULT_BOOTSTRAP_SAMPLES = 10_000


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
        raise TypeError(f"Not an interaction metrics file: {path}")
    return payload


def _record_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = {str(record["question_id"]): record for record in payload["records"]}
    if len(records) != len(payload["records"]):
        raise ValueError("Metrics contain duplicate question IDs")
    return records


def per_task_auc(record: dict[str, Any], max_user_turns: int) -> float:
    success_turn = record.get("success_turn")
    if success_turn is None:
        return 0.0
    success_turn = int(success_turn)
    if max_user_turns == 0:
        return 1.0 if success_turn == 0 else 0.0
    if success_turn == 0:
        return 1.0
    if success_turn > max_user_turns:
        return 0.0
    return (max_user_turns - success_turn + 0.5) / max_user_turns


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("Cannot take a percentile of an empty list")
    ordered = sorted(values)
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def paired_summary(
    ids: list[str],
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    value: Callable[[dict[str, Any]], float],
    *,
    seed: int,
    bootstrap_samples: int,
) -> dict[str, float]:
    differences = [value(candidate[key]) - value(baseline[key]) for key in ids]
    observed = mean(differences)
    rng = random.Random(seed)
    bootstrap = [
        mean(differences[rng.randrange(len(differences))] for _ in differences)
        for _ in range(bootstrap_samples)
    ]
    return {
        "baseline_mean": round(mean(value(baseline[key]) for key in ids), 8),
        "candidate_mean": round(mean(value(candidate[key]) for key in ids), 8),
        "paired_difference": round(observed, 8),
        "paired_bootstrap_95_ci_low": round(percentile(bootstrap, 0.025), 8),
        "paired_bootstrap_95_ci_high": round(percentile(bootstrap, 0.975), 8),
    }


def compare(
    baseline_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    *,
    seed: int = DEFAULT_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
) -> dict[str, Any]:
    if bootstrap_samples < 1:
        raise ValueError("bootstrap_samples must be positive")
    baseline = _record_map(baseline_payload)
    candidate = _record_map(candidate_payload)
    if set(baseline) != set(candidate):
        only_baseline = sorted(set(baseline) - set(candidate))[:10]
        only_candidate = sorted(set(candidate) - set(baseline))[:10]
        raise ValueError(
            "Paired comparison requires identical task IDs; "
            f"baseline-only={only_baseline}, candidate-only={only_candidate}"
        )
    ids = sorted(baseline)
    if not ids:
        raise ValueError("Paired comparison requires at least one task")
    baseline_turns = (
        len(baseline_payload.get("summary", {}).get("ex_by_user_turn", [])) - 1
    )
    candidate_turns = (
        len(candidate_payload.get("summary", {}).get("ex_by_user_turn", [])) - 1
    )
    if baseline_turns != candidate_turns or baseline_turns < 0:
        raise ValueError("Metrics use different or missing user-turn horizons")

    def numeric(field: str) -> Callable[[dict[str, Any]], float]:
        return lambda record: float(record.get(field, 0))

    comparisons = {
        "execution_accuracy": paired_summary(
            ids,
            baseline,
            candidate,
            lambda record: float(bool(record["correct"])),
            seed=seed,
            bootstrap_samples=bootstrap_samples,
        ),
        "normalized_ex_user_turn_auc": paired_summary(
            ids,
            baseline,
            candidate,
            lambda record: per_task_auc(record, baseline_turns),
            seed=seed + 1,
            bootstrap_samples=bootstrap_samples,
        ),
    }
    for index, field in enumerate(
        (
            "user_turns",
            "policy_model_calls",
            "simulator_model_calls",
            "total_inference_calls",
            "database_probes",
            "submission_attempts",
            "repeated_rejected_results",
            "total_tokens",
            "elapsed_ms",
        ),
        start=2,
    ):
        comparisons[field] = paired_summary(
            ids,
            baseline,
            candidate,
            numeric(field),
            seed=seed + index,
            bootstrap_samples=bootstrap_samples,
        )

    baseline_only_correct = sum(
        bool(baseline[key]["correct"]) and not bool(candidate[key]["correct"])
        for key in ids
    )
    candidate_only_correct = sum(
        bool(candidate[key]["correct"]) and not bool(baseline[key]["correct"])
        for key in ids
    )
    accuracy = comparisons["execution_accuracy"]
    baseline_repeats = sum(
        int(baseline[key].get("repeated_rejected_results", 0)) for key in ids
    )
    candidate_repeats = sum(
        int(candidate[key].get("repeated_rejected_results", 0)) for key in ids
    )
    return {
        "comparison_version": "paired-interaction-comparison-v1",
        "tasks": len(ids),
        "max_user_turns": baseline_turns,
        "bootstrap": {"samples": bootstrap_samples, "seed": seed},
        "comparisons": comparisons,
        "discordant_correctness": {
            "baseline_only_correct": baseline_only_correct,
            "candidate_only_correct": candidate_only_correct,
        },
        "predeclared_checks": {
            "candidate_final_ex_noninferior_within_1_point_point_estimate": (
                accuracy["paired_difference"] >= -0.01
            ),
            "candidate_final_ex_noninferior_within_1_point_ci": (
                accuracy["paired_bootstrap_95_ci_low"] >= -0.01
            ),
            "repeat_reduction_relative": (
                round((baseline_repeats - candidate_repeats) / baseline_repeats, 8)
                if baseline_repeats
                else None
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument(
        "--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    baseline = _load(args.baseline)
    candidate = _load(args.candidate)
    result = compare(
        baseline,
        candidate,
        seed=args.seed,
        bootstrap_samples=args.bootstrap_samples,
    )
    result["created_at"] = datetime.now(timezone.utc).isoformat()
    result["inputs"] = {
        "baseline": {
            "path": str(args.baseline.resolve()),
            "sha256": sha256_file(args.baseline),
        },
        "candidate": {
            "path": str(args.candidate.resolve()),
            "sha256": sha256_file(args.candidate),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
