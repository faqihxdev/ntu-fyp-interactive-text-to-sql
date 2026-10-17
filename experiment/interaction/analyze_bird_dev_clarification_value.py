"""Audit and summarize the three-arm BIRD clarification-value experiment."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
from statistics import mean
from typing import Any

from .bird_dev_clarification_value import ARMS, PROTOCOL_VERSION
from .run_support import sha256_file
from .artifact_paths import resolve_recorded_path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SEED = 20260825
BOOTSTRAP_SAMPLES = 10_000
PILOT_MINIMUM_INITIAL_FAILURES = 30
PRIMARY_COMPARISON = (
    "reactive_minus_sql_only",
    "reactive_clarification",
    "sql_only_recovery",
)
COMPARISONS = (
    ("sql_only_minus_no_user", "sql_only_recovery", "no_user"),
    ("reactive_minus_no_user", "reactive_clarification", "no_user"),
    PRIMARY_COMPARISON,
)


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    location = (len(ordered) - 1) * probability
    lower = math.floor(location)
    upper = math.ceil(location)
    if lower == upper:
        return ordered[lower]
    fraction = location - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _bootstrap_mean_ci(values: list[float]) -> list[float]:
    generator = random.Random(BOOTSTRAP_SEED)
    estimates = [
        mean(generator.choice(values) for _ in values)
        for _ in range(BOOTSTRAP_SAMPLES)
    ]
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


def _load_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("protocol") != PROTOCOL_VERSION:
                raise ValueError(f"Protocol mismatch at line {line_number}")
            records.append(record)
    if not records:
        raise ValueError("No task records found")
    return records


def _effective_arm_usage(
    record: dict[str, Any], arm_name: str
) -> tuple[int, dict[str, int]]:
    keys = (
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    arm = record["arms"][arm_name]
    shared = record.get("shared_initial") or {}
    blocks = [arm.get("arm_specific_usage") or {}]
    calls = int(arm.get("arm_specific_model_calls", 0))
    if arm.get("uses_shared_prefix"):
        blocks.append(shared.get("usage") or {})
        calls += int(shared.get("model_calls", 0))
    usage = {key: sum(int(block.get(key, 0)) for block in blocks) for key in keys}
    return calls, usage


def _arm_summary(records: list[dict[str, Any]], arm_name: str) -> dict[str, Any]:
    arms = [record["arms"][arm_name] for record in records]
    calls_and_usage = [_effective_arm_usage(record, arm_name) for record in records]
    successes = [float(bool(arm["official_success"])) for arm in arms]
    return {
        "tasks": len(records),
        "official_successes": int(sum(successes)),
        "official_ex": round(mean(successes), 6),
        "task_bootstrap_95_ci": _bootstrap_mean_ci(successes),
        "mean_user_turns": round(mean(float(arm["user_turns"]) for arm in arms), 4),
        "mean_normal_sql_attempts": round(
            mean(float(arm["normal_sql_attempts"]) for arm in arms), 4
        ),
        "mean_effective_model_calls": round(
            mean(float(value[0]) for value in calls_and_usage), 4
        ),
        "mean_effective_total_tokens": round(
            mean(float(value[1]["total_tokens"]) for value in calls_and_usage), 2
        ),
        "terminations": dict(sorted(Counter(arm["termination"] for arm in arms).items())),
    }


def _paired_difference(
    records: list[dict[str, Any]], candidate: str, baseline: str
) -> dict[str, Any]:
    differences: list[float] = []
    candidate_only: list[int | str] = []
    baseline_only: list[int | str] = []
    both_success: list[int | str] = []
    neither_success: list[int | str] = []
    for record in records:
        question_id = record["task"]["question_id"]
        candidate_success = bool(record["arms"][candidate]["official_success"])
        baseline_success = bool(record["arms"][baseline]["official_success"])
        differences.append(float(candidate_success) - float(baseline_success))
        if candidate_success and not baseline_success:
            candidate_only.append(question_id)
        elif baseline_success and not candidate_success:
            baseline_only.append(question_id)
        elif candidate_success:
            both_success.append(question_id)
        else:
            neither_success.append(question_id)
    return {
        "candidate": candidate,
        "baseline": baseline,
        "difference_percentage_points": round(100 * mean(differences), 4),
        "task_bootstrap_95_ci_percentage_points": [
            round(100 * value, 4) for value in _bootstrap_mean_ci(differences)
        ],
        "candidate_only_successes": len(candidate_only),
        "baseline_only_successes": len(baseline_only),
        "discordant_tasks": len(candidate_only) + len(baseline_only),
        "exact_two_sided_mcnemar_p_value": _exact_mcnemar_p_value(
            len(baseline_only), len(candidate_only)
        ),
        "outcome_question_ids": {
            "candidate_only": candidate_only,
            "baseline_only": baseline_only,
            "both_success": both_success,
            "neither_success": neither_success,
        },
    }


def _candidate_prefix(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        candidate.get("sql"),
        candidate.get("status"),
        candidate.get("official_ex"),
        candidate.get("sph_compatible_ex"),
        candidate.get("fingerprints"),
    )


def _mechanical_audit(records: list[dict[str, Any]]) -> dict[str, Any]:
    violations: Counter[str] = Counter()
    task_errors = 0
    arm_errors = 0
    for record in records:
        if "task_error" in record:
            task_errors += 1
            continue
        if set(record.get("arms", {})) != set(ARMS):
            violations["arm_set"] += 1
            continue
        shared_candidates = record["shared_initial"]["candidates"]
        shared_prefix = tuple(_candidate_prefix(value) for value in shared_candidates)
        shared_success = bool(record["shared_initial"]["official_ex"])
        for arm_name in ARMS:
            arm = record["arms"][arm_name]
            if arm.get("termination") == "arm_error":
                arm_errors += 1
                continue
            if not arm.get("uses_shared_prefix"):
                violations[f"{arm_name}:shared_prefix_flag"] += 1
            arm_prefix = tuple(
                _candidate_prefix(value)
                for value in arm.get("candidates", [])[: len(shared_candidates)]
            )
            if arm_prefix != shared_prefix:
                violations[f"{arm_name}:shared_candidate_prefix"] += 1
            if shared_success and not arm.get("official_success"):
                violations[f"{arm_name}:lost_initial_success"] += 1
        no_user = record["arms"]["no_user"]
        sql_only = record["arms"]["sql_only_recovery"]
        reactive = record["arms"]["reactive_clarification"]
        if no_user.get("normal_sql_attempts") != 1 or no_user.get("user_turns") != 0:
            violations["no_user:budget"] += 1
        if sql_only.get("user_turns") != 0 or sql_only.get("question_answers"):
            violations["sql_only:user_interaction"] += 1
        if int(sql_only.get("normal_sql_attempts", 0)) > 5:
            violations["sql_only:attempt_cap"] += 1
        if int(reactive.get("normal_sql_attempts", 0)) > 5:
            violations["reactive:attempt_cap"] += 1
        if int(reactive.get("user_turns", 0)) > 4:
            violations["reactive:user_turn_cap"] += 1
        if not shared_success:
            if int(sql_only.get("normal_sql_attempts", 0)) < 2:
                violations["sql_only:no_retry_after_rejection"] += 1
            if int(reactive.get("normal_sql_attempts", 0)) < 2:
                violations["reactive:no_retry_after_rejection"] += 1
            if int(reactive.get("user_turns", 0)) < 1:
                violations["reactive:no_question_after_rejection"] += 1
    return {
        "task_errors": task_errors,
        "arm_errors": arm_errors,
        "violations": dict(sorted(violations.items())),
        "passed": task_errors == 0 and arm_errors == 0 and not violations,
    }


def _subset_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    initial_failures = [
        record
        for record in records
        if not bool(record.get("shared_initial", {}).get("official_ex"))
    ]
    return {
        "tasks": len(records),
        "mechanical_audit": _mechanical_audit(records),
        "initial_sql": {
            "successes": len(records) - len(initial_failures),
            "failures_available_for_recovery": len(initial_failures),
            "failure_question_ids": [
                record["task"]["question_id"] for record in initial_failures
            ],
        },
        "arms": {arm: _arm_summary(records, arm) for arm in ARMS},
        "paired_comparisons_all_tasks": {
            label: _paired_difference(records, candidate, baseline)
            for label, candidate, baseline in COMPARISONS
        },
        "paired_comparisons_initial_failures_only": {
            label: _paired_difference(initial_failures, candidate, baseline)
            for label, candidate, baseline in COMPARISONS
            if initial_failures
        },
        "branch_execution_order_counts": {
            " -> ".join(order): count
            for order, count in sorted(
                Counter(
                    tuple(record["branch_execution_order"]) for record in records
                ).items()
            )
        },
    }


def _development_exposure_sensitivity(
    *,
    records: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    selection_path = resolve_recorded_path(manifest["selection"]["path"], EXPERIMENT_ROOT)
    if sha256_file(selection_path) != manifest["selection"]["sha256"]:
        raise ValueError("Full selection hash does not match the run manifest")
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    exposure = selection.get("development_exposure") or {}
    exposed_ids = [int(value) for value in exposure.get("union_question_ids", [])]
    if len(exposed_ids) != 120 or len(exposed_ids) != len(set(exposed_ids)):
        raise ValueError("Full selection must declare 120 unique development IDs")
    record_ids = {int(record["task"]["question_id"]) for record in records}
    if not set(exposed_ids).issubset(record_ids):
        raise ValueError("Development IDs are not a subset of the full run")
    exposed = set(exposed_ids)
    unexposed_records = [
        record
        for record in records
        if int(record["task"]["question_id"]) not in exposed
    ]
    if len(unexposed_records) != 1414:
        raise ValueError(
            f"Expected 1,414 development-unexposed records, found {len(unexposed_records)}"
        )
    return {
        "role": (
            "Prespecified sensitivity analysis excluding the 20-task smoke and "
            "100-task pilot used during protocol development."
        ),
        "excluded_tasks": len(exposed_ids),
        "excluded_question_ids": exposed_ids,
        **_subset_summary(unexposed_records),
    }


def analyze(run_root: Path) -> dict[str, Any]:
    manifest_path = run_root / "run-manifest.json"
    records_path = run_root / "task-records.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Run manifest protocol mismatch")
    if manifest.get("task_records_sha256") != sha256_file(records_path):
        raise ValueError("Task-record hash does not match the run manifest")
    records = _load_records(records_path)
    if len(records) != int(manifest["selection"]["tasks"]):
        raise ValueError("Task-record count does not match the run manifest")

    audit = _mechanical_audit(records)
    initial_failures = [
        record
        for record in records
        if not bool(record.get("shared_initial", {}).get("official_ex"))
    ]
    comparisons = {
        label: _paired_difference(records, candidate, baseline)
        for label, candidate, baseline in COMPARISONS
    }
    recovery_subset = {
        label: _paired_difference(initial_failures, candidate, baseline)
        for label, candidate, baseline in COMPARISONS
        if initial_failures
    }
    primary = comparisons[PRIMARY_COMPARISON[0]]
    order_counts = Counter(tuple(record["branch_execution_order"]) for record in records)
    mechanically_informative = audit["passed"] and bool(initial_failures)
    tier = str(manifest.get("tier"))
    development_sensitivity = (
        _development_exposure_sensitivity(records=records, manifest=manifest)
        if tier == "full1534"
        else None
    )
    pilot_informative = (
        audit["passed"] and len(initial_failures) >= PILOT_MINIMUM_INITIAL_FAILURES
        if tier == "pilot100"
        else None
    )
    sensitivity_passed = (
        development_sensitivity is None
        or bool(development_sensitivity["mechanical_audit"]["passed"])
    )
    passed_for_tier = (
        audit["passed"]
        and sensitivity_passed
        and (tier != "pilot100" or bool(pilot_informative))
    )
    return {
        "schema_version": 1,
        "artifact": "bird-dev-clarification-value-analysis",
        "protocol": PROTOCOL_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "analysis_implementation": {
            "path": str(Path(__file__).resolve()),
            "sha256": sha256_file(Path(__file__)),
        },
        "run": {
            "root": str(run_root.resolve()),
            "tier": tier,
            "manifest_sha256": sha256_file(manifest_path),
            "task_records_sha256": sha256_file(records_path),
            "tasks": len(records),
            "physical_usage": manifest.get("physical_usage"),
            "model": manifest.get("model"),
        },
        "mechanical_audit": audit,
        "initial_sql": {
            "successes": len(records) - len(initial_failures),
            "failures_available_for_recovery": len(initial_failures),
            "failure_question_ids": [
                record["task"]["question_id"] for record in initial_failures
            ],
        },
        "arms": {arm: _arm_summary(records, arm) for arm in ARMS},
        "paired_comparisons_all_tasks": comparisons,
        "paired_comparisons_initial_failures_only": recovery_subset,
        "branch_execution_order_counts": {
            " -> ".join(order): count for order, count in sorted(order_counts.items())
        },
        "development_exposure_sensitivity": development_sensitivity,
        "informativeness": {
            "comparison_is_mechanically_informative": mechanically_informative,
            "pilot_minimum_initial_failures": PILOT_MINIMUM_INITIAL_FAILURES,
            "pilot_has_prespecified_recovery_sample": pilot_informative,
            "analysis_passed_for_tier": passed_for_tier,
            "effective_recovery_tasks": len(initial_failures),
            "primary_discordant_tasks": primary["discordant_tasks"],
            "scope": (
                "The paired design estimates the clarification bundle's added recovery "
                "over matched SQL-only retries under the same hidden rejection "
                "oracle. It does not isolate question wording from answer content."
            ),
            "interpretation": (
                "Mechanically valid: the primary effect may be estimated from "
                "the paired reactive-versus-SQL-only outcomes."
                if mechanically_informative
                else "Not mechanically informative; inspect audit failures or the lack of initial misses."
            ),
        },
    }


def _percent(value: float) -> str:
    return f"{100 * value:.1f}%"


def render_markdown(result: dict[str, Any]) -> str:
    arms = result["arms"]
    primary = result["paired_comparisons_all_tasks"][PRIMARY_COMPARISON[0]]
    pilot_gate = result["informativeness"]["pilot_has_prespecified_recovery_sample"]
    pilot_gate_text = "not applicable for this tier" if pilot_gate is None else str(pilot_gate)
    lines = [
        "# BIRD clarification-value diagnostic",
        "",
        f"**Tier:** {result['run']['tier']} ({result['run']['tasks']} tasks)",
        "",
        "| Arm | Official EX | User turns | SQL attempts | Effective calls |",
        "|---|---:|---:|---:|---:|",
    ]
    for arm in ARMS:
        value = arms[arm]
        lines.append(
            f"| {arm} | {_percent(value['official_ex'])} | "
            f"{value['mean_user_turns']:.2f} | "
            f"{value['mean_normal_sql_attempts']:.2f} | "
            f"{value['mean_effective_model_calls']:.2f} |"
        )
    lines.extend(
        [
            "",
            "## Primary contrast",
            "",
            (
                "Reactive clarification minus SQL-only recovery: "
                f"**{primary['difference_percentage_points']:+.1f} percentage "
                "points** "
                f"(95% task-bootstrap CI "
                f"{primary['task_bootstrap_95_ci_percentage_points'][0]:+.1f} to "
                f"{primary['task_bootstrap_95_ci_percentage_points'][1]:+.1f}; "
                f"discordant tasks {primary['discordant_tasks']}; exact McNemar "
                f"p={primary['exact_two_sided_mcnemar_p_value']:.4g})."
            ),
            "",
            "## Protocol health",
            "",
            f"- Mechanical audit: **{'PASS' if result['mechanical_audit']['passed'] else 'FAIL'}**.",
            (
                "- Initial failures available for recovery: "
                f"**{result['initial_sql']['failures_available_for_recovery']}**."
            ),
            (
                "- Comparison mechanically informative: "
                f"**{result['informativeness']['comparison_is_mechanically_informative']}**."
            ),
            (
                "- Prespecified pilot recovery-sample gate: "
                f"**{pilot_gate_text}**."
            ),
            "",
            result["informativeness"]["scope"],
            "",
        ]
    )
    sensitivity = result.get("development_exposure_sensitivity")
    if sensitivity:
        sensitivity_primary = sensitivity["paired_comparisons_all_tasks"][
            PRIMARY_COMPARISON[0]
        ]
        lines.extend(
            [
                "## Development-unexposed sensitivity",
                "",
                (
                    f"After excluding {sensitivity['excluded_tasks']} smoke/pilot "
                    f"tasks, {sensitivity['tasks']} tasks remain. Reactive minus "
                    f"SQL-only recovery: "
                    f"**{sensitivity_primary['difference_percentage_points']:+.1f} "
                    "percentage points**."
                ),
                "",
            ]
        )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = analyze(args.run_root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.markdown_output:
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(render_markdown(result), encoding="utf-8")
    print(
        f"Wrote {args.output}; audit={result['mechanical_audit']['passed']}; "
        "reactive-minus-SQL-only="
        f"{result['paired_comparisons_all_tasks'][PRIMARY_COMPARISON[0]]['difference_percentage_points']:+.1f} pp"
    )
    return 0 if result["informativeness"]["analysis_passed_for_tier"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
