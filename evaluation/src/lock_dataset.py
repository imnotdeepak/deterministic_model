from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LOCK_VERSION = "0.1.0"
LOCK_FILENAME = "benchmark-lock.json"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def locked_files(dataset: Path) -> list[Path]:
    return sorted(
        path
        for path in dataset.rglob("*")
        if path.is_file() and path.name != LOCK_FILENAME
    )


def content_digest(dataset: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    files = locked_files(dataset)
    for path in files:
        relative = path.relative_to(dataset).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(path)))
        digest.update(b"\0")
    return digest.hexdigest(), len(files)


def dataset_statistics(dataset: Path) -> dict[str, Any]:
    rows = load_jsonl(dataset / "manifest.jsonl")
    split_counts = Counter(row["split"] for row in rows)
    sessions_by_split: dict[str, set[str]] = {}
    classes_by_split: dict[str, set[str]] = {}
    instances_by_split: Counter[str] = Counter()
    for row in rows:
        split = row["split"]
        sessions_by_split.setdefault(split, set()).add(row["session_id"])
        classes_by_split.setdefault(split, set())
        annotation = load_json(dataset / row["annotation"])
        classes_by_split[split].update(
            product["name"] for product in annotation["data"]["products"]
        )
        instances_by_split[split] += len(annotation["instances"])
    return {
        "samples": len(rows),
        "unique_sessions": len({row["session_id"] for row in rows}),
        "split_counts": dict(sorted(split_counts.items())),
        "session_counts": {
            split: len(values) for split, values in sorted(sessions_by_split.items())
        },
        "class_coverage": {
            split: len(values) for split, values in sorted(classes_by_split.items())
        },
        "instance_counts": dict(sorted(instances_by_split.items())),
    }


def create_lock(dataset: Path) -> dict[str, Any]:
    dataset = dataset.resolve()
    lock_path = dataset / LOCK_FILENAME
    if lock_path.exists():
        raise ValueError(
            f"Dataset is already locked: {lock_path}. Create a new dataset version to change it."
        )
    required = ["manifest.jsonl", "schema.json", "catalog.json", "dataset_metadata.json"]
    missing = [name for name in required if not (dataset / name).is_file()]
    if missing:
        raise ValueError(f"Dataset is missing required files: {missing}")
    metadata = load_json(dataset / "dataset_metadata.json")
    digest, file_count = content_digest(dataset)
    lock = {
        "lock_version": LOCK_VERSION,
        "locked_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_id": metadata.get("dataset_id"),
        "dataset_version": metadata.get("dataset_version"),
        "immutable_content_sha256": digest,
        "locked_file_count": file_count,
        "core_hashes": {
            name: sha256_file(dataset / name) for name in required
        },
        "statistics": dataset_statistics(dataset),
        "policy": (
            "Do not modify files covered by this lock. Create a new dataset version for "
            "any image, annotation, catalog, schema, metadata, or split change."
        ),
    }
    lock_path.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
    return lock


def verify_lock(dataset: Path) -> dict[str, Any]:
    dataset = dataset.resolve()
    lock_path = dataset / LOCK_FILENAME
    if not lock_path.is_file():
        raise ValueError(f"Dataset lock does not exist: {lock_path}")
    lock = load_json(lock_path)
    actual_digest, actual_count = content_digest(dataset)
    errors = []
    if actual_digest != lock.get("immutable_content_sha256"):
        errors.append(
            "content hash mismatch: "
            f"expected {lock.get('immutable_content_sha256')}, got {actual_digest}"
        )
    if actual_count != lock.get("locked_file_count"):
        errors.append(
            f"file count mismatch: expected {lock.get('locked_file_count')}, got {actual_count}"
        )
    for name, expected in lock.get("core_hashes", {}).items():
        path = dataset / name
        actual = sha256_file(path) if path.is_file() else None
        if actual != expected:
            errors.append(f"core hash mismatch for {name}: expected {expected}, got {actual}")
    if errors:
        raise ValueError("Dataset lock verification failed:\n- " + "\n- ".join(errors))
    return {
        "verified": True,
        "dataset_id": lock.get("dataset_id"),
        "dataset_version": lock.get("dataset_version"),
        "immutable_content_sha256": actual_digest,
        "locked_file_count": actual_count,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or verify an immutable dataset lock.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = verify_lock(args.dataset) if args.verify else create_lock(args.dataset)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Dataset lock failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
