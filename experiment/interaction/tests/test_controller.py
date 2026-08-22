from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from interaction.context import ContextBundle
from interaction.controller import (
    AgentAction,
    DecisionRequest,
    InteractionConfig,
    InteractionController,
    PolicyTurn,
    SubmissionJudgement,
)
from interaction.trajectory import BenchmarkTask, Usage


class StaticContextBuilder:
    def build(self, task: BenchmarkTask, database_path: Path) -> ContextBundle:
        prompt = f"DATABASE {task.db_id}\nQUESTION {task.question}\nSCHEMA items(value)"
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        return ContextBundle(
            prompt=prompt,
            prompt_sha256=digest,
            schema_sha256=digest,
            description_sha256=digest,
            retrieved_values=(),
            retrieval_queries=0,
            retrieval_elapsed_ms=0.0,
            retrieval_cache_hit=False,
        )


class ScriptedPolicyModel:
    requested_model = "scripted-policy-model"
    reasoning_effort = "none"

    def __init__(self, actions: list[AgentAction]) -> None:
        self.actions = list(actions)
        self.requests: list[DecisionRequest] = []

    def decide(self, request: DecisionRequest) -> PolicyTurn:
        self.requests.append(request)
        action = self.actions.pop(0)
        self.assert_action_allowed(action, request)
        return PolicyTurn(
            response_id=f"response-{len(self.requests)}",
            action=action,
            raw_output=json.dumps(action.to_dict()),
            usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12),
            elapsed_ms=1.0,
        )

    @staticmethod
    def assert_action_allowed(action: AgentAction, request: DecisionRequest) -> None:
        if action.kind not in request.allowed_actions:
            raise AssertionError(
                f"{action.kind} not allowed in {request.allowed_actions} during {request.phase}"
            )


class ScriptedOracle:
    def __init__(
        self, accepted_sql: str, answer: str = "Use the narrower meaning."
    ) -> None:
        self.accepted_sql = accepted_sql
        self.answer_text = answer
        self.judged_sql: list[str] = []
        self.questions: list[str] = []

    def judge(self, task, sql, result) -> SubmissionJudgement:
        self.judged_sql.append(sql)
        accepted = sql == self.accepted_sql
        return SubmissionJudgement(
            accepted=accepted,
            feedback="The answer is correct."
            if accepted
            else "The answer is not correct.",
        )

    def answer(self, task, question, history) -> str:
        self.questions.append(question)
        return self.answer_text


def create_database(root: Path) -> None:
    folder = root / "demo"
    folder.mkdir(parents=True)
    with closing(sqlite3.connect(folder / "demo.sqlite")) as connection:
        connection.execute("CREATE TABLE items (value INTEGER)")
        connection.executemany("INSERT INTO items VALUES (?)", [(12,), (9,)])
        connection.commit()


def task() -> BenchmarkTask:
    return BenchmarkTask(
        question_id="mini-demo-1",
        db_id="demo",
        question="Return the intended value.",
        evidence=None,
        difficulty="interactive",
    )


