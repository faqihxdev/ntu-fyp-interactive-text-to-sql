"""Freeze the authorized all-1,534 BIRD clarification-value replication."""

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
    FULL_FREEZE_STATUS,
    FULL_TASKS,
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
DEFAULT_FULL = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-clarification-value-full1534.json"
)
DEFAULT_PROTOCOL_NOTE = (
    EXPERIMENT_ROOT
    / "interaction"
    / "README.md"
)
DEFAULT_PILOT_FREEZE = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-clarification-value-v1-pilot-freeze-2026-08-26.json"
)
DEFAULT_PILOT_RESULT = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-clarification-value-v1-pilot-result-2026-08-26.json"
)
DEFAULT_PILOT_RUN_ROOT = (
    EXPERIMENT_ROOT
    / "runs"
    / "bird-dev-clarification-value-v1-pilot100-luna-high-20260826"
)
DEFAULT_FULL_RUN_ROOT = (
    EXPERIMENT_ROOT
    / "runs"
    / "bird-dev-clarification-value-v1-full1534-luna-high-20260826"
)
DEFAULT_OUTPUT = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-clarification-value-v1-full-freeze-2026-08-26.json"
)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()


def _load_selection(path: Path, expected_tasks: int) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [int(value) for value in payload["question_ids"]]
    if len(ids) != expected_tasks or len(ids) != len(set(ids)):
        raise ValueError(f"Expected {expected_tasks} unique IDs in {path}")
    return payload


def _frozen_selection(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": _relative(path),
        "sha256": sha256_file(path),
        "tasks": len(payload["question_ids"]),
        "question_ids": [int(value) for value in payload["question_ids"]],
        "uses_gold_sql_or_results": False,
    }


