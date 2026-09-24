import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location(
    "prepare_yolo_localizer", SRC / "prepare_yolo_localizer.py"
)
prepare_yolo_localizer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = prepare_yolo_localizer
SPEC.loader.exec_module(prepare_yolo_localizer)


def test_localizer_lines_replaces_classes_and_preserves_boxes():
    assert prepare_yolo_localizer.localizer_lines(
        "4 0.5 0.4 0.3 0.2\n1 0.2 0.3 0.1 0.1\n", 5
    ) == ["0 0.5 0.4 0.3 0.2", "0 0.2 0.3 0.1 0.1"]


def test_localizer_lines_rejects_invalid_class():
    with pytest.raises(ValueError, match="out of range"):
        prepare_yolo_localizer.localizer_lines("5 0.5 0.5 0.2 0.2\n", 5)


def test_prepare_builds_single_class_dataset(tmp_path):
    source = tmp_path / "source"
    for split in ("train", "val"):
        (source / "images" / split).mkdir(parents=True)
        (source / "labels" / split).mkdir(parents=True)
        Image.new("RGB", (20, 10)).save(source / "images" / split / f"{split}.jpg")
        (source / "labels" / split / f"{split}.txt").write_text(
            "1 0.5 0.5 0.5 0.5\n", encoding="utf-8"
        )
    (source / "dataset.yaml").write_text(
        'train: images/train\nval: images/val\nnames:\n  0: "one"\n  1: "two"\n',
        encoding="utf-8",
    )
    (source / "export-metadata.json").write_text(
        json.dumps({"protected_scene_count": 7}), encoding="utf-8"
    )

    output = tmp_path / "output"
    summary = prepare_yolo_localizer.prepare(source, output)

    assert summary["category_count"] == 1
    assert summary["splits"]["train"] == {"images": 1, "instances": 1}
    assert (output / "labels" / "train" / "train.txt").read_text() == (
        "0 0.5 0.5 0.5 0.5\n"
    )
    assert (output / "images" / "train" / "train.jpg").is_file()
