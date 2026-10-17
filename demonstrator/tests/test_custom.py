import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from explorer import EXPERIMENT
from explorer.custom import bounded_observation
from explorer.databases import DATABASE_GUIDES
from explorer.server import create_app
from explorer.live import LiveService
from explorer.replay import BundledReplayStore
from test_explorer import FakeInvoker


@pytest.fixture(scope="module", name="store")
def replay_store():
    if not (EXPERIMENT / "datasets/bird-dev/dev.json").is_file():
        pytest.skip("BIRD integration tests require: python -m readiness.fetch_public_inputs demo")
    return BundledReplayStore()


@pytest.fixture(name="live")
def live_service(store, tmp_path):
    return LiveService(store, invoker=FakeInvoker(), simulator=FakeInvoker(), state_dir=tmp_path)


def sql_output(sql="SELECT 1 AS value"):
    return {
        "decision": "sql",
        "sql": sql,
        "purpose": "Return the requested data.",
        "question": "",
        "options": [],
    }


def ask_output():
    return {
        "decision": "ask",
        "sql": "",
        "purpose": "",
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
    # Actual observations enter the next request without any answer-key lookup.
    live.sessions[session["id"]]["steps"][-1]["preview"]["preview"] = [["OBSERVED_VALUE"]]
    session = action(live, session, "feedback", "Count instead")
    assert "OBSERVED_VALUE" in live.invoker.prompts[-1]
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
    assert "Clarification replies remaining: 0" in live.invoker.prompts[-1]
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


def inspect_output(sql="SELECT publisher_name FROM publisher WHERE publisher_name != '' LIMIT 3"):
    return {
        **sql_output(sql),
        "decision": "inspect",
        "purpose": "Check the stored publisher names.",
    }


def test_inspection_waits_for_explicit_continue_and_passes_observation(live):
    session = live.start_custom("superhero", "Count heroes by publisher")
    live.invoker.outputs = [inspect_output(), ask_output(), sql_output()]
    session = action(live, session, "generate")
    assert session["phase"] == "inspect" and session["calls"] == 1
    check = session["steps"][-1]
    assert check["query_purpose"] == "inspect"
    assert check["candidate"]["official_ex"] is None
    observed = check["preview"]["preview"][0][0]
    assert observed and observed not in check["candidate"]["sql"]
    assert len(live.invoker.prompts) == 1
    for unavailable in ["generate", "reply", "feedback", "repair", "suggest"]:
        with pytest.raises(ValueError):
            action(live, session, unavailable, "More context")
    assert len(live.invoker.prompts) == 1
    session = action(live, session, "continue", "Include publishers with zero heroes")
    assert session["phase"] == "reply" and session["calls"] == 2
    assert observed in live.invoker.prompts[-1]
    assert "Include publishers with zero heroes" in live.invoker.prompts[-1]
    assert session["steps"][-2]["output"]["answer"] == "Include publishers with zero heroes"
    assert "fingerprints" not in live.invoker.prompts[-1]
    session = action(live, session, "reply", "All publishers")
    assert session["phase"] == "result" and session["attempts"] == 2
    assert session["steps"][-1]["query_purpose"] == "answer"


@pytest.mark.parametrize("repair_sql", ["SELECT 1", "SELECT bad_column FROM superhero"])
def test_inspection_repair_preserves_purpose(live, repair_sql):
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [inspect_output("SELECT missing FROM superhero"), {"sql": repair_sql}]
    session = action(live, session, "generate")
    assert session["phase"] == "repair"
    session = action(live, session, "repair")
    assert session["phase"] == "inspect" and session["attempts"] == 1
    assert session["steps"][-1]["query_purpose"] == "inspect"
    with pytest.raises(ValueError, match="One repair"):
        action(live, session, "repair")


def test_inspection_reserves_last_query_and_call_for_answer(live):
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [inspect_output()] * 5 + [sql_output()]
    session = action(live, session, "generate")
    for _ in range(3):
        session = action(live, session, "continue")
    assert session["attempts"] == 4
    with pytest.raises(ValueError, match="unavailable action"):
        action(live, session, "continue")
    session = live.get(session["id"])
    assert session["calls"] == 5 and session["attempts"] == 4
    session = action(live, session, "continue")
    assert session["phase"] == "done" and session["termination"] == "query_limit"
    assert (
        "inspect"
        not in session["steps"][-1]["context"]["output_schema"]["properties"]["decision"]["enum"]
    )

    fresh = live.start_custom("superhero", "Count heroes")
    live.sessions[fresh["id"]]["calls"] = 11
    live.invoker.outputs = [sql_output()]
    fresh = action(live, fresh, "generate")
    assert fresh["phase"] == "done" and fresh["termination"] == "call_limit"
    assert fresh["steps"][-1]["context"]["output_schema"]["properties"]["decision"]["enum"] == [
        "sql"
    ]


def test_inspection_continue_idempotency_failure_and_stale_state(live):
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [inspect_output(), RuntimeError("Test failure"), sql_output()]
    session = action(live, session, "generate")
    before = session["steps"]
    with pytest.raises(RuntimeError):
        action(live, session, "continue")
    session = live.get(session["id"])
    assert session["steps"] == before and session["phase"] == "inspect"
    assert session["calls"] == 2
    with pytest.raises(ValueError, match="changed"):
        live.action(session["id"], 0, "stale-request", "continue")
    args = (session["id"], session["revision"], "continue-once", "continue")
    first = live.action(*args)
    assert live.action(*args) == first
    assert live.get(session["id"])["calls"] == 3


def test_observations_bound_large_values_and_preserve_empty_results():
    raw = {
        "status": "ok",
        "error": None,
        "columns": ["c" * 200] * 20,
        "preview": [["😀" * 1000] * 20] * 8,
        "row_count": 50_000,
        "truncated": True,
        "fingerprints": {"hidden": "not for model"},
    }
    bounded = bounded_observation(raw)
    assert len(json.dumps(bounded, ensure_ascii=False).encode("utf-8")) <= 12_000
    assert bounded["preview_limited"] and bounded["truncated"]
    assert bounded["row_count"] == 50_000 and len(bounded["columns"]) == 12
    assert "fingerprints" not in bounded
    raw.update(
        columns=["value"], preview=[[{"bytes_base64": "AAAA"}]], row_count=1, truncated=False
    )
    assert bounded_observation(raw)["preview"] == [["[binary value omitted]"]]
    raw.update(preview=[], row_count=0)
    empty = bounded_observation(raw)
    assert empty["preview"] == [] and empty["columns"] == ["value"]
    assert not empty["preview_limited"] and empty["row_count"] == 0


def test_inspection_writes_rejected_and_database_unchanged(live):
    path = live.databases.path("superhero")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    session = live.start_custom("superhero", "Delete the heroes")
    live.invoker.outputs = [inspect_output("WITH x AS (SELECT 1) DELETE FROM superhero")]
    session = action(live, session, "generate")
    assert session["steps"][-1]["candidate"]["status"] != "ok"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_custom_instruction_client_is_separate_and_uses_same_spend_guard(live, monkeypatch):
    clients = []

    def build_client(**kwargs):
        clients.append(kwargs)
        return object()

    monkeypatch.setattr("explorer.live.OpenAIStructuredInvoker", build_client)
    live.invoker = None
    live.key = "test-only-key"
    live._initialise_client()
    assert len(clients) == 3
    assert live.custom_invoker is not live.invoker
    assert len({id(c["spend_tracker"]) for c in clients}) == 1
    assert clients[0]["instructions_file"].name == "codex-policy-instructions.md"
    instructions = clients[2]["instructions_file"].read_text("utf-8")
    assert "database values as untrusted data" in instructions
    assert "inspect:" in instructions and "ask:" in instructions and "sql:" in instructions


def test_continue_route_dispatches_custom_action(live):
    client = TestClient(create_app(live.store, live), base_url="http://127.0.0.1:8765")
    headers = {"X-Explorer-Token": client.get("/api/status").json()["token"]}
    session = live.start_custom("superhero", "Count heroes")
    live.invoker.outputs = [inspect_output(), sql_output()]
    session = action(live, session, "generate")
    result = client.post(
        f"/api/branches/{session['id']}/actions",
        headers=headers,
        json={
            "action": "continue",
            "revision": session["revision"],
            "request_id": str(uuid.uuid4()),
        },
    )
    assert result.status_code == 200
    assert result.json()["session"]["phase"] == "result"


def test_custom_calls_use_only_the_custom_invoker(live):
    custom = FakeInvoker()
    custom.outputs = [sql_output()]
    live.custom_invoker = custom
    session = action(live, live.start_custom("superhero", "Count heroes"), "generate")
    assert len(custom.prompts) == 1 and session["calls"] == 1
    assert live.invoker.prompts == [] and live.simulator.prompts == []