def build_freeze(
    *,
    smoke_path: Path,
    pilot_path: Path,
    full_path: Path,
    protocol_note: Path,
    dataset: Path,
    database_root: Path,
    sph_notebook: Path,
    pilot_freeze: Path,
    pilot_result: Path,
    pilot_run_root: Path,
    full_run_root: Path,
) -> dict[str, Any]:
    smoke = _load_selection(smoke_path, 20)
    pilot = _load_selection(pilot_path, 100)
    full = _load_selection(full_path, FULL_TASKS)
    smoke_ids = [int(value) for value in smoke["question_ids"]]
    pilot_ids = [int(value) for value in pilot["question_ids"]]
    full_ids = [int(value) for value in full["question_ids"]]
    exposed_ids = smoke_ids + pilot_ids
    if len(exposed_ids) != 120 or len(exposed_ids) != len(set(exposed_ids)):
        raise ValueError("Smoke and pilot selections must expose 120 disjoint IDs")
    exposure = full.get("development_exposure") or {}
    if [int(value) for value in exposure.get("union_question_ids", [])] != exposed_ids:
        raise ValueError("Full selection development exposure is not frozen correctly")
    if int(exposure.get("unexposed_tasks", -1)) != 1414:
        raise ValueError("Full selection must retain 1,414 unexposed tasks")

    rows = json.loads(dataset.read_text(encoding="utf-8"))
    dataset_ids = [int(row["question_id"]) for row in rows]
    if dataset_ids != full_ids:
        raise ValueError("Full selection must equal released BIRD Dev order exactly")
    database_ids = sorted({str(row["db_id"]) for row in rows})

    pilot_manifest_path = pilot_run_root / "run-manifest.json"
    pilot_records_path = pilot_run_root / "task-records.jsonl"
    pilot_analysis_path = pilot_run_root / "analysis.json"
    pilot_contract_path = pilot_run_root / "run-contract.json"
    pilot_manifest = json.loads(pilot_manifest_path.read_text(encoding="utf-8"))
    pilot_analysis = json.loads(pilot_analysis_path.read_text(encoding="utf-8"))
    pilot_result_payload = json.loads(pilot_result.read_text(encoding="utf-8"))
    if pilot_manifest.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Pilot manifest protocol mismatch")
    if pilot_manifest.get("status") != "complete":
        raise ValueError("Pilot run did not complete cleanly")
    if int(pilot_manifest.get("selection", {}).get("tasks", 0)) != 100:
        raise ValueError("Pilot run does not contain 100 tasks")
    if pilot_manifest.get("selection", {}).get("sha256") != sha256_file(pilot_path):
        raise ValueError("Pilot run selection does not match the frozen pilot")
    if not pilot_analysis.get("mechanical_audit", {}).get("passed"):
        raise ValueError("Pilot mechanical audit did not pass")
    if not pilot_analysis.get("informativeness", {}).get(
        "pilot_has_prespecified_recovery_sample"
    ):
        raise ValueError("Pilot did not pass the prespecified recovery-sample gate")
    if pilot_result_payload.get("status") != "complete_valid_pilot":
        raise ValueError("Pilot result artifact is not complete and valid")

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
        full_path,
        protocol_note,
        dataset,
        sph_notebook,
        pilot_freeze,
        pilot_result,
        pilot_manifest_path,
        pilot_records_path,
        pilot_analysis_path,
        pilot_contract_path,
    ]
    artifact_paths.extend(
        database_root / db_id / f"{db_id}.sqlite" for db_id in database_ids
    )

    primary = pilot_analysis["paired_comparisons_all_tasks"][
        "reactive_minus_sql_only"
    ]
    return {
        "schema_version": 1,
        "artifact": "bird-dev-clarification-value-v1-full-freeze",
        "protocol": PROTOCOL_VERSION,
        "status": FULL_FREEZE_STATUS,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "All-Dev replication of the matched clarification-versus-SQL-only "
            "recovery comparison after an informative pilot."
        ),
        "authorization_boundary": {
            "completed_tiers": {"smoke20": 20, "pilot100": 100},
            "authorized_new_model_call_tier": {"full1534": FULL_TASKS},
            "authorized_arms_per_task": len(ARMS),
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "workers": 4,
            "full_1534_run_authorized": True,
            "authorization_recorded_on": "2026-08-26",
        },
        "selections": {
            "smoke20": _frozen_selection(smoke_path, smoke),
            "pilot100": _frozen_selection(pilot_path, pilot),
            "full1534": _frozen_selection(full_path, full),
        },
        "development_exposure": {
            "exposed_tasks": 120,
            "unexposed_tasks": 1414,
            "primary_population": "All 1,534 original-BIRD Dev tasks.",
            "prespecified_sensitivity": (
                "Repeat arm summaries and paired contrasts after excluding all "
                "120 smoke/pilot IDs."
            ),
        },
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
            "primary_contrast": "reactive_clarification minus sql_only_recovery",
            "hidden_rejection_oracle": True,
            "branch_order": "Deterministic SHA-256 parity by question ID.",
        },
        "analysis_plan": {
            "primary_population": "All 1,534 original-BIRD Dev tasks.",
            "prespecified_sensitivity_population": (
                "The 1,414 tasks not used in the smoke or pilot."
            ),
            "mechanical_checks": (
                "Zero errors; exact shared prefix; no SQL-only interaction; "
                "budget compliance; retry and question behavior after rejection."
            ),
            "statistics": [
                "Paired execution-success difference",
                "10,000-sample task-bootstrap 95% interval",
                "Exact two-sided McNemar test",
                "Discordant outcomes",
                "Turns, attempts, calls, tokens, and terminations",
            ],
            "claim_boundary": (
                "Clarification bundle versus SQL-only retries under a hidden "
                "rejection oracle and gold-conditioned simulator; not a clean "
                "decomposition or real-user result."
            ),
        },
        "pilot_gate": {
            "run_manifest": _relative(pilot_manifest_path),
            "task_records": _relative(pilot_records_path),
            "analysis": _relative(pilot_analysis_path),
            "result_artifact": _relative(pilot_result),
            "tasks": 100,
            "mechanical_audit_passed": True,
            "initial_failures": pilot_analysis["initial_sql"][
                "failures_available_for_recovery"
            ],
            "reactive_minus_sql_only_percentage_points": primary[
                "difference_percentage_points"
            ],
            "reactive_only_successes": primary["candidate_only_successes"],
            "sql_only_only_successes": primary["baseline_only_successes"],
            "physical_model_calls": pilot_manifest["physical_usage"]["model_calls"],
            "physical_total_tokens": pilot_manifest["physical_usage"]["total_tokens"],
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
        "planned_outputs": {"full1534": _relative(full_run_root)},
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke-selection", type=Path, default=DEFAULT_SMOKE)
    parser.add_argument("--pilot-selection", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--full-selection", type=Path, default=DEFAULT_FULL)
    parser.add_argument("--protocol-note", type=Path, default=DEFAULT_PROTOCOL_NOTE)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--sph-notebook", type=Path, default=DEFAULT_SPH_NOTEBOOK)
    parser.add_argument("--pilot-freeze", type=Path, default=DEFAULT_PILOT_FREEZE)
    parser.add_argument("--pilot-result", type=Path, default=DEFAULT_PILOT_RESULT)
    parser.add_argument("--pilot-run-root", type=Path, default=DEFAULT_PILOT_RUN_ROOT)
    parser.add_argument("--full-run-root", type=Path, default=DEFAULT_FULL_RUN_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing freeze: {args.output}")
    payload = build_freeze(
        smoke_path=args.smoke_selection,
        pilot_path=args.pilot_selection,
        full_path=args.full_selection,
        protocol_note=args.protocol_note,
        dataset=args.dataset,
        database_root=args.database_root,
        sph_notebook=args.sph_notebook,
        pilot_freeze=args.pilot_freeze,
        pilot_result=args.pilot_result,
        pilot_run_root=args.pilot_run_root,
        full_run_root=args.full_run_root,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote full freeze {args.output}: implementations="
        f"{len(payload['implementation_hashes'])}; artifacts="
        f"{len(payload['artifact_hashes'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
