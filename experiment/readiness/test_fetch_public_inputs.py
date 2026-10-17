import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
import zipfile

from readiness.fetch_public_inputs import (
    MARKER,
    MINI_REVISION,
    MINI_SOURCE,
    SNAPSHOT,
    digest,
    download_provenance,
    install,
    missing_files,
    safe_path,
    verify,
    zip_member,
)
from readiness.check_datasets import ReadinessFailure, git_revision


class PublicInputTest(unittest.TestCase):
    def test_download_marker_requires_matching_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "public").write_bytes(b"fixture")
            files = {"public": {"sha256": digest(root / "public")}}
            (root / MARKER).write_text(
                json.dumps(
                    {
                        "source": MINI_SOURCE,
                        "revision": MINI_REVISION,
                        "snapshot_sha256": digest(SNAPSHOT),
                        "files": 1,
                        "text_transform": "LF or CRLF only, selected by recorded SHA-256",
                    }
                )
            )
            with patch("readiness.fetch_public_inputs.mini_files", return_value=files):
                self.assertEqual(download_provenance(root), MINI_REVISION)
                self.assertEqual(git_revision(root), MINI_REVISION)
                (root / "public").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "Existing file differs"):
                    download_provenance(root)

    def test_enclosing_git_repository_is_not_dataset_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "dataset"
            root.mkdir()
            with patch(
                "readiness.check_datasets.subprocess.run",
                return_value=SimpleNamespace(stdout=folder),
            ) as command:
                with self.assertRaisesRegex(ReadinessFailure, "enclosing repository"):
                    git_revision(root)
                self.assertEqual(command.call_count, 1)

    def test_text_hash_normalisation_is_exact(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "data.jsonl"
            path.write_bytes(b"one\ntwo\n")
            expected = hashlib.sha256(b"one\r\ntwo\r\n").hexdigest()
            verify(path, expected, text=True)
            self.assertEqual(path.read_bytes(), b"one\r\ntwo\r\n")
            path.write_bytes(b"different\n")
            with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
                verify(path, expected, text=True)
            self.assertEqual(path.read_bytes(), b"different\n")

    def test_existing_files_are_verified_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "data").write_bytes(b"original")
            expected = hashlib.sha256(b"original").hexdigest()
            self.assertEqual(missing_files(root, {"data": expected}), [])
            self.assertEqual(missing_files(root, {"missing": expected}), ["missing"])
            with self.assertRaisesRegex(ValueError, "Existing file differs"):
                missing_files(root, {"data": "0" * 64})
            with self.assertRaisesRegex(ValueError, "Destination appeared"):
                install(root / "unused", root, ["data"])
            self.assertEqual((root / "data").read_bytes(), b"original")

    def test_archive_paths_and_ambiguous_members_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ("../outside", "/absolute", "C:/outside", "a\\b"):
                with self.assertRaises(ValueError):
                    safe_path(root, name)
            archive = root / "data.zip"
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("../dev.json", b"bad")
            with zipfile.ZipFile(archive) as source:
                with self.assertRaises(ValueError):
                    zip_member(source, "dev.json", root / "result")
            with zipfile.ZipFile(archive, "w") as target:
                target.writestr("a/dev.json", b"one")
                target.writestr("b/dev.json", b"two")
            with zipfile.ZipFile(archive) as source:
                with self.assertRaisesRegex(ValueError, "Expected one"):
                    zip_member(source, "dev.json", root / "result")
