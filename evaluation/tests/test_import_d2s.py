import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from PIL import Image


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location("import_d2s", SRC / "import_d2s.py")
import_d2s = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = import_d2s
SPEC.loader.exec_module(import_d2s)


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "products": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 0},
                },
                "required": ["name", "quantity"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["products"],
    "additionalProperties": False,
}


def make_source(tmp_path: Path, scenes: int = 4) -> Path:
    source = tmp_path / "source"
    images = source / "images"
    annotations = source / "annotations"
    images.mkdir(parents=True)
    annotations.mkdir()
    categories = [
        {"id": 1, "name": "product_one", "supercategory": "test"},
        {"id": 2, "name": "product_two", "supercategory": "test"},
    ]
    split_payloads = {
        "training": {"images": [], "annotations": []},
        "validation": {"images": [], "annotations": []},
    }
    annotation_id = 1
    for scene in range(1, scenes + 1):
        source_split = "training" if scene % 2 else "validation"
        for capture in range(2):
            image_id = scene * 100 + capture
            filename = f"D2S_{image_id:06d}.jpg"
            Image.new("RGB", (100, 80), "white").save(images / filename)
            split_payloads[source_split]["images"].append(
                {"id": image_id, "file_name": filename, "width": 100, "height": 80}
            )
            for instance in range(1 + capture):
                split_payloads[source_split]["annotations"].append(
                    {
                        "id": annotation_id,
                        "image_id": image_id,
                        "category_id": 1 if scene % 2 else 2,
                        "bbox": [10 + instance * 20, 10, 10, 20],
                        "iscrowd": 0,
                    }
                )
                annotation_id += 1
    for source_split, filename in import_d2s.SOURCE_FILES.items():
        payload = {
            "images": split_payloads[source_split]["images"],
            "annotations": split_payloads[source_split]["annotations"],
            "categories": categories,
            "info": {"version": "test"},
            "licenses": [],
        }
        (annotations / filename).write_text(json.dumps(payload), encoding="utf-8")
    return source


def make_output(tmp_path: Path) -> Path:
    output = tmp_path / "inventory-v0"
    output.mkdir()
    (output / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
    return output


def importer_args(source: Path, output: Path, limit: int = 4, seed: int = 42):
    return argparse.Namespace(
        source=source,
        output=output,
        limit=limit,
        seed=seed,
        force=False,
        dry_run=False,
    )


def test_deterministic_sample_selection(tmp_path):
    source = make_source(tmp_path)
    root = import_d2s.find_source_root(source)
    candidates, categories, _ = import_d2s.load_source(root)
    by_id = {int(item["id"]): item for item in categories}
    first, _ = import_d2s.select_valid_candidates(candidates, root, by_id, 4, 42)
    second, _ = import_d2s.select_valid_candidates(candidates, root, by_id, 4, 42)
    assert [item.image_id for item in first] == [item.image_id for item in second]


def test_bbox_conversion():
    assert import_d2s.convert_bbox([1.0, 2.0, 3.0, 4.0], 10, 10) == {
        "x": 1,
        "y": 2,
        "width": 3,
        "height": 4,
    }
    with pytest.raises(ValueError):
        import_d2s.convert_bbox([9, 2, 3, 4], 10, 10)


def test_quantity_aggregation(tmp_path):
    source = make_source(tmp_path)
    candidates, categories, _ = import_d2s.load_source(source)
    candidate = next(item for item in candidates if len(item.annotations) == 2)
    annotation = import_d2s.build_annotation(
        "inv_0001", "images/inv_0001.jpg", candidate,
        {int(item["id"]): item for item in categories},
    )
    assert annotation["data"]["products"][0]["quantity"] == 2
    assert len(annotation["instances"]) == 2


def test_catalog_generation():
    catalog = import_d2s.build_catalog(
        [{"id": 9, "name": "product_nine", "supercategory": "test"}]
    )
    assert catalog == [{
        "id": "d2s_009",
        "name": "product_nine",
        "source_category_id": 9,
        "supercategory": "test",
    }]


def test_invalid_annotation_is_reported(tmp_path):
    source = make_source(tmp_path)
    candidates, categories, _ = import_d2s.load_source(source)
    candidate = candidates[0]
    bad_annotation = dict(candidate.annotations[0])
    bad_annotation["bbox"] = [99, 0, 10, 10]
    bad_candidate = import_d2s.Candidate(
        candidate.source_split, candidate.image, (bad_annotation,)
    )
    valid, errors = import_d2s.validate_candidate(
        bad_candidate, source, {int(item["id"]): item for item in categories}
    )
    assert not valid
    assert any("outside image bounds" in error for error in errors)


def test_duplicate_source_image_prevention(tmp_path):
    source = make_source(tmp_path)
    training_path = source / "annotations" / "D2S_training.json"
    validation_path = source / "annotations" / "D2S_validation.json"
    training = json.loads(training_path.read_text(encoding="utf-8"))
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["images"].append(training["images"][0])
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate D2S image id"):
        import_d2s.load_source(source)


def test_manifest_creation_and_scene_isolation(tmp_path):
    source = make_source(tmp_path)
    output = make_output(tmp_path)
    summary = import_d2s.import_dataset(importer_args(source, output, limit=6))
    rows = [json.loads(line) for line in (output / "manifest.jsonl").read_text().splitlines()]
    assert summary["selected_sample_count"] == 6
    assert len({row["sample_id"] for row in rows}) == 6
    scene_splits = defaultdict(set)
    for row in rows:
        scene_splits[row["session_id"]].add(row["split"])
    assert all(len(splits) == 1 for splits in scene_splits.values())


def test_generated_output_schema_validity(tmp_path):
    source = make_source(tmp_path)
    output = make_output(tmp_path)
    import_d2s.import_dataset(importer_args(source, output, limit=4))
    validator = Draft202012Validator(SCHEMA)
    for path in (output / "annotations").glob("inv_*.json"):
        annotation = json.loads(path.read_text(encoding="utf-8"))
        assert not list(validator.iter_errors(annotation["data"]))
