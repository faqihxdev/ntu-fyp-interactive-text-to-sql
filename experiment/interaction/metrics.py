"""Interaction metrics, including the project-defined EX–turn curve."""

from __future__ import annotations

from collections.abc import Mapping
from statistics import mean, median
from typing import Any

from .controller import REVIEW_SOURCES
from .trajectory import Trajectory


def trajectory_record(
    trajectory: Trajectory,
    *,
    externally_correct: bool | None = None,
) -> dict[str, Any]:
    """Reduce one gold-free trajectory plus an optional separate EX verdict."""

    accepted_events = [
        event
        for event in trajectory.events
        if event.kind == "submission_judgement"
        and event.payload.get("accepted") is True
    ]
    accepted = bool(accepted_events)
    correct = accepted if externally_correct is None else bool(externally_correct)
    success_turn: int | None = None
    success_submission_attempt: int | None = None
    if accepted_events:
        first_accepted = min(accepted_events, key=lambda event: event.sequence)
        success_turn = first_accepted.turn_index
        success_submission_attempt = sum(
            event.kind in {"candidate_exposed", "final_submission"}
            and event.sequence < first_accepted.sequence
            for event in trajectory.events
        )
    elif correct:
        success_turn = max(
            (event.turn_index for event in trajectory.events),
            default=0,
        )
        success_submission_attempt = 1

    repeats = [
        event for event in trajectory.events if event.kind == "repeated_rejected_result"
    ]
    clarification_events = [
        event for event in trajectory.events if event.kind == "user_clarification"
    ]
    simulator_actions = [
        str((event.payload.get("simulator") or {}).get("encoder_action", ""))
        for event in clarification_events
    ]
    simulator_calls = sum(
        int((event.payload.get("simulator") or {}).get("calls", 0))
        for event in clarification_events
    )
    policy_calls = sum(
        event.kind in {"policy_decision", "model_response"}
        for event in trajectory.events
    )
    transport_retries = sum(
        max(0, int(event.payload.get("transport_attempts", 1)) - 1)
        for event in trajectory.events
        if event.kind in {"policy_decision", "model_response"}
    ) + sum(
        max(
            0,
            int((event.payload.get("simulator") or {}).get("transport_attempts", 2))
            - int((event.payload.get("simulator") or {}).get("calls", 2)),
        )
        for event in clarification_events
    )
    seen_user_turns: set[int] = set()
    grounded_actions = 0
    compliant_grounded_actions = 0
    for event in trajectory.events:
        if event.kind == "user_clarification":
            seen_user_turns.add(int(event.turn_index))
            continue
        if event.kind != "policy_decision" or not seen_user_turns:
            continue
        action = event.payload.get("action") or {}
        if action.get("kind") not in {
            "ask_user",
            "query_database",
            "submit_sql",
        }:
            continue
        grounded_actions += 1
        grounded = {int(value) for value in action.get("grounded_user_turns") or []}
        if grounded == seen_user_turns:
            compliant_grounded_actions += 1
    review_sources = []
    for event in trajectory.events:
        if event.kind != "policy_decision":
            continue
        action = event.payload.get("action") or {}
        action_kind = action.get("kind")
        if action_kind == "ask_user" or (
            action_kind == "ask"
            and event.payload.get("phase") in {"proactive_sra", "reactive_sra"}
        ):
            review_sources.append(str(action.get("review_source", "")))
    usage = trajectory.total_usage
    return {
        "question_id": trajectory.task.question_id,
        "policy": trajectory.policy,
        "correct": correct,
        "oracle_accepted": accepted,
        "success_turn": success_turn,
        "success_submission_attempt": success_submission_attempt,
        "first_submission_success": success_submission_attempt == 1,
        "user_turns": len(clarification_events),
        "policy_model_calls": policy_calls,
        "simulator_model_calls": simulator_calls,
        "total_inference_calls": policy_calls + simulator_calls,
        "transport_retries": transport_retries,
        # Retained for metrics-v1 consumers; it always meant policy calls.
        "model_calls": policy_calls,
        "labeled_clarifications": simulator_actions.count("labeled"),
        "unlabeled_clarifications": simulator_actions.count("unlabeled"),
        "unanswerable_clarifications": simulator_actions.count("unanswerable"),
        "targeted_clarifications": sum(
            bool(str(event.payload.get("clarification_target") or "").strip())
            for event in clarification_events
        ),
        "grounded_actions": grounded_actions,
        "compliant_grounded_actions": compliant_grounded_actions,
        "recovery_protocol_retries": sum(
            event.kind == "recovery_protocol_retry" for event in trajectory.events
        ),
        "post_turn_recovery_submissions": sum(
            event.kind == "post_turn_recovery_submission" for event in trajectory.events
        ),
        "sra_review_sources": {
            source: review_sources.count(source) for source in sorted(REVIEW_SOURCES)
        },
        "database_probes": sum(
            event.kind == "database_probe" for event in trajectory.events
        ),
        "submission_attempts": sum(
            event.kind in {"candidate_exposed", "final_submission"}
            for event in trajectory.events
        ),
        "repeated_rejected_results": len(repeats),
        "guard_internal_retries": sum(
            event.payload.get("guard_action") == "internal_retry" for event in repeats
        ),
        "same_sql_repeats": sum(
            event.payload.get("same_normalized_sql") is True for event in repeats
        ),
        "sparse_result_repeats": sum(
            event.payload.get("sparse_result_kind") is not None for event in repeats
        ),
        "termination": trajectory.termination,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": usage.reasoning_tokens,
        "total_tokens": usage.total_tokens,
        "estimated_cost_usd": usage.estimated_cost_usd,
        "elapsed_ms": float(trajectory.total_elapsed_ms or 0.0),
    }


