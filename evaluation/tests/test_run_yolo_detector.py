import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SRC = Path(__file__).parents[1] / "src"
sys.path.insert(0, str(SRC))


def load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SRC / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


run_yolo_detector = load_module("run_yolo_detector")
evaluate = load_module("evaluate")


SCHEMA = {
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


def test_pixel_bbox_clamps_and_uses_exclusive_right_bottom_edges():
    assert run_yolo_detector.pixel_bbox([-1.2, 2.2, 100.1, 50.9], 100, 50) == {
        "x": 0,
        "y": 2,
        "width": 100,
        "height": 48,
    }


def test_prediction_aggregates_instances_and_satisfies_evidence_contract():
    prediction = run_yolo_detector.build_prediction(
        sample_id="inv_test",
        image_width=100,
        image_height=50,
        detections=[
            {"name": "product_one", "confidence": 0.9, "xyxy": [10, 5, 30, 25]},
            {"name": "product_one", "confidence": 0.7, "xyxy": [50, 5, 80, 25]},
        ],
        model_version="test-model",
        checkpoint_sha256="abc",
        confidence_threshold=0.25,
        iou_threshold=0.7,
        latency_ms=4.2,
    )
    truth = {
        "data": {"products": [{"name": "product_one", "quantity": 2}]},
        "instances": [
            {"product_name": "product_one", "bbox": {"x": 10, "y": 5, "width": 20, "height": 20}},
            {"product_name": "product_one", "bbox": {"x": 50, "y": 5, "width": 30, "height": 20}},
        ],
    }

    result = evaluate.evaluate_prediction(
        truth,
        prediction,
        Draft202012Validator(SCHEMA),
        {"product_one"},
        100,
        50,
        0.5,
    )

    assert prediction["data"] == {
        "products": [{"name": "product_one", "quantity": 2}]
    }
    assert prediction["field_metadata"]["/products/0/name"]["confidence"] == 0.7
    assert result["prediction_contract_valid"] is True
    assert result["whole_image_exact"] is True
    assert result["evidence_reference_coverage"] == 1.0
    assert result["evidence_localization_accuracy"] == 1.0


def test_selected_rows_refuses_locked_splits():
    manifest = [
        {"sample_id": "inv_0001", "split": "development"},
        {"sample_id": "inv_0002", "split": "validation"},
    ]

    with pytest.raises(ValueError, match="development-only"):
        run_yolo_detector.selected_rows(manifest, "validation", 0, None)


def test_locked_validation_requires_full_split_and_frozen_configuration():
    manifest = [
        {"sample_id": "inv_0001", "split": "development"},
        {"sample_id": "inv_0002", "split": "validation"},
    ]

    assert run_yolo_detector.selected_rows(
        manifest, "validation", 0, None, allow_locked_validation=True
    ) == [{"sample_id": "inv_0002", "split": "validation"}]
    with pytest.raises(ValueError, match="complete split"):
        run_yolo_detector.selected_rows(
            manifest, "validation", 0, 1, allow_locked_validation=True
        )

    run_yolo_detector.validate_frozen_validation_config(
        split="validation",
        checkpoint_sha256=run_yolo_detector.FROZEN_VALIDATION_CHECKPOINT_SHA256,
        confidence=0.35,
        iou=0.70,
        image_size=960,
    )
    with pytest.raises(ValueError, match="differs from the frozen"):
        run_yolo_detector.validate_frozen_validation_config(
            split="validation",
            checkpoint_sha256=run_yolo_detector.FROZEN_VALIDATION_CHECKPOINT_SHA256,
            confidence=0.40,
            iou=0.70,
            image_size=960,
        )


def test_selected_rows_always_refuses_test_even_with_validation_authorization():
    manifest = [{"sample_id": "inv_0003", "split": "test"}]
    with pytest.raises(ValueError, match="test split"):
        run_yolo_detector.selected_rows(
            manifest, "test", 0, None, allow_locked_validation=True
        )


def test_checkpoint_catalog_requires_exact_order():
    run_yolo_detector.validate_checkpoint_catalog(
        {0: "product_one", 1: "product_two"}, ["product_one", "product_two"]
    )
    with pytest.raises(ValueError, match="order_matches=False"):
        run_yolo_detector.validate_checkpoint_catalog(
            {0: "product_two", 1: "product_one"}, ["product_one", "product_two"]
        )


def test_chunks_never_exceeds_requested_batch_size():
    assert list(run_yolo_detector.chunks(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]
