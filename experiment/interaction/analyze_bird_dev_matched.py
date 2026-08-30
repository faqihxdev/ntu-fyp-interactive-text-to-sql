"""Analyze original-BIRD matched runs and project the 1,534-task protocol."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
from statistics import mean, median
from typing import Any

from .bird_dev_matched import ARMS, PROTOCOL_VERSION
from .run_support import sha256_file


FULL_BIRD_TASKS = 1534
BOOTSTRAP_SEED = 20260824
BOOTSTRAP_SAMPLES = 10_000
LEGACY_ARMS = ("no_user", "sph_cq", "sph_cq_es", "project_hybrid")
PROTOCOL_ARMS = {
    "bird-dev-matched-v2": LEGACY_ARMS,
    "bird-dev-matched-v3": LEGACY_ARMS,
    PROTOCOL_VERSION: ARMS,
}
SUPPORTED_PROTOCOLS = set(PROTOCOL_ARMS)


def _arms_for_protocol(protocol: str) -> tuple[str, ...]:
    try:
        return PROTOCOL_ARMS[protocol]
    except KeyError as exc:
        raise ValueError(f"Unsupported protocol {protocol!r}") from exc


def _paired_specs(protocol: str) -> tuple[tuple[str, str, str], ...]:
    if protocol == PROTOCOL_VERSION:
        return (
            ("proactive_minus_no_user", "proactive_only", "no_user"),
            ("sph_cq_minus_no_user", "sph_cq", "no_user"),
            ("proactive_minus_sph_cq", "proactive_only", "sph_cq"),
            ("hybrid_minus_no_user", "project_hybrid", "no_user"),
            ("hybrid_minus_proactive", "project_hybrid", "proactive_only"),
            ("hybrid_minus_sph_cq", "project_hybrid", "sph_cq"),
            ("sph_cq_es_minus_sph_cq", "sph_cq_es", "sph_cq"),
            ("sph_cq_es_minus_no_user", "sph_cq_es", "no_user"),
        )
    return (
        ("sph_cq_minus_no_user", "sph_cq", "no_user"),
        ("sph_cq_es_minus_no_user", "sph_cq_es", "no_user"),
        ("hybrid_minus_no_user", "project_hybrid", "no_user"),
        ("hybrid_minus_sph_cq", "project_hybrid", "sph_cq"),
        ("hybrid_minus_sph_cq_es", "project_hybrid", "sph_cq_es"),
    )


def _primary_exact_test_labels(protocol: str) -> tuple[str, ...]:
    if protocol == PROTOCOL_VERSION:
        return (
            "proactive_minus_sph_cq",
            "hybrid_minus_proactive",
            "hybrid_minus_sph_cq",
        )
    return ("hybrid_minus_sph_cq", "hybrid_minus_sph_cq_es")


def _load_records(path: Path) -> tuple[list[dict[str, Any]], str]:
    records = []
    record_protocol: str | None = None
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            protocol = str(value.get("protocol", ""))
            if protocol not in SUPPORTED_PROTOCOLS:
                raise ValueError(
                    f"Unsupported protocol {protocol!r} at line {line_number}"
                )
            if record_protocol is None:
                record_protocol = protocol
            elif protocol != record_protocol:
                raise ValueError(f"Mixed protocols at line {line_number}")
            records.append(value)
    if not records or record_protocol is None:
        raise ValueError("No task records found")
    return records, record_protocol


def _load_sensitivity_exclusions(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    exposure = payload.get("development_exposure") or {}
    values = exposure.get("union_ids")
    if values is None:
        values = payload.get("question_ids")
    if not isinstance(values, list) or not values:
        raise ValueError(f"No sensitivity exclusion IDs in {path}")
    result = {str(value) for value in values}
    if len(result) != len(values):
        raise ValueError(f"Duplicate sensitivity exclusion IDs in {path}")
    return result


def _usage_add(target: dict[str, int], source: dict[str, Any]) -> None:
    for key in target:
        target[key] += int(source.get(key, 0))


def _empty_usage() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "cache_write_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }


def _physical_for_record(record: dict[str, Any]) -> tuple[int, dict[str, int]]:
    calls = 0
    usage = _empty_usage()
    shared = record.get("shared_initial") or {}
    calls += int(shared.get("model_calls", 0))
    _usage_add(usage, shared.get("usage") or {})
    for arm in (record.get("arms") or {}).values():
        calls += int(arm.get("arm_specific_model_calls", 0))
        _usage_add(usage, arm.get("arm_specific_usage") or {})
    return calls, usage


def _effective_arm_usage(
    record: dict[str, Any], arm_name: str
) -> tuple[int, dict[str, int]]:
    arm = (record.get("arms") or {}).get(arm_name) or {}
    calls = int(arm.get("arm_specific_model_calls", 0))
    usage = _empty_usage()
    _usage_add(usage, arm.get("arm_specific_usage") or {})
    if arm.get("uses_shared_prefix"):
        shared = record.get("shared_initial") or {}
        calls += int(shared.get("model_calls", 0))
        _usage_add(usage, shared.get("usage") or {})
    return calls, usage


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bootstrap_mean_ci(
    values: list[float],
    *,
    seed: int = BOOTSTRAP_SEED,
    samples: int = BOOTSTRAP_SAMPLES,
) -> list[float]:
    generator = random.Random(seed)
    estimates = [mean(generator.choice(values) for _ in values) for _ in range(samples)]
    return [
        round(_quantile(estimates, 0.025), 6),
        round(_quantile(estimates, 0.975), 6),
    ]


def _exact_mcnemar_p_value(baseline_only: int, candidate_only: int) -> float:
    discordant = baseline_only + candidate_only
    if discordant == 0:
        return 1.0
    smaller = min(baseline_only, candidate_only)
    lower_tail = sum(math.comb(discordant, value) for value in range(smaller + 1)) / (
        2**discordant
    )
    return min(1.0, 2 * lower_tail)


def _cluster_sensitivity(
    differences_by_database: dict[str, list[float]],
    *,
    seed: int = BOOTSTRAP_SEED,
    samples: int = BOOTSTRAP_SAMPLES,
) -> dict[str, Any]:
    clusters = sorted(differences_by_database)
    if len(clusters) < 2:
        return {
            "database_clusters": len(clusters),
            "cluster_bootstrap_95_ci_percentage_points": None,
            "leave_one_database_out_range_percentage_points": None,
            "per_database": {
                cluster: {
                    "tasks": len(differences_by_database[cluster]),
                    "difference_sum": int(sum(differences_by_database[cluster])),
                    "difference_percentage_points": round(
                        100 * mean(differences_by_database[cluster]), 4
                    ),
                }
                for cluster in clusters
            },
        }
    statistics = [
        (sum(differences_by_database[cluster]), len(differences_by_database[cluster]))
        for cluster in clusters
    ]
    generator = random.Random(seed)
    estimates: list[float] = []
    for _ in range(samples):
        selected = [generator.choice(statistics) for _ in clusters]
        estimates.append(
            sum(value[0] for value in selected) / sum(value[1] for value in selected)
        )
    leave_one_out = []
    for omitted in clusters:
        retained = [
            value
            for cluster, values in differences_by_database.items()
            if cluster != omitted
            for value in values
        ]
        leave_one_out.append(mean(retained))
    return {
        "database_clusters": len(clusters),
        "cluster_bootstrap_95_ci_percentage_points": [
            round(100 * _quantile(estimates, 0.025), 4),
            round(100 * _quantile(estimates, 0.975), 4),
        ],
        "leave_one_database_out_range_percentage_points": [
            round(100 * min(leave_one_out), 4),
            round(100 * max(leave_one_out), 4),
        ],
        "per_database": {
            cluster: {
                "tasks": len(differences_by_database[cluster]),
                "difference_sum": int(sum(differences_by_database[cluster])),
                "difference_percentage_points": round(
                    100 * mean(differences_by_database[cluster]), 4
                ),
            }
            for cluster in clusters
        },
    }


def _describe(values: list[float]) -> dict[str, Any]:
    return {
        "mean": round(mean(values), 6),
        "bootstrap_mean_95_ci": _bootstrap_mean_ci(values),
        "median": round(median(values), 6),
        "p90": round(_quantile(values, 0.9), 6),
        "min": round(min(values), 6),
        "max": round(max(values), 6),
    }


def _project(description: dict[str, Any], multiplier: int) -> dict[str, Any]:
    return {
        "central": round(float(description["mean"]) * multiplier),
        "bootstrap_mean_95_ci": [
            round(float(value) * multiplier)
            for value in description["bootstrap_mean_95_ci"]
        ],
    }


def _arm_summary(records: list[dict[str, Any]], arm_name: str) -> dict[str, Any]:
    arms = [(record.get("arms") or {}).get(arm_name) or {} for record in records]
    first_official = [arm.get("official_first_success_turn") for arm in arms]
    first_sph = [arm.get("sph_compatible_first_success_turn") for arm in arms]
    official_values = [float(bool(arm.get("official_success"))) for arm in arms]
    sph_values = [float(bool(arm.get("sph_compatible_success"))) for arm in arms]
    effective = [_effective_arm_usage(record, arm_name) for record in records]
    official_turn_successes = [
        sum(value is not None and int(value) <= turn for value in first_official)
        for turn in range(5)
    ]
    sph_turn_successes = [
        sum(value is not None and int(value) <= turn for value in first_sph)
        for turn in range(5)
    ]
    normal_attempts = [int(arm.get("normal_sql_attempts", 0)) for arm in arms]
    arm_scorer_table = Counter(
        (
            bool(arm.get("official_success")),
            bool(arm.get("sph_compatible_success")),
        )
        for arm in arms
    )
    candidate_scorer_table = Counter(
        (
            bool(candidate.get("official_ex")),
            bool(candidate.get("sph_compatible_ex")),
        )
        for arm in arms
        for candidate in arm.get("candidates", [])
    )
    sra_change_counts: Counter[str] = Counter()
    answer_change_counts: Counter[str] = Counter()
    sra_calls = 0
    sra_calls_changed = 0
    simulator_answer_calls = 0
    simulator_answers_changed = 0
    for arm in arms:
        for event in arm.get("events", []):
            call_kind = str(event.get("call_kind", ""))
            if call_kind.endswith("_sra") or "_sra_" in call_kind:
                sra_calls += 1
                changes = [
                    str(value) for value in event.get("normalization_changes", [])
                ]
                sra_calls_changed += int(bool(changes))
                sra_change_counts.update(changes)
            if call_kind.startswith("user_answer_"):
                simulator_answer_calls += 1
                changes = [
                    str(value)
                    for value in event.get("answer_normalization_changes", [])
                ]
                simulator_answers_changed += int(bool(changes))
                answer_change_counts.update(changes)

    def scorer_table(table: Counter[tuple[bool, bool]]) -> dict[str, int]:
        return {
            "both_match": table[(True, True)],
            "official_only": table[(True, False)],
            "sph_only": table[(False, True)],
            "neither_matches": table[(False, False)],
        }

    return {
        "tasks": len(records),
        "official_successes": sum(bool(arm.get("official_success")) for arm in arms),
        "official_ex_percent": round(
            100 * sum(bool(arm.get("official_success")) for arm in arms) / len(records),
            4,
        ),
        "official_ex_task_bootstrap_95_ci_percent": [
            round(100 * value, 4) for value in _bootstrap_mean_ci(official_values)
        ],
        "official_ex_by_user_turn_percent": [
            round(100 * successes / len(records), 4)
            for successes in official_turn_successes
        ],
        "official_ex_by_user_turn_successes": official_turn_successes,
        "sph_compatible_successes": sum(
            bool(arm.get("sph_compatible_success")) for arm in arms
        ),
        "sph_compatible_ex_percent": round(
            100
            * sum(bool(arm.get("sph_compatible_success")) for arm in arms)
            / len(records),
            4,
        ),
        "sph_compatible_ex_task_bootstrap_95_ci_percent": [
            round(100 * value, 4) for value in _bootstrap_mean_ci(sph_values)
        ],
        "sph_compatible_ex_by_user_turn_percent": [
            round(100 * successes / len(records), 4)
            for successes in sph_turn_successes
        ],
        "sph_compatible_ex_by_user_turn_successes": sph_turn_successes,
        "total_user_turns": sum(int(arm.get("user_turns", 0)) for arm in arms),
        "mean_user_turns": round(
            mean(float(arm.get("user_turns", 0)) for arm in arms), 6
        ),
        "effective_model_calls": sum(value[0] for value in effective),
        "effective_model_calls_per_task": round(
            mean(float(value[0]) for value in effective), 6
        ),
        "effective_tokens": sum(value[1]["total_tokens"] for value in effective),
        "effective_tokens_per_task": round(
            mean(float(value[1]["total_tokens"]) for value in effective), 6
        ),
        "normal_sql_attempts": {
            "total": sum(normal_attempts),
            "mean_per_task": round(mean(normal_attempts), 6),
            "distribution": dict(
                sorted(Counter(str(value) for value in normal_attempts).items())
            ),
        },
        "terminations": dict(
            sorted(
                Counter(str(arm.get("termination", "missing")) for arm in arms).items()
            )
        ),
        "uses_shared_prefix_tasks": sum(
            bool(arm.get("uses_shared_prefix")) for arm in arms
        ),
        "arm_success_scorer_2x2": scorer_table(arm_scorer_table),
        "candidate_scorer_2x2": scorer_table(candidate_scorer_table),
        "normalization": {
            "sra_calls": sra_calls,
            "sra_calls_changed": sra_calls_changed,
            "sra_change_counts": dict(sorted(sra_change_counts.items())),
            "simulator_answer_calls": simulator_answer_calls,
            "simulator_answers_changed": simulator_answers_changed,
            "simulator_answer_change_counts": dict(
                sorted(answer_change_counts.items())
            ),
        },
        "arm_errors": sum(arm.get("termination") == "arm_error" for arm in arms),
    }


def _paired_difference(
    records: list[dict[str, Any]], candidate: str, baseline: str, *, scope: str
) -> dict[str, Any]:
    differences = []
    differences_by_database: dict[str, list[float]] = {}
    outcomes_by_database: dict[str, Counter[tuple[bool, bool]]] = {}
    baseline_only = 0
    candidate_only = 0
    both_success = 0
    both_failure = 0
    for record in records:
        arms = record.get("arms") or {}
        candidate_value = bool((arms.get(candidate) or {}).get("official_success"))
        baseline_value = bool((arms.get(baseline) or {}).get("official_success"))
        difference = float(candidate_value) - float(baseline_value)
        differences.append(difference)
        database = str((record.get("task") or {}).get("db_id", "unknown"))
        differences_by_database.setdefault(database, []).append(difference)
        outcomes_by_database.setdefault(database, Counter()).update(
            [(baseline_value, candidate_value)]
        )
        baseline_only += int(baseline_value and not candidate_value)
        candidate_only += int(candidate_value and not baseline_value)
        both_success += int(baseline_value and candidate_value)
        both_failure += int(not baseline_value and not candidate_value)
    database_sensitivity = _cluster_sensitivity(differences_by_database)
    for database, outcome_counts in outcomes_by_database.items():
        database_sensitivity["per_database"][database]["paired_outcome_2x2"] = {
            "both_success": outcome_counts[(True, True)],
            "baseline_only": outcome_counts[(True, False)],
            "candidate_only": outcome_counts[(False, True)],
            "both_failure": outcome_counts[(False, False)],
        }
    return {
        "candidate": candidate,
        "baseline": baseline,
        "difference_percentage_points": round(100 * mean(differences), 4),
        "task_bootstrap_95_ci_percentage_points": [
            round(100 * value, 4) for value in _bootstrap_mean_ci(differences)
        ],
        "discordant": {
            "baseline_only": baseline_only,
            "candidate_only": candidate_only,
        },
        "paired_outcome_2x2": {
            "both_success": both_success,
            "baseline_only": baseline_only,
            "candidate_only": candidate_only,
            "both_failure": both_failure,
        },
        "exact_mcnemar_p_value": _exact_mcnemar_p_value(
            baseline_only, candidate_only
        ),
        "database_sensitivity": database_sensitivity,
        "interpretation": (
            "final_matched_protocol_comparison"
            if scope == "full_evaluation"
            else (
                "prespecified_unexposed_sensitivity"
                if scope == "prespecified_unexposed_sensitivity"
                else f"{scope}_only_not_a_final_method_claim"
            )
        ),
    }


def _add_holm_adjustment(
    comparisons: dict[str, dict[str, Any]],
    primary_labels: tuple[str, ...],
) -> None:
    ordered = sorted(
        primary_labels,
        key=lambda label: float(comparisons[label]["exact_mcnemar_p_value"]),
    )
    running = 0.0
    family_size = len(ordered)
    for rank, label in enumerate(ordered):
        raw = float(comparisons[label]["exact_mcnemar_p_value"])
        running = max(running, min(1.0, (family_size - rank) * raw))
        comparisons[label]["holm_adjusted_p_value"] = running
    for label, comparison in comparisons.items():
        comparison["primary_exact_test_family"] = label in primary_labels
        if label not in primary_labels:
            comparison["holm_adjusted_p_value"] = None


def _task_outcomes(
    records: list[dict[str, Any]],
    analysis_arms: tuple[str, ...],
    *,
    sensitivity_exclusions: set[str],
) -> list[dict[str, Any]]:
    """Export outcome-only rows without questions, SQL, prompts, or answers."""

    result: list[dict[str, Any]] = []
    for record in records:
        task = record.get("task") or {}
        question_id = str(task.get("question_id"))
        arm_outcomes: dict[str, Any] = {}
        for arm_name in analysis_arms:
            arm = (record.get("arms") or {}).get(arm_name) or {}
            arm_outcomes[arm_name] = {
                "official_success": bool(arm.get("official_success")),
                "sph_compatible_success": bool(
                    arm.get("sph_compatible_success")
                ),
                "official_first_success_turn": arm.get(
                    "official_first_success_turn"
                ),
                "sph_compatible_first_success_turn": arm.get(
                    "sph_compatible_first_success_turn"
                ),
                "user_turns": int(arm.get("user_turns", 0)),
                "normal_sql_attempts": int(arm.get("normal_sql_attempts", 0)),
                "termination": str(arm.get("termination", "missing")),
                "uses_shared_prefix": bool(arm.get("uses_shared_prefix")),
            }
        result.append(
            {
                "question_id": int(question_id),
                "db_id": str(task.get("db_id", "unknown")),
                "included_in_unexposed_sensitivity": (
                    question_id not in sensitivity_exclusions
                ),
                "arms": arm_outcomes,
            }
        )
    return result


def _source_label(path: Path) -> str:
    project_root = Path(__file__).resolve().parents[3]
    try:
        return str(path.resolve().relative_to(project_root)).replace("\\", "/")
    except ValueError:
        return path.name


def _price_projection(
    projected: dict[str, int],
    *,
    input_price: float | None,
    cached_price: float | None,
    output_price: float | None,
    label: str | None,
) -> dict[str, Any] | None:
    if input_price is None or cached_price is None or output_price is None:
        return None
    input_tokens = projected["input_tokens"]
    cached_tokens = min(input_tokens, projected["cached_input_tokens"])
    cache_write_tokens = min(
        input_tokens - cached_tokens,
        projected.get("cache_write_input_tokens", 0),
    )
    uncached_tokens = input_tokens - cached_tokens - cache_write_tokens
    dollars = (
        uncached_tokens * input_price
        + cached_tokens * cached_price
        + cache_write_tokens * input_price * 1.25
        + projected["output_tokens"] * output_price
    ) / 1_000_000
    return {
        "label": label or "user_supplied_rate_scenario",
        "rates_usd_per_million_tokens": {
            "uncached_input": input_price,
            "cached_input": cached_price,
            "cache_write_input": input_price * 1.25,
            "output_including_reasoning": output_price,
        },
        "estimated_usd": round(dollars, 2),
        "warning": (
            "Local estimate from recorded token counts and the supplied API "
            "rates; the provider invoice remains authoritative."
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input-price", type=float)
    parser.add_argument("--cached-input-price", type=float)
    parser.add_argument("--output-price", type=float)
    parser.add_argument("--price-label")
    parser.add_argument("--sensitivity-exclusions", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    records_path = args.run_root / "task-records.jsonl"
    records, record_protocol = _load_records(records_path)
    analysis_arms = _arms_for_protocol(record_protocol)
    for record in records:
        if "task_error" in record:
            continue
        actual_arms = tuple((record.get("arms") or {}).keys())
        if set(actual_arms) != set(analysis_arms):
            raise ValueError(
                f"Record arm set does not match {record_protocol}: {actual_arms}"
            )
    if len(records) == FULL_BIRD_TASKS:
        analysis_scope = "full_evaluation"
    elif len(records) == 100:
        analysis_scope = "bounded_cost_pilot"
    else:
        analysis_scope = "development_smoke"
    task_error_count = sum("task_error" in record for record in records)
    arm_error_count = sum(
        arm.get("termination") == "arm_error"
        for record in records
        for arm in (record.get("arms") or {}).values()
    )
    analysis_valid = task_error_count == 0 and arm_error_count == 0
    paired_differences = {
        label: _paired_difference(
            records,
            candidate,
            baseline,
            scope=analysis_scope,
        )
        for label, candidate, baseline in _paired_specs(record_protocol)
    }
    _add_holm_adjustment(
        paired_differences,
        _primary_exact_test_labels(record_protocol),
    )
    sensitivity: dict[str, Any] | None = None
    sensitivity_exclusion_ids: set[str] = set()
    if args.sensitivity_exclusions is not None:
        excluded_ids = _load_sensitivity_exclusions(args.sensitivity_exclusions)
        sensitivity_exclusion_ids = excluded_ids
        observed_ids = {
            str((record.get("task") or {}).get("question_id")) for record in records
        }
        unknown_ids = sorted(excluded_ids - observed_ids)
        if unknown_ids:
            raise ValueError(
                f"Sensitivity exclusions are absent from this run: {unknown_ids[:5]}"
            )
        sensitivity_records = [
            record
            for record in records
            if str((record.get("task") or {}).get("question_id")) not in excluded_ids
        ]
        if not sensitivity_records:
            raise ValueError("Sensitivity exclusions remove every observed task")
        sensitivity_differences = {
            label: _paired_difference(
                sensitivity_records,
                candidate,
                baseline,
                scope="prespecified_unexposed_sensitivity",
            )
            for label, candidate, baseline in _paired_specs(record_protocol)
        }
        _add_holm_adjustment(
            sensitivity_differences,
            _primary_exact_test_labels(record_protocol),
        )
        sensitivity = {
            "label": "prespecified_protocol_development_id_exclusion",
            "source": str(args.sensitivity_exclusions.resolve()),
            "source_sha256": sha256_file(args.sensitivity_exclusions),
            "excluded_tasks": len(excluded_ids),
            "retained_tasks": len(sensitivity_records),
            "arm_summaries": {
                arm: _arm_summary(sensitivity_records, arm) for arm in analysis_arms
            },
            "paired_differences": sensitivity_differences,
        }
    physical = [_physical_for_record(record) for record in records]
    per_task_calls = [float(value[0]) for value in physical]
    token_keys = tuple(_empty_usage())
    descriptions = {
        "physical_model_calls_per_task": _describe(per_task_calls),
        **{
            f"physical_{key}_per_task": _describe(
                [float(value[1][key]) for value in physical]
            )
            for key in token_keys
        },
    }
    projected = {
        "logical_arm_outcomes": FULL_BIRD_TASKS * len(analysis_arms),
        "physical_model_calls": _project(
            descriptions["physical_model_calls_per_task"], FULL_BIRD_TASKS
        ),
        **{
            key: round(
                descriptions[f"physical_{key}_per_task"]["mean"] * FULL_BIRD_TASKS
            )
            for key in token_keys
        },
    }
    projected_token_cis = {
        key: _project(descriptions[f"physical_{key}_per_task"], FULL_BIRD_TASKS)[
            "bootstrap_mean_95_ci"
        ]
        for key in token_keys
    }
    point_tokens = {key: int(projected[key]) for key in token_keys}
    payload = {
        "schema_version": 2,
        "artifact": "bird-dev-matched-run-analysis",
        "protocol": record_protocol,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": {
            "analysis_scope": analysis_scope,
            "observed_tasks": len(records),
            "full_dataset_tasks": FULL_BIRD_TASKS,
            "full_dataset_observed": len(records) == FULL_BIRD_TASKS,
            "full_logical_arm_outcomes": FULL_BIRD_TASKS * len(analysis_arms),
        },
        "source": {
            "task_records": _source_label(records_path),
            "sha256": sha256_file(records_path),
        },
        "analysis_settings": {
            "paired_bootstrap_seed": BOOTSTRAP_SEED,
            "paired_bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_interval": "percentile_95_percent",
            "exact_test": "two_sided_exact_mcnemar",
            "multiplicity_adjustment": (
                "Holm across the protocol's primary exact-test family"
            ),
        },
        "privacy": {
            "included": (
                "public question IDs, database IDs, binary scorer outcomes, "
                "turn/attempt counts, termination labels, and aggregates"
            ),
            "excluded": (
                "question text, SQL, result rows, prompts, simulator answers, "
                "and trajectory artifacts"
            ),
        },
        "protocol_health": {
            "task_errors": task_error_count,
            "arm_errors": arm_error_count,
            "accuracy_estimates_valid": analysis_valid,
            "resource_projection_valid": analysis_valid,
            "invalidity_reason": (
                None
                if analysis_valid
                else "Task/arm errors make EX incomplete and omit failed-call usage."
            ),
            "scorer_disagreement_candidates": sum(
                bool(candidate.get("scorers_disagree"))
                for record in records
                for arm in (record.get("arms") or {}).values()
                for candidate in arm.get("candidates", [])
            ),
        },
        "arm_summaries": {arm: _arm_summary(records, arm) for arm in analysis_arms},
        "paired_differences": paired_differences,
        "prespecified_sensitivity": sensitivity,
        "task_outcomes": _task_outcomes(
            records,
            analysis_arms,
            sensitivity_exclusions=sensitivity_exclusion_ids,
        ),
        "observed_physical_usage": {
            "tasks": len(records),
            "model_calls": sum(value[0] for value in physical),
            **{key: sum(value[1][key] for value in physical) for key in token_keys},
        },
        "per_task_usage_distribution": descriptions,
        "full_run_projection": {
            "kind": (
                "observed_full_run"
                if len(records) == FULL_BIRD_TASKS
                else "linear_planning_projection"
            ),
            **projected,
            "token_bootstrap_mean_95_ci": projected_token_cis,
            "price": _price_projection(
                point_tokens,
                input_price=args.input_price,
                cached_price=args.cached_input_price,
                output_price=args.output_price,
                label=args.price_label,
            ),
            "projection_warning": (
                "These are the observed complete-run totals; the bootstrap "
                "interval describes task-composition variation, not projected "
                "usage."
                if len(records) == FULL_BIRD_TASKS
                else (
                    f"Linear extrapolation from the observed {len(records)}-task "
                    f"{analysis_scope.replace('_', ' ')}. It is a planning "
                    "estimate, not observed full-run usage."
                    + (
                        " Because task/arm errors occurred, this is an invalid "
                        "lower bound rather than a usable projection."
                        if not analysis_valid
                        else ""
                    )
                )
            ),
        },
        "published_context_not_a_direct_model_comparison": {
            "sphinteract_gpt4_turbo_no_hint_baseline_ex_percent": 32.07,
            "sphinteract_gpt4_turbo_no_hint_cqs_ex_percent": 58.93,
            "sphinteract_gpt4_turbo_no_hint_cqs_es_ex_percent": 53.0,
        },
    }
    output = args.output or args.run_root / "run-analysis.json"
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Analysis: {output}")
    print(
        f"Observed calls={payload['observed_physical_usage']['model_calls']:,}; "
        f"tokens={payload['observed_physical_usage']['total_tokens']:,}"
    )
    resource_label = (
        "Observed full run"
        if len(records) == FULL_BIRD_TASKS
        else f"Projected full {len(analysis_arms)}-arm run"
    )
    print(
        f"{resource_label}: "
        f"calls={projected['physical_model_calls']['central']:,} "
        f"(95% mean CI {projected['physical_model_calls']['bootstrap_mean_95_ci']}); "
        f"tokens={projected['total_tokens']:,}"
    )
    if not analysis_valid:
        print(
            "ANALYSIS INVALID FOR CLAIMS: "
            f"task_errors={task_error_count}; arm_errors={arm_error_count}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
