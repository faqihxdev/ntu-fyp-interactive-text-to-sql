from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from interaction.controller import SubmissionJudgement
from interaction.official_protocol import (
    MiniInteractPrivateBudgetProvider,
    OfficialAction,
    OfficialBudget,
    OfficialDecisionRequest,
    OfficialMiniInteractConfig,
    OfficialMiniInteractController,
    OfficialMiniInteractTools,
    OfficialPolicyTurn,
    allowed_actions,
    format_sra_question,
    load_official_public_tasks,
    normalize_sra_action,
    summarize_official_protocol,
    validate_sra_action,
)
from interaction.trajectory import BenchmarkTask, Usage


class ScriptedOfficialModel:
    requested_model = "scripted-official-model"
    reasoning_effort = "none"

    def __init__(self, actions: list[OfficialAction]) -> None:
        self.actions = list(actions)
        self.requests: list[OfficialDecisionRequest] = []

    def decide(self, request: OfficialDecisionRequest) -> OfficialPolicyTurn:
        self.requests.append(request)
        action = self.actions.pop(0)
        return OfficialPolicyTurn(
            response_id=f"response-{len(self.requests)}",
            action=action,
            raw_output=json.dumps(action.to_dict()),
            usage=Usage(input_tokens=10, output_tokens=2, total_tokens=12),
            elapsed_ms=1.0,
        )


class ScriptedOracle:
    def __init__(
        self, accepted_sql: str, *, invalid_sqls: set[str] | None = None
    ) -> None:
        self.accepted_sql = accepted_sql
        self.invalid_sqls = invalid_sqls or set()
        self.questions: list[str] = []
        self.submissions: list[str] = []

    def answer(self, task, question, history):
        self.questions.append(question)
        return "Use the narrower definition."

    def judge(self, task, sql, result):
        self.submissions.append(sql)
        accepted = sql == self.accepted_sql
        return SubmissionJudgement(
            accepted=accepted,
            feedback="Correct." if accepted else "Incorrect.",
            metadata={
                "execution_error": sql in self.invalid_sqls,
                "timeout_error": False,
            },
        )


class StaticBudgetProvider:
    def __init__(self, total: float) -> None:
        self.total = total

    def total_budget(self, task, *, user_patience_budget):
        return self.total


def task() -> BenchmarkTask:
    return BenchmarkTask(
        question_id="demo_1",
        db_id="demo",
        question="Return the intended value.",
        evidence="THIS MUST NOT ENTER THE INITIAL POLICY REQUEST",
        difficulty="interactive",
    )


