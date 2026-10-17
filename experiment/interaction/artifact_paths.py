"""Resolve paths stored in experiment records."""

from pathlib import Path


def workspace_root(experiment_root: Path) -> Path:
    return experiment_root.resolve().parent


def resolve_recorded_path(value: str | Path, experiment_root: Path) -> Path:
    """Resolve relative experiment paths against the local experiment directory."""
    path = Path(str(value).replace("\\", "/"))
    if path.is_absolute():
        return path.resolve()
    parts = path.parts
    if ".." in parts:
        raise ValueError("Recorded experiment path contains parent traversal")
    if "experiment" in parts:
        tail = parts[parts.index("experiment") + 1:]
        return experiment_root.resolve().joinpath(*tail)
    return (workspace_root(experiment_root) / path).resolve()
