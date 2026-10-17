from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from interaction.result_evidence import (
    number,
    project_mini_trajectory,
    sha256,
)
from interaction.verify_results import (
    check,
    check_prices,
    read_evidence,
    verify_mini_events,
)
from interaction.analyze_standardized_results import ARM_ORDER


def event(sequence, kind, payload, usage=None):
    return {
        "sequence": sequence,
        "kind": kind,
        "turn_index": 0,
        "payload": payload,
        "usage": usage,
    }


class ResultEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.secret = "PRIVATE_ANSWER_AND_CREDENTIAL_SENTINEL"
        self.public = {
            "instance_id": "example_1",
            "selected_database": "example",
            "high_level": False,
        }
        self.record = {
            "task": {
                "question_id": "example_1",
                "db_id": "example",
                "question": self.secret,
            },
            "termination": "forced_submission_rejected",
            "final_sql": self.secret,
            "events": [
                event(0, "candidate_exposed", {"sql": self.secret}),
                event(
                    1,
                    "submission_judgement",
                    {"accepted": False, "submission_kind": "normal"},
                ),
                event(
                    2,
                    "user_clarification",
                    {"answer": self.secret, "simulator": {"calls": 2}},
                ),
                event(3, "candidate_exposed", {"sql": self.secret}),
                event(
                    4,
                    "sql_execution",
                    {"observation": {"status": "ok", "rows": [[self.secret]]}},
                ),
            ],
        }

    def test_only_allowlisted_mini_fields_are_copied(self):
        self.record["events"][2]["payload"]["raw_output"] = self.secret
        result = project_mini_trajectory(self.record, self.public, "hybrid")
        self.assertNotIn(self.secret, json.dumps(result))
        self.assertFalse(result["events"][0]["repeats_rejected_sql"])
        self.assertTrue(result["events"][3]["repeats_rejected_sql"])
        self.assertEqual(result["events"][2]["calls"], 2)
        self.assertEqual(result["events"][4]["status"], "ok")

    def test_unknown_labels_fail_closed(self):
        for field in ("termination", "kind", "normalization"):
            record = copy.deepcopy(self.record)
            if field == "termination":
                record[field] = self.secret
            elif field == "kind":
                record["events"][0][field] = self.secret
            else:
                record["events"].append(
                    event(5, "sra_normalization", {"changes": [self.secret]})
                )
            with self.assertRaisesRegex(ValueError, "label"):
                project_mini_trajectory(record, self.public, "hybrid")

    def test_usage_is_numeric_and_allowlisted(self):
        self.record["events"][2]["usage"] = {"input_tokens": 7, "extra": self.secret}
        result = project_mini_trajectory(self.record, self.public, "hybrid")
        self.assertEqual(result["events"][2]["usage"]["input_tokens"], 7)
        self.assertNotIn("extra", result["events"][2]["usage"])
        for invalid in (self.secret, True, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                number(invalid)

    def test_public_identity_must_match(self):
        self.record["task"]["question_id"] = "wrong"
        with self.assertRaisesRegex(ValueError, "ID mismatch"):
            project_mini_trajectory(self.record, self.public, "hybrid")

    def test_checks_detect_changed_numbers_and_tiny_p_values(self):
        check({"count": 2, "p": 1e-106}, {"count": 2, "p": 1e-106}, "test")
        with self.assertRaisesRegex(ValueError, "test.p"):
            check({"p": 0.0}, {"p": 1e-106}, "test")
        with self.assertRaises(ValueError):
            check({"extra": 1}, {}, "test")
        with self.assertRaises(ValueError):
            check(1, True, "test")

    def test_prices_count_cached_tokens_once(self):
        rates = {
            "ordinary_input": 0.2,
            "cache_read": 0.02,
            "cache_write": 0.25,
            "output": 1.2,
        }
        usage = {
            "input_tokens": 1000,
            "cached_input_tokens": 200,
            "cache_write_input_tokens": 300,
            "output_tokens": 100,
            "estimated_cost_usd": 0.000299,
        }
        check_prices([{"usage": usage}], rates)
        usage["estimated_cost_usd"] = 0.001
        with self.assertRaisesRegex(ValueError, "estimated_cost"):
            check_prices([{"usage": usage}], rates)

    def test_evidence_hashes_and_file_set_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            names = [
                "bird-primary.jsonl.gz",
                "bird-supporting.jsonl.gz",
                "mini-events.jsonl.gz",
            ]
            names += [f"mini-{arm}.metrics.json" for arm in ARM_ORDER]
            files = {}
            for name in names:
                content = b"{}\n"
                raw = (
                    gzip.compress(content, mtime=0) if name.endswith(".gz") else content
                )
                (root / name).write_bytes(raw)
                files[name] = {
                    "bytes": len(raw),
                    "sha256": sha256(raw),
                    "content_sha256": sha256(content),
                }
            manifest = {"schema_version": 1, "files": files}
            (root / "evidence.json").write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(len(read_evidence(root)[1]), 7)
            (root / names[0]).write_bytes(b"damaged")
            with self.assertRaises(ValueError):
                read_evidence(root)
            files["../escape"] = files.pop(names[0])
            (root / "evidence.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "file set"):
                read_evidence(root)

    def test_projected_events_reproduce_audit_counts(self):
        row = project_mini_trajectory(self.record, self.public, "hybrid")
        expected = {
            "trajectories": 1,
            "successes": 0,
            "sra": {
                "raw_decisions": 0,
                "raw_compliant": 0,
                "raw_compliance_rate": None,
                "post_normalization_compliant": 0,
                "post_normalization_compliance_rate": None,
                "normalization_events": 0,
                "normalization_change_counts": {},
                "user_answers": 1,
            },
            "protocol_error_categories": {},
            "normalizer_outcome_strata": {
                "activated": {"trajectories": 0, "successes": 0, "success_rate": None},
                "not_activated": {
                    "trajectories": 1,
                    "successes": 0,
                    "success_rate": 0.0,
                },
            },
            "rejected_sql_repeat_observation": {
                "exact_repeat_events": 1,
                "trajectories_with_exact_repeat": 1,
            },
            "forced_rejected_endpoint_execution": {
                "status_counts": {"ok": 1},
                "trajectories": 1,
                "executable": 1,
            },
        }
        verify_mini_events([row], expected)
        row["events"][3]["repeats_rejected_sql"] = False
        with self.assertRaisesRegex(ValueError, "repeats"):
            verify_mini_events([row], expected)


if __name__ == "__main__":
    unittest.main()
