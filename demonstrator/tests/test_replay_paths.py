"""Verify saved prompt hashes using local fixtures."""

import hashlib
import json

import pytest

from explorer import replay


@pytest.fixture
def saved_context(tmp_path, monkeypatch):
    experiment = tmp_path / "experiment"
    instructions = experiment / "interaction/codex-policy-instructions.md"
    instructions.parent.mkdir(parents=True)
    instructions.write_bytes(b"Fixture instructions.\n")
    run = experiment / "runs/fixture"
    prompt = run / "artifacts/policy/780/shared_initial/0.prompt.txt"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Fixture question.", encoding="utf-8")
    prompt.with_name("0.output-schema.json").write_text("{}", encoding="utf-8")
    store = replay.ReplayStore(run=run)
    monkeypatch.setattr(replay, "EXPERIMENT", experiment)
    monkeypatch.setattr(store, "steps", lambda *_: [{
        "role": "policy",
        "folder": "shared_initial",
        "prompt_sha256": hashlib.sha256(b"Fixture question.").hexdigest(),
        "raw_output": {"sql": "SELECT 1"},
        "usage": {},
    }])
    return store, instructions


@pytest.mark.parametrize("prefix", ["experiment/", "archive/experiment/"])
def test_instruction_path_and_hash(saved_context, prefix):
    store, instructions = saved_context
    contract = {
        "implementation_hashes": {
            prefix + "interaction/codex-policy-instructions.md":
                hashlib.sha256(instructions.read_bytes()).hexdigest(),
        },
    }
    (store.run / "run-contract.json").write_text(json.dumps(contract), encoding="utf-8")
    assert store.context(780, "no_user", 0)["verified"] is True
    instructions.write_bytes(b"Changed instructions.\n")
    store.context.cache_clear()
    with pytest.raises(ValueError, match="instructions differ"):
        store.context(780, "no_user", 0)


@pytest.mark.parametrize("keys", [[], [
    "experiment/interaction/codex-policy-instructions.md",
    "archive/experiment/interaction/codex-policy-instructions.md",
]])
def test_missing_or_ambiguous_instruction_hash_is_rejected(saved_context, keys):
    store, instructions = saved_context
    digest = hashlib.sha256(instructions.read_bytes()).hexdigest()
    (store.run / "run-contract.json").write_text(json.dumps({
        "implementation_hashes": {key: digest for key in keys},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="one system-instruction hash"):
        store.context(780, "no_user", 0)