class InteractionControllerTest(unittest.TestCase):
    def test_no_user_finishes_on_first_executable_submission_without_gold_feedback(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            submitted = "SELECT value FROM items WHERE value = 12"
            model = ScriptedPolicyModel([AgentAction.submit(submitted)])
            oracle = ScriptedOracle("SELECT value FROM items WHERE value = 9")
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(policy="no_user", max_user_turns=0),
            )

            trajectory = controller.run(task(), run_id="no-user-test")

            self.assertEqual(trajectory.termination, "submitted_executable_sql")
            self.assertEqual(trajectory.final_sql, submitted)
            self.assertEqual(oracle.judged_sql, [])
            self.assertEqual(oracle.questions, [])

    def test_proactive_is_structurally_ask_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            model = ScriptedPolicyModel(
                [
                    AgentAction.ask("Which meaning should I use?"),
                    AgentAction.submit("SELECT value FROM items WHERE value = 9"),
                ]
            )
            controller = InteractionController(
                model=model,
                oracle=ScriptedOracle("SELECT value FROM items WHERE value = 9"),
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(policy="proactive", max_user_turns=4),
            )

            trajectory = controller.run(task(), run_id="proactive-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(model.requests[0].allowed_actions, ("ask_user",))

    def test_reactive_round_conditions_next_sql_on_clarification(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            model = ScriptedPolicyModel(
                [
                    AgentAction.submit("SELECT value FROM items WHERE value = 12"),
                    AgentAction.ask("Should I use the narrower meaning?"),
                    AgentAction.submit("SELECT value FROM items WHERE value = 9"),
                ]
            )
            oracle = ScriptedOracle("SELECT value FROM items WHERE value = 9")
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(policy="reactive", max_user_turns=4),
            )

            trajectory = controller.run(task(), run_id="reactive-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(
                trajectory.final_sql, "SELECT value FROM items WHERE value = 9"
            )
            self.assertEqual(oracle.questions, ["Should I use the narrower meaning?"])
            self.assertEqual(
                oracle.judged_sql,
                [
                    "SELECT value FROM items WHERE value = 12",
                    "SELECT value FROM items WHERE value = 9",
                ],
            )
            self.assertEqual(
                sum(event.kind == "user_clarification" for event in trajectory.events),
                1,
            )
            final_request = model.requests[-1]
            self.assertTrue(
                any(
                    record.get("kind") == "user_clarification"
                    and record.get("answer") == "Use the narrower meaning."
                    for record in final_request.history
                )
            )

    def test_guard_retries_same_rejected_result_without_second_user_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            first = "SELECT value FROM items WHERE value = 12"
            same_result = "SELECT 6 + 6 AS value"
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedPolicyModel(
                [
                    AgentAction.submit(first),
                    AgentAction.ask("Which meaning did you intend?"),
                    AgentAction.submit(same_result),
                    AgentAction.submit(correct),
                ]
            )
            oracle = ScriptedOracle(correct)
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(
                    policy="reactive",
                    max_user_turns=4,
                    progress_guard=True,
                    max_guard_retries=2,
                ),
            )

            trajectory = controller.run(task(), run_id="guard-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(oracle.questions), 1)
            self.assertEqual(oracle.judged_sql, [first, correct])
            repeats = [
                event
                for event in trajectory.events
                if event.kind == "repeated_rejected_result"
            ]
            self.assertEqual(len(repeats), 1)
            self.assertEqual(repeats[0].payload["guard_action"], "internal_retry")
            retry_request = model.requests[-1]
            self.assertTrue(
                any(
                    record.get("kind") == "guard_feedback"
                    for record in retry_request.history
                )
            )

    def test_guard_off_exposes_same_result_and_consumes_another_question(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            first = "SELECT value FROM items WHERE value = 12"
            same_result = "SELECT 6 + 6 AS value"
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedPolicyModel(
                [
                    AgentAction.submit(first),
                    AgentAction.ask("Which meaning did you intend?"),
                    AgentAction.submit(same_result),
                    AgentAction.ask("Can you clarify once more?"),
                    AgentAction.submit(correct),
                ]
            )
            oracle = ScriptedOracle(correct)
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(
                    policy="reactive",
                    max_user_turns=4,
                    progress_guard=False,
                ),
            )

            trajectory = controller.run(task(), run_id="no-guard-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(oracle.questions), 2)
            self.assertEqual(oracle.judged_sql, [first, same_result, correct])
            repeats = [
                event
                for event in trajectory.events
                if event.kind == "repeated_rejected_result"
            ]
            self.assertEqual(len(repeats), 1)
            self.assertEqual(repeats[0].payload["guard_action"], "observe_only")

    def test_hybrid_can_probe_then_ask_then_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedPolicyModel(
                [
                    AgentAction.query(
                        "SELECT MIN(value), MAX(value) FROM items",
                        "Check the available value range.",
                    ),
                    AgentAction.ask("Should I use the narrower value?"),
                    AgentAction.submit(correct),
                ]
            )
            oracle = ScriptedOracle(correct)
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(policy="hybrid", max_user_turns=4),
            )

            trajectory = controller.run(task(), run_id="hybrid-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(oracle.questions), 1)
            self.assertEqual(oracle.judged_sql, [correct])
            self.assertEqual(
                sum(event.kind == "database_probe" for event in trajectory.events),
                1,
            )
            self.assertEqual(
                model.requests[0].allowed_actions,
                ("ask_user", "query_database", "submit_sql", "stop"),
            )

    def test_hybrid_must_act_on_answer_before_asking_again(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedPolicyModel(
                [
                    AgentAction.ask("Should I use the narrower value?"),
                    AgentAction.query(
                        "SELECT MIN(value), MAX(value) FROM items",
                        "Check the selected value range.",
                    ),
                    AgentAction.submit(correct),
                ]
            )
            controller = InteractionController(
                model=model,
                oracle=ScriptedOracle(correct),
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(policy="hybrid", max_user_turns=4),
            )

            trajectory = controller.run(task(), run_id="hybrid-ask-act-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            after_answer = model.requests[1:]
            self.assertTrue(after_answer)
            self.assertTrue(
                all(
                    "ask_user" not in request.allowed_actions
                    for request in after_answer
                )
            )
            self.assertEqual(
                after_answer[0].allowed_actions,
                ("query_database", "submit_sql", "stop"),
            )
            self.assertEqual(
                controller._allowed_actions("generate_after_guard"),
                ("query_database", "submit_sql", "stop"),
            )

    def test_grounded_recovery_retries_invalid_metadata_without_user_turn(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedPolicyModel(
                [
                    AgentAction(
                        kind="ask_user",
                        question="What do you mean by intended value?",
                        review_source="AmbQuestion",
                        summary="The intended value is undefined.",
                        grounded_user_turns=(),
                    ),
                    AgentAction(
                        kind="ask_user",
                        question="What do you mean by intended value?",
                        review_source="AmbQuestion",
                        summary="The intended value is undefined.",
                        clarification_target="intended value",
                        grounded_user_turns=(),
                    ),
                    AgentAction(
                        kind="submit_sql",
                        sql=correct,
                        grounded_user_turns=(1,),
                        change_summary="Use the clarified narrower value.",
                        request_contract=("value: use the clarified meaning",),
                    ),
                ]
            )
            oracle = ScriptedOracle(correct)
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(
                    policy="hybrid",
                    max_user_turns=1,
                    recovery_mode="grounded",
                ),
            )

            trajectory = controller.run(task(), run_id="grounding-retry-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(oracle.questions), 1)
            self.assertEqual(
                sum(
                    event.kind == "recovery_protocol_retry"
                    for event in trajectory.events
                ),
                1,
            )

    def test_grounded_recovery_gets_sql_only_attempts_after_last_user_turn(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            create_database(root)
            wrong = "SELECT value FROM items WHERE value = 12"
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedPolicyModel(
                [
                    AgentAction(
                        kind="ask_user",
                        question="What do you mean by intended value?",
                        review_source="AmbQuestion",
                        summary="The intended value is undefined.",
                        clarification_target="intended value",
                        grounded_user_turns=(),
                    ),
                    AgentAction(
                        kind="submit_sql",
                        sql=wrong,
                        grounded_user_turns=(1,),
                        change_summary="Use the clarified interpretation.",
                        request_contract=("value: use the clarified meaning",),
                    ),
                    AgentAction(
                        kind="submit_sql",
                        sql=correct,
                        grounded_user_turns=(1,),
                        change_summary="Repair the filter after the rejected result.",
                        request_contract=("value: use the clarified meaning",),
                    ),
                ]
            )
            oracle = ScriptedOracle(correct)
            controller = InteractionController(
                model=model,
                oracle=oracle,
                database_root=root,
                context_builder=StaticContextBuilder(),
                config=InteractionConfig(
                    policy="hybrid",
                    max_user_turns=1,
                    recovery_mode="grounded",
                    max_post_turn_recovery_submissions=2,
                ),
            )

            trajectory = controller.run(task(), run_id="post-turn-recovery-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(oracle.questions), 1)
            self.assertEqual(oracle.judged_sql, [wrong, correct])
            self.assertEqual(
                sum(
                    event.kind == "post_turn_recovery_submission"
                    for event in trajectory.events
                ),
                1,
            )


if __name__ == "__main__":
    unittest.main()
