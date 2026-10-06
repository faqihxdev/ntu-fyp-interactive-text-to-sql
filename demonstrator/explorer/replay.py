"""Present selected records without rewriting the frozen experiment."""

from copy import deepcopy
from functools import lru_cache
import hashlib
import json
from pathlib import Path

from . import EXPERIMENT
from interaction.bird_dev_matched import build_schema_only_context, database_path
from interaction.sqlite_env import SQLiteEnvironment
from interaction.trajectory import BenchmarkTask

RUN_NAME = "api-bird-dev-clarification-value-v1-full1534-luna-high-20260830"
RUN = EXPERIMENT / "runs" / RUN_NAME
DATABASES = EXPERIMENT / "datasets/bird-dev/dev_databases/dev_databases"
METHODS = {
    "no_user": {"label": "No user", "description": "One initial SQL attempt, then stop."},
    "sql_only_recovery": {
        "label": "SQL-only recovery",
        "description": "Retry rejected SQL without asking.",
    },
    "reactive_clarification": {
        "label": "Reactive clarification",
        "description": "Ask after rejection, then rewrite SQL.",
    },
}
GROUPS = [
    {
        "id": "clarification",
        "label": "Clarification recovers",
        "count": 437,
        "description": "Only clarification reaches the gold result.",
        "ids": [1389, 780],
    },
    {
        "id": "both",
        "label": "Both recover",
        "count": 90,
        "description": "Retry and clarification both reach the gold result.",
        "ids": [794, 1364],
    },
    {
        "id": "retry",
        "label": "Retry alone recovers",
        "count": 17,
        "description": "Retry succeeds; clarification does not.",
        "ids": [1342, 1379],
    },
    {
        "id": "initial",
        "label": "Correct first time",
        "count": 662,
        "description": "The shared first SQL succeeds. Nobody asks.",
        "ids": [737, 820],
    },
    {
        "id": "neither",
        "label": "Neither recovers",
        "count": 328,
        "description": "Neither recovery method reaches the gold result.",
        "ids": [407, 952],
    },
]
NOTES = {
    1389: "The reply changes ‘lowest cost’ from an event’s total expenses to its lowest single expense.",
    780: "The clarification points to full_name rather than superhero_name for Hunter Zolomon.",
    794: "Both methods eventually use the numerical Speed attribute rather than a power name.",
    1364: "The target includes city, county and state. Both methods recover the missing output field.",
    1342: "Retry changes the date to 2019-09-04. Clarification keeps the original date string and misses.",
    1379: "Retry revises the meeting/date filters. Four clarification replies still do not recover the target.",
    737: "A successful shared initial query. Extra SQL attempts and questions are unnecessary here.",
    820: "All three methods stop after the same successful initial query.",
    407: "Clarification changes the query, but the final result still does not match the target.",
    952: "Both recovery methods keep producing queries that miss the target result.",
}
IDS = {i for g in GROUPS for i in g["ids"]}