def create_dataset(root: Path) -> tuple[Path, Path]:
    database_root = root / "data"
    folder = database_root / "demo"
    folder.mkdir(parents=True)
    with closing(sqlite3.connect(folder / "demo.sqlite")) as connection:
        connection.execute("CREATE TABLE items (value INTEGER)")
        connection.executemany("INSERT INTO items VALUES (?)", [(12,), (9,)])
        connection.commit()
    (folder / "demo_schema.txt").write_text(
        "CREATE TABLE items (value INTEGER);", encoding="utf-8"
    )
    (folder / "demo_column_meaning_base.json").write_text(
        json.dumps({"demo|items|value": "The measured value."}), encoding="utf-8"
    )
    (folder / "demo_kb.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": 1,
                        "knowledge": "Visible rule",
                        "description": "Visible",
                        "definition": "Use nine.",
                    }
                ),
                json.dumps(
                    {
                        "id": 2,
                        "knowledge": "Hidden rule",
                        "description": "Hidden",
                        "definition": "Do not expose.",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    public_row = {
        "instance_id": "demo_1",
        "selected_database": "demo",
        "amb_user_query": "Return the intended value.",
        "knowledge_ambiguity": [{"deleted_knowledge": 2}],
    }
    private_row = {
        **public_row,
        "user_query_ambiguity": {"critical_ambiguity": [{}, {}]},
        "knowledge_ambiguity": [
            {"deleted_knowledge": 2},
            {"deleted_knowledge": None},
        ],
    }
    public_path = database_root / "mini_interact.jsonl"
    private_path = root / "private.jsonl"
    public_path.write_text(json.dumps(public_row) + "\n", encoding="utf-8")
    private_path.write_text(json.dumps(private_row) + "\n", encoding="utf-8")
    return public_path, private_path


class OfficialProtocolTest(unittest.TestCase):
    def test_official_task_loader_keeps_paid_context_out_of_task(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)

            loaded = load_official_public_tasks(public_path)

            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].question, "Return the intended value.")
            self.assertIsNone(loaded[0].evidence)

    def test_released_budget_formula_and_costs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, private_path = create_dataset(root)
            provider = MiniInteractPrivateBudgetProvider.from_file(private_path)

            total = provider.total_budget(task(), user_patience_budget=6)
            budget = OfficialBudget(total)
            ask = budget.charge("ask")
            lookup = budget.charge("get_column_meaning")

            self.assertEqual(total, 20.0)
            self.assertEqual(ask["cost"], 2.0)
            self.assertEqual(lookup["cost"], 0.5)
            self.assertEqual(budget.remaining, 17.5)

    def test_force_submit_matches_released_budget_behavior(self) -> None:
        budget = OfficialBudget(1.0)

        budget.charge("execute")

        self.assertTrue(budget.force_submit)
        with self.assertRaisesRegex(ValueError, "must be submit"):
            budget.charge("ask")
        budget.charge("submit")
        self.assertEqual(budget.remaining, -3.0)

    def test_policy_constraints_do_not_relabel_sphinteract(self) -> None:
        self.assertEqual(
            allowed_actions("sphinteract_cq", "discovery", force_submit=False),
            ("get_schema",),
        )
        self.assertNotIn(
            "ask",
            allowed_actions("sphinteract_cq", "generate_initial", force_submit=False),
        )
        self.assertEqual(
            allowed_actions("sphinteract_cq", "reactive_sra", force_submit=False),
            ("ask", "no_ambiguity"),
        )
        self.assertEqual(
            allowed_actions("proactive", "repair_submission", force_submit=False),
            ("submit",),
        )
        self.assertEqual(
            allowed_actions("hybrid", "initial", force_submit=True),
            ("submit",),
        )

    def test_reference_question_does_not_require_sra_metadata(self) -> None:
        OfficialAction(kind="ask", question="Which value do you mean?").validate()

    def test_sra_requires_four_reviews_and_three_plus_one_options(self) -> None:
        action = OfficialAction(
            kind="ask",
            question="Which interpretation should I use?",
            summary="The requested definition is unresolved.",
            reviews={
                "AmbQuestion": "Two meanings remain.",
                "AmbColumn": "The column is known.",
                "AmbOutput": "The output is known.",
                "AmbValue": "The value depends on the meaning.",
            },
            options=(
                "Meaning one",
                "Meaning two",
                "Meaning three",
                "Other—please specify",
            ),
        )

        rendered = format_sra_question(action)

        self.assertIn("A. Meaning one", rendered)
        self.assertIn("D. Other—please specify", rendered)

    def test_sra_normalizer_fills_only_boilerplate_metadata(self) -> None:
        raw = OfficialAction(
            kind="ask",
            question="Which interpretation should I use?",
            reviews={"AmbQuestion": "Two meanings remain."},
            options=("Meaning one", "Meaning two", "Meaning three"),
        )

        normalized, changes = normalize_sra_action(raw)

        self.assertEqual(
            normalized.summary, "A clarification-worthy ambiguity was identified."
        )
        self.assertEqual(normalized.reviews["AmbQuestion"], "Two meanings remain.")
        self.assertEqual(
            normalized.reviews["AmbColumn"],
            "No additional ambiguity was specified.",
        )
        self.assertEqual(normalized.options[-1], "Other—please specify")
        self.assertIn("defaulted_summary", changes)
        self.assertIn("appended_open_option", changes)
        format_sra_question(normalized)

    def test_sra_normalizer_moves_existing_open_option_to_fourth(self) -> None:
        raw = OfficialAction(
            kind="ask",
            question="Which interpretation should I use?",
            summary="One choice remains unresolved.",
            reviews={source: "Reviewed." for source in (
                "AmbQuestion",
                "AmbColumn",
                "AmbOutput",
                "AmbValue",
            )},
            options=("Other—please specify", "One", "Two", "Three"),
        )

        normalized, changes = normalize_sra_action(raw)

        self.assertEqual(normalized.options, ("One", "Two", "Three", "Other—please specify"))
        self.assertEqual(changes, ("moved_open_option_to_fourth",))
        format_sra_question(normalized)

    def test_sra_normalizer_does_not_invent_a_missing_closed_option(self) -> None:
        raw = OfficialAction(
            kind="ask",
            question="Which interpretation should I use?",
            summary="One choice remains unresolved.",
            reviews={source: "Reviewed." for source in (
                "AmbQuestion",
                "AmbColumn",
                "AmbOutput",
                "AmbValue",
            )},
            options=("One", "Two", "Other—please specify"),
        )

        normalized, changes = normalize_sra_action(raw)

        self.assertEqual(normalized.options, raw.options)
        self.assertEqual(changes, ())
        with self.assertRaisesRegex(ValueError, "exactly four"):
            format_sra_question(normalized)

    def test_sra_normalizer_supplies_no_ambiguity_metadata(self) -> None:
        normalized, changes = normalize_sra_action(
            OfficialAction(kind="no_ambiguity")
        )

        self.assertEqual(normalized.summary, "No actionable ambiguity was identified.")
        self.assertEqual(set(normalized.reviews), {
            "AmbQuestion",
            "AmbColumn",
            "AmbOutput",
            "AmbValue",
        })
        self.assertEqual(normalized.options, ())
        self.assertIn("defaulted_summary", changes)
        validate_sra_action(normalized)

    def test_controller_records_raw_sra_action_before_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            correct = "SELECT value FROM items WHERE value = 9"
            raw_sra = OfficialAction(
                kind="ask",
                question="Which definition should I use?",
                reviews={"AmbQuestion": "Two definitions remain."},
                options=("Broad", "Narrow", "Both"),
            )
            model = ScriptedOfficialModel(
                [
                    OfficialAction(kind="get_schema"),
                    raw_sra,
                    OfficialAction(kind="submit", sql=correct),
                ]
            )

            oracle = ScriptedOracle(correct)
            trajectory = OfficialMiniInteractController(
                model=model,
                oracle=oracle,
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(20),
                config=OfficialMiniInteractConfig(policy="proactive"),
            ).run(task(), run_id="normalizer-test")

            policy_events = [
                event for event in trajectory.events if event.kind == "policy_decision"
            ]
            normalization_events = [
                event for event in trajectory.events if event.kind == "sra_normalization"
            ]
            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(policy_events[1].payload["action"]["options"]), 3)
            self.assertEqual(len(normalization_events), 1)
            self.assertIn("D. Other—please specify", oracle.questions[0])

    def test_paid_tools_mask_deleted_knowledge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            tools = OfficialMiniInteractTools.from_file(
                dataset_root=root / "data", public_data=public_path
            )

            names = tools.perform(
                task(),
                OfficialAction(kind="get_all_external_knowledge_names"),
                timeout_seconds=1,
                max_result_rows=100,
            )
            hidden = tools.perform(
                task(),
                OfficialAction(
                    kind="get_knowledge_definition", knowledge_name="Hidden rule"
                ),
                timeout_seconds=1,
                max_result_rows=100,
            )

            self.assertIn("Visible rule", names.text)
            self.assertNotIn("Hidden rule", names.text)
            self.assertEqual(hidden.text, "Knowledge not found or not accessible.")

    def test_sphinteract_sequence_is_submit_then_ask_then_submit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            wrong = "SELECT value FROM items WHERE value = 12"
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedOfficialModel(
                [
                    OfficialAction(kind="get_schema"),
                    OfficialAction(kind="submit", sql=wrong),
                    OfficialAction(
                        kind="ask",
                        question="Which definition should I use?",
                        review_source="AmbQuestion",
                        summary="The intended definition remains unresolved.",
                        reviews={
                            "AmbQuestion": "Two definitions remain.",
                            "AmbColumn": "The value column is known.",
                            "AmbOutput": "One value is requested.",
                            "AmbValue": "The definition changes the filter.",
                        },
                        options=(
                            "Use the broad definition",
                            "Use the narrow definition",
                            "Use both definitions",
                            "Other—please specify",
                        ),
                    ),
                    OfficialAction(kind="submit", sql=correct),
                ]
            )
            oracle = ScriptedOracle(correct)
            controller = OfficialMiniInteractController(
                model=model,
                oracle=oracle,
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(12),
                config=OfficialMiniInteractConfig(policy="sphinteract_cq"),
            )

            trajectory = controller.run(task(), run_id="sphinteract-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(oracle.submissions, [wrong, correct])
            self.assertEqual(len(oracle.questions), 1)
            self.assertIn("A. Use the broad definition", oracle.questions[0])
            self.assertNotIn("ask", model.requests[0].allowed_actions)
            self.assertNotIn("ask", model.requests[1].allowed_actions)
            self.assertEqual(model.requests[2].allowed_actions, ("ask", "no_ambiguity"))
            self.assertNotIn("THIS MUST NOT", json.dumps(model.requests[0].to_dict()))
            summary = summarize_official_protocol([trajectory])
            self.assertEqual(summary["success_rate_percent"], 100.0)
            self.assertEqual(summary["mean_bird_coins"], 9.0)

    def test_proactive_asks_once_and_never_after_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            wrong = "SELECT value FROM items WHERE value = 12"
            correct = "SELECT value FROM items WHERE value = 9"
            sra = OfficialAction(
                kind="ask",
                question="Which definition should I use?",
                review_source="AmbQuestion",
                summary="One definition is unresolved.",
                reviews={
                    source: "Reviewed."
                    for source in (
                        "AmbQuestion",
                        "AmbColumn",
                        "AmbOutput",
                        "AmbValue",
                    )
                },
                options=("Broad", "Narrow", "Both", "Other—please specify"),
            )
            model = ScriptedOfficialModel(
                [
                    OfficialAction(kind="get_schema"),
                    sra,
                    OfficialAction(kind="submit", sql=wrong),
                    OfficialAction(kind="submit", sql=correct),
                ]
            )
            trajectory = OfficialMiniInteractController(
                model=model,
                oracle=ScriptedOracle(correct),
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(20),
                config=OfficialMiniInteractConfig(policy="proactive"),
            ).run(task(), run_id="proactive-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(len(model.requests), 4)
            self.assertNotIn("ask", model.requests[3].allowed_actions)

    def test_hybrid_no_ambiguity_then_uses_reactive_sra_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            wrong = "SELECT value FROM items WHERE value = 12"
            correct = "SELECT value FROM items WHERE value = 9"
            reviews = {
                source: "Reviewed."
                for source in (
                    "AmbQuestion",
                    "AmbColumn",
                    "AmbOutput",
                    "AmbValue",
                )
            }
            model = ScriptedOfficialModel(
                [
                    OfficialAction(kind="get_schema"),
                    OfficialAction(
                        kind="no_ambiguity",
                        summary="No useful proactive distinction remains.",
                        reviews=reviews,
                    ),
                    OfficialAction(kind="submit", sql=wrong),
                    OfficialAction(
                        kind="ask",
                        question="Which definition should I use?",
                        review_source="AmbQuestion",
                        summary="The rejected SQL exposed one distinction.",
                        reviews=reviews,
                        options=(
                            "Broad",
                            "Narrow",
                            "Both",
                            "Other—please specify",
                        ),
                    ),
                    OfficialAction(kind="submit", sql=correct),
                ]
            )
            trajectory = OfficialMiniInteractController(
                model=model,
                oracle=ScriptedOracle(correct),
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(20),
                config=OfficialMiniInteractConfig(policy="hybrid"),
            ).run(task(), run_id="hybrid-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(
                len([e for e in trajectory.events if e.kind == "sra_no_ambiguity"]), 1
            )
            self.assertEqual(
                len([e for e in trajectory.events if e.kind == "user_clarification"]), 1
            )
            self.assertEqual(
                sum(
                    event.payload["cost"]
                    for event in trajectory.events
                    if event.kind == "bird_coin_charge"
                ),
                9.0,
            )

    def test_invalid_submission_gets_one_immediate_submit_only_repair(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            invalid = "SELECT missing FROM items"
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedOfficialModel(
                [
                    OfficialAction(kind="get_schema"),
                    OfficialAction(kind="submit", sql=invalid),
                    OfficialAction(kind="submit", sql=correct),
                ]
            )
            trajectory = OfficialMiniInteractController(
                model=model,
                oracle=ScriptedOracle(correct, invalid_sqls={invalid}),
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(20),
                config=OfficialMiniInteractConfig(policy="sphinteract_cq"),
            ).run(task(), run_id="invalid-repair-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(model.requests[2].allowed_actions, ("submit",))
            exposed = [
                event.payload["submission_kind"]
                for event in trajectory.events
                if event.kind == "candidate_exposed"
            ]
            self.assertEqual(exposed, ["normal", "invalid_sql_repair"])

    def test_reference_retrieval_is_paid_and_initial_context_is_question_only(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            correct = "SELECT value FROM items WHERE value = 9"
            model = ScriptedOfficialModel(
                [
                    OfficialAction(kind="get_schema"),
                    OfficialAction(kind="submit", sql=correct),
                ]
            )
            trajectory = OfficialMiniInteractController(
                model=model,
                oracle=ScriptedOracle(correct),
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(10),
                config=OfficialMiniInteractConfig(policy="mini_react_port"),
            ).run(task(), run_id="reference-test")

            self.assertEqual(trajectory.termination, "accepted_submission")
            self.assertEqual(model.requests[0].history, ())
            self.assertNotIn("THIS MUST NOT", json.dumps(model.requests[0].to_dict()))
            self.assertIn("CREATE TABLE", model.requests[1].history[0]["observation"])
            self.assertEqual(
                sum(
                    event.payload["cost"]
                    for event in trajectory.events
                    if event.kind == "bird_coin_charge"
                ),
                4.0,
            )

    def test_rejected_submission_at_depletion_terminates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            public_path, _ = create_dataset(root)
            wrong = "SELECT value FROM items WHERE value = 12"
            trajectory = OfficialMiniInteractController(
                model=ScriptedOfficialModel([OfficialAction(kind="submit", sql=wrong)]),
                oracle=ScriptedOracle("SELECT value FROM items WHERE value = 9"),
                tools=OfficialMiniInteractTools.from_file(
                    dataset_root=root / "data", public_data=public_path
                ),
                budget_provider=StaticBudgetProvider(3),
                config=OfficialMiniInteractConfig(policy="mini_react_port"),
            ).run(task(), run_id="forced-submit-test")

            self.assertEqual(trajectory.termination, "forced_submission_rejected")


if __name__ == "__main__":
    unittest.main()
