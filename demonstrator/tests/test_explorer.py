from copy import deepcopy
import hashlib
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from explorer.live import LiveError, LiveService
from explorer.replay import IDS, GROUPS, METHODS, ReplayStore
from explorer.server import create_app
from interaction.trajectory import Usage


@pytest.fixture(scope="module")
def store():
    store = ReplayStore()
    if not (store.run / "analysis.json").is_file() or not store.database_root.is_dir():
        pytest.skip("Raw-run integration tests require the original run and BIRD databases; test_bundle.py covers packaged replay.")
    return store


class FakeInvoker:
    instructions = "Test instructions without hidden gold."

    def __init__(self):
        self.prompts = []
        self.outputs = []

    def run(self, prompt, **kwargs):
        self.prompts.append(prompt)
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(output=output, usage=Usage(), served_model="test-only")


@pytest.fixture
def live(store, tmp_path):
    policy, simulator = FakeInvoker(), FakeInvoker()
    return LiveService(store, invoker=policy, simulator=simulator, state_dir=tmp_path)


def test_catalogue_outcomes_are_real(store):
    catalogue = {c["question_id"]: c for c in store.catalogue()["cases"]}
    assert sum(g["count"] for g in GROUPS) == 1534
    assert len(IDS) == 10
    expected = {
        "initial": (True, True, True),
        "both": (False, True, True),
        "retry": (False, True, False),
        "clarification": (False, False, True),
        "neither": (False, False, False),
    }
    for g in GROUPS:
        assert len(g["ids"]) == 2
        for i in g["ids"]:
            case = store.case(i)
            assert catalogue[i]["outcomes"] == {
                m: case["methods"][m]["success"] for m in METHODS
            }
            assert tuple(case["methods"][m]["success"] for m in METHODS) == expected[g["id"]]
            initial = [case["methods"][m]["steps"][0]["candidate"]["sql"] for m in METHODS]
            assert len(set(initial)) == 1


def test_all_replayed_prompts_verified(store):
    for i in IDS:
        for m in METHODS:
            for step in store.steps(i, m):
                context = store.context(i, m, step["id"])
                if step["role"] == "policy":
                    assert context["verified"]
                    assert (
                        hashlib.sha256(context["prompt"].encode()).hexdigest()
                        == step["prompt_sha256"]
                    )
                else:
                    assert context["private"]
                    assert "prompt" not in context


def test_bad_prompt_hash_is_not_presented_as_verified(store, tmp_path):
    copy = ReplayStore(tmp_path, store.database_root)
    copy.record = lambda i: store.record(i)
    copy.steps = lambda i, m: store.steps(i, m)
    with pytest.raises(ValueError, match="missing or its hash"):
        copy.context(1389, "reactive_clarification", 0)


def test_readonly_execution(store):
    path = store.database_root / "student_club/student_club.sqlite"
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    for statement in [
        "DROP TABLE event",
        "DELETE FROM event",
        "SELECT 1; DROP TABLE event",
        "WITH x AS (SELECT 1) DELETE FROM event",
    ]:
        assert store.execute(1389, statement)["status"] != "ok"
    assert store.execute(1389, "SELECT 1 AS safe")["preview"] == [[1]]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_replay_and_fork_do_not_invoke_models(store, live):
    app = create_app(store, live)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/catalogue").status_code == 200
        assert client.get("/api/cases/1389").status_code == 200
        assert client.get("/api/cases/1389/reactive_clarification/0/preview").status_code == 200
        token = client.get("/api/status").json()["token"]
        body = {
            "case_id": 1389,
            "method": "reactive_clarification",
            "step_id": 1,
            "mode": "original",
        }
        assert client.post("/api/branches", json=body).status_code == 403
        assert (
            client.post(
                "/api/branches",
                json=body,
                headers={"x-explorer-token": token, "origin": "https://malicious.example"},
            ).status_code
            == 403
        )
        assert (
            client.post("/api/branches", json=body, headers={"x-explorer-token": token}).status_code
            == 200
        )
        assert client.get("/api/cases/999999").status_code == 404
        assert client.get("/api/status", headers={"host": "attacker.example"}).status_code == 400
    assert live.invoker.prompts == live.simulator.prompts == []


def test_prefix_has_no_future_answer_and_does_not_mutate_saved_record(store, live):
    original = deepcopy(store.record(1389))
    branch = live.fork(1389, "reactive_clarification", 1, "changed")
    assert len(branch["steps"]) == 2
    assert not any(s["kind"] == "answer" for s in branch["steps"])
    live.invoker.outputs = [{"sql": "SELECT 1 AS custom"}]
    result = live.action(branch["id"], 0, "unique-request-1", "reply", "Return the number one.")
    prompt = live.invoker.prompts[0]
    assert "Return the number one." in prompt
    assert (
        store.record(1389)["arms"]["reactive_clarification"]["question_answers"][0]["answer"]
        not in prompt
    )
    assert len(result["session"]["steps"]) == 4
    assert result["session"]["steps"][-1]["candidate"]["official_ex"] is None
    assert store.record(1389) == original


