"""Create report figures from the completed original-BIRD matched analysis.

This is a read-only post-analysis utility. It does not load task text, SQL, or
private simulator material, and it makes no model or evaluator calls.
"""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path
from typing import Any


ARM_ORDER = (
    "no_user",
    "proactive_only",
    "sph_cq",
    "project_hybrid",
    "sph_cq_es",
)
ARM_LABELS = {
    "no_user": "Initial-attempt baseline",
    "proactive_only": "Proactive-only",
    "sph_cq": "Reactive CQ",
    "project_hybrid": "Hybrid",
    "sph_cq_es": "Reactive CQ with early stop",
}
ARM_COLORS = {
    "no_user": "#6B7280",
    "proactive_only": "#E69F00",
    "sph_cq": "#0072B2",
    "project_hybrid": "#009E73",
    "sph_cq_es": "#CC79A7",
}
ARM_MARKERS = {
    "no_user": "circle",
    "proactive_only": "square",
    "sph_cq": "triangle",
    "project_hybrid": "diamond",
    "sph_cq_es": "circle",
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


def _marker(shape: str, x: float, y: float, color: str, size: float = 8) -> str:
    if shape == "circle":
        return (
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{size}" '
            f'fill="{color}" stroke="#FFFFFF" stroke-width="2"/>'
        )
    if shape == "square":
        side = size * 1.8
        return (
            f'<rect x="{x - side / 2:.1f}" y="{y - side / 2:.1f}" '
            f'width="{side:.1f}" height="{side:.1f}" fill="{color}" '
            'stroke="#FFFFFF" stroke-width="2"/>'
        )
    if shape == "triangle":
        points = (
            f"{x:.1f},{y - size:.1f} "
            f"{x - size:.1f},{y + size:.1f} "
            f"{x + size:.1f},{y + size:.1f}"
        )
        return (
            f'<polygon points="{points}" fill="{color}" '
            'stroke="#FFFFFF" stroke-width="2"/>'
        )
    points = (
        f"{x:.1f},{y - size:.1f} "
        f"{x - size:.1f},{y:.1f} "
        f"{x:.1f},{y + size:.1f} "
        f"{x + size:.1f},{y:.1f}"
    )
    return (
        f'<polygon points="{points}" fill="{color}" '
        'stroke="#FFFFFF" stroke-width="2"/>'
    )


def _svg_header(width: int, height: int, title: str, description: str) -> list[str]:
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        ),
        f"<title>{escape(title)}</title>",
        f"<desc>{escape(description)}</desc>",
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
    ]


