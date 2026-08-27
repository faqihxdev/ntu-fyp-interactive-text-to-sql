from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from interaction.bird_dev_matched import ARMS, PROTOCOL_VERSION
from interaction.audit_bird_dev_recovery import audit_recovery, protocol_error_ids


def record(question_id: int, *, error: bool = False, generation: str = "a") -> dict:
    payload = {
        "protocol": PROTOCOL_VERSION,
        "task": {"question_id": question_id, "question": "private"},
        "arms": {
            arm: {"termination": "success", "generation": generation}
            for arm in ARMS
        },
    }
    if error:
        payload["arms"]["no_user"]["termination"] = "arm_error"
    return payload


class RecoveryAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_jsonl(self, name: str, records: list[dict]) -> Path:
        path = self.root / name
        path.write_text(
            "".join(json.dumps(value) + "\n" for value in records),
            encoding="utf-8",
        )
        return path

    def write_freeze(self, name: str, ids: list[int]) -> Path:
        path = self.root / name
        path.write_text(
            json.dumps({"affected_question_ids": ids}),
            encoding="utf-8",
        )
        return path

    def write_selection(self, ids: list[int]) -> Path:
        path = self.root / "selection.json"
        path.write_text(json.dumps({"question_ids": ids}), encoding="utf-8")
        return path

    def test_score_blind_selection_uses_only_error_fields(self) -> None:
        records = {
            "1": record(1),
            "2": record(2, error=True),
        }
        records["1"]["official_success"] = False
        records["2"]["official_success"] = True
        self.assertEqual(protocol_error_ids(records), {"2"})

    def test_audit_verifies_two_recovery_generations(self) -> None:
        initial = self.write_jsonl(
            "initial.jsonl",
            [record(1), record(2, error=True), record(3, error=True)],
        )
        attempt_01 = self.write_jsonl(
            "attempt-01.jsonl",
            [
                record(1),
                record(2, generation="b"),
                record(3, error=True, generation="b"),
            ],
        )
        final = self.write_jsonl(
            "final.jsonl",
            [record(1), record(2, generation="b"), record(3, generation="c")],
        )
        audit = audit_recovery(
            initial_path=initial,
            attempt_01_path=attempt_01,
            final_path=final,
            selection_path=self.write_selection([1, 2, 3]),
            freeze_01_path=self.write_freeze("freeze-01.json", [2, 3]),
            freeze_02_path=self.write_freeze("freeze-02.json", [3]),
        )
        self.assertEqual(audit["status"], "passed")
        self.assertEqual(
            audit["lineage"],
            {
                "retained_original": 1,
                "retained_attempt_01": 1,
                "retained_attempt_02": 1,
            },
        )

    def test_audit_rejects_changes_outside_selection(self) -> None:
        initial = self.write_jsonl("initial.jsonl", [record(1), record(2, error=True)])
        attempt_01 = self.write_jsonl(
            "attempt-01.jsonl",
            [record(1, generation="changed"), record(2, generation="b")],
        )
        final = self.write_jsonl(
            "final.jsonl",
            [record(1, generation="changed"), record(2, generation="b")],
        )
        with self.assertRaisesRegex(ValueError, "outside its frozen selection"):
            audit_recovery(
                initial_path=initial,
                attempt_01_path=attempt_01,
                final_path=final,
                selection_path=self.write_selection([1, 2]),
                freeze_01_path=self.write_freeze("freeze-01.json", [2]),
                freeze_02_path=self.write_freeze("freeze-02.json", []),
            )

    def test_audit_rejects_incomplete_five_arm_replacement(self) -> None:
        initial = self.write_jsonl("initial.jsonl", [record(1, error=True)])
        attempt_01 = self.write_jsonl(
            "attempt-01.jsonl", [record(1, generation="b")]
        )
        incomplete = record(1, generation="b")
        incomplete["arms"].pop("project_hybrid")
        final = self.write_jsonl("final.jsonl", [incomplete])

        with self.assertRaisesRegex(ValueError, "exactly five protocol arms"):
            audit_recovery(
                initial_path=initial,
                attempt_01_path=attempt_01,
                final_path=final,
                selection_path=self.write_selection([1]),
                freeze_01_path=self.write_freeze("freeze-01.json", [1]),
                freeze_02_path=self.write_freeze("freeze-02.json", []),
            )


if __name__ == "__main__":
    unittest.main()
