import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from explorer import EXPERIMENT
from explorer.databases import DATABASE_GUIDES
from explorer.server import create_app
from explorer.live import LiveService
from explorer.replay import ReplayStore
from test_explorer import FakeInvoker


@pytest.fixture(scope="module", name="store")
def replay_store():
    return ReplayStore()


@pytest.fixture(name="live")
def live_service(store, tmp_path):
    return LiveService(store, invoker=FakeInvoker(), simulator=FakeInvoker(), state_dir=tmp_path)


def sql_output(sql="SELECT 1 AS value"):
    return {"decision": "sql", "sql": sql, "question": "", "options": []}


def ask_output():
    return {
        "decision": "ask",
        "sql": "",
        "question": "Which publisher?",
        "options": ["Marvel", "DC", "Other, please specify"],
    }


def action(live, session, kind, text=""):
    return live.action(session["id"], session["revision"], str(uuid.uuid4()), kind, text)["session"]


def test_catalogue_schema_and_new_examples(live):
    original = json.loads((EXPERIMENT / "datasets/bird-dev/dev.json").read_text("utf-8"))
    original_questions = {row["question"].strip().lower() for row in original}
    catalogue = live.databases.catalogue()
    assert len(catalogue) == 11
    for db in catalogue:
        assert len(db["examples"]) == 3 and db["description"]
        assert all(q.strip().lower() not in original_questions for q in db["examples"])
        detail = live.databases.detail(db["id"])
        assert detail["schema"] and detail["tables"]
        assert all(table in detail["schema"] for table in detail["tables"])
    assert live.invoker.prompts == []


def test_custom_database_validation_and_read_only(live):
    for db_id in ["../superhero", "superhero/../../financial", "unknown"]:
        with pytest.raises(ValueError):
            live.start_custom(db_id, "Count rows")
    with pytest.raises(ValueError):
        live.start_custom("superhero", "   ")
    path = live.databases.path("superhero")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for sql in [
        "DROP TABLE superhero",
        "DELETE FROM superhero",
        "SELECT 1; DROP TABLE superhero",
        "WITH x AS (SELECT 1) DELETE FROM superhero",
    ]:
        assert live.databases.execute("superhero", sql)["status"] != "ok"
    assert live.databases.execute("superhero", "SELECT 1 AS value")["preview"] == [[1]]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_custom_does_not_read_gold_or_saved_cases(live, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Custom exploration must not read benchmark answers or tasks")

    for name in ["task", "record", "case"]:
        monkeypatch.setattr(live.store, name, forbidden)
    monkeypatch.setattr(live.evaluator, "private_row", forbidden)
    monkeypatch.setattr(live.evaluator, "evaluate", forbidden)
    session = live.start_custom("superhero", "Which heroes are tallest?")
    assert not live.invoker.prompts
    live.invoker.outputs = [
        ask_output(),
        sql_output("SELECT 'ROW_SENTINEL' AS value"),
        sql_output(),
    ]
    session = action(live, session, "generate")
    assert session["phase"] == "reply" and session["calls"] == 1
    session = action(live, session, "reply", "Marvel only")
    assert session["phase"] == "result" and session["answers"] == 1
    assert session["steps"][-1]["candidate"]["official_ex"] is None
    # Preview values stay outside the next request. A sentinel absent from the query checks this.
    live.sessions[session["id"]]["steps"][-1]["preview"]["preview"] = [["HIDDEN_PREVIEW_SENTINEL"]]
    session = action(live, session, "feedback", "Count instead")
    assert "HIDDEN_PREVIEW_SENTINEL" not in live.invoker.prompts[-1]
    assert "Marvel only" in live.invoker.prompts[-1]
    assert "Count instead" in live.invoker.prompts[-1]
    assert "CREATE TABLE" in live.invoker.prompts[-1]
    assert not live.simulator.prompts
    assert all(
        s["candidate"]["official_ex"] is None for s in session["steps"] if s["kind"] == "sql"
    )


def test_custom_repairs_once_per_generation(live):
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [
        sql_output("SELECT nonexistent FROM superhero"),
        {"sql": "SELECT bad_column FROM superhero"},
        sql_output(),
    ]
    session = action(live, session, "generate")
    assert session["phase"] == "repair" and session["attempts"] == 1
    session = action(live, session, "repair")
    assert session["phase"] == "result" and session["attempts"] == 1
    assert "no such column" in live.invoker.prompts[-1]
    with pytest.raises(ValueError, match="One repair"):
        action(live, session, "repair")
    session = action(live, session, "feedback", "Use only real fields")
    assert session["attempts"] == 2
    assert session["steps"][-1]["candidate"]["status"] == "ok"


def test_custom_limits_and_idempotency(live):
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [ask_output()] * 4 + [sql_output()] * 5
    session = action(live, session, "generate")
    for _ in range(4):
        session = action(live, session, "reply", "Marvel")
    assert session["answers"] == 4 and session["phase"] == "result"
    assert "clarification limit is reached" in live.invoker.prompts[-1]
    for _ in range(4):
        session = action(live, session, "feedback", "Try a different count")
    assert session["attempts"] == 5 and session["phase"] == "done"
    with pytest.raises(ValueError, match="ended"):
        action(live, session, "feedback", "Continue")

    fresh = live.start_custom("superhero", "Count publishers")
    live.invoker.outputs = [sql_output()]
    args = (fresh["id"], 0, "same-request", "generate")
    first = live.action(*args)
    assert live.action(*args) == first
    assert live.get(fresh["id"])["calls"] == 1


def test_custom_failed_call_preserves_input_and_counts_allowance(live):
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [RuntimeError("Mock provider failure")]
    with pytest.raises(RuntimeError):
        action(live, session, "generate")
    refreshed = live.get(session["id"])
    assert refreshed["calls"] == 1 and refreshed["steps"] == []
    assert refreshed["phase"] == "ready" and refreshed["revision"] == 0
    live.sessions[session["id"]]["calls"] = 12
    with pytest.raises(ValueError, match="12-call"):
        action(live, refreshed, "generate")
    assert len(live.invoker.prompts) == 1
    assert action(live, refreshed, "stop")["phase"] == "done"


def test_custom_routes_are_free_until_generate(live):
    client = TestClient(create_app(live.store, live), base_url="http://127.0.0.1:8765")
    status = client.get("/api/status").json()
    headers = {"Origin": "http://127.0.0.1:8765", "X-Explorer-Token": status["token"]}
    assert len(client.get("/api/databases").json()) == len(DATABASE_GUIDES)
    assert client.get("/api/databases/superhero").json()["tables"]
    body = {"db_id": "superhero", "question": "Count heroes"}
    assert client.post("/api/custom", json=body).status_code == 403
    response = client.post("/api/custom", json=body, headers=headers)
    assert response.status_code == 200
    assert response.json()["phase"] == "ready"
    assert live.invoker.prompts == []


def test_summary_agrees_with_frozen_analysis(store):
    summary = store.summary()
    assert [m["successes"] for m in summary["methods"].values()] == [662, 769, 1189]
    assert summary["initial_failures"] == 872
    assert summary["methods"]["sql_only_recovery"]["recovered"] == 107
    assert summary["methods"]["reactive_clarification"]["recovered"] == 527
    assert round(summary["difference"], 2) == 27.38