class ReplayStore:
    def __init__(self, run: Path = RUN, database_root: Path = DATABASES):
        self.run = run
        self.database_root = database_root

    @lru_cache(maxsize=10)
    def record(self, case_id: int) -> dict:
        if case_id not in IDS:
            raise KeyError("Choose one of the ten available cases.")
        return json.loads((self.run / "tasks" / f"{case_id:04}.json").read_text("utf-8"))

    def task(self, case_id: int) -> BenchmarkTask:
        t = self.record(case_id)["task"]
        return BenchmarkTask(
            question_id=case_id,
            db_id=t["db_id"],
            question=t["question"],
            evidence=None,
            difficulty=t["difficulty"],
        )

    @lru_cache(maxsize=10)
    def schema(self, case_id: int) -> str:
        schema = build_schema_only_context(database_path(self.database_root, self.task(case_id)))
        expected = self.record(case_id)["schema_sha256"]
        if hashlib.sha256(schema.encode()).hexdigest() != expected:
            raise ValueError("Local schema differs from the saved run.")
        return schema

    def catalogue(self) -> dict:
        return {
            "run": RUN_NAME,
            "methods": METHODS,
            "groups": GROUPS,
            "cases": [
                {
                    **self.record(i)["task"],
                    "note": NOTES[i],
                    "group": g["id"],
                    "outcomes": {
                        m: self.record(i)["arms"][m]["official_success"] for m in METHODS
                    },
                }
                for g in GROUPS
                for i in g["ids"]
            ],
            "total_tasks": 1534,
        }

    @lru_cache(maxsize=30)
    def steps(self, case_id: int, method: str) -> list[dict]:
        if method not in METHODS:
            raise KeyError("Unknown method.")
        record = self.record(case_id)
        arm = record["arms"][method]
        events = [(e, "shared_initial") for e in record["shared_initial"]["events"]]
        events += [(e, method) for e in arm["events"]]
        candidates = iter(arm["candidates"])
        qa_index = 0
        steps = []
        for event, folder in events:
            output = event.get("normalized_output", event["output"])
            step = {
                "id": len(steps),
                "call_kind": event["call_kind"],
                "folder": folder,
                "role": event["role"],
                "output": output,
                "raw_output": event["output"],
                "prompt_sha256": event["prompt_sha256"],
                "usage": event["usage"],
                "model": event.get("served_model"),
                "source": "saved",
            }
            if "sql" in output:
                step["kind"] = "sql"
                step["candidate"] = next(candidates)
                if step["candidate"]["candidate_kind"] != event["call_kind"]:
                    raise ValueError("Saved event/candidate order is inconsistent.")
            elif event["role"] == "simulator":
                step["kind"] = "answer"
                step["output"] = {"answer": arm["question_answers"][qa_index]["answer"]}
                qa_index += 1
            else:
                step["kind"] = "question"
                step["rendered_question"] = arm["question_answers"][qa_index]["question"]
            steps.append(step)
        if next(candidates, None) is not None:
            raise ValueError("Saved candidate has no corresponding event.")
        return steps

    def case(self, case_id: int) -> dict:
        record = self.record(case_id)
        return {
            "task": record["task"],
            "note": NOTES[case_id],
            "schema": self.schema(case_id),
            "source": RUN_NAME,
            "record_sha256": hashlib.sha256(
                (self.run / "tasks" / f"{case_id:04}.json").read_bytes()
            ).hexdigest(),
            "methods": {
                m: {
                    **METHODS[m],
                    "steps": self.steps(case_id, m),
                    "success": record["arms"][m]["official_success"],
                    "attempts": record["arms"][m]["normal_sql_attempts"],
                    "turns": record["arms"][m]["user_turns"],
                    "termination": record["arms"][m]["termination"],
                }
                for m in METHODS
            },
        }

    def prefix(self, case_id: int, method: str, step_id: int) -> list[dict]:
        steps = self.steps(case_id, method)
        if step_id < 0 or step_id >= len(steps) or steps[step_id]["kind"] != "question":
            raise ValueError("A live branch must start at a saved clarification question.")
        return deepcopy(steps[: step_id + 1])

    @lru_cache(maxsize=256)
    def context(self, case_id: int, method: str, step_id: int) -> dict:
        steps = self.steps(case_id, method)
        if not 0 <= step_id < len(steps):
            raise KeyError("Unknown step.")
        step = steps[step_id]
        # Simulator prompts intentionally remain behind a separate evaluator boundary.
        if step["role"] == "simulator":
            return {
                "role": "simulator",
                "private": True,
                "output": step["raw_output"],
                "description": "The saved simulator received the original question, the clarification and gold SQL. Its answer, not its private prompt, went to the SQL model.",
            }
        directory = self.run / "artifacts/policy" / str(case_id) / step["folder"]
        for path in sorted(directory.glob("*.prompt.txt")):
            prompt = path.read_text("utf-8")
            if hashlib.sha256(prompt.encode()).hexdigest() == step["prompt_sha256"]:
                instructions_path = EXPERIMENT / "interaction/codex-policy-instructions.md"
                contract = json.loads((self.run / "run-contract.json").read_text("utf-8"))
                expected = contract["implementation_hashes"][
                    "codebase/experiment/interaction/codex-policy-instructions.md"
                ]
                if hashlib.sha256(instructions_path.read_bytes()).hexdigest() != expected:
                    raise ValueError("System instructions differ from the saved run.")
                return {
                    "role": "policy",
                    "private": False,
                    "prompt": prompt,
                    "output_schema": json.loads(
                        path.with_name(
                            path.name.replace(".prompt.txt", ".output-schema.json")
                        ).read_text("utf-8")
                    ),
                    "instructions": instructions_path.read_text("utf-8"),
                    "prompt_sha256": step["prompt_sha256"],
                    "verified": True,
                    "output": step["raw_output"],
                    "usage": step["usage"],
                    "source": str(path.relative_to(self.run)).replace("\\", "/"),
                    "description": "Exact saved input, verified against its recorded hash. The structured output is not hidden model reasoning.",
                }
        raise ValueError("The saved prompt is missing or its hash does not match.")

    def execute(self, case_id: int, sql: str) -> dict:
        env = SQLiteEnvironment(
            database_path(self.database_root, self.task(case_id)),
            timeout_seconds=15,
            max_rows=50_000,
            preview_rows=8,
        )
        return env.execute(sql).observation.to_dict()

    @lru_cache(maxsize=256)
    def preview(self, case_id: int, method: str, step_id: int) -> dict:
        steps = self.steps(case_id, method)
        if not 0 <= step_id < len(steps) or steps[step_id]["kind"] != "sql":
            raise KeyError("Choose a SQL step.")
        result = self.execute(case_id, steps[step_id]["candidate"]["sql"])
        result["source"] = (
            "Read-only preview executed now; recorded benchmark scoring is unchanged."
        )
        return result
