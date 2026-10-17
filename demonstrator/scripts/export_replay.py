"""Export selected BIRD records and checked policy inputs for offline replay."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from explorer import APP_ROOT  # noqa: E402
from explorer.replay import DATABASES, IDS, METHODS, RUN, ReplayStore  # noqa: E402


def export(run: Path, databases: Path, destination: Path) -> None:
    store = ReplayStore(run, databases)
    bundle = {
        "version": 1,
        "attribution": "BIRD authors; data and derived previews: CC BY-SA 4.0. See THIRD_PARTY_NOTICES.md.",
        "source": run.name,
        "analysis_sha256": hashlib.sha256((run / "analysis.json").read_bytes()).hexdigest(),
        "run_contract_sha256": hashlib.sha256((run / "run-contract.json").read_bytes()).hexdigest(),
        "summary": store.summary(),
        "cases": {},
    }
    for case_id in sorted(IDS):
        raw = (run / "tasks" / f"{case_id:04}.json").read_bytes()
        db_id = store.task(case_id).db_id
        with (databases / db_id / f"{db_id}.sqlite").open("rb") as handle:
            db_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        entry = {
            "record": raw.decode("utf-8"),
            "record_sha256": hashlib.sha256(raw).hexdigest(),
            "schema": store.schema(case_id),
            "database_sha256": db_hash,
            "contexts": {},
            "previews": {},
        }
        for method in METHODS:
            for step in store.steps(case_id, method):
                key = f"{method}/{step['id']}"
                entry["contexts"][key] = store.context(case_id, method, step["id"])
                if step["kind"] == "sql":
                    preview = dict(store.preview(case_id, method, step["id"]))
                    if preview["status"] == "timeout":
                        raise ValueError(f"Preview timed out: {case_id}/{key}")
                    preview.pop("elapsed_ms", None)
                    preview["source"] = (
                        "Cached read-only preview from the BIRD database; benchmark scores come from the saved run."
                    )
                    entry["previews"][key] = preview
        bundle["cases"][str(case_id)] = entry
    raw = (json.dumps(bundle, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    destination.with_suffix(".sha256").write_bytes(
        (hashlib.sha256(raw).hexdigest() + "\n").encode()
    )
    print(f"Exported {len(IDS)} cases ({len(raw):,} bytes) to {destination}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, default=RUN)
    parser.add_argument("--databases", type=Path, default=DATABASES)
    parser.add_argument("--output", type=Path, default=APP_ROOT / "data/replay.json")
    args = parser.parse_args()
    export(args.run, args.databases, args.output)
