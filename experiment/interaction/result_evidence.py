"""Export retained BIRD records and text-free Mini-Interact verification data."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .analyze_standardized_results import (
    ARM_ORDER,
    _classify_protocol_error,
    _is_valid_sra,
    _official_action,
)
from .make_mini_interact_split import stratum
from .official_protocol import normalize_sra_action

EXPERIMENT = Path(__file__).resolve().parents[1]
PRIMARY = "api-bird-dev-clarification-value-v1-full1534-luna-high-20260830"
SUPPORTING = "api-bird-dev-matched-v4-full1534-luna-high-20260830"
MINI = "api-official-mini-stress-comparison-v2-luna-high-reserve240-20260830"
PRIMARY_RESULT = "api-bird-dev-clarification-value-v1-full1534-result-2026-08-30.json"
SUPPORTING_RESULT = "api-bird-dev-matched-v4-full1534-result-2026-08-30.json"
SUPPORTING_ANALYSIS = "api-bird-dev-matched-v4-public-analysis-2026-08-30.json"
PRIMARY_OUTCOMES = "api-bird-dev-clarification-value-v1-public-outcomes-2026-08-30.json"
PRIMARY_ANALYSIS = "api-bird-dev-clarification-value-v1-public-analysis-2026-08-30.json"
MINI_RESULT = "api-official-mini-interact-stress-comparison-v2-result-2026-08-30.json"
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "output_tokens",
    "reasoning_tokens",
    "total_tokens",
    "estimated_cost_usd",
)
EVENT_KINDS = frozenset(
    {
        "bird_coin_charge",
        "candidate_exposed",
        "database_probe",
        "environment_retrieval",
        "official_protocol_started",
        "policy_decision",
        "repeated_rejected_result",
        "sql_execution",
        "sra_no_ambiguity",
        "sra_normalization",
        "submission_judgement",
        "user_clarification",
        "model_protocol_error",
    }
)
TERMINATIONS = frozenset(
    {
        "accepted_submission",
        "forced_submission_rejected",
        "normal_submission_limit_exhausted",
        "sra_no_ambiguity_after_rejection",
        "user_question_limit_exhausted",
        "model_error",
        "model_protocol_error",
        "oracle_error",
    }
)
EXECUTION_STATES = frozenset(
    {
        "ok",
        "rejected_non_read_query",
        "sql_error",
        "timeout",
        "missing_status",
    }
)


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def rows_from_bytes(raw: bytes) -> list[dict[str, Any]]:
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def enum(value: Any, allowed: set[str] | frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError("Unexpected label in evidence export")
    return value


def number(value: Any) -> int | float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Expected a finite nonnegative count or usage value")
    return value


def normalization_label(value: Any) -> str:
    if value == "appended_open_option":
        return value
    if isinstance(value, str) and value.startswith("defaulted_reviews:"):
        labels = value.split(":", 1)[1].split(",")
        if labels and all(
            x in {"AmbQuestion", "AmbColumn", "AmbOutput", "AmbValue"} for x in labels
        ):
            return value
    raise ValueError("Unexpected normalization label in evidence export")


def project_mini_trajectory(
    record: dict[str, Any],
    public: dict[str, Any],
    arm: str,
) -> dict[str, Any]:
    """Copy only public identifiers, fixed labels, numbers and derived flags."""
    task_id = str(public["instance_id"])
    if str(record["task"]["question_id"]) != task_id:
        raise ValueError("Mini-Interact public/task ID mismatch")
    database = str(public["selected_database"])
    if database != record["task"]["db_id"]:
        raise ValueError("Mini-Interact database mismatch")
    rejected: set[str] = set()
    candidate = ""
    events = []
    for source in record["events"]:
        kind = enum(source["kind"], EVENT_KINDS)
        payload = source.get("payload") or {}
        event = {
            "sequence": number(source["sequence"]),
            "kind": kind,
            "turn": number(source["turn_index"]),
        }
        if source.get("usage") is not None:
            event["usage"] = {
                key: number(source["usage"].get(key, 0)) for key in USAGE_FIELDS
            }
        if kind == "policy_decision":
            event["calls"] = 1
            event["transport_attempts"] = number(payload.get("transport_attempts", 1))
            if payload.get("phase") in {"proactive_sra", "reactive_sra"}:
                action = _official_action(payload.get("action") or {})
                normalized, _ = normalize_sra_action(action)
                event["sra"] = {
                    "raw_valid": _is_valid_sra(action),
                    "normalized_valid": _is_valid_sra(normalized),
                }
        elif kind == "user_clarification":
            simulator = payload.get("simulator") or {}
            event["calls"] = number(simulator.get("calls", 0))
            event["transport_attempts"] = number(simulator.get("transport_attempts", 2))
        elif kind == "sra_normalization":
            event["changes"] = [
                normalization_label(x) for x in payload.get("changes", [])
            ]
        elif kind == "candidate_exposed":
            candidate = str(payload.get("sql") or "").strip()
            event["repeats_rejected_sql"] = bool(candidate and candidate in rejected)
        elif kind == "submission_judgement":
            event["accepted"] = payload.get("accepted") is True
            event["submission_kind"] = enum(
                payload["submission_kind"],
                {"normal", "invalid_sql_repair"},
            )
            if not event["accepted"] and candidate:
                rejected.add(candidate)
        elif kind == "sql_execution":
            event["status"] = enum(
                (payload.get("observation") or {}).get("status", "missing_status"),
                EXECUTION_STATES,
            )
        elif kind == "bird_coin_charge":
            event["cost"] = number(payload.get("cost", 0))
        elif kind == "model_protocol_error":
            event["category"] = _classify_protocol_error(str(payload.get("error", "")))
        events.append(event)
    return {
        "task_id": task_id,
        "arm": enum(arm, set(ARM_ORDER)),
        "database": database,
        "stratum": stratum(public),
        "termination": enum(record["termination"], TERMINATIONS),
        "events": events,
    }


def export(experiment: Path, output: Path) -> dict[str, Any]:
    """Verify source hashes before constructing any publication file."""
    manifests = experiment / "manifests"
    mini_result = load_json(manifests / MINI_RESULT)
    public_path = experiment / "datasets/mini-interact/mini_interact.jsonl"
    public_raw = public_path.read_bytes()
    if sha256(public_raw) != mini_result["integrity"]["public_data_sha256"]:
        raise ValueError("Public Mini-Interact data hash mismatch")
    public = {str(row["instance_id"]): row for row in rows_from_bytes(public_raw)}
    files: dict[str, bytes] = {}
    entries = {}
    for label, run, result_name in (
        ("primary", PRIMARY, PRIMARY_RESULT),
        ("supporting", SUPPORTING, SUPPORTING_RESULT),
    ):
        raw = (experiment / "runs" / run / "task-records.jsonl").read_bytes()
        result = load_json(manifests / result_name)
        if sha256(raw) != result["hashes"]["task_records_sha256"]:
            raise ValueError(f"{label} BIRD source hash mismatch")
        name = f"bird-{label}.jsonl.gz"
        files[name] = gzip.compress(raw, mtime=0)
        entries[name] = {"content_sha256": sha256(raw), "records": 1534}
    mini_source_hashes = {}
    projected = []
    for arm in ARM_ORDER:
        combined = experiment / "runs" / MINI / "combined"
        name = f"mini-{arm}.metrics.json"
        raw = (combined / f"{arm}.metrics.json").read_bytes()
        if sha256(raw) != mini_result["integrity"]["input_metric_sha256"][arm]:
            raise ValueError(f"{arm} metrics hash mismatch")
        files[name] = raw
        entries[name] = {"content_sha256": sha256(raw), "records": 240}
        trajectory_raw = (combined / f"{arm}.jsonl").read_bytes()
        digest = sha256(trajectory_raw)
        if digest != mini_result["integrity"]["input_trajectory_sha256"][arm]:
            raise ValueError(f"{arm} trajectory hash mismatch")
        mini_source_hashes[arm] = digest
        for row in rows_from_bytes(trajectory_raw):
            projected.append(
                project_mini_trajectory(
                    row,
                    public[str(row["task"]["question_id"])],
                    arm,
                )
            )
    raw = "".join(
        json.dumps(row, separators=(",", ":"), ensure_ascii=False) + "\n"
        for row in projected
    ).encode("utf-8")
    files["mini-events.jsonl.gz"] = gzip.compress(raw, mtime=0)
    entries["mini-events.jsonl.gz"] = {"content_sha256": sha256(raw), "records": 960}
    manifest = {
        "schema_version": 1,
        "files": {
            name: {**entries[name], "sha256": sha256(raw), "bytes": len(raw)}
            for name, raw in files.items()
        },
        "mini_source_trajectory_sha256": mini_source_hashes,
        "mini_public_data_sha256": sha256(public_raw),
        "mini_event_fields": (
            "Public task/database IDs and strata, event order, outcomes, usage, "
            "fixed status labels, and derived SRA/repeated-SQL flags."
        ),
        "verification_scope": (
            "Recalculate statistics from recorded outcomes. Mini event flags are "
            "derived from withheld text; this package cannot independently rescore "
            "Mini SQL or inspect its question/answer content."
        ),
    }
    files["evidence.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    for name, raw in files.items():
        path = output / name
        if path.exists() and path.read_bytes() != raw:
            raise ValueError(
                f"Existing evidence differs: {name}; choose a new output directory"
            )
    output.mkdir(parents=True, exist_ok=True)
    for name, raw in files.items():
        (output / name).write_bytes(raw)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=EXPERIMENT)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.experiment, args.output)
    print(f"Exported {len(result['files'])} evidence files to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
