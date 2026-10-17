import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from interaction.sphinteract_source import (
    REQUIRED_PROMPTS,
    SPHINTERACT_NOTEBOOK_LF_SHA256,
    SPHINTERACT_NOTEBOOK_SHA256,
    load_sphinteract_prompts,
)


class NotebookVerificationTest(unittest.TestCase):
    def test_default_accepts_only_the_two_known_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source.ipynb"
            path.write_text(
                json.dumps(
                    {
                        "cells": [
                            {
                                "cell_type": "code",
                                "source": [
                                    f"{key} = {key!r}\n"
                                    for key in sorted(REQUIRED_PROMPTS)
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "interaction.sphinteract_source.sha256_file",
                return_value=SPHINTERACT_NOTEBOOK_SHA256,
            ):
                crlf = load_sphinteract_prompts(path)
            with patch(
                "interaction.sphinteract_source.sha256_file",
                return_value=SPHINTERACT_NOTEBOOK_LF_SHA256,
            ):
                self.assertEqual(load_sphinteract_prompts(path), crlf)
                with self.assertRaisesRegex(ValueError, "Unexpected"):
                    load_sphinteract_prompts(path, expected_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                load_sphinteract_prompts(path)
            custom_hash = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(
                load_sphinteract_prompts(path, expected_sha256=custom_hash), crlf
            )
