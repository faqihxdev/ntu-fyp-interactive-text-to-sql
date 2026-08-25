"""Balanced, resumable execution and paired analysis for the official protocol.

This module owns the comparison seam: deterministic sharding, isolated child
runs, exact-ID merging, and paired task-success/BIRD-coin analysis. The
single-policy runner remains the only module that executes a trajectory.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from .compare_policy_metrics import per_task_auc, percentile
from .official_protocol import POLICIES, PROTOCOL_VERSION
from .run_support import sha256_file
from .trajectory import Trajectory, load_trajectories, stable_json

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
COMPARISON_VERSION = "official-mini-stress-comparison-v2"
COMPARISON_POLICIES = (
    "mini_react_port",
    "sphinteract_cq",
    "proactive",
    "hybrid",
)
DEFAULT_FREEZE = (
    EXPERIMENT_ROOT
    / "manifests"
    / "official-mini-interact-stress-comparison-v2-freeze-2026-08-22.json"
)


@dataclass(frozen=True)
class ComparisonJob:
    shard_index: int
    policy: str
    selection_manifest: Path
    output: Path
    log: Path


def load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def load_selection_ids(path: Path) -> list[str]:
    payload = load_json_object(path)
    ids = [str(value) for value in payload.get("instance_ids", [])]
    if not ids:
        raise ValueError(f"Selection is empty: {path}")
    if len(ids) != len(set(ids)):
        raise ValueError(f"Selection contains duplicate IDs: {path}")
    return ids


def balanced_shards(
    ids: Sequence[str], shard_count: int
) -> tuple[tuple[str, ...], ...]:
    """Round-robin IDs so every shard spans the complete frozen ordering."""

    if shard_count < 1:
        raise ValueError("shard_count must be positive")
    if shard_count > len(ids):
        raise ValueError("shard_count cannot exceed the number of IDs")
    shards = tuple(tuple(ids[index::shard_count]) for index in range(shard_count))
    flattened = [value for shard in shards for value in shard]
    if len(flattened) != len(ids) or set(flattened) != set(ids):
        raise AssertionError("Balanced sharding lost or duplicated an ID")
    return shards


def balanced_launch_order(policies: Sequence[str], shard_index: int) -> tuple[str, ...]:
    """Rotate launch order so no arm is always started first."""

    if not policies:
        raise ValueError("At least one policy is required")
    offset = shard_index % len(policies)
    return tuple(policies[offset:]) + tuple(policies[:offset])


def _resolve_workspace_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else EXPERIMENT_ROOT.parent.parent / path


def validate_freeze(freeze_path: Path) -> dict[str, Any]:
    freeze = load_json_object(freeze_path)
    if freeze.get("status") != "frozen_before_model_calls":
        raise ValueError("Comparison manifest is not frozen before model calls")
    if not freeze.get("approval", {}).get("explicit_large_run_approval"):
        raise ValueError("Comparison manifest lacks explicit large-run approval")
    if freeze.get("comparison_version") != COMPARISON_VERSION:
        raise ValueError("Comparison version mismatch")
    if tuple(freeze.get("arms", [])) != COMPARISON_POLICIES:
        raise ValueError("Frozen arm order does not match the implementation")
    if set(COMPARISON_POLICIES) != POLICIES:
        raise ValueError("The protocol policy registry changed after freeze")

    selection = _resolve_workspace_path(freeze["selection"]["path"])
    if sha256_file(selection) != freeze["selection"]["sha256"]:
        raise ValueError("Reserve selection hash changed after freeze")
    ids = load_selection_ids(selection)
    if len(ids) != int(freeze["selection"]["tasks"]):
        raise ValueError("Reserve selection count changed after freeze")

    for relative_path, expected in freeze["implementation_hashes"].items():
        path = _resolve_workspace_path(relative_path)
        if sha256_file(path) != expected:
            raise ValueError(f"Implementation hash changed after freeze: {path}")
    return freeze


def write_runtime_shard_manifests(
    output_root: Path,
    *,
    reserve_ids: Sequence[str],
    shard_count: int,
    reserve_sha256: str,
) -> list[Path]:
    state_root = output_root / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    manifests: list[Path] = []
    for index, ids in enumerate(balanced_shards(reserve_ids, shard_count)):
        path = state_root / f"shard-{index:02d}.json"
        payload = {
            "schema_version": 1,
            "comparison_version": COMPARISON_VERSION,
            "method": "round_robin_reserve_order",
            "shard_index": index,
            "shard_count": shard_count,
            "parent_reserve_sha256": reserve_sha256,
            "instance_ids": list(ids),
        }
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        manifests.append(path)
    return manifests


def build_jobs(
    output_root: Path,
    shard_manifests: Sequence[Path],
) -> list[ComparisonJob]:
    jobs: list[ComparisonJob] = []
    for shard_index, selection in enumerate(shard_manifests):
        shard_root = output_root / "shards" / f"shard-{shard_index:02d}"
        for policy in balanced_launch_order(COMPARISON_POLICIES, shard_index):
            jobs.append(
                ComparisonJob(
                    shard_index=shard_index,
                    policy=policy,
                    selection_manifest=selection,
                    output=shard_root / f"{policy}.jsonl",
                    log=output_root / "logs" / f"shard-{shard_index:02d}-{policy}.log",
                )
            )
    return jobs


def child_command(job: ComparisonJob, freeze: dict[str, Any]) -> list[str]:
    shared = freeze["shared_stack"]
    return [
        sys.executable,
        "-m",
        "interaction.run_official_mini_interact",
        "--selection-manifest",
        str(job.selection_manifest.resolve()),
        "--confirm-large-run",
        "--policy",
        job.policy,
        "--model",
        str(shared["model"]),
        "--reasoning-effort",
        str(shared["reasoning_effort"]),
        "--user-patience-budget",
        str(shared["user_patience_budget"]),
        "--max-action-turns",
        str(shared["max_action_turns"]),
        "--metric-horizon",
        str(shared["metric_horizon"]),
        "--output",
        str(job.output.resolve()),
    ]


def count_trajectory_lines(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as handle:
        return sum(bool(line.strip()) for line in handle)


def run_jobs(
    jobs: Sequence[ComparisonJob],
    freeze: dict[str, Any],
    *,
    poll_seconds: float = 30.0,
) -> None:
    processes: list[tuple[ComparisonJob, subprocess.Popen[str], Any]] = []
    try:
        for job in jobs:
            job.output.parent.mkdir(parents=True, exist_ok=True)
            job.log.parent.mkdir(parents=True, exist_ok=True)
            log_handle = job.log.open("a", encoding="utf-8", newline="\n")
            process = subprocess.Popen(
                child_command(job, freeze),
                cwd=EXPERIMENT_ROOT,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
            processes.append((job, process, log_handle))

        expected = sum(len(load_selection_ids(job.selection_manifest)) for job in jobs)
        while any(process.poll() is None for _, process, _ in processes):
            complete = sum(
                count_trajectory_lines(job.output) for job, _, _ in processes
            )
            active = sum(process.poll() is None for _, process, _ in processes)
            print(
                f"[{datetime.now(timezone.utc).isoformat()}] "
                f"completed={complete}/{expected}; active_children={active}",
                flush=True,
            )
            time.sleep(max(1.0, poll_seconds))
    except BaseException:
        for _, process, _ in processes:
            if process.poll() is None:
                process.terminate()
        raise
    finally:
        for _, process, handle in processes:
            if process.poll() is None:
                process.wait()
            handle.close()

    failures = [
        {
            "policy": job.policy,
            "shard": job.shard_index,
            "exit_code": process.returncode,
        }
        for job, process, _ in processes
        if process.returncode != 0
    ]
    if failures:
        raise RuntimeError(f"One or more resumable child runs failed: {failures}")


def merge_policy_outputs(
    *,
    policy: str,
    shard_outputs: Sequence[Path],
    expected_ids: Sequence[str],
    destination: Path,
) -> list[Trajectory]:
    by_id: dict[str, Trajectory] = {}
    for path in shard_outputs:
        for trajectory in load_trajectories(path):
            question_id = str(trajectory.task.question_id)
            if question_id in by_id:
                raise ValueError(f"Duplicate {policy} trajectory for {question_id}")
            if trajectory.policy != f"{PROTOCOL_VERSION}:{policy}":
                raise ValueError(f"Policy mismatch in {path}: {trajectory.policy}")
            by_id[question_id] = trajectory
    expected = [str(value) for value in expected_ids]
    missing = [value for value in expected if value not in by_id]
    unexpected = sorted(set(by_id) - set(expected))
    if missing or unexpected:
        raise ValueError(
            f"Incomplete {policy} merge: missing={missing[:10]}, "
            f"unexpected={unexpected[:10]}"
        )
    ordered = [by_id[value] for value in expected]
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(
        "".join(stable_json(trajectory.to_dict()) + "\n" for trajectory in ordered),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(destination)
    return ordered


def _metrics_sections(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    payload = load_json_object(path)
    official = payload.get("official_protocol")
    secondary = payload.get("secondary_policy_analysis")
    if not isinstance(official, dict) or not isinstance(secondary, dict):
        raise TypeError(f"Not standardized official metrics: {path}")
    return official, secondary


def _record_map(section: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records = {str(record["question_id"]): record for record in section["records"]}
    if len(records) != len(section["records"]):
        raise ValueError("Metric section contains duplicate question IDs")
    return records


def paired_bootstrap(
    ids: Sequence[str],
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    value: Callable[[dict[str, Any]], float],
    *,
    seed: int,
    samples: int,
) -> dict[str, float]:
    differences = [value(candidate[key]) - value(baseline[key]) for key in ids]
    rng = random.Random(seed)
    bootstrap = [
        mean(differences[rng.randrange(len(differences))] for _ in differences)
        for _ in range(samples)
    ]
    return {
        "baseline_mean": round(mean(value(baseline[key]) for key in ids), 8),
        "candidate_mean": round(mean(value(candidate[key]) for key in ids), 8),
        "paired_difference": round(mean(differences), 8),
        "paired_bootstrap_95_ci_low": round(percentile(bootstrap, 0.025), 8),
        "paired_bootstrap_95_ci_high": round(percentile(bootstrap, 0.975), 8),
    }


def exact_mcnemar_two_sided(baseline_only: int, candidate_only: int) -> float:
    discordant = baseline_only + candidate_only
    if discordant == 0:
        return 1.0
    tail = min(baseline_only, candidate_only)
    cumulative = sum(math.comb(discordant, value) for value in range(tail + 1))
    return min(1.0, 2 * cumulative / (2**discordant))


def holm_adjust(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return Holm-adjusted p-values while preserving the input keys."""

    if any(not 0.0 <= float(value) <= 1.0 for value in p_values.values()):
        raise ValueError("Holm adjustment requires p-values in [0, 1]")
    ordered = sorted(p_values.items(), key=lambda item: (float(item[1]), item[0]))
    adjusted: dict[str, float] = {}
    running_max = 0.0
    total = len(ordered)
    for rank, (name, value) in enumerate(ordered):
        candidate = min(1.0, (total - rank) * float(value))
        running_max = max(running_max, candidate)
        adjusted[name] = round(running_max, 8)
    return {name: adjusted[name] for name in p_values}


