"""Offline relocation checks, including unchanged freeze-hash enforcement."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from interaction.artifact_paths import resolve_recorded_path, workspace_root
from interaction import run_bird_dev_clarification_value as primary
from interaction import run_bird_dev_matched as supporting


class ArtifactPathsTest(unittest.TestCase):
    def test_both_layouts_and_path_spellings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            for layout in ("nested/experiment", "experiment"):
                experiment = root / layout
                self.assertEqual(workspace_root(experiment), experiment.parent)
                for spelling in (
                    "archive/experiment/manifests/selection.json",
                    "experiment/manifests/selection.json",
                    "archive\\experiment\\manifests\\selection.json",
                ):
                    self.assertEqual(
                        resolve_recorded_path(spelling, experiment),
                        experiment / "manifests/selection.json",
                    )
                absolute = root / "external/input.json"
                self.assertEqual(resolve_recorded_path(absolute, experiment), absolute)
                with self.assertRaisesRegex(ValueError, "traversal"):
                    resolve_recorded_path("experiment/../secret", experiment)
                with self.assertRaisesRegex(ValueError, "traversal"):
                    resolve_recorded_path("../experiment/manifests/selection.json", experiment)
                self.assertEqual(resolve_recorded_path("README.md", experiment),
                                 experiment.parent / "README.md")

    def test_relocated_freezes_accept_same_bytes_and_reject_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            for layout in ("nested/experiment", "experiment"):
                for runner in (primary, supporting):
                    with self.subTest(layout=layout, runner=runner.__name__):
                        experiment = Path(directory) / layout
                        selection = experiment / "manifests/selection.json"
                        selection.parent.mkdir(parents=True, exist_ok=True)
                        selection.write_text('{"question_ids":[1]}', encoding="utf-8")
                        artifact = experiment / "fixture.txt"
                        artifact.write_text("frozen", encoding="utf-8")
                        selection_info = {
                            "path": "archive/experiment/manifests/selection.json",
                            "sha256": hashlib.sha256(selection.read_bytes()).hexdigest(),
                        }
                        payload = {
                            "protocol": runner.PROTOCOL_VERSION,
                            "status": primary.FREEZE_STATUS if runner is primary
                            else "frozen_before_pilot_model_calls",
                            "selection": selection_info,
                            "selections": {"smoke20": selection_info},
                            "shared_stack": {"model": "fixture", "reasoning_effort": "high",
                                             "workers": 4},
                            "sphinteract_source": {
                                "notebook_sha256": runner.SPHINTERACT_NOTEBOOK_SHA256,
                            },
                            "artifact_hashes": {
                                "archive/experiment/fixture.txt": hashlib.sha256(
                                    artifact.read_bytes()
                                ).hexdigest(),
                            },
                        }
                        freeze = experiment / "freeze.json"
                        freeze.write_text(json.dumps(payload), encoding="utf-8")
                        arguments = dict(selection_path=selection, model="fixture",
                                         reasoning_effort="high")
                        if runner is primary:
                            arguments.update(tier="smoke20", workers=4)
                        with patch.object(runner, "EXPERIMENT_ROOT", experiment):
                            runner.validate_freeze(freeze, **arguments)
                            artifact.write_text("changed", encoding="utf-8")
                            with self.assertRaisesRegex(ValueError, "Input artifact changed"):
                                runner.validate_freeze(freeze, **arguments)


if __name__ == "__main__":
    unittest.main()
