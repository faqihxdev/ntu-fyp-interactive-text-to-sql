"""Download and verify public inputs. No credentials or model calls are used."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tempfile
from urllib.request import Request, urlopen
import zipfile

from interaction.sphinteract_source import (
    SPHINTERACT_COMMIT,
    SPHINTERACT_NOTEBOOK_SHA256,
)

EXPERIMENT = Path(__file__).resolve().parents[1]
BIRD_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
BIRD_FREEZE = (
    EXPERIMENT
    / "manifests/api-bird-dev-clarification-value-v1-full1534-freeze-2026-08-30.json"
)
MINI_REVISION = "10253f235dbc6092a97e9c0acd30dba6203d8e59"
MINI_SOURCE = "https://huggingface.co/datasets/birdsql/mini-interact"
SNAPSHOT = EXPERIMENT / "manifests/datasets-readiness.json"
MARKER = ".public-inputs.json"


def digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def safe_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative)
    if (
        parts.is_absolute()
        or ".." in parts.parts
        or "\\" in relative
        or ":" in relative
    ):
        raise ValueError(f"Unsafe archive path: {relative}")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path == root.resolve():
        raise ValueError(f"Path leaves the download directory: {relative}")
    return path


def download(url: str, destination: Path, max_bytes: int = 2_000_000_000) -> None:
    print(f"Downloading {url}", flush=True)
    request = Request(url, headers={"User-Agent": "interactive-text-to-sql-setup"})
    with urlopen(request, timeout=60) as response, destination.open("wb") as target:
        if not response.url.startswith("https://"):
            raise ValueError("Download was redirected to an insecure URL.")
        total = 0
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError("Download exceeds the expected size limit.")
            target.write(chunk)


def verify(path: Path, expected: str, *, text: bool = False) -> None:
    if digest(path) == expected:
        return
    # Historical text hashes include Windows checkout line endings.
    if text:
        raw = path.read_bytes().replace(b"\r\n", b"\n")
        for candidate in (raw, raw.replace(b"\n", b"\r\n")):
            if hashlib.sha256(candidate).hexdigest() == expected:
                path.write_bytes(candidate)
                return
    raise ValueError(
        f"Checksum mismatch: {path.name}. No unverified file was installed."
    )


def missing_files(root: Path, hashes: dict[str, str]) -> list[str]:
    missing = []
    for relative, expected in hashes.items():
        path = safe_path(root, relative)
        if not path.exists():
            missing.append(relative)
        elif not path.is_file() or digest(path) != expected:
            raise ValueError(
                f"Existing file differs: {path}. Move it aside before retrying; it was not overwritten."
            )
    return missing


def install(staging: Path, root: Path, names: list[str]) -> None:
    for relative in names:
        target = safe_path(root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError(f"Destination appeared during download: {target}")
        shutil.move(str(safe_path(staging, relative)), str(target))


def zip_member(archive: zipfile.ZipFile, suffix: str, target: Path) -> None:
    matches = [
        item
        for item in archive.infolist()
        if item.filename == suffix or item.filename.endswith("/" + suffix)
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected one archive member: {suffix}")
    item = matches[0]
    safe_path(target.parent, item.filename)
    if (
        item.is_dir()
        or item.file_size > 2_000_000_000
        or (item.external_attr >> 16) & 0o170000 == 0o120000
    ):
        raise ValueError(f"Unsafe archive member: {item.filename}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(item) as source, target.open("wb") as destination:
        shutil.copyfileobj(source, destination)


def fetch_bird(root: Path) -> None:
    prefix = "datasets/bird-dev/"
    freeze = json.loads(BIRD_FREEZE.read_text("utf-8"))
    hashes = {
        name.split(prefix, 1)[1]: value
        for name, value in freeze["artifact_hashes"].items()
        if prefix in name
    }
    if len(hashes) != 12 or "dev.json" not in hashes:
        raise ValueError("The BIRD freeze must identify metadata and eleven databases.")
    target = root / "bird-dev"
    missing = missing_files(target, hashes)
    if not missing:
        print("BIRD: all 12 files verified.")
        return
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bird-download-", dir=root) as folder:
        staging = Path(folder)
        outer = staging / "dev.zip"
        download(BIRD_URL, outer, max_bytes=500_000_000)
        with zipfile.ZipFile(outer) as archive:
            if "dev.json" in missing:
                path = staging / "files/dev.json"
                zip_member(archive, "dev.json", path)
                verify(path, hashes["dev.json"], text=True)
            dbs = [name for name in missing if name.endswith(".sqlite")]
            if dbs:
                inner = staging / "databases.zip"
                zip_member(archive, "dev_databases.zip", inner)
                with zipfile.ZipFile(inner) as databases:
                    for relative in dbs:
                        path = safe_path(staging / "files", relative)
                        zip_member(databases, "/".join(relative.split("/")[-2:]), path)
                        verify(path, hashes[relative])
        install(staging / "files", target, missing)
    print("BIRD: metadata and eleven databases verified.")


def fetch_sphinteract(root: Path) -> None:
    target = root / "sphinteract-source-21c6fcd"
    name = "zeroshot_experiments.ipynb"
    hashes = {
        name: SPHINTERACT_NOTEBOOK_SHA256,
        "LICENSE": "7a62cd8d5cceb3ba74b1b71735e1e3a87718e742c546f21fc838cdda4831e38e",
    }
    missing = missing_files(target, hashes)
    if not missing:
        print("Sphinteract: notebook verified.")
        return
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".sphinteract-download-", dir=root
    ) as folder:
        staging = Path(folder)
        for relative in missing:
            download(
                f"https://raw.githubusercontent.com/ZhaoFuheng/Sphinteract/{SPHINTERACT_COMMIT}/{relative}",
                staging / relative,
                max_bytes=100_000,
            )
            verify(staging / relative, hashes[relative], text=True)
        install(staging, target, missing)
    print("Sphinteract: pinned notebook verified.")


def mini_files() -> dict[str, dict]:
    snapshot = json.loads(SNAPSHOT.read_text("utf-8"))["snapshots"]["mini_interact"]
    if snapshot["revision"] != MINI_REVISION or snapshot["source"] != MINI_SOURCE:
        raise ValueError(
            "The Mini-Interact snapshot does not identify the pinned release."
        )
    files = {
        name: spec
        for name, spec in snapshot["files"].items()
        if not name.endswith((".sqlite-shm", ".sqlite-wal"))
    }
    if len(files) != 139:
        raise ValueError(
            "The Mini-Interact snapshot must identify 139 public release files."
        )
    return files


def download_provenance(root: Path) -> str:
    marker = json.loads((root / MARKER).read_text("utf-8"))
    files = mini_files()
    expected = {
        "source": MINI_SOURCE,
        "revision": MINI_REVISION,
        "snapshot_sha256": digest(SNAPSHOT),
        "files": len(files),
        "text_transform": "LF or CRLF only, selected by recorded SHA-256",
    }
    if marker != expected:
        raise ValueError(
            "Mini-Interact download provenance does not match the pinned snapshot."
        )
    if missing_files(root, {name: spec["sha256"] for name, spec in files.items()}):
        raise ValueError("Mini-Interact download is incomplete.")
    return MINI_REVISION


def fetch_mini(root: Path) -> None:
    target = root / "mini-interact"
    files = mini_files()
    missing = missing_files(
        target, {name: spec["sha256"] for name, spec in files.items()}
    )
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".mini-download-", dir=root) as folder:
        staging = Path(folder)

        def fetch(relative: str) -> None:
            path = safe_path(staging, relative)
            path.parent.mkdir(parents=True, exist_ok=True)
            spec = files[relative]
            download(
                f"{MINI_SOURCE}/resolve/{MINI_REVISION}/{relative}",
                path,
                max_bytes=max(spec["bytes"] + 1024, 4096),
            )
            verify(path, spec["sha256"], text=not relative.endswith(".sqlite"))
            if path.stat().st_size != spec["bytes"]:
                raise ValueError(f"Unexpected installed size: {relative}")

        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(fetch, missing))
        install(staging, target, missing)
    target.mkdir(parents=True, exist_ok=True)
    marker = {
        "source": MINI_SOURCE,
        "revision": MINI_REVISION,
        "snapshot_sha256": digest(SNAPSHOT),
        "files": len(files),
        "text_transform": "LF or CRLF only, selected by recorded SHA-256",
    }
    (target / MARKER).write_bytes((json.dumps(marker, indent=2) + "\n").encode())
    download_provenance(target)
    print(
        "Mini-Interact: all 139 public release files verified. Private answer keys are obtained separately."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", choices=["demo", "bird", "sphinteract", "mini"])
    parser.add_argument("--datasets-root", type=Path, default=EXPERIMENT / "datasets")
    args = parser.parse_args()
    try:
        if args.input in {"demo", "bird"}:
            fetch_bird(args.datasets_root)
        if args.input in {"demo", "sphinteract"}:
            fetch_sphinteract(args.datasets_root)
        if args.input == "mini":
            fetch_mini(args.datasets_root)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        parser.exit(1, f"Setup failed: {exc}\n")


if __name__ == "__main__":
    main()