def analyze_comparison(
    metrics_by_policy: dict[str, Path],
    *,
    expected_ids: Sequence[str],
    seed: int,
    bootstrap_samples: int,
    noninferiority_margin: float,
) -> dict[str, Any]:
    if set(metrics_by_policy) != set(COMPARISON_POLICIES):
        raise ValueError("Analysis requires exactly the four frozen policies")
    ids = [str(value) for value in expected_ids]
    official_maps: dict[str, dict[str, dict[str, Any]]] = {}
    secondary_maps: dict[str, dict[str, dict[str, Any]]] = {}
    policy_summaries: dict[str, Any] = {}
    for policy, path in metrics_by_policy.items():
        official, secondary = _metrics_sections(path)
        official_map = _record_map(official)
        secondary_map = _record_map(secondary)
        if set(official_map) != set(ids) or set(secondary_map) != set(ids):
            raise ValueError(f"{policy} metrics do not cover the frozen IDs")
        official_maps[policy] = official_map
        secondary_maps[policy] = secondary_map
        policy_summaries[policy] = {
            "successes": sum(bool(official_map[key]["success"]) for key in ids),
            "success_rate": round(
                mean(float(bool(official_map[key]["success"])) for key in ids), 8
            ),
            "mean_bird_coins": round(
                mean(float(official_map[key]["bird_coins"]) for key in ids), 8
            ),
            "mean_user_questions": round(
                mean(float(official_map[key]["user_questions"]) for key in ids), 8
            ),
            "mean_submissions": round(
                mean(float(official_map[key]["submissions"]) for key in ids), 8
            ),
            "mean_total_tokens": round(
                mean(float(official_map[key]["total_tokens"]) for key in ids), 8
            ),
            "system_error_terminations": sum(
                str(official_map[key]["termination"])
                in {"model_error", "oracle_error", "model_protocol_error"}
                for key in ids
            ),
        }

    comparisons: dict[str, Any] = {}
    comparators = ("mini_react_port", "sphinteract_cq", "proactive")
    for comparison_index, baseline_policy in enumerate(comparators):
        baseline = official_maps[baseline_policy]
        candidate = official_maps["hybrid"]
        baseline_secondary = secondary_maps[baseline_policy]
        candidate_secondary = secondary_maps["hybrid"]
        success = paired_bootstrap(
            ids,
            baseline,
            candidate,
            lambda record: float(bool(record["success"])),
            seed=seed + comparison_index * 20,
            samples=bootstrap_samples,
        )
        coins = paired_bootstrap(
            ids,
            baseline,
            candidate,
            lambda record: float(record["bird_coins"]),
            seed=seed + comparison_index * 20 + 1,
            samples=bootstrap_samples,
        )
        measures = {
            "task_success": success,
            "bird_coins": coins,
            "user_questions": paired_bootstrap(
                ids,
                baseline,
                candidate,
                lambda record: float(record["user_questions"]),
                seed=seed + comparison_index * 20 + 2,
                samples=bootstrap_samples,
            ),
            "submissions": paired_bootstrap(
                ids,
                baseline,
                candidate,
                lambda record: float(record["submissions"]),
                seed=seed + comparison_index * 20 + 3,
                samples=bootstrap_samples,
            ),
            "total_tokens": paired_bootstrap(
                ids,
                baseline,
                candidate,
                lambda record: float(record["total_tokens"]),
                seed=seed + comparison_index * 20 + 4,
                samples=bootstrap_samples,
            ),
            "secondary_ex_user_turn_auc": paired_bootstrap(
                ids,
                baseline_secondary,
                candidate_secondary,
                lambda record: per_task_auc(record, 4),
                seed=seed + comparison_index * 20 + 5,
                samples=bootstrap_samples,
            ),
        }
        baseline_only = sum(
            bool(baseline[key]["success"]) and not bool(candidate[key]["success"])
            for key in ids
        )
        candidate_only = sum(
            bool(candidate[key]["success"]) and not bool(baseline[key]["success"])
            for key in ids
        )
        measures["discordant_success"] = {
            "baseline_only": baseline_only,
            "hybrid_only": candidate_only,
            "exact_mcnemar_two_sided_p": round(
                exact_mcnemar_two_sided(baseline_only, candidate_only), 8
            ),
        }
        measures["predeclared_interpretation"] = {
            "success_superiority": success["paired_bootstrap_95_ci_low"] > 0,
            "success_noninferior": (
                success["paired_bootstrap_95_ci_low"] >= -noninferiority_margin
            ),
            "fewer_coins": coins["paired_bootstrap_95_ci_high"] < 0,
            "efficiency_advantage": (
                success["paired_bootstrap_95_ci_low"] >= -noninferiority_margin
                and coins["paired_bootstrap_95_ci_high"] < 0
            ),
        }
        comparisons[f"hybrid_minus_{baseline_policy}"] = measures

    primary_comparisons = (
        "hybrid_minus_mini_react_port",
        "hybrid_minus_sphinteract_cq",
    )
    primary_raw_p = {
        name: float(
            comparisons[name]["discordant_success"]["exact_mcnemar_two_sided_p"]
        )
        for name in primary_comparisons
    }
    primary_adjusted_p = holm_adjust(primary_raw_p)
    for name in primary_comparisons:
        comparisons[name]["discordant_success"][
            "holm_adjusted_primary_p"
        ] = primary_adjusted_p[name]

    return {
        "analysis_version": COMPARISON_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tasks": len(ids),
        "arms": list(COMPARISON_POLICIES),
        "bootstrap": {"samples": bootstrap_samples, "seed": seed},
        "success_noninferiority_margin": noninferiority_margin,
        "policy_summaries": policy_summaries,
        "paired_comparisons": comparisons,
        "primary_exact_test_family": {
            "method": "Holm",
            "comparisons": list(primary_comparisons),
            "raw_p": primary_raw_p,
            "adjusted_p": primary_adjusted_p,
        },
        "claim_boundary": (
            "Matched local Mini-Interact Stress adaptation; not an official "
            "leaderboard submission or a cross-protocol ranking."
        ),
        "inputs": {
            policy: {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for policy, path in metrics_by_policy.items()
        },
    }


def finalize_policy(
    *,
    policy: str,
    freeze: dict[str, Any],
    reserve_manifest: Path,
    output: Path,
    log: Path,
) -> None:
    job = ComparisonJob(-1, policy, reserve_manifest, output, log)
    command = child_command(job, freeze)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8", newline="\n") as handle:
        completed = subprocess.run(
            command,
            cwd=EXPERIMENT_ROOT,
            stdout=handle,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    if completed.returncode != 0:
        raise RuntimeError(f"Finalization failed for {policy}; see {log}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-manifest", type=Path, default=DEFAULT_FREEZE)
    parser.add_argument("--confirm-large-run", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        freeze = validate_freeze(args.freeze_manifest)
        reserve_manifest = _resolve_workspace_path(freeze["selection"]["path"])
        reserve_ids = load_selection_ids(reserve_manifest)
        output_root = _resolve_workspace_path(freeze["execution"]["output_root"])
        shard_count = int(freeze["execution"]["shard_count"])
        shard_manifests = write_runtime_shard_manifests(
            output_root,
            reserve_ids=reserve_ids,
            shard_count=shard_count,
            reserve_sha256=freeze["selection"]["sha256"],
        )
        jobs = build_jobs(output_root, shard_manifests)
        expected_trajectories = len(reserve_ids) * len(COMPARISON_POLICIES)
        if len(jobs) != int(freeze["execution"]["maximum_concurrent_children"]):
            raise ValueError("Frozen concurrency does not match the job plan")
        if expected_trajectories != int(freeze["execution"]["trajectories"]):
            raise ValueError("Frozen trajectory count does not match the job plan")
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"COMPARISON REFUSED: {exc}")
        return 2

    print(
        f"Comparison={COMPARISON_VERSION}; tasks={len(reserve_ids)}; "
        f"arms={len(COMPARISON_POLICIES)}; trajectories={expected_trajectories}; "
        f"children={len(jobs)}"
    )
    print(
        f"Model={freeze['shared_stack']['model']}; "
        f"effort={freeze['shared_stack']['reasoning_effort']}; "
        f"output={output_root}"
    )
    if args.dry_run:
        print("Dry run only: no model, simulator, evaluator, or SQL calls made.")
        return 0
    if not args.confirm_large_run:
        print("COMPARISON REFUSED: --confirm-large-run is required")
        return 2

    try:
        run_jobs(jobs, freeze, poll_seconds=args.poll_seconds)
        combined_root = output_root / "combined"
        combined_outputs: dict[str, Path] = {}
        for policy in COMPARISON_POLICIES:
            destination = combined_root / f"{policy}.jsonl"
            merge_policy_outputs(
                policy=policy,
                shard_outputs=[
                    output_root / "shards" / f"shard-{index:02d}" / f"{policy}.jsonl"
                    for index in range(shard_count)
                ],
                expected_ids=reserve_ids,
                destination=destination,
            )
            combined_outputs[policy] = destination
            finalize_policy(
                policy=policy,
                freeze=freeze,
                reserve_manifest=reserve_manifest,
                output=destination,
                log=output_root / "logs" / f"finalize-{policy}.log",
            )

        metrics_by_policy = {
            policy: path.with_suffix(".metrics.json")
            for policy, path in combined_outputs.items()
        }
        analysis = analyze_comparison(
            metrics_by_policy,
            expected_ids=reserve_ids,
            seed=int(freeze["analysis"]["bootstrap_seed"]),
            bootstrap_samples=int(freeze["analysis"]["bootstrap_samples"]),
            noninferiority_margin=float(
                freeze["analysis"]["success_noninferiority_margin"]
            ),
        )
        analysis_path = output_root / "comparison.json"
        analysis_path.write_text(
            json.dumps(analysis, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Comparison complete: {analysis_path}")
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"COMPARISON INCOMPLETE BUT RESUMABLE: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
