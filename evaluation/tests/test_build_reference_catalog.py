import importlib.util
import sys
from pathlib import Path


SRC = Path(__file__).parents[1] / "src"
SPEC = importlib.util.spec_from_file_location(
    "build_reference_catalog", SRC / "build_reference_catalog.py"
)
build_reference_catalog = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = build_reference_catalog
SPEC.loader.exec_module(build_reference_catalog)


def test_excluded_scene_ids_uses_every_benchmark_split():
    manifest = [
        {"sample_id": "one", "source_image_id": 709, "split": "development"},
        {"sample_id": "two", "source_image_id": 1421, "split": "validation"},
        {"sample_id": "three", "source_image_id": 9902, "split": "test"},
    ]

    assert build_reference_catalog.excluded_scene_ids(manifest) == {7, 14, 99}


def test_reference_selection_excludes_benchmark_scenes_and_prefers_opposite_views():
    images = {
        100: {"id": 100, "width": 100, "height": 100, "file_name": "excluded.jpg"},
        200: {"id": 200, "width": 100, "height": 100, "file_name": "front.jpg"},
        205: {"id": 205, "width": 100, "height": 100, "file_name": "back.jpg"},
        203: {"id": 203, "width": 100, "height": 100, "file_name": "side.jpg"},
    }
    annotations = [
        {"id": 1, "image_id": 100, "category_id": 9, "bbox": [0, 0, 90, 90]},
        {"id": 2, "image_id": 200, "category_id": 9, "bbox": [10, 10, 50, 50]},
        {"id": 3, "image_id": 205, "category_id": 9, "bbox": [10, 10, 40, 40]},
        {"id": 4, "image_id": 203, "category_id": 9, "bbox": [10, 10, 80, 80]},
    ]

    selected = build_reference_catalog.select_references(
        images, annotations, [9], {1}, references_per_product=2
    )

    assert [item["image"]["id"] for item in selected[9]] == [200, 205]


def test_padded_crop_box_clamps_to_image_bounds():
    assert build_reference_catalog.padded_crop_box([0, 0, 20, 10], 100, 80) == (
        0,
        0,
        22,
        12,
    )
