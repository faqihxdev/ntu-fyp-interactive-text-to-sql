"""Reproduce and verify the private Mini-Interact merge safely on Windows."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

if __package__:
    from .check_datasets import (
        DEFAULT_DATASETS_ROOT,
        DEFAULT_MINI_PRIVATE_GT,
        DEFAULT_MINI_PRIVATE_MERGED,
        ReadinessFailure,
        check_mini_interact_private,
    )
else:
    from check_datasets import (
        DEFAULT_DATASETS_ROOT,
        DEFAULT_MINI_PRIVATE_GT,
        DEFAULT_MINI_PRIVATE_MERGED,
        ReadinessFailure,
        check_mini_interact_private,
    )


DEFAULT_PUBLIC = DEFAULT_DATASETS_ROOT / "mini-interact" / "mini_interact.jsonl"
DEFAULT_OFFICIAL_SCRIPT = (
    DEFAULT_DATASETS_ROOT / "bird-interact-evaluator" / "combine_public_with_gt.py"
)


def run_official_merge(
    public_file: Path,
    private_gt_file: Path,
    output_file: Path,
    official_script: Path,
    *,
    force: bool,
) -> None:
    if not official_script.is_file():
        raise ReadinessFailure(f"Official merge script is missing: {official_script}")
    if output_file.exists() and not force:
        raise ReadinessFailure(
            f"Output already exists: {output_file}. Use --force only after verifying the target."
        )
    output_file.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(official_script),
            str(public_file),
            str(private_gt_file),
            str(output_file),
        ],
        check=True,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public", type=Path, default=DEFAULT_PUBLIC)
    parser.add_argument("--private-gt", type=Path, default=DEFAULT_MINI_PRIVATE_GT)
    parser.add_argument("--output", type=Path, default=DEFAULT_MINI_PRIVATE_MERGED)
    parser.add_argument("--official-script", type=Path, default=DEFAULT_OFFICIAL_SCRIPT)
    parser.add_argument(
        "--merge",
        action="store_true",
        help="Run the pinned official merger before validation; otherwise verify only.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow --merge to replace the exact output path.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.merge:
            run_official_merge(
                args.public,
                args.private_gt,
                args.output,
                args.official_script,
                force=args.force,
            )
        result = check_mini_interact_private(
            args.public,
            args.private_gt,
            args.output,
        )
        if result["status"] != "pass":
            raise ReadinessFailure(str(result))
    except (ReadinessFailure, subprocess.CalledProcessError) as exc:
        print(f"MINI-INTERACT PREPARATION FAIL: {exc}")
        return 1

    merge = result["canonical_merge"]
    print("Mini-Interact private merge: PASS")
    print(f"- rows: {result['rows']}")
    print(f"- public field mismatches: {result['public_field_mismatches']}")
    print(f"- custom test-case rows: {merge['custom_test_case_rows']}")
    print(f"- default fallback rows: {merge['default_execution_fallback_rows']}")
    print(f"- SHA-256: {merge['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
