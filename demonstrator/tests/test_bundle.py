"""Replay and live-input boundaries without downloaded datasets or credentials."""

import hashlib
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from explorer import APP_ROOT
from explorer import live as live_module
from explorer.live import LiveService
from explorer.replay import BundledReplayStore, IDS, METHODS
from explorer.server import create_app
from test_explorer import FakeInvoker
import test_explorer


@pytest.fixture
def store(tmp_path):
    return BundledReplayStore(database_root=tmp_path / "missing-databases")


@pytest.fixture
def service(store, tmp_path, monkeypatch):
    monkeypatch.setattr(live_module, "EXPERIMENT", tmp_path / "experiment")
    monkeypatch.setattr(live_module, "load_dotenv", lambda *a, **kw: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return LiveService(store, state_dir=tmp_path / "sessions")


def test_bundle_outcomes(store):
    test_explorer.test_catalogue_outcomes_are_real(store)


def test_bundle_prompt_hashes(store):
    test_explorer.test_all_replayed_prompts_verified(store)


def test_every_case_context_and_preview_works_offline(store, service):
    with TestClient(create_app(store, service), base_url="http://localhost") as client:
        status = client.get("/api/status").json()["live"]
        assert status["available"] is False
        assert status["branch_inputs_ready"] is False
        assert status["databases_ready"] is False
        assert client.get("/api/databases").json() == []
        for case_id in IDS:
            assert client.get(f"/api/cases/{case_id}").status_code == 200
            for method in METHODS:
                for step in store.steps(case_id, method):
                    url = f"/api/cases/{case_id}/{method}/{step['id']}"
                    assert client.get(url + "/context").status_code == 200
                    if step["kind"] == "sql":
                        response = client.get(url + "/preview")
                        assert response.status_code == 200
                        assert response.json()["source"].startswith("Cached")
        assert client.get("/api/cases/780/no_user/-1/context").status_code == 404
        assert client.get("/api/cases/780/reactive_clarification/1/preview").status_code == 404
    assert "evaluator" not in service.__dict__
    assert "prompts" not in service.__dict__


@pytest.mark.parametrize("mode", ["original", "changed"])
def test_missing_inputs_fail_before_paid_call(service, mode):
    service.invoker = FakeInvoker()
    branch = service.fork(780, "reactive_clarification", 1, mode)
    assert len(branch["steps"]) == 2
    with pytest.raises(ValueError, match="fetch_public_inputs"):
        service.action(branch["id"], 0, "reply-test-1", "reply", "Use full_name")
    assert service.invoker.prompts == []
    assert service.sessions[branch["id"]]["calls"] == 0


def test_custom_needs_database_but_no_gold_or_sphinteract(service, store):
    database = store.database_root / "superhero/superhero.sqlite"
    database.parent.mkdir(parents=True)
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE example(value TEXT)")
        connection.execute("INSERT INTO example VALUES ('hello')")
    invoker = FakeInvoker()
    invoker.outputs = [
        {
            "decision": "sql",
            "sql": "SELECT value FROM example",
            "purpose": "Answer",
            "question": "",
            "options": [],
        }
    ]
    service.invoker = service.custom_invoker = invoker
    branch = service.start_custom("superhero", "Show the values")
    result = service.action(branch["id"], 0, "custom-test-1", "generate")["session"]
    assert result["calls"] == 1
    assert result["steps"][0]["preview"]["preview"] == [["hello"]]
    assert "evaluator" not in service.__dict__ and "prompts" not in service.__dict__


def test_bundle_tampering_is_rejected(tmp_path):
    path = tmp_path / "replay.json"
    path.write_bytes((APP_ROOT / "data/replay.json").read_bytes() + b" ")
    path.with_suffix(".sha256").write_bytes((APP_ROOT / "data/replay.sha256").read_bytes())
    with pytest.raises(ValueError, match="checksum"):
        BundledReplayStore(path)
    payload = json.loads(path.read_bytes())
    payload["cases"]["780"]["record"] += " "
    raw = json.dumps(payload).encode()
    path.write_bytes(raw)
    path.with_suffix(".sha256").write_text(hashlib.sha256(raw).hexdigest())
    with pytest.raises(ValueError, match="original checksum"):
        BundledReplayStore(path)
