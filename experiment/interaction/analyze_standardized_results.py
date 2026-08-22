"""Aggregate paired Mini-Interact outcomes without making model calls.

The command reads completed metrics and the excluded formatting-run shards,
then writes only aggregate JSON and SVG figures. It never copies task IDs,
questions, SQL, simulator answers, or hidden ground truth into tracked output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from itertools import pairwise
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

ARM_ORDER = (
    "mini_react_port",
    "sphinteract_cq",
    "proactive",
    "hybrid",
)
ARM_LABELS = {
    "mini_react_port": "Reference port",
    "sphinteract_cq": "Sph-derived reactive",
    "proactive": "Proactive-only",
    "hybrid": "Hybrid",
}
ARM_COLORS = {
    "mini_react_port": "#0072B2",
    "sphinteract_cq": "#E69F00",
    "proactive": "#009E73",
    "hybrid": "#CC79A7",
}
ARM_MARKERS = {
    "mini_react_port": "circle",
    "sphinteract_cq": "square",
    "proactive": "triangle",
    "hybrid": "diamond",
}

TERMINAL_GROUPS = (
    "accepted",
    "forced_rejected",
    "sra_no_ambiguity",
    "budget_or_attempt_limit",
    "model_or_protocol_error",
    "oracle_error",
)
TERMINAL_LABELS = {
    "accepted": "Accepted",
    "forced_rejected": "Forced SQL rejected",
    "sra_no_ambiguity": 'SRA stopped: "NO AMBIGUITY"',
    "budget_or_attempt_limit": "Question/submission limit",
    "model_or_protocol_error": "Model/protocol error",
    "oracle_error": "Oracle error",
}
TERMINAL_COLORS = {
    "accepted": "#009E73",
    "forced_rejected": "#D55E00",
    "sra_no_ambiguity": "#E69F00",
    "budget_or_attempt_limit": "#56B4E9",
    "model_or_protocol_error": "#CC79A7",
    "oracle_error": "#6C757D",
}


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object: {path}")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record_map(records: Any, *, path: Path, section: str) -> dict[str, dict[str, Any]]:
    if not isinstance(records, list):
        raise TypeError(f"Missing {section} records in {path}")
    mapped = {str(record["question_id"]): record for record in records}
    if len(mapped) != len(records):
        raise ValueError(f"Duplicate task IDs in {path} ({section})")
    return mapped


def _load_arm(path: Path) -> dict[str, Any]:
    payload = _load_json(path)
    official = payload.get("official_protocol") or {}
    secondary = payload.get("secondary_policy_analysis") or {}
    official_records = _record_map(
        official.get("records"), path=path, section="official_protocol"
    )
    secondary_records = _record_map(
        secondary.get("records"), path=path, section="secondary_policy_analysis"
    )
    if set(official_records) != set(secondary_records):
        raise ValueError(f"Official and secondary task sets differ in {path}")
    records = {
        question_id: {
            **official_records[question_id],
            **secondary_records[question_id],
        }
        for question_id in official_records
    }
    return {
        "path": path,
        "payload": payload,
        "records": records,
        "secondary_summary": secondary.get("summary") or {},
    }


def _success_curve(
    records: dict[str, dict[str, Any]], horizon: int
) -> list[dict[str, Any]]:
    total = len(records)
    curve = []
    for turn in range(horizon + 1):
        correct = sum(
            record.get("success_turn") is not None
            and int(record["success_turn"]) <= turn
            for record in records.values()
        )
        curve.append(
            {
                "user_turn": turn,
                "successes": correct,
                "success_rate_percent": round(100 * correct / total, 4),
            }
        )
    return curve


def _normalized_auc(curve: list[dict[str, Any]], total: int) -> float:
    if len(curve) == 1:
        return round(int(curve[0]["successes"]) / total, 8)
    rates = [int(point["successes"]) / total for point in curve]
    area = sum((left + right) / 2 for left, right in pairwise(rates))
    return round(area / (len(curve) - 1), 8)


def _terminal_group(reason: str) -> str:
    if reason == "accepted_submission":
        return "accepted"
    if reason == "forced_submission_rejected":
        return "forced_rejected"
    if reason == "sra_no_ambiguity_after_rejection":
        return "sra_no_ambiguity"
    if reason in {
        "normal_submission_limit_exhausted",
        "user_question_limit_exhausted",
    }:
        return "budget_or_attempt_limit"
    if reason in {"model_error", "model_protocol_error"}:
        return "model_or_protocol_error"
    if reason == "oracle_error":
        return "oracle_error"
    raise ValueError(f"Unclassified terminal reason: {reason}")


def _terminal_counts(records: dict[str, dict[str, Any]]) -> dict[str, Any]:
    raw = Counter(str(record["termination"]) for record in records.values())
    grouped = Counter()
    for reason, count in raw.items():
        grouped[_terminal_group(reason)] += count
    return {
        "raw": dict(sorted(raw.items())),
        "grouped": {group: grouped[group] for group in TERMINAL_GROUPS},
    }


def _paired_outcomes(
    baseline: dict[str, dict[str, Any]],
    hybrid: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    ids = sorted(hybrid)
    both = [
        question_id
        for question_id in ids
        if baseline[question_id]["correct"] and hybrid[question_id]["correct"]
    ]
    baseline_only = [
        question_id
        for question_id in ids
        if baseline[question_id]["correct"] and not hybrid[question_id]["correct"]
    ]
    hybrid_only = [
        question_id
        for question_id in ids
        if not baseline[question_id]["correct"] and hybrid[question_id]["correct"]
    ]
    neither = [
        question_id
        for question_id in ids
        if not baseline[question_id]["correct"] and not hybrid[question_id]["correct"]
    ]
    return {
        "both_succeed": len(both),
        "baseline_only_succeeds": len(baseline_only),
        "hybrid_only_succeeds": len(hybrid_only),
        "neither_succeeds": len(neither),
        "hybrid_only_baseline_terminal_reasons": dict(
            sorted(
                Counter(baseline[item]["termination"] for item in hybrid_only).items()
            )
        ),
        "baseline_only_hybrid_terminal_reasons": dict(
            sorted(
                Counter(hybrid[item]["termination"] for item in baseline_only).items()
            )
        ),
    }


def _aborted_run_ids(aborted_run_dir: Path | None) -> set[str]:
    if aborted_run_dir is None:
        return set()
    shard_root = aborted_run_dir / "shards"
    ids: set[str] = set()
    for path in sorted(shard_root.glob("shard-*/*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            ids.add(str(payload["task"]["question_id"]))
    return ids


def _sensitivity(
    arms: dict[str, dict[str, Any]], excluded_ids: set[str]
) -> dict[str, Any] | None:
    if not excluded_ids:
        return None
    all_ids = set(arms["hybrid"]["records"])
    included = sorted(all_ids - excluded_ids)
    if not included:
        raise ValueError("Sensitivity exclusion removed every task")
    successes = {
        arm: sum(bool(arms[arm]["records"][item]["correct"]) for item in included)
        for arm in ARM_ORDER
    }
    rates = {arm: round(successes[arm] / len(included), 8) for arm in ARM_ORDER}
    differences = {
        f"hybrid_minus_{baseline}": round(rates["hybrid"] - rates[baseline], 8)
        for baseline in ARM_ORDER[:-1]
    }
    return {
        "excluded_ids_count": len(all_ids & excluded_ids),
        "included_tasks": len(included),
        "successes": successes,
        "success_rates": rates,
        "paired_success_rate_differences": differences,
        "ids_withheld_from_output": True,
    }


def _svg_text(
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
        return f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{size}" fill="{color}"/>'
    if shape == "square":
        side = size * 1.8
        return (
            f'<rect x="{x - side / 2:.1f}" y="{y - side / 2:.1f}" '
            f'width="{side:.1f}" height="{side:.1f}" fill="{color}"/>'
        )
    if shape == "triangle":
        points = (
            f"{x:.1f},{y - size:.1f} "
            f"{x - size:.1f},{y + size:.1f} "
            f"{x + size:.1f},{y + size:.1f}"
        )
        return f'<polygon points="{points}" fill="{color}"/>'
    points = (
        f"{x:.1f},{y - size:.1f} "
        f"{x - size:.1f},{y:.1f} "
        f"{x:.1f},{y + size:.1f} "
        f"{x + size:.1f},{y:.1f}"
    )
    return f'<polygon points="{points}" fill="{color}"/>'


def _write_success_figure(
    output: Path, analysis: dict[str, Any], *, horizon: int
) -> None:
    width, height = 1600, 1000
    left, right, top, bottom = 170, 250, 210, 180
    plot_width = width - left - right
    plot_height = height - top - bottom
    y_max = 40

    def x_scale(turn: int) -> float:
        return left + plot_width * turn / horizon

    def y_scale(value: float) -> float:
        return top + plot_height * (y_max - value) / y_max

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        ),
        "<title>Task success over user turns</title>",
        (
            "<desc>Cumulative task success at each user-turn budget for four "
            "matched Mini-Interact policy arms.</desc>"
        ),
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        _svg_text(left, 70, "Task success over user turns", size=42, weight=500),
        _svg_text(
            left,
            112,
            "Mini-Interact Stress adaptation · 240 paired tasks per arm · Luna/high",
            size=22,
            fill="#52606D",
        ),
    ]
    for tick in range(0, y_max + 1, 5):
        y = y_scale(tick)
        parts.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" '
            f'y2="{y:.1f}" stroke="#D9DEE3" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(
                left - 20, y + 8, f"{tick}%", size=20, anchor="end", fill="#52606D"
            )
        )
    for turn in range(horizon + 1):
        x = x_scale(turn)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" '
            f'y2="{top + plot_height}" stroke="#EEF1F4" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(x, top + plot_height + 42, str(turn), size=22, anchor="middle")
        )
    parts.extend(
        [
            (
                f'<rect x="{left}" y="{top}" width="{plot_width}" '
                f'height="{plot_height}" fill="none" stroke="#AAB4BE" '
                'stroke-width="1.5"/>'
            ),
            _svg_text(
                left + plot_width / 2,
                top + plot_height + 90,
                "Maximum user questions used",
                size=24,
                weight=500,
                anchor="middle",
            ),
            (
                f'<text x="48" y="{top + plot_height / 2:.1f}" '
                'font-family="Arial, sans-serif" font-size="24" font-weight="500" '
                'text-anchor="middle" fill="#17212B" '
                f'transform="rotate(-90 48 {top + plot_height / 2:.1f})">'
                "Cumulative task success (%)</text>"
            ),
        ]
    )

    endpoint_offsets = {
        "mini_react_port": -8,
        "sphinteract_cq": 30,
        "proactive": -12,
        "hybrid": -8,
    }
    for arm in ARM_ORDER:
        curve = analysis["arms"][arm]["success_by_user_turn"]
        points = [
            (
                x_scale(int(point["user_turn"])),
                y_scale(float(point["success_rate_percent"])),
            )
            for point in curve
        ]
        point_string = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        color = ARM_COLORS[arm]
        parts.append(
            f'<polyline points="{point_string}" fill="none" stroke="{color}" '
            'stroke-width="5" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, y in points:
            parts.append(_marker(ARM_MARKERS[arm], x, y, color))
        final = float(curve[-1]["success_rate_percent"])
        parts.append(
            _svg_text(
                points[-1][0] + 22,
                points[-1][1] + endpoint_offsets[arm],
                f"{final:.1f}%",
                size=22,
                weight=500,
                fill=color,
            )
        )

    legend_y = 148
    for index, arm in enumerate(ARM_ORDER):
        column = index % 2
        row = index // 2
        x = left + column * 610
        y = legend_y + row * 42
        color = ARM_COLORS[arm]
        parts.append(
            f'<line x1="{x}" y1="{y}" x2="{x + 42}" y2="{y}" '
            f'stroke="{color}" stroke-width="5"/>'
        )
        parts.append(_marker(ARM_MARKERS[arm], x + 21, y, color, 7))
        auc = analysis["arms"][arm]["normalized_ex_user_turn_auc"]
        parts.append(
            _svg_text(
                x + 58,
                y + 8,
                f"{ARM_LABELS[arm]}  ·  AUC {auc:.3f}",
                size=21,
            )
        )
    parts.append(
        _svg_text(
            width - right,
            972,
            "Turn 0 means success before any user answer; AUC uses turns 0–4.",
            size=18,
            anchor="end",
            fill="#52606D",
        )
    )
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def _write_terminal_figure(output: Path, analysis: dict[str, Any]) -> None:
    width, height = 1600, 1000
    left, right, top, bottom = 330, 110, 240, 290
    plot_width = width - left - right
    plot_height = height - top - bottom
    total = analysis["integrity"]["tasks_per_arm"]
    bar_height = 72
    gap = (plot_height - len(ARM_ORDER) * bar_height) / (len(ARM_ORDER) - 1)

    def x_scale(value: int) -> float:
        return left + plot_width * value / total

    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}" viewBox="0 0 {width} {height}" role="img">'
        ),
        "<title>How trajectories ended</title>",
        (
            "<desc>Stacked counts of accepted and failed terminal outcomes for "
            "four matched Mini-Interact policy arms.</desc>"
        ),
        f'<rect width="{width}" height="{height}" fill="#FFFFFF"/>',
        _svg_text(left, 70, "How trajectories ended", size=42, weight=500),
        _svg_text(
            left,
            112,
            "Most failures ended with an executable but rejected final SQL; SRA early stopping was also common.",
            size=22,
            fill="#52606D",
        ),
    ]
    for tick in range(0, total + 1, 60):
        x = x_scale(tick)
        parts.append(
            f'<line x1="{x:.1f}" y1="{top - 24}" x2="{x:.1f}" '
            f'y2="{top + plot_height}" stroke="#E3E7EB" stroke-width="1"/>'
        )
        parts.append(
            _svg_text(x, top - 45, str(tick), size=20, anchor="middle", fill="#52606D")
        )
    for index, arm in enumerate(ARM_ORDER):
        y = top + index * (bar_height + gap)
        parts.append(
            _svg_text(
                left - 24,
                y + bar_height / 2 + 8,
                ARM_LABELS[arm],
                size=23,
                weight=500 if arm == "hybrid" else 400,
                anchor="end",
            )
        )
        cursor = left
        grouped = analysis["arms"][arm]["terminal_outcomes"]["grouped"]
        for group in TERMINAL_GROUPS:
            count = int(grouped[group])
            segment_width = plot_width * count / total
            if count:
                parts.append(
                    f'<rect x="{cursor:.1f}" y="{y:.1f}" width="{segment_width:.1f}" '
                    f'height="{bar_height}" fill="{TERMINAL_COLORS[group]}"/>'
                )
                if segment_width >= 48:
                    label_color = (
                        "#17212B" if group == "budget_or_attempt_limit" else "#FFFFFF"
                    )
                    parts.append(
                        _svg_text(
                            cursor + segment_width / 2,
                            y + bar_height / 2 + 8,
                            str(count),
                            size=21,
                            weight=500,
                            anchor="middle",
                            fill=label_color,
                        )
                    )
            cursor += segment_width
        parts.append(
            f'<rect x="{left}" y="{y}" width="{plot_width}" height="{bar_height}" '
            'fill="none" stroke="#AAB4BE" stroke-width="1.2"/>'
        )
    parts.append(
        _svg_text(
            left + plot_width / 2,
            top + plot_height + 58,
            f"Trajectories (n={total} per arm)",
            size=24,
            weight=500,
            anchor="middle",
        )
    )

    legend_y = 815
    legend_columns = 3
    legend_width = 410
    for index, group in enumerate(TERMINAL_GROUPS):
        column = index % legend_columns
        row = index // legend_columns
        x = left + column * legend_width
        y = legend_y + row * 50
        parts.append(
            f'<rect x="{x}" y="{y - 18}" width="26" height="26" '
            f'fill="{TERMINAL_COLORS[group]}"/>'
        )
        parts.append(_svg_text(x + 40, y + 4, TERMINAL_LABELS[group], size=19))
    parts.append(
        _svg_text(
            left,
            945,
            '"NO AMBIGUITY" is a controller stop after rejection—not a gold label about the task.',
            size=18,
            fill="#52606D",
        )
    )
    parts.append("</svg>")
    output.write_text("\n".join(parts) + "\n", encoding="utf-8")


def analyze(
    run_dir: Path,
    *,
    aborted_run_dir: Path | None,
    output_json: Path,
    figure_dir: Path,
    horizon: int = 4,
) -> dict[str, Any]:
    arms = {
        arm: _load_arm(run_dir / "combined" / f"{arm}.metrics.json")
        for arm in ARM_ORDER
    }
    id_sets = {arm: set(data["records"]) for arm, data in arms.items()}
    if any(id_sets[arm] != id_sets[ARM_ORDER[0]] for arm in ARM_ORDER[1:]):
        raise ValueError("All arms must contain the same paired task IDs")
    task_ids = id_sets[ARM_ORDER[0]]
    if not task_ids:
        raise ValueError("No completed tasks found")

    arm_results: dict[str, Any] = {}
    for arm in ARM_ORDER:
        records = arms[arm]["records"]
        curve = _success_curve(records, horizon)
        auc = _normalized_auc(curve, len(records))
        recorded_auc = float(
            arms[arm]["secondary_summary"].get("normalized_ex_user_turn_auc", -1)
        )
        if abs(auc - recorded_auc) > 1e-6:
            raise ValueError(
                f"Recomputed AUC differs for {arm}: {auc} versus {recorded_auc}"
            )
        successes = sum(bool(record["correct"]) for record in records.values())
        arm_results[arm] = {
            "label": ARM_LABELS[arm],
            "successes": successes,
            "success_rate": round(successes / len(records), 8),
            "success_by_user_turn": curve,
            "normalized_ex_user_turn_auc": auc,
            "mean_user_questions": round(
                sum(float(record["user_turns"]) for record in records.values())
                / len(records),
                8,
            ),
            "terminal_outcomes": _terminal_counts(records),
        }

    hybrid = arms["hybrid"]["records"]
    paired = {
        baseline: _paired_outcomes(arms[baseline]["records"], hybrid)
        for baseline in ARM_ORDER[:-1]
    }
    all_fail = sum(
        all(not bool(arms[arm]["records"][item]["correct"]) for arm in ARM_ORDER)
        for item in task_ids
    )
    all_succeed = sum(
        all(bool(arms[arm]["records"][item]["correct"]) for arm in ARM_ORDER)
        for item in task_ids
    )
    any_succeed = sum(
        any(bool(arms[arm]["records"][item]["correct"]) for arm in ARM_ORDER)
        for item in task_ids
    )
    aborted_ids = _aborted_run_ids(aborted_run_dir)

    output_json.parent.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)
    success_figure = figure_dir / "official-mini-interact-success-vs-user-turns.svg"
    terminal_figure = figure_dir / "official-mini-interact-terminal-outcomes.svg"
    result = {
        "schema_version": 1,
        "record_type": "paired_terminal_error_analysis",
        "analysis_date": "2026-08-22",
        "scope": (
            "Read-only aggregate analysis of the completed standardized local "
            "Mini-Interact Stress adaptation; no model calls or task content exported."
        ),
        "integrity": {
            "arms": len(ARM_ORDER),
            "tasks_per_arm": len(task_ids),
            "identical_paired_id_sets": True,
            "task_ids_withheld_from_output": True,
            "input_metric_sha256": {
                arm: _sha256(arms[arm]["path"]) for arm in ARM_ORDER
            },
        },
        "arms": arm_results,
        "paired_hybrid_outcomes": paired,
        "shared_outcomes": {
            "all_four_succeed": all_succeed,
            "at_least_one_arm_succeeds": any_succeed,
            "all_four_fail": all_fail,
            "hybrid_failures": len(task_ids) - arm_results["hybrid"]["successes"],
            "hybrid_failures_shared_by_all_four": all_fail,
        },
        "aborted_v1_sensitivity": _sensitivity(arms, aborted_ids),
        "figures": {
            "success_vs_user_turns": success_figure.name,
            "terminal_outcomes": terminal_figure.name,
        },
        "interpretation_boundary": (
            "Terminal labels show where a controller stopped, not the semantic root "
            "cause of a wrong SQL query. Paired arms also contain independent "
            "stochastic model calls."
        ),
    }
    output_json.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_success_figure(success_figure, result, horizon=horizon)
    _write_terminal_figure(terminal_figure, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--aborted-run-dir", type=Path)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--figure-dir", type=Path, required=True)
    args = parser.parse_args()
    result = analyze(
        args.run_dir,
        aborted_run_dir=args.aborted_run_dir,
        output_json=args.output_json,
        figure_dir=args.figure_dir,
    )
    print(
        "Wrote aggregate analysis for "
        f"{result['integrity']['tasks_per_arm']} paired tasks per arm."
    )


if __name__ == "__main__":
    main()
