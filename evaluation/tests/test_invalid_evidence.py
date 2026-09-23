import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[1] / "src" / "evaluate.py"
spec = importlib.util.spec_from_file_location("evaluate", MODULE_PATH)
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)

def test_invalid_evidence_bbox_is_excluded_from_localization_pool():
    pred = {
        "evidence": [
            {
                "id": "ev_bad",
                "media_id": "med_1",
                "source_type": "image_region",
                "coordinate_space": "original_pixels",
                "bbox": {"x": 10, "y": 10, "width": -5, "height": 20},
                "rotation_degrees": 0,
                "page_index": None,
                "frame_index": None,
                "timestamp_ms": None,
                "model_version": "test",
                "media_available": True,
            }
        ]
    }

    errors, evidence_by_id = evaluate.validate_evidence(pred, 100, 100)

    assert any("invalid bbox" in error for error in errors)
    assert "ev_bad" not in evidence_by_id


def test_manifest_limit_matches_runner_order():
    rows = [
        {"sample_id": "inv_0003", "split": "development"},
        {"sample_id": "inv_0001", "split": "test"},
        {"sample_id": "inv_0002", "split": "development"},
    ]

    selected = evaluate.select_manifest_rows(rows, "development", 1)

    assert [row["sample_id"] for row in selected] == ["inv_0002"]


def test_manifest_offset_matches_runner_order():
    rows = [
        {"sample_id": f"inv_{index:04d}", "split": "development"}
        for index in range(1, 6)
    ]

    selected = evaluate.select_manifest_rows(rows, "development", 2, offset=2)

    assert [row["sample_id"] for row in selected] == ["inv_0003", "inv_0004"]
