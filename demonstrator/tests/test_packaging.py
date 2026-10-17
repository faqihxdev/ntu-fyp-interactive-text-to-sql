"""Offline launcher and Git-rule checks in an isolated copy, without databases."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


CODEBASE = Path(__file__).resolve().parents[2]


def run(command, cwd, **kwargs):
    return subprocess.run(
        command, cwd=cwd, capture_output=True, text=True, timeout=30, **kwargs
    )


@pytest.fixture
def isolated(tmp_path):
    root = tmp_path / "code with spaces"
    root.mkdir()
    for name in ("run-demo.dev.sh", "run-demo.dev.ps1", ".gitignore", ".gitattributes"):
        shutil.copy2(CODEBASE / name, root / name)
    helper = root / "demonstrator/scripts/dev-processes.ps1"
    helper.parent.mkdir(parents=True)
    # Replace process handling completely. -Stop must never touch real listeners.
    helper.write_text(
        "function Clear-DemoPorts { param($Ports, $DemoRoot)\n"
        '  Write-Output ("fixture-root=" + $DemoRoot)\n'
        '  Write-Output ("fixture-ports=" + ($Ports -join ","))\n'
        "}\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture
def powershell():
    executable = shutil.which("powershell.exe")
    if os.name != "nt" or executable is None:
        pytest.skip("The launcher targets Windows PowerShell.")
    return executable


@pytest.fixture
def git_bash():
    if os.name != "nt":
        pytest.skip("The launcher targets Git Bash on Windows.")
    candidates = [
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe",
        Path.home() / "scoop/apps/git/current/bin/bash.exe",
    ]
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            probe = run([str(candidate), "-c", "uname -s"], CODEBASE)
            if probe.returncode == 0 and probe.stdout.startswith(("MINGW", "MSYS", "CYGWIN")):
                return str(candidate)
    pytest.skip("Git Bash is unavailable; WSL bash is not a substitute.")


@pytest.fixture
def git():
    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git is needed to check ignore rules.")
    return executable


def test_powershell_help_without_parent_project(isolated, powershell):
    result = run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(isolated / "run-demo.dev.ps1"), "-Help"],
        isolated.parent,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage:" in result.stdout
    assert not (isolated / "demonstrator/.local").exists()


def test_powershell_stop_uses_local_demo_and_ports(isolated, powershell):
    result = run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         str(isolated / "run-demo.dev.ps1"), "-Stop", "-Port", "9001",
         "-ApiPort", "9002"],
        isolated.parent,
    )
    assert result.returncode == 0, result.stderr
    assert f"fixture-root={isolated / 'demonstrator'}" in result.stdout
    assert "fixture-ports=9001,9002" in result.stdout
    assert not (isolated / "demonstrator/.local").exists()


@pytest.mark.parametrize("args,expected", [(["--help"], 0), (["--bad"], 2),
                                         (["--port", "invalid"], 2)])
def test_bash_help_and_validation(isolated, git_bash, args, expected):
    result = run([git_bash, str(isolated / "run-demo.dev.sh"), *args], isolated.parent)
    assert result.returncode == expected, result.stderr
    assert not (isolated / "demonstrator/.local").exists()


def test_bash_forwards_stop_to_local_powershell(isolated, git_bash, powershell):
    result = run(
        [git_bash, str(isolated / "run-demo.dev.sh"), "--stop", "--port", "9003",
         "--api-port", "9004"],
        isolated.parent,
    )
    assert result.returncode == 0, result.stderr
    assert f"fixture-root={isolated / 'demonstrator'}" in result.stdout
    assert "fixture-ports=9003,9004" in result.stdout


def test_git_rules_are_self_contained(isolated, git):
    # Test ignore rules in a temporary repository with no parent configuration.
    assert run([git, "init", "--quiet"], isolated).returncode == 0
    ignored = [
        "experiment/datasets/private/gt.jsonl", "experiment/runs/run/task.json",
        "demonstrator/.local/session.json", "demonstrator/.env",
        "experiment/.env.local", "experiment/.venv/pyvenv.cfg",
        "demonstrator/frontend/node_modules/example/index.js",
        "example.sqlite", "backup.zip", "tmp/check.json",
    ]
    for name in ignored:
        result = run([git, "check-ignore", "--no-index", "--quiet", name], isolated)
        assert result.returncode == 0, (name, result.stderr)
    for name in ("experiment/.env.example", "demonstrator/.env.example",
                 "experiment/uv.lock", "experiment/manifests/result.json",
                 "experiment/interaction/codex-policy-instructions.md"):
        result = run([git, "check-ignore", "--no-index", "--quiet", name], isolated)
        assert result.returncode == 1, (name, result.stderr)
    attributes = run([git, "check-attr", "eol", "--", "run-demo.dev.sh"], isolated)
    assert attributes.returncode == 0, attributes.stderr
    assert "eol: lf" in attributes.stdout


def test_manifest_bytes_survive_git_line_ending_rules(isolated, git):
    assert run([git, "init", "--quiet"], isolated).returncode == 0
    manifest = isolated / "experiment/manifests/fixture.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b'{\r\n  "frozen": true\r\n}\r\n')
    relative = manifest.relative_to(isolated).as_posix()
    raw = run([git, "hash-object", "--no-filters", relative], isolated)
    filtered = run([git, "-c", "core.autocrlf=true", "hash-object",
                    f"--path={relative}", relative], isolated)
    assert raw.returncode == filtered.returncode == 0
    assert raw.stdout == filtered.stdout
