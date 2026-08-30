"""Freeze one direct-API original-BIRD run before any benchmark model calls."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .bird_dev_clarification_value import (
    PROTOCOL_VERSION as CLARIFICATION_PROTOCOL,
)
from .bird_dev_matched import PROTOCOL_VERSION as MATCHED_PROTOCOL
from .run_bird_dev_clarification_value import (
    DEFAULT_DATABASE_ROOT,
    DEFAULT_DATASET,
    DEFAULT_POLICY_INSTRUCTIONS,
    DEFAULT_SIMULATOR_INSTRUCTIONS,
    DEFAULT_SPH_NOTEBOOK,
    OPENAI_FREEZE_STATUS,
)
from .run_support import sha256_file
from .sphinteract_source import SPHINTERACT_NOTEBOOK_SHA256


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EXPERIMENT_ROOT.parents[1]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _selection(path: Path) -> tuple[list[str], dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    ids = [str(value) for value in payload["question_ids"]]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Selection must contain unique question_ids")
    return ids, payload


def _implementation_paths(experiment: str) -> list[Path]:
    interaction = Path(__file__).resolve().parent
    common = [
        interaction / "bird_dev_matched.py",
        interaction / "sphinteract_source.py",
        interaction / "sqlite_env.py",
        interaction / "trajectory.py",
        interaction / "run_support.py",
        interaction / "inference_runtime.py",
        interaction / "openai_structured.py",
        interaction / "model.py",
        EXPERIMENT_ROOT / "pyproject.toml",
        EXPERIMENT_ROOT / "uv.lock",
    ]
    if experiment == "clarification_value":
        return [
            interaction / "bird_dev_clarification_value.py",
            interaction / "run_bird_dev_clarification_value.py",
            *common,
        ]
    return [interaction / "run_bird_dev_matched.py", *common]


def build_freeze(args: argparse.Namespace) -> dict[str, Any]:
    ids, selection = _selection(args.selection_manifest)
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    dataset_ids = {str(row["question_id"]) for row in dataset}
    missing = sorted(set(ids) - dataset_ids)
    if missing:
        raise ValueError(f"Selection contains unknown IDs: {missing[:10]}")
    selected_db_ids = sorted(
        {
            str(row["db_id"])
            for row in dataset
            if str(row["question_id"]) in set(ids)
        }
    )
    database_paths = [
        args.database_root / db_id / f"{db_id}.sqlite"
        for db_id in selected_db_ids
    ]
    protocol = (
        CLARIFICATION_PROTOCOL
        if args.experiment == "clarification_value"
        else MATCHED_PROTOCOL
    )
    artifact_paths = [
        args.selection_manifest,
        args.dataset,
        args.sph_notebook,
        DEFAULT_POLICY_INSTRUCTIONS,
        DEFAULT_SIMULATOR_INSTRUCTIONS,
        *database_paths,
    ]
    return {
        "schema_version": 1,
        "artifact": "openai-api-original-bird-run-freeze-v1",
        "experiment": args.experiment,
        "protocol": protocol,
        "status": OPENAI_FREEZE_STATUS,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "Direct Responses API replication under the same frozen controller "
            "and selected BIRD tasks."
        ),
        "authorization": {
            "explicit_paid_api_run_approval": True,
            "approval_recorded_on": "2026-08-30",
            "maximum_spend_usd": args.max_spend_usd,
        },
        "selections": {
            args.tier: {
                "path": _relative(args.selection_manifest),
                "sha256": sha256_file(args.selection_manifest),
                "tasks": len(ids),
                "selection_declared_tasks": selection.get("tasks", len(ids)),
            }
        },
        "shared_stack": {
            "surface": "openai_responses_api",
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "verbosity": "low",
            "service_tier": "default",
            "store": False,
            "workers": args.workers,
            "max_output_tokens": args.max_output_tokens,
            "maximum_transport_retries": args.max_transport_retries,
            "maximum_spend_usd": args.max_spend_usd,
        },
        "sphinteract_source": {
            "notebook_sha256": SPHINTERACT_NOTEBOOK_SHA256,
        },
        "implementation_hashes": {
            _relative(path): sha256_file(path)
            for path in _implementation_paths(args.experiment)
        },
        "artifact_hashes": {
            _relative(path): sha256_file(path) for path in artifact_paths
        },
        "planned_output": _relative(args.output_root),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--experiment",
        choices=("clarification_value", "matched"),
        required=True,
    )
    parser.add_argument("--tier", required=True)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--sph-notebook", type=Path, default=DEFAULT_SPH_NOTEBOOK)
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--workers", type=int, required=True)
    parser.add_argument("--max-output-tokens", type=int, default=8_192)
    parser.add_argument("--max-transport-retries", type=int, default=3)
    parser.add_argument("--max-spend-usd", type=float, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing freeze: {args.output}")
    if args.workers < 1 or args.max_output_tokens < 1:
        raise ValueError("Workers and maximum output tokens must be positive")
    if args.max_transport_retries < 0 or args.max_spend_usd <= 0:
        raise ValueError("Retry count cannot be negative and spend must be positive")
    payload = build_freeze(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.experiment} {args.tier} API freeze: {args.output}; "
        f"tasks={payload['selections'][args.tier]['tasks']}; "
        f"cap=${args.max_spend_usd:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
