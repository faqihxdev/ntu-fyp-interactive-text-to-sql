"""Audit score-blind whole-task recovery without exporting private records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .bird_dev_matched import ARMS, PROTOCOL_VERSION
from .run_support import sha256_file


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ROOT = (
    EXPERIMENT_ROOT / "runs" / "bird-dev-matched-v4-full1534-luna-high-20260824"
)
DEFAULT_INITIAL = (
    DEFAULT_RUN_ROOT
    / "recovery"
    / "attempt-01"
    / "original-invalid-run"
    / "task-records.jsonl"
)
DEFAULT_ATTEMPT_01 = (
    DEFAULT_RUN_ROOT
    / "recovery"
    / "attempt-02"
    / "original-invalid-run"
    / "task-records.jsonl"
)
DEFAULT_FINAL = DEFAULT_RUN_ROOT / "task-records.jsonl"
DEFAULT_SELECTION = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-v4-full1534.json"
)
DEFAULT_FREEZE_01 = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-matched-v4-recovery-freeze-2026-08-25.json"
)
DEFAULT_FREEZE_02 = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-matched-v4-recovery-attempt-02-freeze-2026-08-25.json"
)
DEFAULT_JSON_OUTPUT = (
    EXPERIMENT_ROOT
    / "manifests"
    / "bird-dev-matched-v4-recovery-audit-2026-08-25.json"
)
DEFAULT_MARKDOWN_OUTPUT = (
    EXPERIMENT_ROOT
    / "notes"
    / "bird-dev-matched-v4-recovery-audit-2026-08-25.md"
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _record_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(_stable_json(record).encode("utf-8")).hexdigest()


def load_records(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            question_id = str(record.get("task", {}).get("question_id"))
            if question_id == "None" or question_id in records:
                raise ValueError(f"Invalid or duplicate question ID at {path}:{line_number}")
            records[question_id] = record
    if not records:
        raise ValueError(f"No records found: {path}")
    return records


def protocol_error_ids(records: dict[str, dict[str, Any]]) -> set[str]:
    """Select records using protocol-error fields only, never task outcomes."""

    return {
        question_id
        for question_id, record in records.items()
        if "task_error" in record
        or any(
            arm.get("termination") == "arm_error"
            for arm in (record.get("arms") or {}).values()
        )
    }


def _frozen_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {str(value) for value in payload["affected_question_ids"]}


def _selection_ids(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("question_ids")
    if not isinstance(values, list) or not values:
        raise ValueError(f"Selection has no question IDs: {path}")
    result = {str(value) for value in values}
    if len(result) != len(values):
        raise ValueError(f"Selection contains duplicate question IDs: {path}")
    return result


def _validate_protocol_records(
    label: str,
    records: dict[str, dict[str, Any]],
) -> None:
    wrong_protocol = [
        question_id
        for question_id, record in records.items()
        if record.get("protocol") != PROTOCOL_VERSION
    ]
    if wrong_protocol:
        raise ValueError(
            f"{label} contains records outside {PROTOCOL_VERSION}: "
            f"{wrong_protocol[:5]}"
        )


def _validate_complete_records(
    label: str,
    records: dict[str, dict[str, Any]],
    question_ids: set[str],
) -> None:
    incomplete = [
        question_id
        for question_id in sorted(question_ids)
        if set((records[question_id].get("arms") or {}).keys()) != set(ARMS)
    ]
    if incomplete:
        raise ValueError(
            f"{label} does not contain exactly five protocol arms for: "
            f"{incomplete[:5]}"
        )


def audit_recovery(
    *,
    initial_path: Path,
    attempt_01_path: Path,
    final_path: Path,
    selection_path: Path,
    freeze_01_path: Path,
    freeze_02_path: Path,
) -> dict[str, Any]:
    initial = load_records(initial_path)
    attempt_01 = load_records(attempt_01_path)
    final = load_records(final_path)
    expected_ids = _selection_ids(selection_path)
    if not (set(initial) == set(attempt_01) == set(final)):
        raise ValueError("Recovery generations do not contain the same task IDs")
    if set(final) != expected_ids:
        raise ValueError("Recovery generations do not match the frozen full selection")
    _validate_protocol_records("Initial generation", initial)
    _validate_protocol_records("Attempt-01 generation", attempt_01)
    _validate_protocol_records("Final generation", final)

    initial_errors = protocol_error_ids(initial)
    attempt_01_errors = protocol_error_ids(attempt_01)
    final_errors = protocol_error_ids(final)
    frozen_initial = _frozen_ids(freeze_01_path)
    frozen_attempt_01 = _frozen_ids(freeze_02_path)
    if initial_errors != frozen_initial:
        raise ValueError("Initial error-only selection does not match its freeze")
    if attempt_01_errors != frozen_attempt_01:
        raise ValueError("Attempt-01 error-only selection does not match its freeze")
    if final_errors:
        raise ValueError("Final aggregate still contains protocol errors")

    retained_original = set(initial) - initial_errors
    retained_attempt_01 = initial_errors - attempt_01_errors
    retained_attempt_02 = attempt_01_errors

    _validate_complete_records("Original retained generation", initial, retained_original)
    _validate_complete_records(
        "Attempt-01 retained generation", attempt_01, retained_attempt_01
    )
    _validate_complete_records("Final generation", final, set(final))

    def unchanged(left: dict[str, Any], right: dict[str, Any], ids: set[str]) -> bool:
        return all(_record_hash(left[value]) == _record_hash(right[value]) for value in ids)

    if not unchanged(initial, attempt_01, retained_original):
        raise ValueError("Attempt 01 changed a record outside its frozen selection")
    if not unchanged(initial, final, retained_original):
        raise ValueError("Final merge changed an original clean record")
    if not unchanged(attempt_01, final, retained_attempt_01):
        raise ValueError("Attempt 02 changed an attempt-01 clean replacement")
    if any(
        _record_hash(initial[value]) == _record_hash(attempt_01[value])
        for value in initial_errors
    ):
        raise ValueError("Attempt 01 failed to replace one of its selected records")
    if any(
        _record_hash(attempt_01[value]) == _record_hash(final[value])
        for value in retained_attempt_02
    ):
        raise ValueError("Attempt 02 failed to replace one of its selected records")

    return {
        "schema_version": 2,
        "artifact": "bird-dev-matched-v4-recovery-audit",
        "status": "passed",
        "model_calls": 0,
        "selection_rule": {
            "fields": ["task_error", "arms.*.termination == arm_error"],
            "excluded_fields": ["scores", "SQL", "task content", "user turns"],
        },
        "sources": {
            "initial_invalid_sha256": sha256_file(initial_path),
            "attempt_01_invalid_sha256": sha256_file(attempt_01_path),
            "final_sha256": sha256_file(final_path),
            "selection_sha256": sha256_file(selection_path),
            "freeze_01_sha256": sha256_file(freeze_01_path),
            "freeze_02_sha256": sha256_file(freeze_02_path),
        },
        "tasks": len(final),
        "initial_selected_ids": sorted(map(int, initial_errors)),
        "attempt_02_selected_ids": sorted(map(int, attempt_01_errors)),
        "lineage": {
            "retained_original": len(retained_original),
            "retained_attempt_01": len(retained_attempt_01),
            "retained_attempt_02": len(retained_attempt_02),
        },
        "checks": {
            "same_task_ids_across_generations": True,
            "task_ids_match_frozen_full_selection": True,
            "all_records_match_protocol": True,
            "initial_selection_matches_freeze": True,
            "attempt_02_selection_matches_freeze": True,
            "unselected_records_unchanged": True,
            "selected_records_replaced_in_their_generation": True,
            "selected_unit_is_complete_task_record": True,
            "final_records_have_exact_five_arm_set": True,
            "final_protocol_errors": 0,
        },
        "privacy": "Aggregate counts, public question IDs, and hashes only; no task text, SQL, answers, scores, or trajectories are exported.",
    }


def render_markdown(audit: dict[str, Any]) -> str:
    lineage = audit["lineage"]
    return "\n".join(
        [
            "# Original-BIRD v4 recovery audit",
            "",
            "**Status:** passed (read-only; zero model calls)",
            "",
            "Selection was recomputed using only `task_error` and arm termination "
            "equal to `arm_error`. The selected IDs exactly match both pre-recovery "
            "freeze manifests.",
            "",
            f"- Retained original records: {lineage['retained_original']}",
            f"- Retained attempt-01 replacements: {lineage['retained_attempt_01']}",
            f"- Retained attempt-02 replacements: {lineage['retained_attempt_02']}",
            "- Retained records are canonically JSON-equivalent across merges.",
            "- Every retained replacement has the exact five-arm protocol set.",
            "- The final aggregate contains zero task or arm protocol errors.",
            "",
            "The export contains only aggregate counts, public BIRD question IDs, "
            "and file hashes. It does not contain task text, SQL, answers, scores, "
            "or private trajectories.",
            "",
        ]
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initial", type=Path, default=DEFAULT_INITIAL)
    parser.add_argument("--attempt-01", type=Path, default=DEFAULT_ATTEMPT_01)
    parser.add_argument("--final", type=Path, default=DEFAULT_FINAL)
    parser.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parser.add_argument("--freeze-01", type=Path, default=DEFAULT_FREEZE_01)
    parser.add_argument("--freeze-02", type=Path, default=DEFAULT_FREEZE_02)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit = audit_recovery(
        initial_path=args.initial,
        attempt_01_path=args.attempt_01,
        final_path=args.final,
        selection_path=args.selection,
        freeze_01_path=args.freeze_01,
        freeze_02_path=args.freeze_02,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(render_markdown(audit), encoding="utf-8")
    print(f"Recovery audit passed for {audit['tasks']} tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
