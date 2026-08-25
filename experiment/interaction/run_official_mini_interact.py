"""Run the standardized Mini-Interact literature-comparison protocol.

The runner uses the released action costs and paid context retrieval through the
local compatibility module. It refuses implicit or unapproved dataset-scale
runs. Subscription-proxy outputs remain provisional development evidence.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from codex_proxy.runner import CodexSubscriptionProxy

from .codex_official_policy import (
    OFFICIAL_REFERENCE_PROMPT_SOURCE,
    CodexOfficialPolicyModel,
)
from .metrics import summarize_trajectories
from .mini_interact import (
    SANITIZED_SIMULATOR_ADAPTER_VERSION,
    SIMULATOR_SANITIZER_VERSION,
    SanitizedCodexMiniInteractUserSimulator,
)
from .official_protocol import (
    OFFICIAL_SOURCE_REVISION,
    POLICIES,
    PROTOCOL_VERSION,
    MiniInteractPrivateBudgetProvider,
    OfficialMiniInteractConfig,
    OfficialMiniInteractController,
    OfficialMiniInteractTools,
    load_official_public_tasks,
    summarize_official_protocol,
)
from .oracles import OfficialMiniInteractLiveOracle
from .run_support import (
    require_explicit_inference_settings,
    select_explicit_tasks,
    sha256_file,
)
from .trajectory import append_trajectory, load_trajectories

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_ROOT = EXPERIMENT_ROOT / "datasets" / "mini-interact"
DEFAULT_PUBLIC_DATA = DEFAULT_DATASET_ROOT / "mini_interact.jsonl"
DEFAULT_PRIVATE_MERGE = (
    EXPERIMENT_ROOT
    / "datasets"
    / "private"
    / "mini-interact"
    / "mini_interact_merged_20251112.jsonl"
)


def default_output(policy: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return EXPERIMENT_ROOT / "runs" / f"official-mini-{policy}-{stamp}.jsonl"


def validate_existing_trajectories(
    trajectories,
    *,
    selected_ids: set[str],
    policy: str,
    model: str,
    reasoning_effort: str,
    user_patience_budget: float,
    max_action_turns: int,
    progress_guard: bool,
    guard_fingerprint: str,
    max_guard_retries: int,
) -> None:
    """Refuse to resume into output created under a different configuration."""

    seen: set[str] = set()
    for trajectory in trajectories:
        question_id = str(trajectory.task.question_id)
        if question_id in seen or question_id not in selected_ids:
            raise ValueError("Existing output has duplicate or stale task IDs")
        seen.add(question_id)
        config = trajectory.config
        checks = {
            "policy": trajectory.policy == f"{PROTOCOL_VERSION}:{policy}",
            "requested_model": str(config.get("requested_model", ""))
            == f"codex_subscription_proxy:{model}",
            "reasoning_effort": config.get("reasoning_effort") == reasoning_effort,
            "user_patience_budget": float(
                config.get("user_patience_budget", -1)
            )
            == user_patience_budget,
            "max_action_turns": int(config.get("max_action_turns", -1))
            == max_action_turns,
            "progress_guard": bool(config.get("progress_guard", False))
            == progress_guard,
            "guard_fingerprint": config.get("guard_fingerprint")
            == guard_fingerprint,
            "max_guard_retries": int(config.get("max_guard_retries", -1))
            == max_guard_retries,
        }
        failed = [name for name, passed in checks.items() if not passed]
        if failed:
            raise ValueError(
                f"Existing output is stale for {question_id}: {failed}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-data", type=Path, default=DEFAULT_PUBLIC_DATA)
    parser.add_argument("--private-merge", type=Path, default=DEFAULT_PRIVATE_MERGE)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument("--selection-manifest", type=Path)
    parser.add_argument("--instance-id", action="append", default=[])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--confirm-large-run", action="store_true")
    parser.add_argument("--policy", choices=sorted(POLICIES), default="mini_react_port")
    parser.add_argument("--user-patience-budget", type=float, default=6.0)
    parser.add_argument("--max-action-turns", type=int, default=60)
    parser.add_argument("--progress-guard", action="store_true")
    parser.add_argument(
        "--guard-fingerprint",
        choices=("bird_set", "visible_multiset", "ordered_visible"),
        default="bird_set",
    )
    parser.add_argument("--max-guard-retries", type=int, default=1)
    parser.add_argument(
        "--metric-horizon",
        type=int,
        default=4,
        help="Secondary project success-by-user-turn horizon.",
    )
    parser.add_argument("--model", default="gpt-5.6-luna")
    parser.add_argument(
        "--reasoning-effort",
        help="Required for a real model run; omitted safely for --dry-run.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def select_tasks(tasks, args: argparse.Namespace):
    requested_ids = list(args.instance_id)
    if args.selection_manifest:
        if requested_ids or args.limit is not None or args.all:
            raise ValueError(
                "--selection-manifest cannot be combined with IDs/limit/--all"
            )
        payload = json.loads(args.selection_manifest.read_text(encoding="utf-8"))
        requested_ids = [str(value) for value in payload["instance_ids"]]
    return select_explicit_tasks(
        tasks,
        requested_ids=requested_ids,
        offset=args.offset,
        limit=args.limit,
        run_all=args.all,
        confirmed_large_run=args.confirm_large_run,
    )


def main() -> int:
    args = parse_args()
    try:
        require_explicit_inference_settings(
            dry_run=args.dry_run,
            reasoning_effort=args.reasoning_effort,
        )
        tasks = load_official_public_tasks(args.public_data)
        selected = select_tasks(tasks, args)
        budget_provider = MiniInteractPrivateBudgetProvider.from_file(
            args.private_merge
        )
        budgets = [
            budget_provider.total_budget(
                task, user_patience_budget=args.user_patience_budget
            )
            for task in selected
        ]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"OFFICIAL MINI-INTERACT RUN REFUSED: {exc}")
        return 2

    print(
        f"Selected {len(selected)} task(s): "
        + ", ".join(str(task.question_id) for task in selected)
    )
    print(
        f"Protocol={PROTOCOL_VERSION}, policy={args.policy}, model={args.model}, "
        f"effort={args.reasoning_effort or 'unset (dry-run)'}"
    )
    print(
        "Official-formula bird-coin totals: "
        f"min={min(budgets):g}, mean={mean(budgets):.2f}, max={max(budgets):g}"
    )
    if args.dry_run:
        print("Dry run only: no model, simulator, evaluator, or SQL calls made.")
        return 0

    output = args.output or default_output(args.policy)
    artifact_root = output.with_suffix("").with_name(output.stem + "-artifacts")
    policy_proxy = CodexSubscriptionProxy(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        verbosity="low",
        instructions_file=Path(__file__).with_name("codex-policy-instructions.md"),
    )
    simulator_proxy = CodexSubscriptionProxy(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        verbosity="low",
        instructions_file=Path(__file__).with_name(
            "codex-user-simulator-instructions.md"
        ),
    )
    model = CodexOfficialPolicyModel(
        proxy=policy_proxy,
        artifact_directory=artifact_root / "policy",
    )
    simulator = SanitizedCodexMiniInteractUserSimulator(
        proxy=simulator_proxy,
        dataset_root=args.database_root,
        artifact_directory=artifact_root / "user-simulator-private",
    )
    oracle = OfficialMiniInteractLiveOracle.from_file(
        args.private_merge,
        database_root=args.database_root,
        answerer=simulator,
    )
    config = OfficialMiniInteractConfig(
        policy=args.policy,
        user_patience_budget=args.user_patience_budget,
        max_action_turns=args.max_action_turns,
        progress_guard=args.progress_guard,
        guard_fingerprint=args.guard_fingerprint,
        max_guard_retries=args.max_guard_retries,
    )
    controller = OfficialMiniInteractController(
        model=model,
        oracle=oracle,
        tools=OfficialMiniInteractTools.from_file(
            dataset_root=args.database_root,
            public_data=args.public_data,
        ),
        budget_provider=budget_provider,
        config=config,
    )

    existing = load_trajectories(output) if output.is_file() else []
    validate_existing_trajectories(
        existing,
        selected_ids={str(task.question_id) for task in selected},
        policy=args.policy,
        model=args.model,
        reasoning_effort=str(args.reasoning_effort),
        user_patience_budget=args.user_patience_budget,
        max_action_turns=args.max_action_turns,
        progress_guard=args.progress_guard,
        guard_fingerprint=args.guard_fingerprint,
        max_guard_retries=args.max_guard_retries,
    )
    completed = {str(trajectory.task.question_id) for trajectory in existing}
    for task in selected:
        if str(task.question_id) in completed:
            continue
        trajectory = controller.run(task)
        append_trajectory(output, trajectory)
        protocol = summarize_official_protocol([trajectory])
        print(
            f"- {task.question_id}: {trajectory.termination}; "
            f"success={protocol['successes']}/1; "
            f"coins={protocol['mean_bird_coins']:g}; "
            f"questions={protocol['mean_user_questions']:g}"
        )

    selected_ids = {str(task.question_id) for task in selected}
    trajectories = [
        trajectory
        for trajectory in load_trajectories(output)
        if str(trajectory.task.question_id) in selected_ids
    ]
    metrics: dict[str, Any] = {
        "official_protocol": summarize_official_protocol(trajectories),
        "secondary_policy_analysis": summarize_trajectories(
            trajectories, max_user_turns=args.metric_horizon
        ),
    }
    metrics_path = output.with_suffix(".metrics.json")
    metrics_path.write_text(
        json.dumps(metrics, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "status": "provisional_official_protocol_compatibility",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "protocol": {
            "version": PROTOCOL_VERSION,
            "official_source_revision": OFFICIAL_SOURCE_REVISION,
            "mode": "stress",
            "budget_formula": "12 + 2 * m_amb",
            "paper_lambda_pat": 3,
            "runner_user_patience_coins": args.user_patience_budget,
            "max_action_turns": args.max_action_turns,
            "max_user_questions": config.max_user_questions,
            "max_normal_submissions": config.max_normal_submissions,
            "evaluator": "official_live_agent_test_case_default",
            "claim": (
                "Local Mini-Interact Stress adaptation; the reference arm is a "
                "structured port and Sphinteract-derived arms are adaptations. "
                "This is not an official leaderboard submission."
            ),
            "known_adaptations": [
                "Structured JSON actions replace the reference XML/thought parser.",
                "Sphinteract timing/SRA is mapped into Mini actions and scoring.",
                "The simulator uses the Codex proxy plus deterministic leakage checks.",
                "The live scorer runs on an ephemeral copy with a parent timeout.",
                "Read-only SELECT/WITH is enforced for the public Query-only release.",
                "Malformed structured actions terminate without the release's 0.2-coin penalty.",
            ],
        },
        "trajectory_file": str(output.resolve()),
        "trajectory_sha256": sha256_file(output),
        "metrics_file": str(metrics_path.resolve()),
        "metrics_sha256": sha256_file(metrics_path),
        "public_data_sha256": sha256_file(args.public_data),
        "private_merge_sha256": sha256_file(args.private_merge),
        "private_data_redistributable": False,
        "private_artifacts_restricted": True,
        "clear_query_source": "ambiguous_query_fallback",
        "selected_instance_ids": [str(task.question_id) for task in selected],
        "policy": args.policy,
        "config": asdict(config),
        "model": {
            "surface": "codex_subscription_proxy",
            "id": args.model,
            "reasoning_effort": args.reasoning_effort,
            "verbosity": "low",
            "codex_cli_version": policy_proxy.codex_cli_version,
            "authentication": "chatgpt",
            "service_tier": "default",
            "ephemeral_per_call": True,
            "user_config_ignored": True,
            "tools_expected": False,
            "max_transport_retries": policy_proxy.max_transport_retries,
            "monetary_cost_available": False,
            "known_mismatch": (
                "Codex subscription proxy, not a direct Responses API call."
            ),
        },
        "simulator": {
            "adapter_version": SANITIZED_SIMULATOR_ADAPTER_VERSION,
            "sanitizer_version": SIMULATOR_SANITIZER_VERSION,
            "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "calls_per_answer": 2,
        },
        "implementation": {
            "controller_sha256": sha256_file(
                Path(__file__).with_name("official_protocol.py")
            ),
            "policy_adapter_sha256": sha256_file(
                Path(__file__).with_name("codex_official_policy.py")
            ),
            "official_reference_prompt_source_sha256": sha256_file(
                OFFICIAL_REFERENCE_PROMPT_SOURCE
            ),
            "runner_sha256": sha256_file(Path(__file__)),
            "simulator_adapter_sha256": sha256_file(
                Path(__file__).with_name("mini_interact.py")
            ),
            "oracle_adapter_sha256": sha256_file(
                Path(__file__).with_name("oracles.py")
            ),
            "live_evaluator_adapter_sha256": sha256_file(
                EXPERIMENT_ROOT / "readiness" / "mini_interact_live_eval.py"
            ),
            "metrics_adapter_sha256": sha256_file(
                Path(__file__).with_name("metrics.py")
            ),
            "policy_instructions_sha256": sha256_file(
                Path(__file__).with_name("codex-policy-instructions.md")
            ),
            "simulator_instructions_sha256": sha256_file(
                Path(__file__).with_name("codex-user-simulator-instructions.md")
            ),
        },
        "summary": metrics["official_protocol"],
    }
    manifest_path = output.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Trajectories: {output}")
    print(f"Metrics: {metrics_path}")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
