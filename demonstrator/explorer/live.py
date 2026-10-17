"""User-triggered live calls. No automatic trajectory loop or benchmark writes."""

from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import threading
import time
import uuid

from dotenv import load_dotenv

from . import APP_ROOT, EXPERIMENT
from .replay import ReplayStore
from .databases import DatabaseStore
from .custom import CustomQuestions
from interaction.bird_dev_matched import (
    ANSWER_OUTPUT_SCHEMA,
    SQL_OUTPUT_SCHEMA,
    BirdDevMatchedController,
    BirdDevPrivateEvaluator,
    clean_generated_sql,
    load_original_bird_rows,
    normalize_simulator_answer,
    normalize_sra_payload,
    render_clarification,
    sra_output_schema,
)
from interaction.openai_structured import OpenAIStructuredInvoker, SpendTracker
from interaction.sphinteract_source import load_sphinteract_prompts


class LiveError(ValueError):
    pass


class LiveService:
    def __init__(
        self, store: ReplayStore, invoker=None, simulator=None, state_dir: Path | None = None
    ):
        self.store = store
        self.state_dir = state_dir or APP_ROOT / ".local"
        load_dotenv(EXPERIMENT / ".env", override=False)
        load_dotenv(APP_ROOT / ".env", override=True)
        self.key = os.environ.get("OPENAI_API_KEY", "")
        self.model = os.environ.get("DEMO_MODEL", "gpt-5.6-luna")
        self.reasoning = os.environ.get("DEMO_REASONING", "high")
        self.invoker = invoker
        self.custom_invoker = invoker
        self.simulator = simulator
        self.tracker = None
        self.sessions = {}
        self.lock = threading.RLock()
        self.call_lock = threading.Lock()
        self.databases = DatabaseStore(store.database_root)
        self.custom = CustomQuestions(self.databases, self._call)
        self.prompts = load_sphinteract_prompts(
            EXPERIMENT / "datasets/sphinteract-source-21c6fcd/zeroshot_experiments.ipynb"
        )
        self.evaluator = BirdDevPrivateEvaluator(
            rows=load_original_bird_rows(EXPERIMENT / "datasets/bird-dev/dev.json"),
            database_root=store.database_root,
            timeout_seconds=15,
            max_rows=50_000,
        )
        # Only pure prompt builders are reused, not the automatic controller loop.
        self.builder = BirdDevMatchedController(
            policy_invoker=invoker,
            simulator_invoker=simulator,
            evaluator=self.evaluator,
            prompts=self.prompts,
            database_root=store.database_root,
            artifact_root=self.state_dir,
        )

    def status(self):
        return {
            "available": bool(self.key or self.invoker),
            "model": self.model,
            "reasoning": self.reasoning,
            "max_calls_per_branch": 12,
            "max_usd": float(os.environ.get("DEMO_MAX_USD", "2")),
            "spent_usd": self.tracker.spent_usd if self.tracker else None,
        }

    def _initialise_client(self):
        if self.invoker:
            return
        if not self.key:
            raise LiveError("No server API key is configured. Saved replay still works.")
        self.tracker = SpendTracker(
            max_usd=float(os.environ.get("DEMO_MAX_USD", "2")),
            reservation_usd=0.10,
            journal_path=self.state_dir / "spend.jsonl",
        )
        options = dict(
            api_key=self.key,
            model=self.model,
            reasoning_effort=self.reasoning,
            spend_tracker=self.tracker,
            max_output_tokens=4096,
            timeout_seconds=180,
            max_transport_retries=0,
        )
        self.invoker = OpenAIStructuredInvoker(
            **options, instructions_file=EXPERIMENT / "interaction/codex-policy-instructions.md"
        )
        self.simulator = OpenAIStructuredInvoker(
            **options,
            instructions_file=EXPERIMENT / "interaction/codex-user-simulator-instructions.md",
        )
        self.custom_invoker = OpenAIStructuredInvoker(
            **options, instructions_file=APP_ROOT / "explorer/custom-instructions.md"
        )

    def fork(self, case_id: int, method: str, step_id: int, mode: str):
        if mode not in {"original", "changed"}:
            raise LiveError("Choose original-target or changed-intent mode.")
        prefix = self.store.prefix(case_id, method, step_id)
        with self.lock:
            # Bound in-memory sessions. Restarting the local server clears them.
            if len(self.sessions) >= 64:
                raise LiveError(
                    "The 64-branch limit is reached. Restart the local server to clear sessions."
                )
            sid = str(uuid.uuid4())
            self.sessions[sid] = {
                "id": sid,
                "case_id": case_id,
                "method": method,
                "fork_step": step_id,
                "mode": mode,
                "steps": prefix,
                "phase": "reply",
                "revision": 0,
                "calls": 0,
                "created_at": time.time(),
                "responses": {},
                "attempts": sum(
                    s["kind"] == "sql" and "invalid_repair" not in s["call_kind"] for s in prefix
                ),
                "answers": sum(s["kind"] == "answer" for s in prefix),
                "error": None,
            }
            self._persist(self.sessions[sid])
            return self.public(self.sessions[sid])

    def _persist(self, session):
        directory = self.state_dir / "branches"
        directory.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in session.items() if k != "responses"}
        destination = directory / f"{session['id']}.json"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        temporary.replace(destination)

    def start_custom(self, db_id: str, question: str):
        self.databases.path(db_id)
        question = question.strip()
        if not question or len(question) > 4000:
            raise LiveError("Enter a question of up to 4,000 characters.")
        with self.lock:
            if len(self.sessions) >= 64:
                raise LiveError("The 64-conversation limit is reached. Restart the local server.")
            sid = str(uuid.uuid4())
            session = {
                "id": sid,
                "db_id": db_id,
                "question": question,
                "mode": "custom",
                "steps": [],
                "phase": "ready",
                "revision": 0,
                "calls": 0,
                "created_at": time.time(),
                "responses": {},
                "attempts": 0,
                "answers": 0,
                "error": None,
            }
            self.sessions[sid] = session
            self._persist(session)
            return self.public(session)

    def public(self, session):
        return deepcopy({k: v for k, v in session.items() if k not in {"responses", "created_at"}})

    def get(self, sid):
        with self.lock:
            if sid not in self.sessions:
                raise KeyError("This branch has expired. Start again from a saved question.")
            return self.public(self.sessions[sid])

    def _histories(self, session):
        sqls, qa, question = [], [], ""
        for step in session["steps"]:
            if step["kind"] == "sql":
                sqls.append(step["candidate"]["sql"])
            elif step["kind"] == "question":
                question = step["rendered_question"]
            elif step["kind"] == "answer":
                qa.append((question, step["output"]["answer"]))
        return sqls, qa

    def _call(self, session, prompt, schema, role="policy"):
        if session["calls"] >= 12:
            raise LiveError(
                "This branch reached its 12-call limit. Saved replay is still available."
            )
        self._initialise_client()
        invoker = self.simulator if role == "simulator" else self.invoker
        if session.get("mode") == "custom":
            invoker = self.custom_invoker
        session["calls"] += 1
        self._persist(session)
        result = invoker.run(
            prompt,
            output_schema=schema,
            artifact_directory=self.state_dir / "calls" / session["id"],
            case_id=f"live-{session['calls']}",
        )
        context = {
            "role": role,
            "output_schema": schema,
            "prompt": prompt if role == "policy" else None,
            "private": role == "simulator",
            "instructions": getattr(invoker, "instructions", "") if role == "policy" else None,
            "description": "Actual live input. No saved future steps are supplied."
            if role == "policy"
            else "Gold-conditioned simulator input remains private; only its answer reaches the SQL model.",
            "output": result.output,
            "usage": asdict(result.usage),
            "model": result.served_model,
        }
        return result.output, context

    def action(self, sid: str, revision: int, request_id: str, action: str, text: str = ""):
        # One bounded call at a time, also preventing concurrent duplicate spending.
        if not self.call_lock.acquire(blocking=False):
            raise LiveError("Another live request is running. Wait for it to finish.")
        try:
            with self.lock:
                if sid not in self.sessions:
                    raise KeyError("This branch has expired.")
                session = self.sessions[sid]
                if request_id in session["responses"]:
                    return session["responses"][request_id]
                if revision != session["revision"]:
                    raise LiveError("This branch changed. Refresh its state before continuing.")
                if session["phase"] == "done":
                    raise LiveError("This branch has ended. Start another from the saved replay.")
                working = deepcopy(session)
            # Validation precedes any invocation. Failures consume call allowance but not transcript state.
            try:
                extra = self._perform(working, action, text.strip())
                working["error"] = None
            except Exception:
                with self.lock:
                    session["calls"] = working["calls"]
                    self._persist(session)
                raise
            with self.lock:
                working["revision"] += 1
                response = {"session": self.public(working), **extra}
                working["responses"][request_id] = response
                self.sessions[sid] = working
                self._persist(working)
                return response
        finally:
            self.call_lock.release()

    def _perform(self, session, action, text):
        if session["mode"] == "custom":
            return self.custom.perform(session, action, text)
        case_id = session["case_id"]
        task = self.store.task(case_id)
        schema = self.store.schema(case_id)
        sqls, qa = self._histories(session)
        if action == "stop":
            session["phase"] = "done"
            return {}
        if action == "suggest":
            if session["phase"] != "reply":
                raise LiveError("Reply suggestions are available only at a question.")
            question = session["steps"][-1]["rendered_question"]
            if session["mode"] == "original":
                prompt = self.prompts.feedback_prefix + self.prompts.feedback.format(
                    query=self.evaluator.private_row(task)["SQL"],
                    question=question,
                    nlq=task.question,
                )
                prompt += '\nReturn only JSON with one string field, "answer".'
                output, _ = self._call(session, prompt, ANSWER_OUTPUT_SCHEMA, "simulator")
                answer, _ = normalize_simulator_answer(output["answer"])
                return {"suggestion": answer, "suggestion_kind": "gold-conditioned"}
            prompt = (
                "Suggest one possible plain-English reply to this clarification. You do not know "
                "the visitor's intent. Present a possibility, not a correct answer. Do not output SQL.\n"
                f"Original question: {task.question}\nSchema:\n{schema}\n"
                f"Conversation: {json.dumps(qa)}\nClarification: {question}"
            )
            output, _ = self._call(session, prompt, ANSWER_OUTPUT_SCHEMA)
            return {
                "suggestion": output["answer"],
                "suggestion_kind": "possible reply, not an oracle",
            }
        if action == "reply":
            if session["phase"] != "reply" or not text:
                raise LiveError("Enter a reply to the current clarification.")
            if session["attempts"] >= 5 or session["answers"] >= 4:
                raise LiveError("The inherited five-attempt/four-reply limit has been reached.")
            question = session["steps"][-1]["rendered_question"]
            qa.append((question, text))
            prompt = self.builder._sql_after_feedback_prompt(
                schema=schema, task=task, incorrect_sqls=sqls, question_answers=qa
            )
            if session["mode"] == "changed":
                prompt += (
                    "\nThis is an exploratory branch. The visitor may change the original request. "
                    "Follow their latest reply, even if it differs from the original question. "
                    "Previous queries are superseded, not necessarily incorrect for that new intent."
                )
            output, context = self._call(session, prompt, SQL_OUTPUT_SCHEMA)
            session["steps"].append(
                {
                    "id": len(session["steps"]),
                    "kind": "answer",
                    "source": "live",
                    "role": "visitor",
                    "output": {"answer": text},
                }
            )
            session["answers"] += 1
            session["attempts"] += 1
            self._append_sql(session, output, context, repaired=False)
            return {}
        if action == "repair":
            if session["phase"] != "repair":
                raise LiveError("One repair is available only after invalid SQL.")
            candidate = session["steps"][-1]["candidate"]
            prompt = self.builder._repair_sql_prompt(
                schema=schema, sql=candidate["sql"], error=candidate["error"]
            )
            output, context = self._call(session, prompt, SQL_OUTPUT_SCHEMA)
            self._append_sql(session, output, context, repaired=True)
            return {}
        if action == "ask":
            if session["phase"] not in {"result", "repair"}:
                raise LiveError("A further question is available only after SQL generation.")
            if session["attempts"] >= 5 or session["answers"] >= 4:
                raise LiveError("The inherited five-attempt/four-reply limit has been reached.")
            if session["mode"] == "changed" and not text:
                raise LiveError("Describe what still needs changing before asking again.")
            prompt = self.builder._reactive_sra_prompt(
                schema=schema,
                task=task,
                incorrect_sqls=sqls,
                question_answers=qa,
                allow_no_ambiguity=False,
            )
            if session["mode"] == "changed":
                prompt += f"\nVisitor feedback on the last result: {text}\nFollow the visitor's revised intent. No evaluator has judged the new intent."
            output, context = self._call(
                session, prompt, sra_output_schema(allow_no_ambiguity=False)
            )
            normalised, _ = normalize_sra_payload(output)
            session["steps"].append(
                {
                    "id": len(session["steps"]),
                    "kind": "question",
                    "source": "live",
                    "role": "policy",
                    "output": normalised,
                    "rendered_question": render_clarification(normalised),
                    "context": context,
                    "visitor_feedback": text,
                }
            )
            session["phase"] = "reply"
            return {}
        raise LiveError("Unknown live action.")

    def _append_sql(self, session, output, context, repaired):
        sql = clean_generated_sql(output["sql"])
        preview = self.store.execute(session["case_id"], sql)
        candidate = {
            "sql": sql,
            "status": preview["status"],
            "error": preview["error"],
            "official_ex": None,
            "row_count": preview["row_count"],
        }
        if session["mode"] == "original":
            candidate, _ = self.evaluator.evaluate(self.store.task(session["case_id"]), sql)
        session["steps"].append(
            {
                "id": len(session["steps"]),
                "kind": "sql",
                "role": "policy",
                "source": "live",
                "output": output,
                "candidate": candidate,
                "preview": preview,
                "context": context,
                "call_kind": "live_invalid_repair" if repaired else "live_sql",
            }
        )
        invalid = preview["status"] != "ok"
        if candidate["official_ex"] is True or (
            session["attempts"] >= 5 and (not invalid or repaired)
        ):
            session["phase"] = "done"
        elif invalid and not repaired:
            session["phase"] = "repair"
        else:
            session["phase"] = "result"
