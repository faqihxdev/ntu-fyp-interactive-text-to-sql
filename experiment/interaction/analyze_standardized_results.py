"""Aggregate paired Mini-Interact outcomes without making model calls.

The command reads completed metrics and the excluded formatting-run shards,
then writes only aggregate JSON, Markdown, and SVG figures. It never copies task
IDs, questions, SQL, simulator answers, or hidden ground truth into tracked
output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter, defaultdict
from itertools import pairwise
from pathlib import Path
from statistics import mean
from typing import Any
from xml.sax.saxutils import escape

from .compare_policy_metrics import percentile
from .make_mini_interact_split import stratum
from .official_comparison import analyze_comparison
from .official_protocol import (
    OfficialAction,
    normalize_sra_action,
    validate_sra_action,
)

ARM_ORDER = (
    "mini_react_port",
    "sphinteract_cq",
    "proactive",
    "hybrid",
)
ARM_LABELS = {
    "mini_react_port": "Local reference port",
    "sphinteract_cq": "Sph CQs&ES-derived",
    "proactive": "Proactive-only",
    "hybrid": "Hybrid",
}
ARM_COLORS = {
    "mini_react_port": "#0072B2",
    "sphinteract_cq": "#E69F00",
    "proactive": "#009E73",
    "hybrid": "#CC79A7",
}
ARM_MARKERS = {
    "mini_react_port": "circle",
    "sphinteract_cq": "square",
    "proactive": "triangle",
    "hybrid": "diamond",
}

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DATA = (
    EXPERIMENT_ROOT / "datasets" / "mini-interact" / "mini_interact.jsonl"
)
DEFAULT_BOOTSTRAP_SEED = 20260822
DEFAULT_BOOTSTRAP_SAMPLES = 10_000
DEFAULT_NONINFERIORITY_MARGIN = 0.05
MARGIN_SENSITIVITY = (0.0, 0.025, 0.05)

TERMINAL_GROUPS = (
    "accepted",
    "forced_rejected",
    "sra_no_ambiguity",
    "budget_or_attempt_limit",
    "model_or_protocol_error",
    "oracle_error",
)
TERMINAL_LABELS = {
    "accepted": "Accepted",
    "forced_rejected": "Forced SQL rejected",
    "sra_no_ambiguity": 'SRA stopped: "NO AMBIGUITY"',
    "budget_or_attempt_limit": "Question/submission limit",
    "model_or_protocol_error": "Model/protocol error",
    "oracle_error": "Oracle error",
}
TERMINAL_COLORS = {
    "accepted": "#009E73",
    "forced_rejected": "#D55E00",
    "sra_no_ambiguity": "#E69F00",
    "budget_or_attempt_limit": "#56B4E9",
    "model_or_protocol_error": "#CC79A7",
    "oracle_error": "#6C757D",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_map(records: Any, *, path: Path, section: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise TypeError(f"Missing {section} records in {path}")
    mapped = {str(record["question_id"]): record for record in records}
    if len(mapped) != len(records):
        raise ValueError(f"Duplicate task IDs in {path} ({section})")
    return mapped


def _load_arm(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    official = payload.get("official_protocol") or {}
    secondary = payload.get("secondary_policy_analysis") or {}
    official_records = _record_map(
        official.get("records"), path=path, section="official_protocol"
    )
    secondary_records = _record_map(
        secondary.get("records"), path=path, section="secondary_policy_analysis"
    )
    if set(official_records) != set(secondary_records):
        raise ValueError(f"Official and secondary task sets differ in {path}")
    records = {
        question_id: {
            **official_records[question_id],
            **secondary_records[question_id],
        }
        for question_id in official_records
    }
    return {
        "path": path,
        "payload": payload,
        "records": records,
        "secondary_summary": secondary.get("summary") or {},
    }


def _success_curve(
    records: dict[str, dict[str, Any]], horizon: int
) -> list[dict[str, Any]]:
    total = len(records)
    curve = []
    for turn in range(horizon + 1):
        correct = sum(
            record.get("success_turn") is not None
            and int(record["success_turn"]) <= turn
            for record in records.values()
        )
        curve.append(
            {
                "user_turn": turn,
                "successes": correct,
                "success_rate_percent": round(100 * correct / total, 4),
            }
        )
    return curve


def _normalized_auc(curve: list[dict[str, Any]], total: int) -> float:
    if len(curve) == 1:
        return round(int(curve[0]["successes"]) / total, 8)
    rates = [int(point["successes"]) / total for point in curve]
    area = sum((left + right) / 2 for left, right in pairwise(rates))
    return round(area / (len(curve) - 1), 8)


def _terminal_group(reason: str) -> str:
    if reason == "accepted_submission":
        return "accepted"
    if reason == "forced_submission_rejected":
        return "forced_rejected"
    if reason == "sra_no_ambiguity_after_rejection":
        return "sra_no_ambiguity"
    if reason in {
        "normal_submission_limit_exhausted",
        "user_question_limit_exhausted",
    }:
        return "budget_or_attempt_limit"
    if reason in {"model_error", "model_protocol_error"}:
        return "model_or_protocol_error"
    if reason == "oracle_error":
        return "oracle_error"
    raise ValueError(f"Unclassified terminal reason: {reason}")


def _terminal_counts(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = Counter(str(record["termination"]) for record in records.values())
    grouped = Counter()
    for reason, count in raw.items():
        grouped[_terminal_group(reason)] += count
    return {
        "raw": dict(sorted(raw.items())),
        "grouped": {group: grouped[group] for group in TERMINAL_GROUPS},
    }


def _paired_outcomes(
    baseline: dict[str, dict[str, Any]],
    hybrid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ids = sorted(hybrid)
    both = [
        question_id
        for question_id in ids
        if baseline[question_id]["correct"] and hybrid[question_id]["correct"]
    ]
    baseline_only = [
        question_id
        for question_id in ids
        if baseline[question_id]["correct"] and not hybrid[question_id]["correct"]
    ]
    hybrid_only = [
        question_id
        for question_id in ids
        if not baseline[question_id]["correct"] and hybrid[question_id]["correct"]
    ]
    neither = [
        question_id
        for question_id in ids
        if not baseline[question_id]["correct"] and not hybrid[question_id]["correct"]
    ]
    return {
        "both_succeed": len(both),
        "baseline_only_succeeds": len(baseline_only),
        "hybrid_only_succeeds": len(hybrid_only),
        "neither_succeeds": len(neither),
        "hybrid_only_baseline_terminal_reasons": dict(
            sorted(
                Counter(baseline[item]["termination"] for item in hybrid_only).items()
            )
        ),
        "baseline_only_hybrid_terminal_reasons": dict(
            sorted(
                Counter(hybrid[item]["termination"] for item in baseline_only).items()
            )
        ),
    }


def _aborted_run_ids(aborted_run_dir: Path | None) -> set[str]:
    if aborted_run_dir is None:
        return set()
    shard_root = aborted_run_dir / "shards"
    ids: set[str] = set()
    for path in sorted(shard_root.glob("shard-*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            ids.add(str(payload["task"]["question_id"]))
    return ids


def _sensitivity(
    arms: dict[str, dict[str, Any]], excluded_ids: set[str]
) -> dict[str, Any] | None:
    if not excluded_ids:
        return None
    all_ids = set(arms["hybrid"]["records"])
    included = sorted(all_ids - excluded_ids)
    if not included:
        raise ValueError("Sensitivity exclusion removed every task")
    arm_metrics: dict[str, dict[str, Any]] = {}
    for arm in ARM_ORDER:
        records = {item: arms[arm]["records"][item] for item in included}
        curve = _success_curve(records, 4)
        arm_metrics[arm] = {
            "successes": sum(bool(record["correct"]) for record in records.values()),
            "success_rate": round(
                sum(bool(record["correct"]) for record in records.values())
                / len(records),
                8,
            ),
            "success_by_user_turn": curve,
            "normalized_ex_user_turn_auc": _normalized_auc(curve, len(records)),
            "mean_user_questions": round(
                mean(float(record["user_turns"]) for record in records.values()), 8
            ),
            "mean_bird_coins": round(
                mean(float(record["bird_coins"]) for record in records.values()), 8
            ),
            "mean_submissions": round(
                mean(float(record["submissions"]) for record in records.values()), 8
            ),
            "mean_policy_model_calls": round(
                mean(
                    float(record["policy_model_calls"])
                    for record in records.values()
                ),
                8,
            ),
            "mean_simulator_model_calls": round(
                mean(
                    float(record["simulator_model_calls"])
                    for record in records.values()
                ),
                8,
            ),
            "mean_total_inference_calls": round(
                mean(
                    float(record["policy_model_calls"])
                    + float(record["simulator_model_calls"])
                    for record in records.values()
                ),
                8,
            ),
            "mean_total_tokens": round(
                mean(float(record["total_tokens"]) for record in records.values()),
                8,
            ),
        }
    successes = {arm: values["successes"] for arm, values in arm_metrics.items()}
    rates = {arm: values["success_rate"] for arm, values in arm_metrics.items()}
    differences = {
        f"hybrid_minus_{baseline}": round(rates["hybrid"] - rates[baseline], 8)
        for baseline in ARM_ORDER[:-1]
    }
    return {
        "excluded_ids_count": len(all_ids & excluded_ids),
        "included_tasks": len(included),
        "successes": successes,
        "success_rates": rates,
        "all_reported_arm_metrics": arm_metrics,
        "paired_success_rate_differences": differences,
        "ids_withheld_from_output": True,
    }


def _four_arm_overlap(arms: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return every four-arm success/failure cell without exposing task IDs."""

    ids = sorted(arms[ARM_ORDER[0]]["records"])
    cells: Counter[str] = Counter()
    for question_id in ids:
        bits = "".join(
            "1" if bool(arms[arm]["records"][question_id]["correct"]) else "0"
            for arm in ARM_ORDER
        )
        cells[bits] += 1
    return {
        "arm_bit_order": list(ARM_ORDER),
        "bit_meaning": "1=task success; 0=task failure",
        "cells": {
            format(value, "04b"): cells[format(value, "04b")]
            for value in range(16)
        },
        "tasks": len(ids),
    }


