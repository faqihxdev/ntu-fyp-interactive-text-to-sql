"""Create the thesis figure for the frozen clarification-value experiment.

This post-analysis utility reads aggregate JSON only. It makes no model,
simulator, evaluator, or database calls.
"""

from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
from typing import Any


ARMS = ("no_user", "sql_only_recovery", "reactive_clarification")
LABELS = {
    "no_user": "No user",
    "sql_only_recovery": "SQL-only recovery",
    "reactive_clarification": "Reactive clarification",
}
COLORS = {
    "no_user": "#6B7280",
    "sql_only_recovery": "#E69F00",
    "reactive_clarification": "#0072B2",
}


def _figure_data(source: dict[str, Any]) -> dict[str, Any]:
    """Normalize a frozen aggregate or private read-only analysis for plotting."""

    if source.get("artifact") == "bird-dev-clarification-value-v1-full-result":
        validity = source["validity"]
        primary_source = source["primary_contrast"]
        sensitivity_source = source["development_unexposed_sensitivity"]
        return {
            "tasks": int(validity["tasks"]),
            "audit_passed": bool(validity["mechanical_audit_passed"]),
            "initial_failures": int(
                validity["initial_failures_available_for_recovery"]
            ),
            "arms": source["arms"],
            "primary": {
                "difference_percentage_points": float(
                    primary_source["difference_percentage_points_all_tasks"]
                ),
                "task_bootstrap_95_ci_percentage_points": list(
                    primary_source["task_bootstrap_95_ci_percentage_points"]
                ),
            },
            "sensitivity": {
                "tasks": int(sensitivity_source["tasks"]),
                "difference_percentage_points": float(
                    sensitivity_source[
                        "reactive_minus_sql_only_percentage_points"
                    ]
                ),
            },
        }

    return {
        "tasks": int(source.get("run", {}).get("tasks", 0)),
        "audit_passed": bool(source.get("mechanical_audit", {}).get("passed")),
        "initial_failures": int(
            source.get("initial_sql", {}).get("failures_available_for_recovery", 0)
        ),
        "arms": source["arms"],
        "primary": source["paired_comparisons_all_tasks"][
            "reactive_minus_sql_only"
        ],
        "sensitivity": {
            "tasks": int(source["development_exposure_sensitivity"]["tasks"]),
            **source["development_exposure_sensitivity"][
                "paired_comparisons_all_tasks"
            ]["reactive_minus_sql_only"],
        },
    }


def _text(
    x: float,
    y: float,
    value: str,
    *,
    size: int = 24,
    weight: int = 400,
    anchor: str = "start",
    fill: str = "#17212B",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
        f'fill="{fill}">{escape(value)}</text>'
    )


def _axis(
    parts: list[str],
    *,
    left: float,
    right: float,
    top: float,
    bottom: float,
    maximum: float,
    ticks: tuple[int, ...],
    suffix: str,
) -> None:
    width = right - left
    for tick in ticks:
        x = left + width * tick / maximum
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{bottom}" '
            'stroke="#E3E7EB" stroke-width="1"/>'
        )
        parts.append(
            _text(x, bottom + 34, f"{tick}{suffix}", size=18, anchor="middle", fill="#52606D")
        )
    parts.append(
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" '
        'stroke="#8A96A3" stroke-width="1.5"/>'
    )