def _write_turn_curve(output: Path, analysis: dict[str, Any]) -> None:
    width, height = 1600, 1000
    left, right, top, bottom = 170, 320, 220, 175
    plot_width = width - left - right
    plot_height = height - top - bottom
    horizon = 4
    y_max = 80.0

    def x_scale(turn: int) -> float:
        return left + plot_width * turn / horizon

    def y_scale(value: float) -> float:
        return top + plot_height * (y_max - value) / y_max

    parts = _svg_header(
        width,
        height,
        "Execution accuracy over user turns",
        "Cumulative BIRD execution accuracy for five controller configurations. "
        "Ask-first tasks remain unresolved at turn zero until an answer is received.",
    )
    parts.extend(
        [
            _text(left, 68, "Execution accuracy over user turns", size=42, weight=500),
            _text(
                left,
                111,
                "Cumulative success after each answer. Ask-first tasks await their first scored SQL.",
                size=22,
                fill="#52606D",
            ),
        ]
    )
    for tick in range(0, 81, 10):
        y = y_scale(float(tick))
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
            f'y2="{y:.1f}" stroke="#D9DEE3" stroke-width="1"/>'
        )
        parts.append(
            _text(left - 20, y + 8, f"{tick}%", size=20, anchor="end", fill="#52606D")
        )
    for turn in range(horizon + 1):
        x = x_scale(turn)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
            f'y2="{top + plot_height}" stroke="#EEF1F4" stroke-width="1"/>'
        )
        parts.append(_text(x, top + plot_height + 42, str(turn), size=22, anchor="middle"))
    parts.extend(
        [
            (
                f'<rect x="{left}" y="{top}" width="{plot_width}" '
                f'height="{plot_height}" fill="none" stroke="#AAB4BE" '
                'stroke-width="1.5"/>'
            ),
            _text(
                left + plot_width / 2,
                top + plot_height + 90,
                "Maximum simulated-user answers received",
                size=24,
                weight=500,
                anchor="middle",
            ),
            (
                f'<text x="48" y="{top + plot_height / 2:.1f}" '
                'font-family="Arial, sans-serif" font-size="24" font-weight="500" '
                'text-anchor="middle" fill="#17212B" '
                f'transform="rotate(-90 48 {top + plot_height / 2:.1f})">'
                "Cumulative execution accuracy (%)</text>"
            ),
        ]
    )

    endpoint_offsets = {
        "no_user": 0,
        "proactive_only": 9,
        "sph_cq": -16,
        "project_hybrid": 20,
        "sph_cq_es": 1,
    }
    summaries = analysis["arm_summaries"]
    for arm in ARM_ORDER:
        curve = [float(value) for value in summaries[arm]["official_ex_by_user_turn_percent"]]
        points = [(x_scale(turn), y_scale(value)) for turn, value in enumerate(curve)]
        point_string = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        color = ARM_COLORS[arm]
        parts.append(
            f'<polyline points="{point_string}" fill="none" stroke="{color}" '
            'stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, y in points:
            parts.append(_marker(ARM_MARKERS[arm], x, y, color))
        parts.append(
            _text(
                points[-1][0] + 20,
                points[-1][1] + endpoint_offsets[arm],
                f"{curve[-1]:.1f}%",
                size=21,
                weight=500,
                fill=color,
            )
        )

    proactive_turn_zero_y = y_scale(
        float(summaries["proactive_only"]["official_ex_by_user_turn_percent"][0])
    )
    hybrid_turn_zero_y = y_scale(
        float(summaries["project_hybrid"]["official_ex_by_user_turn_percent"][0])
    )
    callout_y = 690
    parts.extend(
        [
            (
                f'<line x1="{x_scale(0) + 8:.1f}" '
                f'y1="{(proactive_turn_zero_y + hybrid_turn_zero_y) / 2:.1f}" '
                f'x2="230" y2="{callout_y - 10}" stroke="#9A6700" '
                'stroke-width="2"/>'
            ),
            (
                f'<rect x="220" y="{callout_y - 24}" width="430" height="68" '
                'rx="8" fill="#FFF7E6" stroke="#D39E00" stroke-width="1.5"/>'
            ),
            _text(
                240,
                callout_y + 3,
                "Proactive and hybrid at turn 0:",
                size=19,
                weight=500,
                fill="#694D00",
            ),
            _text(
                240,
                callout_y + 29,
                "ask-first tasks have no SQL yet.",
                size=19,
                fill="#694D00",
            ),
        ]
    )

    legend_y = 148
    for index, arm in enumerate(ARM_ORDER):
        column = index % 3
        row = index // 3
        x = left + column * 410
        y = legend_y + row * 42
        color = ARM_COLORS[arm]
        parts.append(
            f'<line x1="{x}" y1="{y}" x2="{x + 42}" y2="{y}" '
            f'stroke="{color}" stroke-width="5"/>'
        )
        parts.append(_marker(ARM_MARKERS[arm], x + 21, y, color, 7))
        parts.append(_text(x + 56, y + 8, ARM_LABELS[arm], size=20))
    parts.append(
        _text(
            width - right,
            970,
            "At turn 0, methods are at different stages of their work.",
            size=20,
            anchor="end",
            fill="#52606D",
        )
    )
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _write_tradeoff(output: Path, analysis: dict[str, Any]) -> None:
    width, height = 1600, 1000
    left, right, top, bottom = 180, 250, 200, 170
    plot_width = width - left - right
    plot_height = height - top - bottom
    x_max = 2.1
    y_min, y_max = 40.0, 80.0

    def x_scale(value: float) -> float:
        return left + plot_width * value / x_max

    def y_scale(value: float) -> float:
        return top + plot_height * (y_max - value) / (y_max - y_min)

    parts = _svg_header(
        width,
        height,
        "Accuracy and interaction burden",
        "Final execution accuracy versus mean user turns; bubble area tracks model calls.",
    )
    parts.extend(
        [
            _text(left, 68, "Accuracy and interaction burden", size=42, weight=500),
            _text(
                left,
                111,
                "Higher and farther left is better; circle area tracks effective model calls per task.",
                size=22,
                fill="#52606D",
            ),
        ]
    )
    for tick in range(40, 81, 5):
        y = y_scale(float(tick))
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
            f'y2="{y:.1f}" stroke="#D9DEE3" stroke-width="1"/>'
        )
        parts.append(
            _text(left - 20, y + 8, f"{tick}%", size=20, anchor="end", fill="#52606D")
        )
    for index in range(0, 8):
        turn = 0.3 * index
        if turn > x_max + 1e-9:
            continue
        x = x_scale(turn)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
            f'y2="{top + plot_height}" stroke="#EEF1F4" stroke-width="1"/>'
        )
        parts.append(_text(x, top + plot_height + 42, f"{turn:.1f}", size=20, anchor="middle"))
    parts.extend(
        [
            (
                f'<rect x="{left}" y="{top}" width="{plot_width}" '
                f'height="{plot_height}" fill="none" stroke="#AAB4BE" '
                'stroke-width="1.5"/>'
            ),
            _text(
                left + plot_width / 2,
                top + plot_height + 91,
                "Mean user turns per task",
                size=24,
                weight=500,
                anchor="middle",
            ),
            (
                f'<text x="50" y="{top + plot_height / 2:.1f}" '
                'font-family="Arial, sans-serif" font-size="24" font-weight="500" '
                'text-anchor="middle" fill="#17212B" '
                f'transform="rotate(-90 50 {top + plot_height / 2:.1f})">'
                "Final official execution accuracy (%)</text>"
            ),
        ]
    )

    label_offsets = {
        "no_user": (28, 22),
        "proactive_only": (26, -20),
        "sph_cq": (-28, -34),
        "project_hybrid": (30, 34),
        "sph_cq_es": (28, -22),
    }
    summaries = analysis["arm_summaries"]
    for arm in ARM_ORDER:
        values = summaries[arm]
        turns = float(values["mean_user_turns"])
        accuracy = float(values["official_ex_percent"])
        calls = float(values["effective_model_calls_per_task"])
        x, y = x_scale(turns), y_scale(accuracy)
        radius = 13 + 3.2 * calls
        color = ARM_COLORS[arm]
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{radius:.1f}" '
            f'fill="{color}" fill-opacity="0.82" stroke="#FFFFFF" stroke-width="3"/>'
        )
        dx, dy = label_offsets[arm]
        anchor = "end" if dx < 0 else "start"
        parts.append(
            _text(
                x + dx,
                y + dy,
                f"{ARM_LABELS[arm]}: {accuracy:.1f}%",
                size=21,
                weight=500,
                anchor=anchor,
                fill=color,
            )
        )
        parts.append(
            _text(
                x + dx,
                y + dy + 27,
                f"{turns:.2f} turns, {calls:.2f} calls",
                size=18,
                anchor=anchor,
                fill="#52606D",
            )
        )
    parts.append(
        _text(
            left,
            955,
            "Hybrid showed no detectable EX difference from reactive CQ and used more turns and calls.",
            size=20,
            fill="#52606D",
        )
    )
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def plot(analysis_path: Path, output_dir: Path) -> tuple[Path, Path]:
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    if analysis.get("protocol_health", {}).get("accuracy_estimates_valid") is not True:
        raise ValueError("Analysis is not marked valid for accuracy reporting.")
    if int(analysis.get("scope", {}).get("observed_tasks", 0)) != 1534:
        raise ValueError("Expected the completed 1,534-task analysis.")
    output_dir.mkdir(parents=True, exist_ok=True)
    turn_curve = output_dir / "bird-dev-v4-ex-over-user-turns.svg"
    tradeoff = output_dir / "bird-dev-v4-accuracy-interaction.svg"
    _write_turn_curve(turn_curve, analysis)
    _write_tradeoff(tradeoff, analysis)
    return turn_curve, tradeoff


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("analysis", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    outputs = plot(args.analysis, args.output_dir)
    print("\n".join(str(path) for path in outputs))


if __name__ == "__main__":
    main()
