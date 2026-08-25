"""Run all no-model readiness checks for the original-BIRD matched bridge."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
from statistics import mean

from .bird_dev_matched import (
    ARMS,
    PROTOCOL_VERSION,
    BirdDevPrivateEvaluator,
    build_schema_only_context,
    load_original_bird_rows,
    load_original_bird_tasks,
)
from .run_bird_dev_matched import (
    DEFAULT_DATABASE_ROOT,
    DEFAULT_DATASET,
    DEFAULT_SPH_NOTEBOOK,
    EXPERIMENT_ROOT,
)
from .run_support import sha256_file
from .sphinteract_source import SPHINTERACT_COMMIT, load_sphinteract_prompts


DEFAULT_OUTPUT = (
    EXPERIMENT_ROOT / "manifests" / "bird-dev-matched-v4-readiness-2026-08-24.json"
)
KNOWN_GOLD_TIMEOUT_IDS = {701}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--sph-notebook", type=Path, default=DEFAULT_SPH_NOTEBOOK)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = load_original_bird_rows(args.dataset)
    tasks = load_original_bird_tasks(args.dataset)
    if len(tasks) != 1534:
        raise ValueError(f"Expected 1,534 BIRD tasks, found {len(tasks)}")
    prompts = load_sphinteract_prompts(args.sph_notebook)
    database_ids = sorted({task.db_id for task in tasks})
    database_files = {
        database_id: args.database_root / database_id / f"{database_id}.sqlite"
        for database_id in database_ids
    }
    schemas = {
        database_id: build_schema_only_context(path)
        for database_id, path in database_files.items()
    }
    evaluator = BirdDevPrivateEvaluator(
        rows=rows,
        database_root=args.database_root,
        timeout_seconds=120.0,
        max_rows=500_000,
    )

    def check(task):
        row = evaluator.private_row(task)
        scored, _ = evaluator.evaluate(task, str(row["SQL"]))
        return {
            "question_id": task.question_id,
            "status": scored["status"],
            "truncated": scored["truncated"],
            "official_self_match": scored["official_ex"],
            "sph_self_match": scored["sph_compatible_ex"],
        }

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        checks = list(pool.map(check, tasks))
    statuses = Counter(str(check["status"]) for check in checks)
    failures = [
        check
        for check in checks
        if check["status"] != "ok"
        or check["truncated"]
        or not check["official_self_match"]
        or not check["sph_self_match"]
    ]
    unexpected_failures = [
        check
        for check in failures
        if int(check["question_id"]) not in KNOWN_GOLD_TIMEOUT_IDS
        or check["status"] != "timeout"
    ]
    if unexpected_failures:
        status = "failed"
    elif failures:
        status = "passed_with_known_full_run_timeout"
    else:
        status = "passed"
    payload = {
        "schema_version": 1,
        "protocol": PROTOCOL_VERSION,
        "arms": list(ARMS),
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "dataset": {
            "path": "codebase/experiment/datasets/bird-dev/dev.json",
            "sha256": sha256_file(args.dataset),
            "tasks": len(tasks),
            "databases": len(database_ids),
        },
        "database_files": {
            database_id: {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
                "schema_chars": len(schemas[database_id]),
            }
            for database_id, path in database_files.items()
        },
        "schema_context": {
            "mode": "CREATE TABLE statements only; no values, descriptions, or evidence",
            "mean_chars_per_database": mean(map(len, schemas.values())),
            "all_nonempty": all(schemas.values()),
        },
        "sphinteract_source": {
            "commit": SPHINTERACT_COMMIT,
            "notebook_sha256": sha256_file(args.sph_notebook),
            "literal_prompt_lengths": {
                "clarification_prefix": len(prompts.clarification_prefix),
                "feedback_prefix": len(prompts.feedback_prefix),
                "sql_initial": len(prompts.sql_initial),
            },
        },
        "gold_execution": {
            "status_counts": dict(statuses),
            "failures": failures,
            "unexpected_failures": unexpected_failures,
            "known_timeout_policy": {
                "question_ids": sorted(KNOWN_GOLD_TIMEOUT_IDS),
                "local_measurement": (
                    "Question 701's released gold SQL timed out at both 120 and "
                    "300 seconds on 2026-08-24. It remains in the denominator as "
                    "a scorer-timeout failure; it is absent from smoke10 and "
                    "pilot100. Any final full run must report this caveat."
                ),
            },
            "official_self_matches": sum(
                bool(check["official_self_match"]) for check in checks
            ),
            "sph_compatible_self_matches": sum(
                bool(check["sph_self_match"]) for check in checks
            ),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        f"Readiness {payload['status']}: tasks={len(tasks)}; "
        f"statuses={dict(statuses)}; failures={len(failures)}; "
        f"unexpected={len(unexpected_failures)}"
    )
    print(f"Manifest: {args.output}")
    return 0 if not unexpected_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