def _binomial_cdf(k: int, n: int, probability: float) -> float:
    return sum(
        math.comb(n, value)
        * probability**value
        * (1.0 - probability) ** (n - value)
        for value in range(k + 1)
    )


def _solve_decreasing_binomial_cdf(k: int, n: int, target: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(90):
        midpoint = (low + high) / 2.0
        if _binomial_cdf(k, n, midpoint) > target:
            low = midpoint
        else:
            high = midpoint
    return (low + high) / 2.0


def _clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact interval for the hybrid-win share among discordant task pairs."""

    if not 0 <= k <= n or n <= 0:
        raise ValueError("Clopper-Pearson requires 0 <= k <= n and n > 0")
    lower = (
        0.0
        if k == 0
        else _solve_decreasing_binomial_cdf(k - 1, n, 1.0 - alpha / 2.0)
    )
    upper = (
        1.0
        if k == n
        else _solve_decreasing_binomial_cdf(k, n, alpha / 2.0)
    )
    return lower, upper


def _official_action(payload: dict[str, Any]) -> OfficialAction:
    return OfficialAction(
        kind=str(payload.get("kind", "")),
        sql=payload.get("sql"),
        question=payload.get("question"),
        table_name=payload.get("table_name"),
        column_name=payload.get("column_name"),
        knowledge_name=payload.get("knowledge_name"),
        purpose=payload.get("purpose"),
        review_source=payload.get("review_source"),
        summary=payload.get("summary"),
        reviews=payload.get("reviews"),
        options=tuple(payload.get("options") or ()),
    )


def _is_valid_sra(action: OfficialAction) -> bool:
    try:
        action.validate()
        validate_sra_action(action)
    except ValueError:
        return False
    return True


def _classify_protocol_error(message: str) -> str:
    folded = message.casefold()
    if any(
        marker in folded
        for marker in ("sra", "answer option", "summary", "review all four")
    ):
        return "malformed_sra_action"
    if "requires non-empty sql" in folded:
        return "empty_sql_action"
    return "other_protocol_error"


def _load_raw_trajectory_audit(path: Path) -> dict[str, Any]:
    normalization_changes: Counter[str] = Counter()
    protocol_errors: Counter[str] = Counter()
    raw_sra_decisions = 0
    raw_sra_compliant = 0
    post_normalization_compliant = 0
    normalization_events = 0
    sra_user_answers = 0
    trajectories = 0
    successful = 0
    normalized_trajectories = 0
    normalized_successes = 0
    unnormalized_trajectories = 0
    unnormalized_successes = 0
    repeated_after_rejection_events = 0
    trajectories_with_rejected_sql_repeat = 0
    forced_endpoint_status: Counter[str] = Counter()

    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise TypeError(f"Expected an object at {path}:{line_number}")
            trajectories += 1
            is_success = payload.get("termination") == "accepted_submission"
            successful += int(is_success)
            had_normalization = False
            rejected_sql: set[str] = set()
            current_candidate: str | None = None
            repeated_in_trajectory = False
            final_execution_status: str | None = None

            for event in payload.get("events") or []:
                kind = str(event.get("kind", ""))
                event_payload = event.get("payload") or {}
                if kind == "policy_decision" and event_payload.get("phase") in {
                    "proactive_sra",
                    "reactive_sra",
                }:
                    raw_sra_decisions += 1
                    action = _official_action(event_payload.get("action") or {})
                    raw_valid = _is_valid_sra(action)
                    raw_sra_compliant += int(raw_valid)
                    normalized, _ = normalize_sra_action(action)
                    post_normalization_compliant += int(_is_valid_sra(normalized))
                elif kind == "sra_normalization":
                    normalization_events += 1
                    had_normalization = True
                    normalization_changes.update(
                        str(value) for value in event_payload.get("changes") or []
                    )
                elif kind == "user_clarification":
                    sra_user_answers += 1
                elif kind == "model_protocol_error":
                    protocol_errors[
                        _classify_protocol_error(str(event_payload.get("error", "")))
                    ] += 1
                elif kind == "candidate_exposed":
                    current_candidate = str(event_payload.get("sql") or "").strip()
                    if current_candidate and current_candidate in rejected_sql:
                        repeated_after_rejection_events += 1
                        repeated_in_trajectory = True
                elif kind == "submission_judgement":
                    if (
                        event_payload.get("accepted") is not True
                        and current_candidate
                    ):
                        rejected_sql.add(current_candidate)
                elif kind == "sql_execution":
                    observation = event_payload.get("observation") or {}
                    final_execution_status = str(
                        observation.get("status", "missing_status")
                    )

            if payload.get("termination") == "forced_submission_rejected":
                forced_endpoint_status[final_execution_status or "no_execution"] += 1

            if repeated_in_trajectory:
                trajectories_with_rejected_sql_repeat += 1
            if had_normalization:
                normalized_trajectories += 1
                normalized_successes += int(is_success)
            else:
                unnormalized_trajectories += 1
                unnormalized_successes += int(is_success)

    return {
        "trajectories": trajectories,
        "successes": successful,
        "sra": {
            "raw_decisions": raw_sra_decisions,
            "raw_compliant": raw_sra_compliant,
            "raw_compliance_rate": (
                round(raw_sra_compliant / raw_sra_decisions, 8)
                if raw_sra_decisions
                else None
            ),
            "post_normalization_compliant": post_normalization_compliant,
            "post_normalization_compliance_rate": (
                round(post_normalization_compliant / raw_sra_decisions, 8)
                if raw_sra_decisions
                else None
            ),
            "normalization_events": normalization_events,
            "normalization_change_counts": dict(sorted(normalization_changes.items())),
            "user_answers": sra_user_answers,
        },
        "normalizer_outcome_strata": {
            "activated": {
                "trajectories": normalized_trajectories,
                "successes": normalized_successes,
                "success_rate": (
                    round(normalized_successes / normalized_trajectories, 8)
                    if normalized_trajectories
                    else None
                ),
            },
            "not_activated": {
                "trajectories": unnormalized_trajectories,
                "successes": unnormalized_successes,
                "success_rate": (
                    round(unnormalized_successes / unnormalized_trajectories, 8)
                    if unnormalized_trajectories
                    else None
                ),
            },
            "interpretation": (
                "Observational only: normalizer activation is not randomized and "
                "may mark harder or less compliant trajectories."
            ),
        },
        "protocol_error_categories": dict(sorted(protocol_errors.items())),
        "rejected_sql_repeat_observation": {
            "exact_repeat_events": repeated_after_rejection_events,
            "trajectories_with_exact_repeat": trajectories_with_rejected_sql_repeat,
            "enforced_by_controller": False,
            "scope": (
                "Exact stripped-string repeats after a prior scorer rejection; "
                "not semantic SQL equivalence and not answer incorporation."
            ),
        },
        "forced_rejected_endpoint_execution": {
            "trajectories": sum(forced_endpoint_status.values()),
            "executable": forced_endpoint_status["ok"],
            "status_counts": dict(sorted(forced_endpoint_status.items())),
            "definition": (
                "Execution status of the final SQL execution in trajectories "
                "terminating with forced_submission_rejected."
            ),
        },
    }


def _load_public_metadata(
    path: Path, expected_ids: set[str]
) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise TypeError(f"Expected an object at {path}:{line_number}")
            question_id = str(row["instance_id"])
            if question_id not in expected_ids:
                continue
            metadata[question_id] = {
                "database": str(row["selected_database"]),
                "stratum": stratum(row),
            }
    if set(metadata) != expected_ids:
        raise ValueError("Public Mini-Interact data do not cover the paired task IDs")
    return metadata


def _grouped_success(
    arms: dict[str, dict[str, Any]],
    metadata: dict[str, dict[str, str]],
    group_key: str,
) -> dict[str, Any]:
    grouped_ids: dict[str, list[str]] = defaultdict(list)
    for question_id, values in metadata.items():
        grouped_ids[values[group_key]].append(question_id)
    result: dict[str, Any] = {}
    for group, ids in sorted(grouped_ids.items()):
        arm_values: dict[str, Any] = {}
        for arm in ARM_ORDER:
            successes = sum(
                bool(arms[arm]["records"][question_id]["correct"])
                for question_id in ids
            )
            arm_values[arm] = {
                "successes": successes,
                "success_rate": round(successes / len(ids), 8),
            }
        result[group] = {"tasks": len(ids), "arms": arm_values}
    return result


def _cluster_bootstrap(
    differences_by_cluster: dict[str, list[float]],
    *,
    seed: int,
    samples: int,
) -> tuple[float, float]:
    clusters = sorted(differences_by_cluster)
    rng = random.Random(seed)
    values = []
    for _ in range(samples):
        sampled = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        differences = [
            value
            for cluster in sampled
            for value in differences_by_cluster[cluster]
        ]
        values.append(mean(differences))
    return percentile(values, 0.025), percentile(values, 0.975)


def _database_robustness(
    arms: dict[str, dict[str, Any]],
    metadata: dict[str, dict[str, str]],
    *,
    seed: int,
    samples: int,
) -> dict[str, Any]:
    ids = list(metadata)
    databases = sorted({values["database"] for values in metadata.values()})
    result: dict[str, Any] = {}
    for index, baseline in enumerate(ARM_ORDER[:-1]):
        differences = {
            question_id: float(bool(arms["hybrid"]["records"][question_id]["correct"]))
            - float(bool(arms[baseline]["records"][question_id]["correct"]))
            for question_id in ids
        }
        by_database: dict[str, list[float]] = defaultdict(list)
        for question_id, value in differences.items():
            by_database[metadata[question_id]["database"]].append(value)
        low, high = _cluster_bootstrap(
            by_database,
            seed=seed + index,
            samples=samples,
        )
        full_difference = mean(differences.values())
        leave_one_out = {
            database: mean(
                value
                for question_id, value in differences.items()
                if metadata[question_id]["database"] != database
            )
            for database in databases
        }
        full_sign = 0 if full_difference == 0 else (1 if full_difference > 0 else -1)
        reversals = [
            database
            for database, value in leave_one_out.items()
            if value != 0 and (1 if value > 0 else -1) != full_sign
        ]
        result[f"hybrid_minus_{baseline}"] = {
            "paired_difference": round(full_difference, 8),
            "database_cluster_bootstrap_95_ci": [round(low, 8), round(high, 8)],
            "clusters": len(databases),
            "leave_one_database_out_min": round(min(leave_one_out.values()), 8),
            "leave_one_database_out_max": round(max(leave_one_out.values()), 8),
            "leave_one_database_out_direction_reversals": reversals,
        }
    return result


def _paired_audit(
    arms: dict[str, dict[str, Any]],
    *,
    seed: int,
    samples: int,
    noninferiority_margin: float,
) -> dict[str, Any]:
    ids = list(arms[ARM_ORDER[0]]["records"])
    metrics_paths = {arm: arms[arm]["path"] for arm in ARM_ORDER}
    comparison = analyze_comparison(
        metrics_paths,
        expected_ids=ids,
        seed=seed,
        bootstrap_samples=samples,
        noninferiority_margin=noninferiority_margin,
    )
    comparisons = comparison["paired_comparisons"]
    for values in comparisons.values():
        discordant = values["discordant_success"]
        baseline_only = int(discordant["baseline_only"])
        hybrid_only = int(discordant["hybrid_only"])
        total_discordant = baseline_only + hybrid_only
        lower, upper = _clopper_pearson(hybrid_only, total_discordant)
        discordant["hybrid_win_share_exact_95_ci"] = [
            round(lower, 8),
            round(upper, 8),
        ]
        discordant["conditional_paired_difference_exact_95_ci"] = [
            round((2 * lower - 1) * total_discordant / len(ids), 8),
            round((2 * upper - 1) * total_discordant / len(ids), 8),
        ]

    proactive = comparisons["hybrid_minus_proactive"]
    success_low = float(
        proactive["task_success"]["paired_bootstrap_95_ci_low"]
    )
    coin_high = float(proactive["bird_coins"]["paired_bootstrap_95_ci_high"])
    margin_sensitivity = [
        {
            "success_noninferiority_margin": margin,
            "success_lower_bound_passes": success_low >= -margin,
            "fewer_coins_passes": coin_high < 0,
            "combined_efficiency_gate_passes": success_low >= -margin
            and coin_high < 0,
        }
        for margin in MARGIN_SENSITIVITY
    ]
    return {
        "bootstrap": comparison["bootstrap"],
        "paired_comparisons": comparisons,
        "primary_exact_test_family": comparison["primary_exact_test_family"],
        "proactive_margin_sensitivity": margin_sensitivity,
        "input_metric_sha256": {
            arm: _sha256(arms[arm]["path"]) for arm in ARM_ORDER
        },
    }


def _svg_text(
    x: float,
    y: float,
    value: str,
    *,
    size: int = 24,
    weight: int = 400,
    anchor: str = "start",
    fill: str = "#17212B",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
        f'fill="{fill}">{escape(value)}</text>'
    )


def _marker(shape: str, x: float, y: float, color: str, size: float = 8) -> str:
    if shape == "circle":
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{size}" fill="{color}"/>'
    if shape == "square":
        side = size * 1.8
        return (
            f'<rect x="{x - side / 2:.1f}" y="{y - side / 2:.1f}" '
            f'width="{side:.1f}" height="{side:.1f}" fill="{color}"/>'
        )
    if shape == "triangle":
        points = (
            f"{x:.1f},{y - size:.1f} "
            f"{x - size:.1f},{y + size:.1f} "
            f"{x + size:.1f},{y + size:.1f}"
        )
        return f'<polygon points="{points}" fill="{color}"/>'
    points = (
        f"{x:.1f},{y - size:.1f} "
        f"{x - size:.1f},{y:.1f} "
        f"{x:.1f},{y + size:.1f} "
        f"{x + size:.1f},{y:.1f}"
    )
    return f'<polygon points="{points}" fill="{color}"/>'


def _write_success_figure(
    output: Path, analysis: dict[str, Any], *, horizon: int
) -> None:
    width, height = 1600, 1000
    left, right, top, bottom = 170, 250, 210, 180
    plot_width = width - left - right
    plot_height = height - top - bottom
    y_max = 40

    def x_scale(turn: int) -> float:
        return left + plot_width * turn / horizon

    def y_scale(value: float) -> float:
        return top + plot_height * (y_max - value) / y_max

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        ),
        "<title>Task success over user turns</title>",
        (
            "<desc>Cumulative task success at each user-turn budget for four "
            "matched Mini-Interact policy arms.</desc>"
        ),
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        _svg_text(left, 70, "Task success over user turns", size=42, weight=500),
        _svg_text(
            left,
            112,
            "Mini-Interact Stress adaptation - 240 paired tasks per arm - Luna/high",
            size=22,
            fill="#52606D",
        ),
    ]
    for tick in range(0, y_max + 1, 5):
        y = y_scale(tick)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
            f'y2="{y:.1f}" stroke="#D9DEE3" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(
                left - 20, y + 8, f"{tick}%", size=20, anchor="end", fill="#52606D"
            )
        )
    for turn in range(horizon + 1):
        x = x_scale(turn)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
            f'y2="{top + plot_height}" stroke="#EEF1F4" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(x, top + plot_height + 42, str(turn), size=22, anchor="middle")
        )
    parts.extend(
        [
            (
                f'<rect x="{left}" y="{top}" width="{plot_width}" '
                f'height="{plot_height}" fill="none" stroke="#AAB4BE" '
                'stroke-width="1.5"/>'
            ),
            _svg_text(
                left + plot_width / 2,
                top + plot_height + 90,
                "Maximum user questions used",
                size=24,
                weight=500,
                anchor="middle",
            ),
            (
                f'<text x="48" y="{top + plot_height / 2:.1f}" '
                'font-family="Arial, sans-serif" font-size="24" font-weight="500" '
                'text-anchor="middle" fill="#17212B" '
                f'transform="rotate(-90 48 {top + plot_height / 2:.1f})">'
                "Cumulative task success (%)</text>"
            ),
        ]
    )

    endpoint_offsets = {
        "mini_react_port": -8,
        "sphinteract_cq": 30,
        "proactive": -12,
        "hybrid": -8,
    }
    for arm in ARM_ORDER:
        curve = analysis["arms"][arm]["success_by_user_turn"]
        points = [
            (
                x_scale(int(point["user_turn"])),
                y_scale(float(point["success_rate_percent"])),
            )
            for point in curve
        ]
        point_string = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        color = ARM_COLORS[arm]
        parts.append(
            f'<polyline points="{point_string}" fill="none" stroke="{color}" '
            'stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, y in points:
            parts.append(_marker(ARM_MARKERS[arm], x, y, color))
        final = float(curve[-1]["success_rate_percent"])
        parts.append(
            _svg_text(
                points[-1][0] + 22,
                points[-1][1] + endpoint_offsets[arm],
                f"{final:.1f}%",
                size=22,
                weight=500,
                fill=color,
            )
        )

    legend_y = 148
    for index, arm in enumerate(ARM_ORDER):
        column = index % 2
        row = index // 2
        x = left + column * 610
        y = legend_y + row * 42
        color = ARM_COLORS[arm]
        parts.append(
            f'<line x1="{x}" y1="{y}" x2="{x + 42}" y2="{y}" '
            f'stroke="{color}" stroke-width="5"/>'
        )
        parts.append(_marker(ARM_MARKERS[arm], x + 21, y, color, 7))
        auc = analysis["arms"][arm]["normalized_ex_user_turn_auc"]
        parts.append(
            _svg_text(
                x + 58,
                y + 8,
                f"{ARM_LABELS[arm]} - AUC {auc:.3f}",
                size=21,
            )
        )
    parts.append(
        _svg_text(
            width - right,
            972,
            "Turn 0 means success before any user answer; AUC uses turns 0-4.",
            size=18,
            anchor="end",
            fill="#52606D",
        )
    )
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _write_terminal_figure(output: Path, analysis: dict[str, Any]) -> None:
    width, height = 1600, 1000
    left, right, top, bottom = 330, 110, 240, 290
    plot_width = width - left - right
    plot_height = height - top - bottom
    total = analysis["integrity"]["tasks_per_arm"]
    bar_height = 72
    gap = (plot_height - len(ARM_ORDER) * bar_height) / (len(ARM_ORDER) - 1)

    def x_scale(value: int) -> float:
        return left + plot_width * value / total

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        ),
        "<title>How trajectories ended</title>",
        (
            "<desc>Stacked counts of accepted and failed terminal outcomes for "
            "four matched Mini-Interact policy arms.</desc>"
        ),
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        _svg_text(left, 70, "How trajectories ended", size=42, weight=500),
        _svg_text(
            left,
            112,
            "Rejected forced submissions dominated; SRA early stopping was also common.",
            size=22,
            fill="#52606D",
        ),
    ]
    for tick in range(0, total + 1, 60):
        x = x_scale(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 24}" x2="{x:.1f}" '
            f'y2="{top + plot_height}" stroke="#E3E7EB" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(x, top - 45, str(tick), size=20, anchor="middle", fill="#52606D")
        )
    for index, arm in enumerate(ARM_ORDER):
        y = top + index * (bar_height + gap)
        parts.append(
            _svg_text(
                left - 24,
                y + bar_height / 2 + 8,
                ARM_LABELS[arm],
                size=23,
                weight=500 if arm == "hybrid" else 400,
                anchor="end",
            )
        )
        cursor = left
        grouped = analysis["arms"][arm]["terminal_outcomes"]["grouped"]
        for group in TERMINAL_GROUPS:
            count = int(grouped[group])
            segment_width = plot_width * count / total
            if count:
                parts.append(
                    f'<rect x="{cursor:.1f}" y="{y:.1f}" width="{segment_width:.1f}" '
                    f'height="{bar_height}" fill="{TERMINAL_COLORS[group]}"/>'
                )
                if segment_width >= 48:
                    label_color = (
                        "#17212B" if group == "budget_or_attempt_limit" else "#FFFFFF"
                    )
                    parts.append(
                        _svg_text(
                            cursor + segment_width / 2,
                            y + bar_height / 2 + 8,
                            str(count),
                            size=21,
                            weight=500,
                            anchor="middle",
                            fill=label_color,
                        )
                    )
            cursor += segment_width
        parts.append(
            f'<rect x="{left}" y="{y}" width="{plot_width}" height="{bar_height}" '
            'fill="none" stroke="#AAB4BE" stroke-width="1.2"/>'
        )
    parts.append(
        _svg_text(
            left + plot_width / 2,
            top + plot_height + 58,
            f"Trajectories (n={total} per arm)",
            size=24,
            weight=500,
            anchor="middle",
        )
    )

    legend_y = 815
    legend_columns = 3
    legend_width = 410
    for index, group in enumerate(TERMINAL_GROUPS):
        column = index % legend_columns
        row = index // legend_columns
        x = left + column * legend_width
        y = legend_y + row * 50
        parts.append(
            f'<rect x="{x}" y="{y - 18}" width="26" height="26" '
            f'fill="{TERMINAL_COLORS[group]}"/>'
        )
        parts.append(_svg_text(x + 40, y + 4, TERMINAL_LABELS[group], size=19))
    parts.append(
        _svg_text(
            left,
            945,
            '"NO AMBIGUITY" is a controller stop after rejection - not a gold label about the task.',
            size=18,
            fill="#52606D",
        )
    )
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _write_audit_markdown(output: Path, analysis: dict[str, Any]) -> None:
    lines = [
        "# Read-only audit of the standardized Mini-Interact result",
        "",
        "**Date:** 23 August 2026  ",
        "**Boundary:** existing trajectories and metrics only; no model, simulator, or evaluator calls.",
        "",
        "## Verified arm summaries",
        "",
        "| Local controller bundle | Success | Coins/task | Questions/task | Total calls/task | Proxy tokens/task |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    total = int(analysis["integrity"]["tasks_per_arm"])
    for arm in ARM_ORDER:
        values = analysis["arms"][arm]
        lines.append(
            "| "
            f"{values['label']} | {values['successes']}/{total} "
            f"({100 * values['success_rate']:.2f}%) | "
            f"{values['mean_bird_coins']:.3f} | "
            f"{values['mean_user_questions']:.3f} | "
            f"{values['mean_total_inference_calls']:.3f} | "
            f"{values['mean_total_tokens']:.0f} |"
        )

    lines.extend(
        [
            "",
            "## Paired statistical checks",
            "",
            "| Hybrid minus comparator | Success difference (95% task bootstrap) | Discordants | Exact McNemar | Holm-adjusted primary p |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    paired = analysis["paired_statistical_audit"]["paired_comparisons"]
    for baseline in ARM_ORDER[:-1]:
        key = f"hybrid_minus_{baseline}"
        values = paired[key]
        success = values["task_success"]
        discordant = values["discordant_success"]
        adjusted = discordant.get("holm_adjusted_primary_p")
        adjusted_text = f"{adjusted:.6f}" if adjusted is not None else "n/a"
        lines.append(
            "| "
            f"{ARM_LABELS[baseline]} | "
            f"{100 * success['paired_difference']:+.2f} pp "
            f"[{100 * success['paired_bootstrap_95_ci_low']:+.2f}, "
            f"{100 * success['paired_bootstrap_95_ci_high']:+.2f}] | "
            f"{discordant['baseline_only']} / {discordant['hybrid_only']} | "
            f"{discordant['exact_mcnemar_two_sided_p']:.6f} | "
            f"{adjusted_text} |"
        )

    lines.extend(
        [
            "",
            "The bootstrap resamples the 240 realized task differences. It does not include new model or simulator draws. The exact test conditions on discordant task pairs; disagreement at a threshold is therefore reported rather than resolved in favour of one method.",
            "",
            "## Raw versus normalized SRA compliance",
            "",
            "| Bundle | Raw SRA decisions | Raw compliant | Post-normalization compliant | Normalizer events | Success when activated |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    raw_audit = analysis.get("raw_trajectory_audit") or {}
    for arm in ARM_ORDER:
        values = raw_audit.get(arm)
        if not values:
            continue
        sra = values["sra"]
        normalized = values["normalizer_outcome_strata"]["activated"]
        activated_rate = (
            "n/a"
            if normalized["success_rate"] is None
            else f"{100 * normalized['success_rate']:.2f}%"
        )
        lines.append(
            f"| {ARM_LABELS[arm]} | {sra['raw_decisions']} | "
            f"{sra['raw_compliant']} | {sra['post_normalization_compliant']} | "
            f"{sra['normalization_events']} | {activated_rate} |"
        )
    lines.extend(
        [
            "",
            "Normalizer outcome strata are observational: activation was not randomized and may identify harder or less format-compliant trajectories.",
            "The normalizer is part of the SRA bundles; this table does not establish that it is causally neutral.",
            "",
            "## Forced rejected endpoints",
            "",
            "| Bundle | Executable final endpoint | Forced rejected endpoints |",
            "|---|---:|---:|",
        ]
    )
    for arm in ARM_ORDER:
        values = raw_audit.get(arm)
        if not values:
            continue
        endpoint = values["forced_rejected_endpoint_execution"]
        lines.append(
            f"| {ARM_LABELS[arm]} | {endpoint['executable']} | "
            f"{endpoint['trajectories']} |"
        )
    overlap = analysis["four_arm_success_overlap"]
    lines.extend(
        [
            "",
            "The machine-readable audit also exports all 16 four-arm success/failure cells. Non-zero cells are listed below in reference/reactive/proactive/hybrid bit order:",
            "",
            ", ".join(
                f"`{bits}`={count}"
                for bits, count in overlap["cells"].items()
                if count
            ),
            "",
            "## Six-ID exclusion sensitivity",
            "",
            "| Bundle | Success | Coins/task | Questions/task | All calls/task | AUC | Proxy tokens/task |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    sensitivity = analysis.get("aborted_v1_sensitivity") or {}
    for arm in ARM_ORDER:
        values = (sensitivity.get("all_reported_arm_metrics") or {}).get(arm)
        if not values:
            continue
        lines.append(
            f"| {ARM_LABELS[arm]} | {values['successes']}/"
            f"{sensitivity['included_tasks']} "
            f"({100 * values['success_rate']:.2f}%) | "
            f"{values['mean_bird_coins']:.3f} | "
            f"{values['mean_user_questions']:.3f} | "
            f"{values['mean_total_inference_calls']:.3f} | "
            f"{values['normalized_ex_user_turn_auc']:.4f} | "
            f"{values['mean_total_tokens']:.0f} |"
        )
    lines.extend(
        [
            "",
            "## Robustness and interpretation boundaries",
            "",
        ]
    )
    database = analysis.get("database_robustness") or {}
    for baseline in ARM_ORDER[:-1]:
        values = database.get(f"hybrid_minus_{baseline}")
        if not values:
            continue
        interval = values["database_cluster_bootstrap_95_ci"]
        lines.append(
            f"- Hybrid minus {ARM_LABELS[baseline]}: database-cluster bootstrap "
            f"95% interval [{100 * interval[0]:+.2f}, {100 * interval[1]:+.2f}] pp; "
            f"leave-one-database-out direction reversals: "
            f"{len(values['leave_one_database_out_direction_reversals'])}."
        )
    lines.extend(
        [
            "- The six-ID exclusion is a post hoc sensitivity after a full V2 restart; it does not undo the protocol correction learned from partial V1 runs.",
            "- Shared failure and terminal labels are descriptive endpoints, not semantic diagnoses of SQL reasoning or answer incorporation.",
            "- The model alias is mutable, policy and simulator use independent stochastic calls, and the gold-conditioned simulator is protected by lexical rather than semantic leakage checks.",
            "",
            "## Rejected-SQL repetition observation",
            "",
            "The final standardized controller did not structurally block repeated rejected SQL. The counts below are observations from retained history, not enforced invariants:",
            "",
            "| Bundle | Exact repeat events after rejection | Trajectories affected |",
            "|---|---:|---:|",
        ]
    )
    for arm in ARM_ORDER:
        values = raw_audit.get(arm)
        if not values:
            continue
        repeat = values["rejected_sql_repeat_observation"]
        lines.append(
            f"| {ARM_LABELS[arm]} | {repeat['exact_repeat_events']} | "
            f"{repeat['trajectories_with_exact_repeat']} |"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def analyze(
    run_dir: Path,
    *,
    aborted_run_dir: Path | None,
    output_json: Path,
    figure_dir: Path,
    public_data: Path | None = DEFAULT_PUBLIC_DATA,
    output_markdown: Path | None = None,
    horizon: int = 4,
    bootstrap_seed: int = DEFAULT_BOOTSTRAP_SEED,
    bootstrap_samples: int = DEFAULT_BOOTSTRAP_SAMPLES,
    noninferiority_margin: float = DEFAULT_NONINFERIORITY_MARGIN,
) -> dict[str, Any]:
    arms = {
        arm: _load_arm(run_dir / "combined" / f"{arm}.metrics.json")
        for arm in ARM_ORDER
    }
    id_sets = {arm: set(data["records"]) for arm, data in arms.items()}
    if any(id_sets[arm] != id_sets[ARM_ORDER[0]] for arm in ARM_ORDER[1:]):
        raise ValueError("All arms must contain the same paired task IDs")
    task_ids = id_sets[ARM_ORDER[0]]
    if not task_ids:
        raise ValueError("No completed tasks found")

    arm_results: dict[str, Any] = {}
    for arm in ARM_ORDER:
        records = arms[arm]["records"]
        curve = _success_curve(records, horizon)
        auc = _normalized_auc(curve, len(records))
        recorded_auc = float(
            arms[arm]["secondary_summary"].get("normalized_ex_user_turn_auc", -1)
        )
        if abs(auc - recorded_auc) > 1e-6:
            raise ValueError(
                f"Recomputed AUC differs for {arm}: {auc} versus {recorded_auc}"
            )
        successes = sum(bool(record["correct"]) for record in records.values())
        mean_value = lambda key: round(  # noqa: E731 - compact metric reducer.
            mean(float(record[key]) for record in records.values()), 8
        )
        arm_results[arm] = {
            "label": ARM_LABELS[arm],
            "successes": successes,
            "success_rate": round(successes / len(records), 8),
            "success_by_user_turn": curve,
            "normalized_ex_user_turn_auc": auc,
            "mean_user_questions": round(
                sum(float(record["user_turns"]) for record in records.values())
                / len(records),
                8,
            ),
            "mean_bird_coins": mean_value("bird_coins"),
            "mean_submissions": mean_value("submissions"),
            "mean_policy_model_calls": mean_value("policy_model_calls"),
            "mean_simulator_model_calls": mean_value("simulator_model_calls"),
            "mean_total_inference_calls": round(
                mean(
                    float(record["policy_model_calls"])
                    + float(record["simulator_model_calls"])
                    for record in records.values()
                ),
                8,
            ),
            "mean_total_tokens": mean_value("total_tokens"),
            "terminal_outcomes": _terminal_counts(records),
        }

    hybrid = arms["hybrid"]["records"]
    paired = {
        baseline: _paired_outcomes(arms[baseline]["records"], hybrid)
        for baseline in ARM_ORDER[:-1]
    }
    all_fail = sum(
        all(not bool(arms[arm]["records"][item]["correct"]) for arm in ARM_ORDER)
        for item in task_ids
    )
    all_succeed = sum(
        all(bool(arms[arm]["records"][item]["correct"]) for arm in ARM_ORDER)
        for item in task_ids
    )
    any_succeed = sum(
        any(bool(arms[arm]["records"][item]["correct"]) for arm in ARM_ORDER)
        for item in task_ids
    )
    aborted_ids = _aborted_run_ids(aborted_run_dir)
    paired_audit = _paired_audit(
        arms,
        seed=bootstrap_seed,
        samples=bootstrap_samples,
        noninferiority_margin=noninferiority_margin,
    )
    raw_audit = {
        arm: _load_raw_trajectory_audit(run_dir / "combined" / f"{arm}.jsonl")
        for arm in ARM_ORDER
        if (run_dir / "combined" / f"{arm}.jsonl").exists()
    }
    public_metadata = (
        _load_public_metadata(public_data, task_ids)
        if public_data is not None and public_data.exists()
        else None
    )

    output_json.parent.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    success_figure = figure_dir / "official-mini-interact-success-vs-user-turns.svg"
    terminal_figure = figure_dir / "official-mini-interact-terminal-outcomes.svg"
    result = {
        "schema_version": 3,
        "record_type": "standardized_result_read_only_audit",
        "analysis_date": "2026-08-23",
        "scope": (
            "Read-only aggregate analysis of the completed standardized local "
            "Mini-Interact Stress adaptation; no model calls or task content exported."
        ),
        "integrity": {
            "arms": len(ARM_ORDER),
            "tasks_per_arm": len(task_ids),
            "identical_paired_id_sets": True,
            "task_ids_withheld_from_output": True,
            "input_metric_sha256": {
                arm: _sha256(arms[arm]["path"]) for arm in ARM_ORDER
            },
            "input_trajectory_sha256": {
                arm: _sha256(run_dir / "combined" / f"{arm}.jsonl")
                for arm in ARM_ORDER
                if (run_dir / "combined" / f"{arm}.jsonl").exists()
            },
            "public_data_sha256": (
                _sha256(public_data)
                if public_data is not None and public_data.exists()
                else None
            ),
        },
        "arms": arm_results,
        "paired_statistical_audit": paired_audit,
        "paired_hybrid_outcomes": paired,
        "shared_outcomes": {
            "all_four_succeed": all_succeed,
            "at_least_one_arm_succeeds": any_succeed,
            "all_four_fail": all_fail,
            "hybrid_failures": len(task_ids) - arm_results["hybrid"]["successes"],
            "hybrid_failures_shared_by_all_four": all_fail,
        },
        "four_arm_success_overlap": _four_arm_overlap(arms),
        "aborted_v1_sensitivity": _sensitivity(arms, aborted_ids),
        "raw_trajectory_audit": raw_audit or None,
        "success_by_public_stratum": (
            _grouped_success(arms, public_metadata, "stratum")
            if public_metadata is not None
            else None
        ),
        "success_by_database": (
            _grouped_success(arms, public_metadata, "database")
            if public_metadata is not None
            else None
        ),
        "database_robustness": (
            _database_robustness(
                arms,
                public_metadata,
                seed=bootstrap_seed + 1000,
                samples=bootstrap_samples,
            )
            if public_metadata is not None
            else None
        ),
        "figures": {
            "success_vs_user_turns": success_figure.name,
            "terminal_outcomes": terminal_figure.name,
        },
        "interpretation_boundary": (
            "Terminal labels show where a controller stopped, not the semantic root "
            "cause of a wrong SQL query. Paired arms also contain independent "
            "stochastic model and simulator calls. Task/bootstrap intervals do "
            "not include that run-to-run variation."
        ),
    }
    output_json.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_success_figure(success_figure, result, horizon=horizon)
    _write_terminal_figure(terminal_figure, result)
    if output_markdown is not None:
        _write_audit_markdown(output_markdown, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--aborted-run-dir", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path)
    parser.add_argument("--figure-dir", type=Path, required=True)
    parser.add_argument("--public-data", type=Path, default=DEFAULT_PUBLIC_DATA)
    parser.add_argument("--bootstrap-seed", type=int, default=DEFAULT_BOOTSTRAP_SEED)
    parser.add_argument(
        "--bootstrap-samples", type=int, default=DEFAULT_BOOTSTRAP_SAMPLES
    )
    parser.add_argument(
        "--noninferiority-margin",
        type=float,
        default=DEFAULT_NONINFERIORITY_MARGIN,
    )
    args = parser.parse_args()
    result = analyze(
        args.run_dir,
        aborted_run_dir=args.aborted_run_dir,
        output_json=args.output_json,
        figure_dir=args.figure_dir,
        public_data=args.public_data,
        output_markdown=args.output_markdown,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_samples=args.bootstrap_samples,
        noninferiority_margin=args.noninferiority_margin,
    )
    print(
        "Wrote aggregate analysis for "
        f"{result['integrity']['tasks_per_arm']} paired tasks per arm."
    )


if __name__ == "__main__":
    main()