def _summary(values: list[int | float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "total": 0.0}
    return {
        "mean": round(float(mean(values)), 6),
        "median": round(float(median(values)), 6),
        "total": round(float(sum(values)), 6),
    }


def summarize_records(
    records: list[dict[str, Any]],
    *,
    max_user_turns: int,
) -> dict[str, Any]:
    if max_user_turns < 0:
        raise ValueError("max_user_turns must be non-negative")
    total = len(records)
    curve: list[dict[str, int | float]] = []
    for turn in range(max_user_turns + 1):
        correct = sum(
            record["success_turn"] is not None and int(record["success_turn"]) <= turn
            for record in records
        )
        curve.append(
            {
                "user_turn": turn,
                "correct": correct,
                "execution_accuracy_percent": (
                    round(100 * correct / total, 4) if total else 0.0
                ),
            }
        )
    rates = [(float(point["correct"]) / total if total else 0.0) for point in curve]
    if max_user_turns == 0:
        normalized_auc = rates[0] if rates else 0.0
    else:
        normalized_auc = (
            sum(
                (rates[index] + rates[index + 1]) / 2 for index in range(max_user_turns)
            )
            / max_user_turns
        )

    correct = sum(bool(record["correct"]) for record in records)
    repeats = sum(int(record["repeated_rejected_results"]) for record in records)
    labeled_clarifications = sum(
        int(record["labeled_clarifications"]) for record in records
    )
    unlabeled_clarifications = sum(
        int(record["unlabeled_clarifications"]) for record in records
    )
    unanswerable_clarifications = sum(
        int(record["unanswerable_clarifications"]) for record in records
    )
    grounded_actions = sum(int(record["grounded_actions"]) for record in records)
    compliant_grounded_actions = sum(
        int(record["compliant_grounded_actions"]) for record in records
    )
    classified_clarifications = (
        labeled_clarifications + unlabeled_clarifications + unanswerable_clarifications
    )
    sra_review_sources = {
        source: sum(
            int(record["sra_review_sources"].get(source, 0)) for record in records
        )
        for source in sorted(REVIEW_SOURCES)
    }
    return {
        "tasks": total,
        "correct": correct,
        "final_execution_accuracy_percent": (
            round(100 * correct / total, 4) if total else 0.0
        ),
        "ex_by_user_turn": curve,
        "normalized_ex_user_turn_auc": round(normalized_auc, 6),
        "tasks_with_user_interaction": sum(
            record["user_turns"] > 0 for record in records
        ),
        "first_submission_successes": sum(
            record["first_submission_success"] for record in records
        ),
        "tasks_with_repeated_result": sum(
            record["repeated_rejected_results"] > 0 for record in records
        ),
        "repeated_rejected_results": repeats,
        "guard_internal_retries": sum(
            int(record["guard_internal_retries"]) for record in records
        ),
        "same_sql_repeats": sum(int(record["same_sql_repeats"]) for record in records),
        "sparse_result_repeats": sum(
            int(record["sparse_result_repeats"]) for record in records
        ),
        "user_turns": _summary([int(record["user_turns"]) for record in records]),
        "policy_model_calls": _summary(
            [int(record["policy_model_calls"]) for record in records]
        ),
        "simulator_model_calls": _summary(
            [int(record["simulator_model_calls"]) for record in records]
        ),
        "total_inference_calls": _summary(
            [int(record["total_inference_calls"]) for record in records]
        ),
        "transport_retries": _summary(
            [int(record["transport_retries"]) for record in records]
        ),
        "clarification_answerability": {
            "classified": classified_clarifications,
            "labeled": labeled_clarifications,
            "unlabeled": unlabeled_clarifications,
            "unanswerable": unanswerable_clarifications,
            "answerable_precision": (
                round(
                    (labeled_clarifications + unlabeled_clarifications)
                    / classified_clarifications,
                    6,
                )
                if classified_clarifications
                else None
            ),
            "scope": (
                "Mini-Interact simulator classification only; not a human rating "
                "and not ask recall."
            ),
        },
        "feedback_grounding": {
            "targeted_clarifications": sum(
                int(record["targeted_clarifications"]) for record in records
            ),
            "eligible_actions": grounded_actions,
            "compliant_actions": compliant_grounded_actions,
            "compliance_rate": (
                round(compliant_grounded_actions / grounded_actions, 6)
                if grounded_actions
                else None
            ),
            "protocol_retries": sum(
                int(record["recovery_protocol_retries"]) for record in records
            ),
            "post_turn_recovery_submissions": sum(
                int(record["post_turn_recovery_submissions"]) for record in records
            ),
            "scope": (
                "Structural acknowledgement of prior answers; it does not prove "
                "semantic SQL correctness."
            ),
        },
        "sra_review_sources": sra_review_sources,
        "model_calls": _summary([int(record["model_calls"]) for record in records]),
        "database_probes": _summary(
            [int(record["database_probes"]) for record in records]
        ),
        "submission_attempts": _summary(
            [int(record["submission_attempts"]) for record in records]
        ),
        "total_tokens": _summary([int(record["total_tokens"]) for record in records]),
        "estimated_cost_usd": _summary(
            [float(record["estimated_cost_usd"]) for record in records]
        ),
        "elapsed_ms": _summary([float(record["elapsed_ms"]) for record in records]),
    }


def summarize_trajectories(
    trajectories: list[Trajectory],
    *,
    max_user_turns: int,
    external_correctness: Mapping[int | str, bool] | None = None,
) -> dict[str, Any]:
    records = [
        trajectory_record(
            trajectory,
            externally_correct=(
                external_correctness.get(trajectory.task.question_id)
                if external_correctness is not None
                else None
            ),
        )
        for trajectory in trajectories
    ]
    return {
        "metric_version": "interaction-metrics-v3",
        "auc_definition": (
            "Trapezoidal area under cumulative execution accuracy at user turns "
            f"0..{max_user_turns}, divided by {max_user_turns or 1}."
        ),
        "summary": summarize_records(records, max_user_turns=max_user_turns),
        "records": records,
    }
