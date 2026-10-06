"""Explicit two-call smoke for the running local server. Never a benchmark."""

import argparse
import json
import uuid

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Make exactly two paid API calls")
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live explicitly to permit the two API calls.")
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=240) as client:
        status = client.get("/api/status").json()
        assert status["live"]["available"], "Configure the server API key first."
        headers = {"x-explorer-token": status["token"]}
        response = client.post(
            "/api/branches",
            headers=headers,
            json={
                "case_id": 1389,
                "method": "reactive_clarification",
                "step_id": 1,
                "mode": "original",
            },
        )
        response.raise_for_status()
        branch = response.json()
        endpoint = f"/api/branches/{branch['id']}/actions"
        response = client.post(
            endpoint,
            headers=headers,
            json={"revision": 0, "request_id": str(uuid.uuid4()), "action": "suggest"},
        )
        response.raise_for_status()
        suggested = response.json()
        assert suggested["session"]["calls"] == 1
        assert suggested["session"]["phase"] == "reply"
        response = client.post(
            endpoint,
            headers=headers,
            json={
                "revision": 1,
                "request_id": str(uuid.uuid4()),
                "action": "reply",
                "text": suggested["suggestion"],
            },
        )
        response.raise_for_status()
        result = response.json()["session"]
        assert result["calls"] == 2
        assert len(result["steps"]) == 4
        assert result["steps"][-1]["source"] == "live"
        last = result["steps"][-1]
        assert last["candidate"]["status"] == "ok"
        summary = {
            "branch_id": result["id"],
            "calls": result["calls"],
            "model": last["context"]["model"],
            "phase": result["phase"],
            "sql_status": last["candidate"]["status"],
            "gold_match": last["candidate"]["official_ex"],
            "total_demo_spend_usd": client.get("/api/status").json()["live"]["spent_usd"],
        }
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
