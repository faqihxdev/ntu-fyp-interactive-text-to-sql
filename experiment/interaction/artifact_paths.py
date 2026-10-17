"""Resolve saved experiment paths in working and standalone checkouts."""

from pathlib import Path


def workspace_root(experiment_root: Path) -> Path:
    root = experiment_root.resolve()
    return root.parent.parent if root.parent.name == "codebase" else root.parent


def resolve_recorded_path(value: str | Path, experiment_root: Path) -> Path:
    """Map historical path prefixes locally; leave recorded hashes unchanged."""
    path = Path(str(value).replace("\\", "/"))
    if path.is_absolute():
        return path.resolve()
    parts = path.parts
    for prefix in (("codebase", "experiment"), ("experiment",)):
        if parts[:len(prefix)] == prefix:
            tail = parts[len(prefix):]
            if ".." in tail:
                raise ValueError("Recorded experiment path contains parent traversal")
            return experiment_root.resolve().joinpath(*tail)
    return (workspace_root(experiment_root) / path).resolve()
