"""Run a frozen three-arm BIRD clarification-value experiment tier.

Only the exact 20-task smoke, 100-task pilot, and 1,534-task full-Dev tiers are
accepted. Every tier requires its own explicit confirmation and freeze.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from statistics import mean
import time
from typing import Any

from codex_proxy.runner import CodexSubscriptionProxy

from .bird_dev_clarification_value import (
    ARMS,
    PROTOCOL_VERSION,
    BirdDevClarificationValueController,
)
from .bird_dev_matched import (
    BirdDevMatchedConfig,
    BirdDevPrivateEvaluator,
    build_schema_only_context,
    database_path,
    load_original_bird_rows,
    load_original_bird_tasks,
)
from .run_support import sha256_file
from .sphinteract_source import (
    SPHINTERACT_NOTEBOOK_SHA256,
    load_sphinteract_prompts,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EXPERIMENT_ROOT.parents[1]
DEFAULT_DATASET = EXPERIMENT_ROOT / "datasets" / "bird-dev" / "dev.json"
DEFAULT_DATABASE_ROOT = (
    EXPERIMENT_ROOT / "datasets" / "bird-dev" / "dev_databases" / "dev_databases"
)
DEFAULT_SPH_NOTEBOOK = (
    EXPERIMENT_ROOT
    / "datasets"
    / "sphinteract-source-21c6fcd"
    / "zeroshot_experiments.ipynb"
)
DEFAULT_POLICY_INSTRUCTIONS = Path(__file__).with_name("codex-policy-instructions.md")
DEFAULT_SIMULATOR_INSTRUCTIONS = Path(__file__).with_name(
    "codex-user-simulator-instructions.md"
)
SMOKE_TASKS = 20
PILOT_TASKS = 100
FULL_TASKS = 1534
RUN_CONTRACT_FILENAME = "run-contract.json"
FREEZE_STATUS = "frozen_before_smoke_and_pilot_model_calls"
PILOT_FREEZE_STATUS = "frozen_before_pilot_model_calls_after_smoke"
FULL_FREEZE_STATUS = "frozen_before_full_model_calls_after_pilot"


def run_tier(task_count: int) -> str:
    if task_count == SMOKE_TASKS:
        return "smoke20"
    if task_count == PILOT_TASKS:
        return "pilot100"
    if task_count == FULL_TASKS:
        return "full1534"
    raise ValueError(
        f"Only exact {SMOKE_TASKS}-task smoke, {PILOT_TASKS}-task pilot, or "
        f"{FULL_TASKS}-task full-Dev selections are supported; got {task_count}"
    )


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _load_selection(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = [str(value) for value in payload["question_ids"]]
    if not values or len(values) != len(set(values)):
        raise ValueError("Selection must contain unique question_ids")
    if int(payload.get("tasks", len(values))) != len(values):
        raise ValueError("Selection task count does not match question_ids")
    return values


def _select_tasks(tasks, ids: list[str]):
    by_id = {str(task.question_id): task for task in tasks}
    missing = [value for value in ids if value not in by_id]
    if missing:
        raise ValueError(f"Unknown BIRD question IDs: {missing}")
    return [by_id[value] for value in ids]


def validate_freeze(
    path: Path,
    *,
    tier: str,
    selection_path: Path,
    model: str,
    reasoning_effort: str,
    workers: int,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol") != PROTOCOL_VERSION:
        raise ValueError("Freeze protocol does not match the runner")
    expected_status_by_tier = {
        "smoke20": FREEZE_STATUS,
        "pilot100": PILOT_FREEZE_STATUS,
        "full1534": FULL_FREEZE_STATUS,
    }
    expected_status = expected_status_by_tier[tier]
    if payload.get("status") != expected_status:
        raise ValueError(f"Freeze status must be {expected_status!r}")
    selection = (payload.get("selections") or {}).get(tier) or {}
    if selection.get("path") != _relative(selection_path):
        raise ValueError("Selection path does not match the frozen tier")
    if selection.get("sha256") != sha256_file(selection_path):
        raise ValueError("Selection hash changed after freeze")
    shared = payload.get("shared_stack") or {}
    if shared.get("model") != model:
        raise ValueError("Model changed after freeze")
    if shared.get("reasoning_effort") != reasoning_effort:
        raise ValueError("Reasoning effort changed after freeze")
    if int(shared.get("workers", -1)) != workers:
        raise ValueError("Worker count changed after freeze")
    if (
        payload.get("sphinteract_source", {}).get("notebook_sha256")
        != SPHINTERACT_NOTEBOOK_SHA256
    ):
        raise ValueError("Frozen Sphinteract source hash is unexpected")
    frozen_files = {
        "Implementation": payload.get("implementation_hashes", {}),
        "Input artifact": payload.get("artifact_hashes", {}),
    }
    for label, hashes in frozen_files.items():
        for relative_path, expected in hashes.items():
            frozen_file = WORKSPACE_ROOT / relative_path
            if not frozen_file.is_file():
                raise ValueError(f"{label} is missing after freeze: {relative_path}")
            if sha256_file(frozen_file) != expected:
                raise ValueError(f"{label} changed after freeze: {relative_path}")
    return payload


def build_run_contract(
    *,
    tasks,
    selection_path: Path,
    dataset_path: Path,
    database_root: Path,
    sph_notebook: Path,
    freeze_manifest: Path,
    model: str,
    reasoning_effort: str,
    workers: int,
    config: BirdDevMatchedConfig,
) -> dict[str, Any]:
    implementation_paths = [
        Path(__file__),
        Path(__file__).with_name("bird_dev_clarification_value.py"),
        Path(__file__).with_name("bird_dev_matched.py"),
        Path(__file__).with_name("sphinteract_source.py"),
        EXPERIMENT_ROOT / "codex_proxy" / "runner.py",
        DEFAULT_POLICY_INSTRUCTIONS,
        DEFAULT_SIMULATOR_INSTRUCTIONS,
    ]
    database_hashes = {
        task.db_id: sha256_file(database_path(database_root, task)) for task in tasks
    }
    return {
        "schema_version": 1,
        "protocol": PROTOCOL_VERSION,
        "arms": list(ARMS),
        "selection": {
            "path": _relative(selection_path),
            "sha256": sha256_file(selection_path),
            "tasks": len(tasks),
        },
        "dataset": {
            "path": _relative(dataset_path),
            "sha256": sha256_file(dataset_path),
        },
        "databases": {
            "root": _relative(database_root),
            "sha256_by_db_id": dict(sorted(database_hashes.items())),
        },
        "sphinteract_notebook": {
            "path": _relative(sph_notebook),
            "sha256": sha256_file(sph_notebook),
        },
        "freeze_manifest": {
            "path": _relative(freeze_manifest),
            "sha256": sha256_file(freeze_manifest),
        },
        "model": {
            "surface": "codex_subscription_proxy",
            "requested": model,
            "reasoning_effort": reasoning_effort,
            "verbosity": "low",
            "maximum_transport_retries": 1,
        },
        "workers": workers,
        "controller_config": asdict(config),
        "implementation_hashes": {
            _relative(file): sha256_file(file) for file in implementation_paths
        },
    }


def ensure_run_contract(
    path: Path,
    expected: dict[str, Any],
    *,
    existing_record_count: int,
) -> str:
    expected_digest = _sha256_json(expected)
    if path.is_file():
        actual = json.loads(path.read_text(encoding="utf-8"))
        if actual != expected:
            raise ValueError(
                "Existing run contract does not match the requested inputs or settings"
            )
        return expected_digest
    if existing_record_count:
        raise ValueError(
            "Existing task records have no run contract; refusing an unverifiable resume"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return expected_digest


def _task_record_path(output_root: Path, question_id: int | str) -> Path:
    return output_root / "tasks" / f"{int(question_id):04d}.json"


def _validate_record(
    path: Path,
    expected_id: str,
    *,
    expected_contract_sha256: str,
) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol") != PROTOCOL_VERSION:
        raise ValueError(f"Record protocol mismatch: {path}")
    if str(payload.get("task", {}).get("question_id")) != expected_id:
        raise ValueError(f"Record task mismatch: {path}")
    if "task_error" not in payload and set(payload.get("arms", {})) != set(ARMS):
        raise ValueError(f"Record arm set mismatch: {path}")
    if payload.get("run_contract_sha256") != expected_contract_sha256:
        raise ValueError(f"Record run contract mismatch: {path}")
    return payload


def _record_requires_retry(payload: dict[str, Any]) -> bool:
    return "task_error" in payload or any(
        arm.get("termination") == "arm_error"
        for arm in (payload.get("arms") or {}).values()
    )


def _write_record(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(_stable_json(payload) + "\n", encoding="utf-8")
    temporary.replace(path)


def _task_error_record(task, exc: Exception) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "protocol": PROTOCOL_VERSION,
        "task": {
            "question_id": task.question_id,
            "db_id": task.db_id,
            "question": task.question,
            "difficulty": task.difficulty,
            "evidence_visible": False,
        },
        "task_error": {"type": type(exc).__name__, "message": str(exc)},
        "arms": {},
    }


def _arm_console_status(value: dict[str, Any]) -> str:
    if value.get("termination") == "arm_error":
        return "error"
    return "ok" if value.get("official_success") else "miss"


def _usage_totals(records: list[dict[str, Any]]) -> dict[str, int]:
    totals = {
        "model_calls": 0,
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 0,
    }
    for record in records:
        shared = record.get("shared_initial") or {}
        totals["model_calls"] += int(shared.get("model_calls", 0))
        usage_blocks = [shared.get("usage") or {}]
        for arm in (record.get("arms") or {}).values():
            totals["model_calls"] += int(arm.get("arm_specific_model_calls", 0))
            usage_blocks.append(arm.get("arm_specific_usage") or {})
        for usage in usage_blocks:
            for key in totals:
                if key != "model_calls":
                    totals[key] += int(usage.get(key, 0))
    return totals


def _served_models(records: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for record in records:
        event_groups = [(record.get("shared_initial") or {}).get("events") or []]
        event_groups.extend(
            arm.get("events") or []
            for arm in (record.get("arms") or {}).values()
        )
        for events in event_groups:
            for event in events:
                served = event.get("served_model")
                if isinstance(served, str) and served.strip():
                    values.add(served.strip())
    return sorted(values)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--sph-notebook", type=Path, default=DEFAULT_SPH_NOTEBOOK)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--freeze-manifest", type=Path)
    parser.add_argument("--confirm-smoke-run", action="store_true")
    parser.add_argument("--confirm-pilot-run", action="store_true")
    parser.add_argument("--confirm-full-run", action="store_true")
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        ids = _load_selection(args.selection_manifest)
        tier = run_tier(len(ids))
        if args.workers < 1:
            raise ValueError("--workers must be positive")
        if not args.dry_run:
            if not (args.reasoning_effort or "").strip():
                raise ValueError("--reasoning-effort is required for model calls")
            if args.freeze_manifest is None:
                raise ValueError("Model calls require --freeze-manifest")
            if tier == "smoke20" and not args.confirm_smoke_run:
                raise ValueError("The smoke requires --confirm-smoke-run")
            if tier == "pilot100" and not args.confirm_pilot_run:
                raise ValueError("The pilot requires --confirm-pilot-run")
            if tier == "full1534" and not args.confirm_full_run:
                raise ValueError("The full run requires --confirm-full-run")
            validate_freeze(
                args.freeze_manifest,
                tier=tier,
                selection_path=args.selection_manifest,
                model=args.model,
                reasoning_effort=str(args.reasoning_effort),
                workers=args.workers,
            )
        tasks = _select_tasks(load_original_bird_tasks(args.dataset), ids)
        rows = load_original_bird_rows(args.dataset)
        prompts = load_sphinteract_prompts(args.sph_notebook)
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"CLARIFICATION VALUE RUN REFUSED: {exc}")
        return 2

    print(
        f"Protocol={PROTOCOL_VERSION}; tier={tier}; tasks={len(tasks)}; "
        f"model={args.model}; effort={args.reasoning_effort or 'unset (dry-run)'}; "
        f"workers={args.workers}"
    )
    print(f"Selection={args.selection_manifest}")
    print(f"Output root={args.output_root}")

    if args.dry_run:
        contexts = [
            build_schema_only_context(database_path(args.database_root, task))
            for task in tasks
        ]
        print(
            "Dry run passed: no model/evaluator calls. "
            f"Schema chars total={sum(map(len, contexts)):,}; "
            f"mean={mean(map(len, contexts)):,.0f}; "
            f"Sph notebook={sha256_file(args.sph_notebook)}"
        )
        return 0

    args.output_root.mkdir(parents=True, exist_ok=True)
    run_started_at = datetime.now(timezone.utc)
    run_started = time.monotonic()
    config = BirdDevMatchedConfig()
    existing_record_count = sum(
        _task_record_path(args.output_root, task.question_id).is_file()
        for task in tasks
    )
    try:
        run_contract = build_run_contract(
            tasks=tasks,
            selection_path=args.selection_manifest,
            dataset_path=args.dataset,
            database_root=args.database_root,
            sph_notebook=args.sph_notebook,
            freeze_manifest=args.freeze_manifest,
            model=args.model,
            reasoning_effort=str(args.reasoning_effort),
            workers=args.workers,
            config=config,
        )
        run_contract_path = args.output_root / RUN_CONTRACT_FILENAME
        run_contract_sha256 = ensure_run_contract(
            run_contract_path,
            run_contract,
            existing_record_count=existing_record_count,
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"CLARIFICATION VALUE RUN REFUSED: {exc}")
        return 2

    evaluator = BirdDevPrivateEvaluator(
        rows=rows,
        database_root=args.database_root,
        timeout_seconds=config.sql_timeout_seconds,
        max_rows=config.max_result_rows,
    )
    policy_proxy = CodexSubscriptionProxy(
        model=args.model,
        reasoning_effort=str(args.reasoning_effort),
        verbosity="low",
        instructions_file=DEFAULT_POLICY_INSTRUCTIONS,
        max_transport_retries=1,
    )
    simulator_proxy = CodexSubscriptionProxy(
        model=args.model,
        reasoning_effort=str(args.reasoning_effort),
        verbosity="low",
        instructions_file=DEFAULT_SIMULATOR_INSTRUCTIONS,
        max_transport_retries=1,
    )

    completed: dict[str, dict[str, Any]] = {}
    pending = []
    for task in tasks:
        path = _task_record_path(args.output_root, task.question_id)
        if path.is_file():
            existing = _validate_record(
                path,
                str(task.question_id),
                expected_contract_sha256=run_contract_sha256,
            )
            if _record_requires_retry(existing):
                pending.append(task)
            else:
                completed[str(task.question_id)] = existing
        else:
            pending.append(task)
    print(
        f"Existing valid={len(completed)}; pending or retryable errors={len(pending)}"
    )

    def run_one(task):
        controller = BirdDevClarificationValueController(
            policy_invoker=policy_proxy,
            simulator_invoker=simulator_proxy,
            evaluator=evaluator,
            prompts=prompts,
            database_root=args.database_root,
            artifact_root=args.output_root / "artifacts",
            config=config,
        )
        try:
            record = controller.run_task(task)
        except Exception as exc:  # noqa: BLE001 - retain task-level failures.
            record = _task_error_record(task, exc)
        record["run_contract_sha256"] = run_contract_sha256
        _write_record(_task_record_path(args.output_root, task.question_id), record)
        return task, record

    with ThreadPoolExecutor(max_workers=min(args.workers, len(pending) or 1)) as pool:
        futures = {pool.submit(run_one, task): task for task in pending}
        for future in as_completed(futures):
            task, record = future.result()
            completed[str(task.question_id)] = record
            status = (
                "task_error"
                if "task_error" in record
                else ", ".join(
                    f"{arm}={_arm_console_status(value)}"
                    for arm, value in record["arms"].items()
                )
            )
            print(
                f"[{datetime.now(timezone.utc).isoformat()}] "
                f"completed={len(completed)}/{len(tasks)}; id={task.question_id}; "
                f"{status}",
                flush=True,
            )

    ordered = [completed[str(task.question_id)] for task in tasks]
    merged = args.output_root / "task-records.jsonl"
    merged.write_text(
        "".join(_stable_json(record) + "\n" for record in ordered),
        encoding="utf-8",
    )
    usage = _usage_totals(ordered)
    task_error_count = sum("task_error" in record for record in ordered)
    arm_error_count = sum(
        arm.get("termination") == "arm_error"
        for record in ordered
        for arm in record.get("arms", {}).values()
    )
    manifest = {
        "schema_version": 1,
        "protocol": PROTOCOL_VERSION,
        "arms": list(ARMS),
        "tier": tier,
        "status": (
            "complete"
            if task_error_count == 0 and arm_error_count == 0
            else "invalid_protocol_errors"
        ),
        "started_at": run_started_at.isoformat(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "wall_elapsed_seconds": round(time.monotonic() - run_started, 3),
        "selection": {
            "path": _relative(args.selection_manifest),
            "sha256": sha256_file(args.selection_manifest),
            "tasks": len(tasks),
            "question_ids": [task.question_id for task in tasks],
        },
        "dataset": {
            "path": _relative(args.dataset),
            "sha256": sha256_file(args.dataset),
        },
        "database_root": _relative(args.database_root),
        "sphinteract_notebook": {
            "path": _relative(args.sph_notebook),
            "sha256": sha256_file(args.sph_notebook),
        },
        "freeze_manifest": {
            "path": _relative(args.freeze_manifest),
            "sha256": sha256_file(args.freeze_manifest),
        },
        "model": {
            "surface": "codex_subscription_proxy",
            "requested": args.model,
            "reasoning_effort": args.reasoning_effort,
            "verbosity": "low",
            "codex_cli_version": policy_proxy.codex_cli_version,
            "served_model_ids_if_emitted": _served_models(ordered),
            "immutable_served_snapshot_available": False,
            "monetary_cost_available": False,
        },
        "workers": args.workers,
        "branch_order_rule": (
            "SHA-256 parity of protocol plus question ID. Smoke and pilot were "
            "prebalanced; full Dev retains the deterministic natural assignment."
        ),
        "run_contract": {
            "path": _relative(run_contract_path),
            "sha256": run_contract_sha256,
        },
        "task_records": _relative(merged),
        "task_records_sha256": sha256_file(merged),
        "physical_usage": usage,
        "task_errors": task_error_count,
        "arm_errors": arm_error_count,
    }
    manifest_path = args.output_root / "run-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Merged records: {merged}")
    print(f"Run manifest: {manifest_path}")
    print(
        f"Physical usage: calls={usage['model_calls']:,}; "
        f"tokens={usage['total_tokens']:,}"
    )
    if task_error_count or arm_error_count:
        print(
            "CLARIFICATION VALUE RUN INVALID: "
            f"task_errors={task_error_count}; arm_errors={arm_error_count}"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
