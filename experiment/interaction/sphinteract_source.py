"""Load the pinned Sphinteract prompt literals without executing its notebook.

The source repository is MIT licensed.  We deliberately extract only literal
assignments from the official zero-shot notebook at a pinned revision; no
notebook code is evaluated.  This keeps the local comparison tied to an
auditable upstream artifact while allowing the prompts to remain upstream.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


SPHINTERACT_COMMIT = "21c6fcdd01f8173b3eaf51df796aee6d18e5fa4b"
SPHINTERACT_NOTEBOOK_SHA256 = (
    "4daf7a8bac96d9cca66e91d5dee5639f6890568a27f47778c039d23311132108"
)
REQUIRED_PROMPTS = {
    "feedback_prefix_v1",
    "feedback_v2",
    "cq_prefix_v1",
    "SRA",
    "SRA_ES",
    "sql_generation",
    "sql_generation_v2",
    "fix_invalid_v1",
    "sql_generation_selfdebug",
}


@dataclass(frozen=True)
class SphinteractPrompts:
    feedback_prefix: str
    feedback: str
    clarification_prefix: str
    clarification: str
    clarification_early_stop: str
    sql_initial: str
    sql_after_feedback: str
    sql_invalid_repair: str
    sql_self_debug: str


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _literal_assignments(source: str) -> dict[str, str]:
    """Return top-level string assignments from one Python source cell."""

    values: dict[str, str] = {}
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or target.id not in REQUIRED_PROMPTS:
            continue
        value = ast.literal_eval(node.value)
        if not isinstance(value, str):
            raise TypeError(f"Sphinteract prompt {target.id} is not a string")
        values[target.id] = value
    return values


def load_sphinteract_prompts(
    notebook_path: Path,
    *,
    expected_sha256: str = SPHINTERACT_NOTEBOOK_SHA256,
) -> SphinteractPrompts:
    """Load and verify prompt constants from the pinned official notebook."""

    notebook_path = notebook_path.resolve()
    if not notebook_path.is_file():
        raise FileNotFoundError(notebook_path)
    actual_hash = sha256_file(notebook_path)
    if actual_hash.casefold() != expected_sha256.casefold():
        raise ValueError(
            "Unexpected Sphinteract notebook hash: "
            f"expected {expected_sha256}, got {actual_hash}"
        )
    payload: Any = json.loads(notebook_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cells"), list):
        raise ValueError("Sphinteract notebook has no cells array")
    values: dict[str, str] = {}
    for cell in payload["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source_value = cell.get("source", [])
        if isinstance(source_value, list):
            source = "".join(str(part) for part in source_value)
        else:
            source = str(source_value)
        try:
            values.update(_literal_assignments(source))
        except SyntaxError:
            # Unrelated historical cells may not parse on the current Python.
            continue
    missing = sorted(REQUIRED_PROMPTS - set(values))
    if missing:
        raise ValueError(f"Missing Sphinteract prompt literals: {missing}")
    return SphinteractPrompts(
        feedback_prefix=values["feedback_prefix_v1"],
        feedback=values["feedback_v2"],
        clarification_prefix=values["cq_prefix_v1"],
        clarification=values["SRA"],
        clarification_early_stop=values["SRA_ES"],
        sql_initial=values["sql_generation"],
        sql_after_feedback=values["sql_generation_v2"],
        sql_invalid_repair=values["fix_invalid_v1"],
        sql_self_debug=values["sql_generation_selfdebug"],
    )