def render(analysis: dict[str, Any]) -> str:
    if analysis.get("protocol") != "bird-dev-clarification-value-v1":
        raise ValueError("Unexpected clarification-value protocol")
    data = _figure_data(analysis)
    if data["tasks"] != 1534:
        raise ValueError("Expected the complete 1,534-task analysis")
    if not data["audit_passed"]:
        raise ValueError("Mechanical audit must pass before plotting")

    width, height = 1600, 900
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img">',
        "<title>Matched clarification value on BIRD Dev</title>",
        "<desc>Execution accuracy and recovery after the shared initial-attempt prefix for three matched arms.</desc>",
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        _text(
            90,
            70,
            f"Matched clarification value on {data['tasks']:,} BIRD Dev tasks",
            size=42,
            weight=500,
        ),
        _text(
            90,
            112,
            "One physical initial-attempt prefix; equal five-generation cap for both recovery arms.",
            size=22,
            fill="#52606D",
        ),
        '<line x1="800" y1="160" x2="800" y2="690" stroke="#D8DEE4" stroke-width="2"/>',
    ]

    # Panel A: final execution accuracy with bootstrap intervals.
    parts.extend(
        [
            _text(90, 186, "A  Final execution accuracy", size=28, weight=600),
            _text(90, 220, "Official BIRD set-of-rows equality", size=19, fill="#52606D"),
        ]
    )
    left_a, right_a, top_a, bottom_a = 280.0, 735.0, 270.0, 590.0
    _axis(
        parts,
        left=left_a,
        right=right_a,
        top=top_a,
        bottom=bottom_a,
        maximum=80,
        ticks=(0, 20, 40, 60, 80),
        suffix="%",
    )
    for index, arm in enumerate(ARMS):
        y = 325 + index * 105
        summary = data["arms"][arm]
        value = 100 * float(summary["official_ex"])
        lower, upper = [100 * float(item) for item in summary["task_bootstrap_95_ci"]]
        value_x = left_a + (right_a - left_a) * value / 80
        lower_x = left_a + (right_a - left_a) * lower / 80
        upper_x = left_a + (right_a - left_a) * upper / 80
        parts.extend(
            [
                _text(left_a - 18, y + 8, LABELS[arm], size=21, anchor="end"),
                f'<rect x="{left_a}" y="{y - 22}" width="{value_x - left_a:.1f}" '
                f'height="44" rx="4" fill="{COLORS[arm]}" fill-opacity="0.88"/>',
                f'<line x1="{lower_x:.1f}" y1="{y}" x2="{upper_x:.1f}" y2="{y}" '
                'stroke="#17212B" stroke-width="3"/>',
                f'<line x1="{lower_x:.1f}" y1="{y - 10}" x2="{lower_x:.1f}" y2="{y + 10}" '
                'stroke="#17212B" stroke-width="3"/>',
                f'<line x1="{upper_x:.1f}" y1="{y - 10}" x2="{upper_x:.1f}" y2="{y + 10}" '
                'stroke="#17212B" stroke-width="3"/>',
                _text(value_x + 12, y + 8, f"{value:.2f}%", size=21, weight=600, fill=COLORS[arm]),
            ]
        )

    # Panel B: recoveries among the shared-initial failures.
    initial_failures = data["initial_failures"]
    if initial_failures <= 0:
        raise ValueError("Expected at least one shared-initial failure")
    parts.extend(
        [
            _text(850, 186, "B  Recovery after initial failure", size=28, weight=600),
            _text(
                850,
                220,
                f"{initial_failures:,} shared-initial failures",
                size=19,
                fill="#52606D",
            ),
        ]
    )
    left_b, right_b, top_b, bottom_b = 1040.0, 1510.0, 270.0, 590.0
    _axis(
        parts,
        left=left_b,
        right=right_b,
        top=top_b,
        bottom=bottom_b,
        maximum=60,
        ticks=(0, 15, 30, 45, 60),
        suffix="%",
    )
    initial_successes = int(data["arms"]["no_user"]["official_successes"])
    recoveries = {
        arm: int(data["arms"][arm]["official_successes"]) - initial_successes
        for arm in ARMS
    }
    for index, arm in enumerate(ARMS):
        y = 325 + index * 105
        count = recoveries[arm]
        if not 0 <= count <= initial_failures:
            raise ValueError(f"Invalid recovery count for {arm}: {count}")
        rate = 100 * count / initial_failures
        value_x = left_b + (right_b - left_b) * rate / 60
        label_inside = rate >= 45
        label_x = value_x - 12 if label_inside else value_x + 12
        parts.extend(
            [
                _text(left_b - 18, y + 8, LABELS[arm], size=21, anchor="end"),
                f'<rect x="{left_b}" y="{y - 22}" width="{max(value_x - left_b, 2):.1f}" '
                f'height="44" rx="4" fill="{COLORS[arm]}" fill-opacity="0.88"/>',
                _text(
                    label_x,
                    y + 8,
                    f"{count}/{initial_failures} ({rate:.1f}%)",
                    size=21,
                    weight=600,
                    anchor="end" if label_inside else "start",
                    fill="#FFFFFF" if label_inside else COLORS[arm],
                ),
            ]
        )

    primary = data["primary"]
    sensitivity = data["sensitivity"]
    lower, upper = primary["task_bootstrap_95_ci_percentage_points"]
    parts.extend(
        [
            '<rect x="90" y="700" width="1420" height="115" rx="8" fill="#F2F5F7"/>',
            _text(120, 748, "Primary paired effect", size=22, weight=600, fill="#52606D"),
            _text(
                120,
                788,
                f"Reactive - SQL-only: +{primary['difference_percentage_points']:.2f} points "
                f"(95% CI +{lower:.2f} to +{upper:.2f})",
                size=29,
                weight=600,
                fill="#0072B2",
            ),
            _text(
                1480,
                786,
                f"{sensitivity['tasks']:,}-task unexposed sensitivity: "
                f"+{sensitivity['difference_percentage_points']:.2f} points",
                size=21,
                anchor="end",
                fill="#52606D",
            ),
            _text(
                1510,
                860,
                "Bars show retained final trajectories; whiskers are task-bootstrap 95% intervals.",
                size=18,
                anchor="end",
                fill="#6B7280",
            ),
            "</svg>",
        ]
    )
    return "\n".join(parts) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(analysis), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
