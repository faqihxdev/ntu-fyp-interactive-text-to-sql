from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from interaction.bird_dev_matched import ARMS, PROTOCOL_VERSION
from interaction.run_bird_dev_matched import (
    _sha256_json,
    _validate_record,
    ensure_run_contract,
)


class BirdDevResumeContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.contract_path = self.root / "run-contract.json"
        self.contract = {
            "schema_version": 1,
            "protocol": PROTOCOL_VERSION,
            "arms": list(ARMS),
            "selection": {"sha256": "selection-a", "tasks": 1},
            "model": {"requested": "model-a", "reasoning_effort": "high"},
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_contract_is_written_and_exact_match_can_resume(self) -> None:
        digest = ensure_run_contract(
            self.contract_path,
            self.contract,
            existing_record_count=0,
        )
        self.assertEqual(digest, _sha256_json(self.contract))
        self.assertEqual(
            json.loads(self.contract_path.read_text(encoding="utf-8")),
            self.contract,
        )
        self.assertEqual(
            ensure_run_contract(
                self.contract_path,
                self.contract,
                existing_record_count=1,
            ),
            digest,
        )

    def test_changed_model_or_selection_is_rejected(self) -> None:
        ensure_run_contract(
            self.contract_path,
            self.contract,
            existing_record_count=0,
        )
        changed = dict(self.contract)
        changed["model"] = {"requested": "model-b", "reasoning_effort": "high"}
        with self.assertRaisesRegex(ValueError, "does not match"):
            ensure_run_contract(
                self.contract_path,
                changed,
                existing_record_count=1,
            )

    def test_legacy_records_without_contract_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "no run contract"):
            ensure_run_contract(
                self.contract_path,
                self.contract,
                existing_record_count=1,
            )

    def test_record_must_bind_to_the_same_contract(self) -> None:
        digest = _sha256_json(self.contract)
        record_path = self.root / "0001.json"
        record = {
            "schema_version": 1,
            "protocol": PROTOCOL_VERSION,
            "task": {"question_id": 1},
            "arms": {arm: {} for arm in ARMS},
            "run_contract_sha256": digest,
        }
        record_path.write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(
            _validate_record(
                record_path,
                "1",
                expected_contract_sha256=digest,
            ),
            record,
        )
        with self.assertRaisesRegex(ValueError, "run contract mismatch"):
            _validate_record(
                record_path,
                "1",
                expected_contract_sha256="different",
            )


if __name__ == "__main__":
    unittest.main()
