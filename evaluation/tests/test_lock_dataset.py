import importlib.util
import json
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).parents[1] / "src"
SPEC = importlib.util.spec_from_file_location("lock_dataset", SRC / "lock_dataset.py")
lock_dataset = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lock_dataset
SPEC.loader.exec_module(lock_dataset)


def make_dataset(tmp_path: Path) -> Path:
    dataset = tmp_path / "dataset"
    (dataset / "images").mkdir(parents=True)
    (dataset / "annotations").mkdir()
    (dataset / "images/inv_0001.jpg").write_bytes(b"image")
    annotation = {
        "data": {"products": [{"name": "product", "quantity": 1}]},
        "instances": [{"product_name": "product", "bbox": {}}],
    }
    (dataset / "annotations/inv_0001.json").write_text(
        json.dumps(annotation), encoding="utf-8"
    )
    row = {
        "sample_id": "inv_0001",
        "annotation": "annotations/inv_0001.json",
        "image": "images/inv_0001.jpg",
        "split": "validation",
        "session_id": "session_1",
    }
    (dataset / "manifest.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (dataset / "schema.json").write_text("{}\n", encoding="utf-8")
    (dataset / "catalog.json").write_text("[]\n", encoding="utf-8")
    (dataset / "dataset_metadata.json").write_text(
        json.dumps({"dataset_id": "test", "dataset_version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    return dataset


def test_create_and_verify_lock(tmp_path):
    dataset = make_dataset(tmp_path)

    created = lock_dataset.create_lock(dataset)
    verified = lock_dataset.verify_lock(dataset)

    assert created["statistics"]["samples"] == 1
    assert created["statistics"]["class_coverage"] == {"validation": 1}
    assert verified["verified"] is True


def test_verification_detects_changes_and_lock_cannot_be_overwritten(tmp_path):
    dataset = make_dataset(tmp_path)
    lock_dataset.create_lock(dataset)
    with pytest.raises(ValueError, match="already locked"):
        lock_dataset.create_lock(dataset)

    (dataset / "annotations/inv_0001.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="content hash mismatch"):
        lock_dataset.verify_lock(dataset)
