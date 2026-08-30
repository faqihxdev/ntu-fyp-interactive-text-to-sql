"""Freeze the 240-task Mini-Interact comparison for direct API execution."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from .codex_official_policy import OFFICIAL_REFERENCE_PROMPT_SOURCE
from .official_comparison import COMPARISON_POLICIES, COMPARISON_VERSION
from .run_official_mini_interact import (
    DEFAULT_PRIVATE_MERGE,
    DEFAULT_PUBLIC_DATA,
)
from .run_support import sha256_file


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EXPERIMENT_ROOT.parents[1]
DEFAULT_SELECTION = EXPERIMENT_ROOT / "manifests" / "mini-interact-reserve-240.json"


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(WORKSPACE_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def build_freeze(args: argparse.Namespace) -> dict:
    selection_payload = json.loads(args.selection.read_text(encoding="utf-8"))
    ids = [str(value) for value in selection_payload["instance_ids"]]
    if len(ids) != 240 or len(ids) != len(set(ids)):
        raise ValueError("Mini-Interact production selection must contain 240 IDs")
    interaction = Path(__file__).resolve().parent
    implementation_paths = [
        interaction / "official_comparison.py",
        interaction / "run_official_mini_interact.py",
        interaction / "official_protocol.py",
        interaction / "codex_official_policy.py",
        interaction / "mini_interact.py",
        interaction / "oracles.py",
        interaction / "metrics.py",
        interaction / "compare_policy_metrics.py",
        interaction / "trajectory.py",
        interaction / "run_support.py",
        interaction / "inference_runtime.py",
        interaction / "openai_structured.py",
        interaction / "model.py",
        EXPERIMENT_ROOT / "readiness" / "mini_interact_live_eval.py",
        EXPERIMENT_ROOT / "pyproject.toml",
        EXPERIMENT_ROOT / "uv.lock",
    ]
    artifact_paths = [
        args.selection,
        args.public_data,
        args.private_merge,
        OFFICIAL_REFERENCE_PROMPT_SOURCE,
        interaction / "codex-policy-instructions.md",
        interaction / "codex-user-simulator-instructions.md",
    ]
    children = args.shard_count * len(COMPARISON_POLICIES)
    return {
        "schema_version": 1,
        "comparison_version": COMPARISON_VERSION,
        "status": "frozen_before_model_calls",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "question": (
            "Under one Mini-Interact Stress adaptation, how do the reactive, "
            "proactive, hybrid, and structured reference policies compare when "
            "the task set, model, simulator, scorer, costs, and budgets are fixed?"
        ),
        "claim_boundary": (
            "Matched local Mini-Interact Stress adaptation; not an official "
            "leaderboard submission or cross-protocol ranking."
        ),
        "approval": {
            "explicit_large_run_approval": True,
            "approved_scope": "All 240 reserve IDs x four arms using Luna/high",
            "approved_trajectories": 960,
            "approval_date": "2026-08-30",
            "maximum_spend_usd_across_children": round(
                children * args.max_spend_usd_per_child, 2
            ),
        },
        "selection": {
            "path": _relative(args.selection),
            "sha256": sha256_file(args.selection),
            "tasks": len(ids),
            "selection_uses_private_fields": False,
        },
        "arms": list(COMPARISON_POLICIES),
        "shared_stack": {
            "surface": "openai_responses_api",
            "model": "gpt-5.6-luna",
            "reasoning_effort": "high",
            "verbosity": "low",
            "service_tier": "default",
            "store": False,
            "same_model_for_policy_and_simulator": True,
            "user_patience_budget": 6,
            "max_action_turns": 60,
            "metric_horizon": 4,
            "max_output_tokens": 8_192,
            "maximum_transport_retries": 3,
            "maximum_spend_usd_per_child": args.max_spend_usd_per_child,
        },
        "execution": {
            "output_root": _relative(args.output_root),
            "trajectories": len(ids) * len(COMPARISON_POLICIES),
            "shard_count": args.shard_count,
            "tasks_per_shard": len(ids) // args.shard_count,
            "maximum_concurrent_children": children,
            "sharding": "Round-robin by frozen reserve-manifest order.",
            "resume": "Each child skips completed task IDs.",
        },
        "analysis": {
            "bootstrap_samples": 10_000,
            "bootstrap_seed": 20260822,
            "success_noninferiority_margin": 0.05,
        },
        "dataset_hashes": {
            "public_mini_interact_sha256": sha256_file(args.public_data),
            "private_merge_sha256": sha256_file(args.private_merge),
            "official_reference_prompt_source_sha256": sha256_file(
                OFFICIAL_REFERENCE_PROMPT_SOURCE
            ),
        },
        "implementation_hashes": {
            _relative(path): sha256_file(path) for path in implementation_paths
        },
        "artifact_hashes": {
            _relative(path): sha256_file(path) for path in artifact_paths
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--public-data", type=Path, default=DEFAULT_PUBLIC_DATA)
    parser.add_argument("--private-merge", type=Path, default=DEFAULT_PRIVATE_MERGE)
    parser.add_argument("--shard-count", type=int, default=8)
    parser.add_argument("--max-spend-usd-per-child", type=float, default=2.5)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite existing freeze: {args.output}")
    if args.shard_count < 1 or 240 % args.shard_count:
        raise ValueError("Shard count must be a positive divisor of 240")
    if args.max_spend_usd_per_child <= 0:
        raise ValueError("Per-child spend cap must be positive")
    payload = build_freeze(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote Mini-Interact API freeze: tasks=240; arms=4; "
        f"children={payload['execution']['maximum_concurrent_children']}; "
        f"aggregate cap=${payload['approval']['maximum_spend_usd_across_children']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
