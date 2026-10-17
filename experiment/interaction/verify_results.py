"""Recalculate the three studies from bundled evidence, without model calls."""

from __future__ import annotations

import argparse
from collections import Counter
import gzip
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from . import analyze_standardized_results as mini_analysis
from .analyze_bird_dev_clarification_value import _mechanical_audit
from .analyze_bird_dev_clarification_value_public import analyze_projection
from .export_bird_dev_clarification_value_public import build_public_projection
from .result_evidence import (
    EXPERIMENT,
    MINI_RESULT,
    PRIMARY_ANALYSIS,
    PRIMARY_OUTCOMES,
    PRIMARY_RESULT,
    SUPPORTING_ANALYSIS,
    SUPPORTING_RESULT,
    USAGE_FIELDS,
    load_json,
    rows_from_bytes,
    sha256,
)


def check(actual: Any, expected: Any, label: str) -> None:
    """Fail with a field path, without echoing record contents."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError(f"Field mismatch: {label}")
        for key in expected:
            check(actual[key], expected[key], f"{label}.{key}")
    elif isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            raise ValueError(f"Length mismatch: {label}")
        for i, (left, right) in enumerate(zip(actual, expected, strict=True)):
            check(left, right, f"{label}[{i}]")
    elif isinstance(expected, float) and type(actual) in (int, float):
        tolerance = 1e-8 if label.endswith("estimated_cost_usd") else 0.0
        if not math.isclose(actual, expected, rel_tol=1e-10, abs_tol=tolerance):
            raise ValueError(f"Value mismatch: {label}")
    elif actual != expected or (
        isinstance(expected, bool) and type(actual) is not bool
    ):
        raise ValueError(f"Value mismatch: {label}")


def read_evidence(root: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    manifest = load_json(root / "evidence.json")
    check(manifest["schema_version"], 1, "evidence.schema_version")
    expected = {
        "bird-primary.jsonl.gz",
        "bird-supporting.jsonl.gz",
        "mini-events.jsonl.gz",
    }
    expected.update(f"mini-{arm}.metrics.json" for arm in mini_analysis.ARM_ORDER)
    if set(manifest["files"]) != expected:
        raise ValueError("Evidence file set mismatch")
    contents = {}
    for name, entry in manifest["files"].items():
        raw = (root / name).read_bytes()
        check(len(raw), entry["bytes"], f"{name}.bytes")
        check(sha256(raw), entry["sha256"], f"{name}.sha256")
        content = gzip.decompress(raw) if name.endswith(".gz") else raw
        check(sha256(content), entry["content_sha256"], f"{name}.content_sha256")
        contents[name] = content
    return manifest, contents


def sum_usage(events: list[dict[str, Any]]) -> dict[str, int | float]:
    return {
        key: sum((event.get("usage") or {}).get(key, 0) for event in events)
        for key in USAGE_FIELDS
    }


def check_prices(events: list[dict[str, Any]], rates: dict[str, float]) -> None:
    for event in events:
        usage = event.get("usage")
        if not usage:
            continue
        total = usage["input_tokens"]
        cached = min(total, usage.get("cached_input_tokens", 0))
        written = min(total - cached, usage.get("cache_write_input_tokens", 0))
        cost = (
            (total - cached - written) * rates["ordinary_input"]
            + cached * rates["cache_read"]
            + written * rates["cache_write"]
            + usage["output_tokens"] * rates["output"]
        ) / 1_000_000
        check(cost, usage["estimated_cost_usd"], "usage.estimated_cost_usd")


def verify_usage(
    bird: dict[str, list[dict[str, Any]]],
    mini_rows: list[dict[str, Any]],
    expected: dict[str, Any],
) -> None:
    methods = {(row["study"], row["method"]): row for row in expected["methods"]}
    for name, rows in bird.items():
        study = "Primary BIRD" if name == "primary" else "Supporting BIRD"
        physical = []
        for row in rows:
            physical.extend(row["shared_initial"]["events"])
            for arm in row["arms"].values():
                physical.extend(arm["events"])
        physical = [e for e in physical if e["kind"] == "model_call"]
        check_prices(physical, expected["pricing_usd_per_million"])
        counts = {
            "recorded_calls": sum(e.get("transport_attempts", 1) for e in physical),
            "completed_calls": len(physical),
            **sum_usage(physical),
        }
        check(counts, expected["physical_retained"][name], f"usage.{name}.physical")
        for arm_name in rows[0]["arms"]:
            arms = [row["arms"][arm_name] for row in rows]
            events = []
            for row, arm in zip(rows, arms, strict=True):
                events.extend(arm["events"])
                if arm["uses_shared_prefix"]:
                    events.extend(row["shared_initial"]["events"])
            events = [e for e in events if e["kind"] == "model_call"]
            counts = {
                "tasks": len(rows),
                "successes": sum(a["official_success"] for a in arms),
                "questions": sum(a["user_turns"] for a in arms),
                "sql_attempts": sum(a["normal_sql_attempts"] for a in arms),
                "recorded_calls": sum(e.get("transport_attempts", 1) for e in events),
                "completed_calls": len(events),
                "policy_completed_calls": sum(e["role"] == "policy" for e in events),
                "simulator_completed_calls": sum(
                    e["role"] == "simulator" for e in events
                ),
                "terminations": dict(Counter(a["termination"] for a in arms)),
                **sum_usage(events),
            }
            check(
                counts,
                {k: methods[study, arm_name][k] for k in counts},
                f"usage.{name}.{arm_name}",
            )
    for arm in mini_analysis.ARM_ORDER:
        rows = [r for r in mini_rows if r["arm"] == arm]
        events = [e for r in rows for e in r["events"]]
        check_prices(events, expected["pricing_usd_per_million"])
        policy_calls = sum(
            e.get("calls", 0) for e in events if e["kind"] == "policy_decision"
        )
        simulator_calls = sum(
            e.get("calls", 0) for e in events if e["kind"] == "user_clarification"
        )
        counts = {
            "tasks": len(rows),
            "successes": sum(r["termination"] == "accepted_submission" for r in rows),
            "questions": sum(e["kind"] == "user_clarification" for e in events),
            "sql_attempts": sum(e.get("submission_kind") == "normal" for e in events),
            "recorded_calls": policy_calls + simulator_calls,
            "policy_completed_calls": policy_calls,
            "simulator_completed_calls": simulator_calls,
            "terminations": dict(Counter(r["termination"] for r in rows)),
            **sum_usage(events),
        }
        check(
            counts,
            {k: methods["Mini-Interact", arm][k] for k in counts},
            f"usage.mini.{arm}",
        )


def verify_mini_events(rows: list[dict[str, Any]], expected: dict[str, Any]) -> None:
    """Check event-count arithmetic; text-derived flags remain supplied evidence."""
    events = [e for r in rows for e in r["events"]]
    sra = [e["sra"] for e in events if "sra" in e]
    normalizations = [e for e in events if e["kind"] == "sra_normalization"]
    changes = Counter(x for e in normalizations for x in e["changes"])
    errors = Counter(
        e["category"] for e in events if e["kind"] == "model_protocol_error"
    )
    n = len(sra)
    check(
        {
            "raw_decisions": n,
            "raw_compliant": sum(x["raw_valid"] for x in sra),
            "raw_compliance_rate": round(sum(x["raw_valid"] for x in sra) / n, 8)
            if n
            else None,
            "post_normalization_compliant": sum(x["normalized_valid"] for x in sra),
            "post_normalization_compliance_rate": round(
                sum(x["normalized_valid"] for x in sra) / n, 8
            )
            if n
            else None,
            "normalization_events": len(normalizations),
            "normalization_change_counts": dict(changes),
            "user_answers": sum(e["kind"] == "user_clarification" for e in events),
        },
        expected["sra"],
        "mini.events.sra",
    )
    check(
        dict(errors),
        expected["protocol_error_categories"],
        "mini.events.protocol_errors",
    )
    check(len(rows), expected["trajectories"], "mini.events.trajectories")
    check(
        sum(r["termination"] == "accepted_submission" for r in rows),
        expected["successes"],
        "mini.events.successes",
    )
    for activated, label in ((True, "activated"), (False, "not_activated")):
        group = [
            r
            for r in rows
            if any(e["kind"] == "sra_normalization" for e in r["events"]) == activated
        ]
        successes = sum(r["termination"] == "accepted_submission" for r in group)
        check(
            {
                "trajectories": len(group),
                "successes": successes,
                "success_rate": round(successes / len(group), 8) if group else None,
            },
            expected["normalizer_outcome_strata"][label],
            f"mini.events.{label}",
        )
    repeats = expected["rejected_sql_repeat_observation"]
    check(
        sum(e.get("repeats_rejected_sql", False) for e in events),
        repeats["exact_repeat_events"],
        "mini.events.repeats",
    )
    check(
        sum(
            any(e.get("repeats_rejected_sql", False) for e in r["events"]) for r in rows
        ),
        repeats["trajectories_with_exact_repeat"],
        "mini.events.repeated_trajectories",
    )
    statuses = Counter()
    for row in rows:
        if row["termination"] == "forced_submission_rejected":
            states = [
                e["status"] for e in row["events"] if e["kind"] == "sql_execution"
            ]
            statuses[states[-1] if states else "no_execution"] += 1
    endpoint = expected["forced_rejected_endpoint_execution"]
    check(dict(statuses), endpoint["status_counts"], "mini.events.endpoint_status")
    check(
        sum(statuses.values()), endpoint["trajectories"], "mini.events.endpoint_count"
    )
    check(statuses["ok"], endpoint["executable"], "mini.events.endpoint_executable")


def verify_mini_record(row: dict[str, Any], metrics: dict[str, Any]) -> None:
    events = row["events"]
    check([e["sequence"] for e in events], list(range(len(events))), "mini.event_order")
    official = next(
        r
        for r in metrics["official_protocol"]["records"]
        if str(r["question_id"]) == row["task_id"]
    )
    secondary = next(
        r
        for r in metrics["secondary_policy_analysis"]["records"]
        if str(r["question_id"]) == row["task_id"]
    )
    questions = [e for e in events if e["kind"] == "user_clarification"]
    submissions = [e for e in events if e["kind"] == "submission_judgement"]
    accepted = [e for e in submissions if e["accepted"]]
    charges = [e for e in events if e["kind"] == "bird_coin_charge"]
    usage = sum_usage(events)
    counts = {
        "success": bool(accepted),
        "user_questions": len(questions),
        "submissions": len(submissions),
        "normal_submissions": sum(
            e["submission_kind"] == "normal" for e in submissions
        ),
        "invalid_sql_repairs": sum(
            e["submission_kind"] == "invalid_sql_repair" for e in submissions
        ),
        "submissions_before_first_question": sum(
            e["sequence"] < questions[0]["sequence"] for e in submissions
        )
        if questions
        else len(submissions),
        "sra_no_ambiguity": sum(e["kind"] == "sra_no_ambiguity" for e in events),
        "policy_model_calls": sum(e["kind"] == "policy_decision" for e in events),
        "simulator_model_calls": sum(e["calls"] for e in questions),
        "actions": len(charges),
        "bird_coins": round(sum(e["cost"] for e in charges), 3),
        "total_tokens": usage["total_tokens"],
        "termination": row["termination"],
    }
    check(counts, {k: official[k] for k in counts}, "mini.task_metrics")
    check(
        accepted[0]["turn"] if accepted else None,
        secondary["success_turn"],
        "mini.success_turn",
    )
    for field in ("input_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
        check(usage[field], secondary[field], f"mini.task_usage.{field}")


def verify(results: Path, manifests: Path, output: Path) -> dict[str, Any]:
    evidence, contents = read_evidence(results)
    bird = {
        name: rows_from_bytes(contents[f"bird-{name}.jsonl.gz"])
        for name in ("primary", "supporting")
    }
    for name, result_name in (
        ("primary", PRIMARY_RESULT),
        ("supporting", SUPPORTING_RESULT),
    ):
        result = load_json(manifests / result_name)
        check(
            sha256(contents[f"bird-{name}.jsonl.gz"]),
            result["hashes"]["task_records_sha256"],
            f"{name}.retained_hash",
        )
        check(len(bird[name]), 1534, f"{name}.tasks")
        check(
            len({r["task"]["question_id"] for r in bird[name]}),
            1534,
            f"{name}.unique_tasks",
        )
    output.mkdir(parents=True, exist_ok=True)
    projection = load_json(manifests / PRIMARY_OUTCOMES)
    check(
        sha256((manifests / PRIMARY_OUTCOMES).read_bytes()),
        load_json(manifests / PRIMARY_RESULT)["hashes"]["public_outcomes_sha256"],
        "primary.published_projection_hash",
    )
    regenerated = build_public_projection(
        records=bird["primary"],
        protocol_development_ids={
            r["question_id"]
            for r in projection["rows"]
            if r["clarification_value_smoke_or_pilot"]
        },
        source=projection["source"],
    )
    check(regenerated, projection, "primary.projection")
    check(
        _mechanical_audit(bird["primary"])["passed"], True, "primary.mechanical_audit"
    )
    print("Checking primary BIRD statistics...", flush=True)
    primary = analyze_projection(regenerated)
    primary["projection_sha256"] = sha256((manifests / PRIMARY_OUTCOMES).read_bytes())
    check(primary, load_json(manifests / PRIMARY_ANALYSIS), "primary.analysis")
    (output / "primary-analysis.json").write_text(
        json.dumps(primary, indent=2) + "\n", encoding="utf-8"
    )
    print("PASS primary BIRD: 1,534 tasks", flush=True)

    with tempfile.TemporaryDirectory(prefix="fyp-evidence-") as directory:
        scratch = Path(directory)
        (scratch / "task-records.jsonl").write_bytes(
            contents["bird-supporting.jsonl.gz"]
        )
        print("Checking supporting BIRD statistics...", flush=True)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "interaction.analyze_bird_dev_matched",
                "--run-root",
                str(scratch),
                "--output",
                str(output / "supporting-analysis.json"),
                "--sensitivity-exclusions",
                str(manifests / "bird-dev-matched-v4-full1534.json"),
            ],
            check=True,
            cwd=EXPERIMENT,
        )
        supporting = load_json(output / "supporting-analysis.json")
        frozen = load_json(manifests / SUPPORTING_ANALYSIS)
        check(
            sha256((manifests / SUPPORTING_ANALYSIS).read_bytes()),
            load_json(manifests / SUPPORTING_RESULT)["hashes"][
                "public_analysis_sha256"
            ],
            "supporting.published_analysis_hash",
        )
        for key in (
            "scope",
            "protocol_health",
            "arm_summaries",
            "paired_differences",
            "task_outcomes",
            "observed_physical_usage",
            "per_task_usage_distribution",
        ):
            check(supporting[key], frozen[key], f"supporting.{key}")
        for key in frozen["prespecified_sensitivity"]:
            if key != "source":
                check(
                    supporting["prespecified_sensitivity"][key],
                    frozen["prespecified_sensitivity"][key],
                    f"supporting.exclusions.{key}",
                )
        print("PASS supporting BIRD: 1,534 tasks", flush=True)

        frozen_mini = load_json(manifests / MINI_RESULT)
        combined = scratch / "combined"
        combined.mkdir()
        mini_rows = rows_from_bytes(contents["mini-events.jsonl.gz"])
        check(len(mini_rows), 960, "mini.event_records")
        metadata = {}
        for row in mini_rows:
            entry = {"database": row["database"], "stratum": row["stratum"]}
            if row["task_id"] in metadata:
                check(entry, metadata[row["task_id"]], "mini.metadata")
            metadata[row["task_id"]] = entry
        for arm in mini_analysis.ARM_ORDER:
            raw = contents[f"mini-{arm}.metrics.json"]
            check(
                sha256(raw),
                frozen_mini["integrity"]["input_metric_sha256"][arm],
                f"mini.{arm}.metrics_hash",
            )
            (combined / f"{arm}.metrics.json").write_bytes(raw)
            metrics = json.loads(raw)
            rows = [r for r in mini_rows if r["arm"] == arm]
            check(len(rows), 240, f"mini.{arm}.tasks")
            check(len({r["task_id"] for r in rows}), 240, f"mini.{arm}.unique_tasks")
            check(
                sorted(r["task_id"] for r in rows),
                sorted(
                    str(r["question_id"])
                    for r in metrics["official_protocol"]["records"]
                ),
                f"mini.{arm}.paired_ids",
            )
            for row in rows:
                verify_mini_record(row, metrics)
            verify_mini_events(rows, frozen_mini["raw_trajectory_audit"][arm])
        check(
            evidence["mini_source_trajectory_sha256"],
            frozen_mini["integrity"]["input_trajectory_sha256"],
            "mini.source_hashes",
        )
        check(
            evidence["mini_public_data_sha256"],
            frozen_mini["integrity"]["public_data_sha256"],
            "mini.public_data_hash",
        )
        print("Checking Mini-Interact statistics...", flush=True)
        mini = mini_analysis.analyze(
            scratch,
            aborted_run_dir=None,
            output_json=output / "mini-analysis.json",
            figure_dir=output / "figures",
            public_data=None,
        )
        for key in (
            "arms",
            "paired_statistical_audit",
            "paired_hybrid_outcomes",
            "shared_outcomes",
            "four_arm_success_overlap",
            "aborted_v1_sensitivity",
        ):
            check(mini[key], frozen_mini[key], f"mini.{key}")
        arms = {
            arm: mini_analysis._load_arm(combined / f"{arm}.metrics.json")
            for arm in mini_analysis.ARM_ORDER
        }
        for key, group in (
            ("success_by_public_stratum", "stratum"),
            ("success_by_database", "database"),
        ):
            mini[key] = mini_analysis._grouped_success(arms, metadata, group)
            check(mini[key], frozen_mini[key], f"mini.{key}")
        mini["database_robustness"] = mini_analysis._database_robustness(
            arms,
            metadata,
            seed=mini_analysis.DEFAULT_BOOTSTRAP_SEED + 1000,
            samples=mini_analysis.DEFAULT_BOOTSTRAP_SAMPLES,
        )
        check(
            mini["database_robustness"],
            frozen_mini["database_robustness"],
            "mini.database_robustness",
        )
        (output / "mini-analysis.json").write_text(
            json.dumps(mini, indent=2) + "\n", encoding="utf-8"
        )
        print("PASS Mini-Interact: 240 paired tasks, four controllers", flush=True)
    verify_usage(bird, mini_rows, load_json(manifests / "usage-summary.json"))
    print("PASS calls, tokens, cache counts and estimated costs", flush=True)
    result = {
        "passed": True,
        "studies": 3,
        "model_calls": 0,
        "scope": evidence["verification_scope"],
    }
    (output / "verification.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=EXPERIMENT / "results")
    parser.add_argument("--output", type=Path, default=EXPERIMENT / "runs/verification")
    args = parser.parse_args()
    try:
        verify(args.results.resolve(), EXPERIMENT / "manifests", args.output.resolve())
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        print(f"VERIFICATION FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"Verified all three studies. Reports: {args.output}")
    print("Recorded outcomes were checked; SQL correctness was not rescored.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