def test_changed_intent_never_scores_or_uses_gold_simulator(live, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Changed intent must not read gold or call the evaluator.")

    monkeypatch.setattr(live.evaluator, "private_row", forbidden)
    monkeypatch.setattr(live.evaluator, "evaluate", forbidden)
    b = live.fork(1389, "reactive_clarification", 1, "changed")
    live.invoker.outputs = [{"answer": "I mean the largest single expense."}, {"sql": "SELECT 1"}]
    result = live.action(b["id"], 0, "suggest-1", "suggest")
    assert result["suggestion_kind"] == "possible reply, not an oracle"
    live.action(b["id"], 1, "reply-001", "reply", "Return one.")
    assert live.simulator.prompts == []


def test_original_target_accepts_gold_and_finishes(live):
    b = live.fork(1389, "reactive_clarification", 1, "original")
    gold = live.evaluator.private_row(live.store.task(1389))["SQL"]
    live.invoker.outputs = [{"sql": gold}]
    result = live.action(b["id"], 0, "original1", "reply", "Use the lowest single expense.")
    assert result["session"]["phase"] == "done"
    assert result["session"]["steps"][-1]["candidate"]["official_ex"]
    assert gold not in live.invoker.prompts[0]


def test_original_suggestion_is_private_and_does_not_submit(live):
    b = live.fork(1389, "reactive_clarification", 1, "original")
    live.simulator.outputs = [{"answer": "Choose the lowest single expense."}]
    result = live.action(b["id"], 0, "suggest-2", "suggest")
    assert result["suggestion_kind"] == "gold-conditioned"
    assert result["session"]["phase"] == "reply"
    assert len(result["session"]["steps"]) == 2
    assert live.invoker.prompts == []


def test_duplicate_request_is_free_and_stale_revision_rejected(live):
    b = live.fork(1389, "reactive_clarification", 1, "changed")
    live.invoker.outputs = [{"sql": "SELECT 1"}]
    first = live.action(b["id"], 0, "duplicate1", "reply", "One")
    again = live.action(b["id"], 0, "duplicate1", "reply", "Different text ignored")
    assert first == again
    assert len(live.invoker.prompts) == 1
    with pytest.raises(LiveError, match="changed"):
        live.action(b["id"], 0, "another-1", "ask", "Explain why")


def test_invalid_sql_repair_requires_click_and_can_only_happen_once(live):
    b = live.fork(1389, "reactive_clarification", 1, "changed")
    live.invoker.outputs = [{"sql": "BROKEN SQL"}, {"sql": "STILL BROKEN"}]
    r = live.action(b["id"], 0, "reply-001", "reply", "One")
    assert r["session"]["phase"] == "repair"
    assert len(live.invoker.prompts) == 1
    r = live.action(b["id"], 1, "repair-01", "repair")
    assert r["session"]["phase"] == "result"
    assert r["session"]["attempts"] == 2
    with pytest.raises(LiveError):
        live.action(b["id"], 2, "repair-02", "repair")


def test_cap_includes_inherited_prefix_and_changed_feedback_required(live):
    steps = live.store.steps(407, "reactive_clarification")
    last_question = [s for s in steps if s["kind"] == "question"][-1]
    b = live.fork(407, "reactive_clarification", last_question["id"], "changed")
    assert b["answers"] == 3 and b["attempts"] == 4
    live.invoker.outputs = [{"sql": "SELECT 1"}]
    r = live.action(b["id"], 0, "lastreply", "reply", "One")
    assert r["session"]["phase"] == "done"
    with pytest.raises(LiveError):
        live.action(b["id"], 1, "over-cap1", "ask", "More")


def test_failed_call_keeps_prefix_and_counts_attempted_api_call(live):
    b = live.fork(1389, "reactive_clarification", 1, "changed")
    live.invoker.outputs = [RuntimeError("test API failure")]
    with pytest.raises(RuntimeError):
        live.action(b["id"], 0, "failed001", "reply", "One")
    after = live.get(b["id"])
    assert after["steps"] == b["steps"]
    assert after["revision"] == 0
    assert after["calls"] == 1


def test_only_question_forks_allowed(live):
    for step in [-1, 0, 2, 500]:
        with pytest.raises(ValueError):
            live.fork(1389, "reactive_clarification", step, "original")


def test_next_clarification_waits_for_another_explicit_reply(live):
    b = live.fork(1389, "reactive_clarification", 1, "changed")
    live.invoker.outputs = [
        {"sql": "SELECT 1"},
        {
            "decision": "ask",
            "summary": "The requested value is unclear.",
            "reviews": {
                "AmbQuestion": "Which value?",
                "AmbTableColumn": "None",
                "AmbOutput": "One column",
                "AmbValue": "One or two",
            },
            "question": "Which value should I return?",
            "options": ["One", "Two", "Three", "Other—please specify"],
        },
    ]
    live.action(b["id"], 0, "first001", "reply", "One")
    with pytest.raises(LiveError, match="Describe"):
        live.action(b["id"], 1, "askempty", "ask")
    result = live.action(b["id"], 1, "nextq001", "ask", "I meant a different number.")
    assert result["session"]["phase"] == "reply"
    assert result["session"]["steps"][-1]["kind"] == "question"
    assert result["session"]["answers"] == 1
    assert "I meant a different number." in live.invoker.prompts[-1]
    assert len(live.invoker.prompts) == 2
    assert not live.simulator.prompts


def test_call_cap_and_in_flight_request_do_not_spend(live):
    b = live.fork(1389, "reactive_clarification", 1, "changed")
    live.sessions[b["id"]]["calls"] = 12
    with pytest.raises(LiveError, match="12-call"):
        live.action(b["id"], 0, "capped01", "reply", "One")
    live.call_lock.acquire()
    try:
        with pytest.raises(LiveError, match="Another live request"):
            live.action(b["id"], 0, "busy0001", "reply", "One")
    finally:
        live.call_lock.release()
    assert not live.invoker.prompts
