"""Custom questions have execution feedback, but no benchmark answer key."""

import json

from interaction.bird_dev_matched import SQL_OUTPUT_SCHEMA, clean_generated_sql


def decision_schema(can_ask: bool, can_inspect: bool) -> dict:
    decisions = ["sql"]
    if can_ask:
        decisions.append("ask")
    if can_inspect:
        decisions.append("inspect")
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string", "enum": decisions},
            "sql": {"type": "string"},
            "purpose": {"type": "string", "maxLength": 240},
            "question": {"type": "string"},
            "options": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
        },
        "required": ["decision", "sql", "purpose", "question", "options"],
    }


def bounded_observation(preview: dict) -> dict:
    """Keep each visible/model observation within 12 KB, without result fingerprints."""
    limited = False

    def short(value, limit=160):
        nonlocal limited
        if isinstance(value, dict) and "bytes_base64" in value:
            limited = True
            return "[binary value omitted]"
        if isinstance(value, str) and len(value) > limit:
            limited = True
            return value[:limit] + " [truncated]"
        return value

    result = {
        "status": preview["status"],
        "error": short(preview.get("error"), 512),
        "columns": [short(c, 80) for c in preview["columns"][:12]],
        "preview": [],
        "row_count": preview["row_count"],
        "truncated": preview["truncated"],
        "preview_limited": False,
    }
    limited |= len(preview["columns"]) > 12
    for row in preview["preview"][:8]:
        result["preview"].append([short(value) for value in row[:12]])
        if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 12_000:
            result["preview"].pop()
            limited = True
            break
    result["preview_limited"] = limited or preview["row_count"] > len(result["preview"])
    return result


class CustomQuestions:
    def __init__(self, databases, call):
        self.databases = databases
        self.call = call

    def perform(self, session, action: str, text: str):
        if action == "stop":
            session["phase"] = "done"
            session["termination"] = "user"
            return {}
        phase = session["phase"]
        if action == "repair":
            if phase != "repair":
                raise ValueError("One repair is available after invalid SQL.")
        elif action == "generate":
            if phase != "ready":
                raise ValueError("The first response has already been generated.")
        elif action == "reply":
            if phase != "reply" or not text:
                raise ValueError("Answer the clarification before continuing.")
            if session["answers"] >= 4:
                raise ValueError("This conversation reached its four-answer limit.")
        elif action == "feedback":
            if phase != "result" or not text:
                raise ValueError("Describe the change you want before continuing.")
        elif action == "continue":
            if phase != "inspect":
                raise ValueError("Continue is available after a database check.")
        else:
            raise ValueError("This action is unavailable for custom questions.")
        if action != "repair" and session["attempts"] >= 5:
            raise ValueError("This conversation reached its five-query limit.")

        schema = self.databases.detail(session["db_id"])["schema"]
        # Only this conversation's queries and observations enter custom model history.
        history = []
        for step in session["steps"]:
            if step["kind"] == "sql":
                c = step["candidate"]
                history.append(
                    {
                        "sql": c["sql"],
                        "query_purpose": step.get("query_purpose", "answer"),
                        "observation": step["preview"],
                    }
                )
            elif step["kind"] == "question":
                history.append(
                    {
                        "question": step["output"]["question"],
                        "options": step["output"].get("options", []),
                    }
                )
            else:
                history.append({"user": step["output"]["answer"]})
        answers = session["answers"] + (action == "reply")
        can_ask = answers < 4 and session["calls"] < 11
        # Reserve the last normal query and last call for an answer rather than another check.
        can_inspect = session["attempts"] < 4 and session["calls"] < 11
        output_schema = decision_schema(can_ask, can_inspect)
        prompt = (
            "Choose the next action for this database question.\n"
            f"Allowed actions: {', '.join(output_schema['properties']['decision']['enum'])}.\n"
            f"Normal queries remaining (including inspections): {5 - session['attempts']}.\n"
            f"Clarification replies remaining: {4 - answers}.\n"
            f"Model calls remaining, including this call: {12 - session['calls']}.\n"
            f"Database schema:\n{schema}\nOriginal request: {session['question']}\n"
            f"Conversation:\n{json.dumps(history, ensure_ascii=False)}\n"
            f"User's latest message: {text or '(No new message.)'}\n"
        )
        if action == "repair":
            last_sql = next(s for s in reversed(session["steps"]) if s["kind"] == "sql")
            prompt += (
                "Repair the last invalid SQL using its execution error. Return JSON with only sql."
            )
            output, context = self.call(session, prompt, SQL_OUTPUT_SCHEMA)
            decision = "inspect" if last_sql.get("query_purpose") == "inspect" else "sql"
        else:
            output, context = self.call(session, prompt, output_schema)
            decision = output["decision"]
            if decision not in output_schema["properties"]["decision"]["enum"]:
                raise ValueError("The model returned an unavailable action. Retry explicitly.")
        if decision == "ask":
            if not can_ask or not output["question"].strip():
                raise ValueError(
                    "The model did not return a usable clarification. Retry explicitly."
                )
        elif decision not in {"sql", "inspect"} or not output["sql"].strip():
            raise ValueError("The model did not return a usable query. Retry explicitly.")

        if text:
            session["steps"].append(
                {
                    "id": len(session["steps"]),
                    "kind": "answer",
                    "source": "live",
                    "role": "visitor",
                    "output": {"answer": text},
                }
            )
        session["answers"] = answers
        if decision == "ask":
            session["steps"].append(
                {
                    "id": len(session["steps"]),
                    "kind": "question",
                    "source": "live",
                    "role": "policy",
                    "output": output,
                    "context": context,
                }
            )
            session["phase"] = "reply"
        else:
            sql = clean_generated_sql(output["sql"])
            preview = bounded_observation(self.databases.execute(session["db_id"], sql))
            session["steps"].append(
                {
                    "id": len(session["steps"]),
                    "kind": "sql",
                    "source": "live",
                    "role": "policy",
                    "output": output,
                    "context": context,
                    "call_kind": "custom_invalid_repair" if action == "repair" else "custom_sql",
                    "query_purpose": "inspect" if decision == "inspect" else "answer",
                    "candidate": {
                        "sql": sql,
                        "official_ex": None,
                        "status": preview["status"],
                        "error": preview["error"],
                        "row_count": preview["row_count"],
                    },
                    "preview": preview,
                }
            )
            session["attempts"] += action != "repair"
            invalid = preview["status"] != "ok"
            if invalid and action != "repair":
                session["phase"] = "repair"
            else:
                session["phase"] = "inspect" if decision == "inspect" else "result"
                if session["attempts"] >= 5:
                    session["phase"] = "done"
                    session["termination"] = "query_limit"
        if session["calls"] >= 12:
            session["phase"] = "done"
            session["termination"] = "call_limit"
        return {}
