import importlib.util
import json
import sys
from pathlib import Path


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))
SPEC = importlib.util.spec_from_file_location("export_d2s_yolo", SRC / "export_d2s_yolo.py")
export_d2s_yolo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = export_d2s_yolo
SPEC.loader.exec_module(export_d2s_yolo)
from import_d2s import Candidate  # noqa: E402


def candidate(scene_id: int, capture: int, categories: list[int]) -> Candidate:
    image_id = scene_id * 100 + capture
    return Candidate(
        "training",
        {
            "id": image_id,
            "file_name": f"D2S_{image_id:06d}.jpg",
            "width": 200,
            "height": 100,
        },
        tuple(
            {
                "id": image_id * 1000 + index,
                "image_id": image_id,
                "category_id": category_id,
                "bbox": [20, 10, 40, 20],
            }
            for index, category_id in enumerate(categories)
        ),
    )


def test_scene_split_is_deterministic_and_keeps_every_class_in_training():
    candidates = [
        candidate(1, 0, [1]),
        candidate(2, 0, [1, 2]),
        candidate(3, 0, [2]),
        candidate(4, 0, [1, 2]),
    ]

    first = export_d2s_yolo.assign_training_splits(candidates, 42, 0.25)
    second = export_d2s_yolo.assign_training_splits(candidates, 42, 0.25)

    assert first == second
    assert list(first.values()).count("val") == 1
    train_categories = set()
    for item in candidates:
        if first[item.session_id] == "train":
            train_categories.update(annotation["category_id"] for annotation in item.annotations)
    assert train_categories == {1, 2}


def test_yolo_lines_convert_xywh_to_normalized_center_format():
    lines = export_d2s_yolo.yolo_lines(candidate(1, 0, [7]), {7: 3})

    assert lines == ["3 0.20000000 0.20000000 0.20000000 0.20000000"]


def test_protected_sessions_include_only_locked_validation_and_test(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = [
        {"session_id": "d2s_scene_0001", "split": "development"},
        {"session_id": "d2s_scene_0002", "split": "validation"},
        {"session_id": "d2s_scene_0003", "split": "test"},
    ]
    (dataset / "manifest.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )

    assert export_d2s_yolo.protected_sessions(dataset) == {
        "d2s_scene_0002",
        "d2s_scene_0003",
    }
