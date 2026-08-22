"""Shared safety and provenance helpers for interaction runners."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

from .trajectory import BenchmarkTask

MAX_UNAPPROVED_TASKS = 10


def require_explicit_inference_settings(
    *, dry_run: bool, reasoning_effort: str | None
) -> None:
    """Prevent a real model run from inheriting an unreviewed effort preset."""

    if not dry_run and not (reasoning_effort or "").strip():
        raise ValueError(
            "--reasoning-effort is required for a model run; choose it explicitly "
            "after development calibration"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_explicit_tasks(
    tasks: Sequence[BenchmarkTask],
    *,
    requested_ids: list[str],
    offset: int,
    limit: int | None,
    run_all: bool,
    confirmed_large_run: bool,
) -> list[BenchmarkTask]:
    """Select tasks and require an approval flag above the small-run ceiling."""

    by_id = {str(task.question_id): task for task in tasks}
    if requested_ids:
        if limit is not None or run_all:
            raise ValueError("explicit IDs cannot be combined with --limit/--all")
        if len(requested_ids) != len(set(requested_ids)):
            raise ValueError("explicit IDs contain duplicates")
        missing = [value for value in requested_ids if value not in by_id]
        if missing:
            raise ValueError(f"Unknown task IDs: {missing}")
        selected = [by_id[value] for value in requested_ids]
    elif run_all:
        if not confirmed_large_run:
            raise ValueError("--all requires --confirm-large-run after user approval")
        selected = list(tasks)
    elif limit is None:
        raise ValueError("Select explicit IDs/--limit, or approved --all")
    else:
        if limit < 1 or offset < 0:
            raise ValueError("--limit must be positive and --offset non-negative")
        selected = list(tasks[offset : offset + limit])

    if len(selected) > MAX_UNAPPROVED_TASKS and not confirmed_large_run:
        raise ValueError(
            f"Selection has {len(selected)} tasks; more than "
            f"{MAX_UNAPPROVED_TASKS} requires --confirm-large-run after user approval"
        )
    return selected
