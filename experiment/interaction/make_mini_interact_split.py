"""Create a deterministic public-only Mini-Interact pilot/confirmation split."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .mini_interact import load_jsonl
from .run_support import sha256_file

EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PUBLIC_DATA = (
    EXPERIMENT_ROOT / "datasets" / "mini-interact" / "mini_interact.jsonl"
)
DEFAULT_PILOT = EXPERIMENT_ROOT / "manifests" / "mini-interact-pilot.json"
DEFAULT_CONFIRMATION = EXPERIMENT_ROOT / "manifests" / "mini-interact-confirmation.json"
SELECTION_VERSION = "mini-interact-public-development-split-v1"
DEFAULT_SEED = 20260820
FORCED_DEVELOPMENT_IDS = ("alien_1",)


def ambiguity_count(row: dict[str, Any]) -> int:
    user_ambiguity = row.get("user_query_ambiguity") or {}
    return len(user_ambiguity.get("critical_ambiguity") or []) + len(
        row.get("knowledge_ambiguity") or []
    )


def ambiguity_bucket(count: int) -> str:
    if count <= 2:
        return "1-2"
    if count <= 4:
        return "3-4"
    return "5+"


def stratum(row: dict[str, Any]) -> str:
    level = "high" if bool(row.get("high_level")) else "low"
    return f"{level}:{ambiguity_bucket(ambiguity_count(row))}"


def stable_rank(seed: int, instance_id: str) -> str:
    return hashlib.sha256(f"{seed}:{instance_id}".encode()).hexdigest()


def choose_pilot(
    rows: list[dict[str, Any]],
    *,
    pilot_size: int,
    seed: int,
    forced_ids: tuple[str, ...] = FORCED_DEVELOPMENT_IDS,
) -> list[str]:
    if not 1 <= pilot_size < len(rows):
        raise ValueError("pilot size must be between one and dataset size minus one")
    by_id = {str(row["instance_id"]): row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Mini-Interact instance IDs must be unique")
    missing = [value for value in forced_ids if value not in by_id]
    if missing:
        raise ValueError(f"Forced development IDs are absent: {missing}")
    if len(forced_ids) > pilot_size:
        raise ValueError("pilot size is smaller than the forced development set")

    selected = list(forced_ids)
    selected_set = set(selected)
    used_databases = {str(by_id[value]["selected_database"]) for value in selected}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row["instance_id"]) not in selected_set:
            grouped[stratum(row)].append(row)
    for candidates in grouped.values():
        candidates.sort(key=lambda row: stable_rank(seed, str(row["instance_id"])))

    strata = sorted(grouped)
    while len(selected) < pilot_size:
        made_progress = False
        for name in strata:
            candidates = grouped[name]
            index = next(
                (
                    i
                    for i, row in enumerate(candidates)
                    if str(row["selected_database"]) not in used_databases
                ),
                None,
            )
            if index is None:
                continue
            row = candidates.pop(index)
            instance_id = str(row["instance_id"])
            selected.append(instance_id)
            selected_set.add(instance_id)
            used_databases.add(str(row["selected_database"]))
            made_progress = True
            if len(selected) == pilot_size:
                break
        if not made_progress:
            break

    if len(selected) < pilot_size:
        remaining = sorted(
            (row for row in rows if str(row["instance_id"]) not in selected_set),
            key=lambda row: stable_rank(seed, str(row["instance_id"])),
        )
        selected.extend(
            str(row["instance_id"]) for row in remaining[: pilot_size - len(selected)]
        )
    return selected


def manifest(
    *,
    purpose: str,
    public_data: Path,
    seed: int,
    instance_ids: list[str],
    rows_by_id: dict[str, dict[str, Any]],
    excluded_instance_ids: list[str],
) -> dict[str, Any]:
    strata = Counter(stratum(rows_by_id[value]) for value in instance_ids)
    return {
        "schema_version": 1,
        "selection_version": SELECTION_VERSION,
        "purpose": purpose,
        "public_data": {
            "path": str(public_data.resolve()),
            "sha256": sha256_file(public_data),
        },
        "selection_uses_private_fields": False,
        "seed": seed,
        "method": (
            "Force already-inspected development IDs, then cycle over public "
            "high_level x ambiguity-count strata while preferring unseen databases; "
            "rank ties by SHA-256(seed:instance_id)."
        ),
        "forced_development_ids": list(FORCED_DEVELOPMENT_IDS),
        "instance_ids": instance_ids,
        "excluded_instance_ids": excluded_instance_ids,
        "count": len(instance_ids),
        "database_count": len(
            {str(rows_by_id[value]["selected_database"]) for value in instance_ids}
        ),
        "strata": dict(sorted(strata.items())),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-data", type=Path, default=DEFAULT_PUBLIC_DATA)
    parser.add_argument("--pilot-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--pilot-output", type=Path, default=DEFAULT_PILOT)
    parser.add_argument(
        "--confirmation-output", type=Path, default=DEFAULT_CONFIRMATION
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_jsonl(args.public_data)
    rows_by_id = {str(row["instance_id"]): row for row in rows}
    pilot_ids = choose_pilot(rows, pilot_size=args.pilot_size, seed=args.seed)
    pilot_set = set(pilot_ids)
    confirmation_ids = [
        str(row["instance_id"])
        for row in rows
        if str(row["instance_id"]) not in pilot_set
    ]
    pilot = manifest(
        purpose="development_only_excluded_from_confirmation",
        public_data=args.public_data,
        seed=args.seed,
        instance_ids=pilot_ids,
        rows_by_id=rows_by_id,
        excluded_instance_ids=[],
    )
    confirmation = manifest(
        purpose="held_out_policy_confirmation",
        public_data=args.public_data,
        seed=args.seed,
        instance_ids=confirmation_ids,
        rows_by_id=rows_by_id,
        excluded_instance_ids=pilot_ids,
    )
    for path, payload in (
        (args.pilot_output, pilot),
        (args.confirmation_output, confirmation),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"{path}: {payload['count']} IDs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
