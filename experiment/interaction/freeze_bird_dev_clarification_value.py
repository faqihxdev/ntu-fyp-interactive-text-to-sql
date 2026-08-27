"""Freeze the authorized BIRD clarification-value smoke and pilot protocol."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .bird_dev_clarification_value import ARMS, PROTOCOL_VERSION
from .run_bird_dev_clarification_value import (
    DEFAULT_DATABASE_ROOT,
    DEFAULT_DATASET,
    DEFAULT_POLICY_INSTRUCTIONS,
    DEFAULT_SIMULATOR_INSTRUCTIONS,
    DEFAULT_SPH_NOTEBOOK,
    EXPERIMENT_ROOT,
    PILOT_FREEZE_STATUS,
    WORKSPACE_ROOT,
)
from .run_support import sha256_file
from .sphinteract_source import (
    SPHINTERACT_COMMIT,
    SPHINTERACT_NOTEBOOK_SHA256,
)


DEFAULT_SMOKE = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-clarification-value-smoke20.json"
)
DEFAULT_PILOT = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-clarification-value-pilot100.json"
)
DEFAULT_PROTOCOL_NOTE = (
    EXPERIMENT_ROOT
    / "notes"
    / "bird-dev-clarification-value-protocol-2026-08-26.md"
)
DEFAULT_OUTPUT = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-clarification-value-v1-pilot-freeze-2026-08-26.json"
)
DEFAULT_SMOKE_RUN_ROOT = (
    EXPERIMENT_ROOT
    / "runs"
    / "bird-dev-clarification-value-v1-smoke20-luna-high-20260826"
)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()


def _selection(path: Path, expected_tasks: int) -> tuple[dict[str, Any], list[int]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [int(value) for value in payload["question_ids"]]
    if len(ids) != expected_tasks or len(ids) != len(set(ids)):
        raise ValueError(f"Expected {expected_tasks} unique IDs in {path}")
    return (
        {
            "path": _relative(path),
            "sha256": sha256_file(path),
            "tasks": expected_tasks,
            "question_ids": ids,
            "uses_gold_sql_or_results": False,
        },
        ids,
    )


def build_freeze(
    *,
    smoke_path: Path,
    pilot_path: Path,
    protocol_note: Path,
    dataset: Path,
    database_root: Path,
    sph_notebook: Path,
    smoke_run_root: Path,
) -> dict[str, Any]:
    smoke, smoke_ids = _selection(smoke_path, 20)
    pilot, pilot_ids = _selection(pilot_path, 100)
    if set(smoke_ids) & set(pilot_ids):
        raise ValueError("Smoke and pilot selections must be disjoint")
    rows = json.loads(dataset.read_text(encoding="utf-8"))
    by_id = {int(row["question_id"]): row for row in rows}
    selected_db_ids = sorted(
        {str(by_id[value]["db_id"]) for value in smoke_ids + pilot_ids}
    )
    smoke_manifest_path = smoke_run_root / "run-manifest.json"
    smoke_records_path = smoke_run_root / "task-records.jsonl"
    smoke_analysis_path = smoke_run_root / "analysis.json"
    smoke_manifest = json.loads(smoke_manifest_path.read_text(encoding="utf-8"))
    smoke_analysis = json.loads(smoke_analysis_path.read_text(encoding="utf-8"))
    if smoke_manifest.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Smoke manifest protocol mismatch")
    if smoke_manifest.get("status") != "complete":
        raise ValueError("Smoke run did not complete cleanly")
    if int(smoke_manifest.get("selection", {}).get("tasks", 0)) != 20:
        raise ValueError("Smoke run does not contain 20 tasks")
    if not smoke_analysis.get("mechanical_audit", {}).get("passed"):
        raise ValueError("Smoke mechanical audit did not pass")

    interaction_root = Path(__file__).resolve().parent
    implementation_paths = [
        interaction_root / "bird_dev_clarification_value.py",
        interaction_root / "run_bird_dev_clarification_value.py",
        interaction_root / "analyze_bird_dev_clarification_value.py",
        interaction_root / "make_bird_dev_clarification_value_selections.py",
        Path(__file__),
        interaction_root / "bird_dev_matched.py",
        interaction_root / "sphinteract_source.py",
        interaction_root / "sqlite_env.py",
        interaction_root / "trajectory.py",
        interaction_root / "run_support.py",
        EXPERIMENT_ROOT / "codex_proxy" / "runner.py",
        interaction_root / "tests" / "test_bird_dev_clarification_value.py",
        EXPERIMENT_ROOT / "pyproject.toml",
        EXPERIMENT_ROOT / "uv.lock",
    ]
    artifact_paths = [
        DEFAULT_POLICY_INSTRUCTIONS,
        DEFAULT_SIMULATOR_INSTRUCTIONS,
        smoke_path,
        pilot_path,
        protocol_note,
        dataset,
        sph_notebook,
        smoke_manifest_path,
        smoke_records_path,
        smoke_analysis_path,
    ]
    artifact_paths.extend(
        database_root / db_id / f"{db_id}.sqlite" for db_id in selected_db_ids
    )
    return {
        "schema_version": 1,
        "artifact": "bird-dev-clarification-value-v1-pilot-freeze",
        "protocol": PROTOCOL_VERSION,
        "status": PILOT_FREEZE_STATUS,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Matched control for clarification value beyond equal SQL-only "
            "recovery opportunity."
        ),
        "authorization_boundary": {
            "completed_tier": {"smoke20": 20},
            "authorized_new_model_call_tier": {"pilot100": 100},
            "authorized_arms_per_task": len(ARMS),
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "workers": 4,
            "full_1534_run_authorized": False,
            "runner_supports_full_run": False,
        },
        "selections": {"smoke20": smoke, "pilot100": pilot},
        "shared_stack": {
            "surface": "codex_subscription_proxy",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "verbosity": "low",
            "workers": 4,
            "transport_timeout_seconds": 600,
            "maximum_transport_retries": 1,
            "served_snapshot_immutable_identifier_available": False,
        },
        "protocol_constraints": {
            "arms": list(ARMS),
            "shared_initial": "One physical initial SQL copied to every arm.",
            "maximum_normal_sql_attempts": {
                "no_user": 1,
                "sql_only_recovery": 5,
                "reactive_clarification": 5,
            },
            "maximum_user_turns": {
                "no_user": 0,
                "sql_only_recovery": 0,
                "reactive_clarification": 4,
            },
            "invalid_sql_repairs_per_normal_attempt": 1,
            "primary_metric": "Original-BIRD execution equality.",
            "primary_contrast": (
                "reactive_clarification minus sql_only_recovery"
            ),
            "hidden_rejection_oracle": True,
            "branch_order": "Deterministic SHA-256-balanced order by question ID.",
        },
        "analysis_plan": {
            "mechanical_checks": (
                "Zero errors; exact shared prefix; no SQL-only interaction; "
                "budget compliance; retry and question behavior after rejection."
            ),
            "pilot_minimum_initial_failures": 30,
            "statistics": [
                "Paired execution-success difference",
                "10,000-sample task-bootstrap 95% interval",
                "Exact two-sided McNemar test",
                "Discordant outcomes",
                "Turns, attempts, calls, tokens, and terminations",
            ],
            "claim_boundary": (
                "Clarification bundle versus SQL-only retries; not a clean "
                "decomposition of question, answer, and prompt wording."
            ),
        },
        "smoke_gate": {
            "run_manifest": _relative(smoke_manifest_path),
            "task_records": _relative(smoke_records_path),
            "analysis": _relative(smoke_analysis_path),
            "tasks": 20,
            "mechanical_audit_passed": True,
            "initial_failures": smoke_analysis["initial_sql"][
                "failures_available_for_recovery"
            ],
            "physical_model_calls": smoke_manifest["physical_usage"]["model_calls"],
            "physical_total_tokens": smoke_manifest["physical_usage"]["total_tokens"],
            "role": "Plumbing gate only; smoke effect size is not method evidence.",
        },
        "sphinteract_source": {
            "repository": "https://github.com/ZhaoFuheng/Sphinteract.git",
            "commit": SPHINTERACT_COMMIT,
            "notebook_sha256": SPHINTERACT_NOTEBOOK_SHA256,
            "license": "MIT",
        },
        "implementation_hashes": {
            _relative(path): sha256_file(path) for path in implementation_paths
        },
        "artifact_hashes": {
            _relative(path): sha256_file(path) for path in artifact_paths
        },
        "planned_outputs": {
            "pilot100": (
                "codebase/experiment/runs/"
                "bird-dev-clarification-value-v1-pilot100-luna-high-20260826"
            ),
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-selection", type=Path, default=DEFAULT_SMOKE)
    parser.add_argument("--pilot-selection", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--protocol-note", type=Path, default=DEFAULT_PROTOCOL_NOTE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--sph-notebook", type=Path, default=DEFAULT_SPH_NOTEBOOK)
    parser.add_argument(
        "--smoke-run-root", type=Path, default=DEFAULT_SMOKE_RUN_ROOT
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing freeze: {args.output}")
    payload = build_freeze(
        smoke_path=args.smoke_selection,
        pilot_path=args.pilot_selection,
        protocol_note=args.protocol_note,
        dataset=args.dataset,
        database_root=args.database_root,
        sph_notebook=args.sph_notebook,
        smoke_run_root=args.smoke_run_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote freeze {args.output}: implementations="
        f"{len(payload['implementation_hashes'])}; artifacts="
        f"{len(payload['artifact_hashes'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
