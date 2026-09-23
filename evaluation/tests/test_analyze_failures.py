import importlib.util
import json
import sys
from pathlib import Path

import pytest


SRC = Path(__file__).parents[1] / "src"
SPEC = importlib.util.spec_from_file_location(
    "analyze_failures", SRC / "analyze_failures.py"
)
analyze_failures = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = analyze_failures
SPEC.loader.exec_module(analyze_failures)


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def build_dataset(tmp_path: Path, split: str = "validation") -> Path:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = [
        {
            "sample_id": "inv_0001",
            "annotation": "annotations/inv_0001.json",
            "split": split,
            "tags": ["rotation_1"],
        },
        {
            "sample_id": "inv_0002",
            "annotation": "annotations/inv_0002.json",
            "split": split,
            "tags": ["rotation_2"],
        },
    ]
    (dataset / "manifest.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8"
    )
    write_json(
        dataset / "annotations/inv_0001.json",
        {"data": {"products": [{"name": "alpha", "quantity": 2}]}},
    )
    write_json(
        dataset / "annotations/inv_0002.json",
        {"data": {"products": [{"name": "beta", "quantity": 1}]}},
    )
    return dataset


def test_analyze_classifies_identity_quantity_localization_and_routing(tmp_path):
    dataset = build_dataset(tmp_path)
    predictions = tmp_path / "predictions"
    candidates_a = tmp_path / "candidate-a"
    candidates_b = tmp_path / "candidate-b"
    decisions = tmp_path / "decisions"

    write_json(
        predictions / "inv_0001.json",
        {"data": {"products": [{"name": "wrong", "quantity": 2}]}},
    )
    write_json(
        predictions / "inv_0002.json",
        {"data": {"products": [{"name": "beta", "quantity": 1}]}},
    )
    write_json(
        candidates_a / "inv_0001.json",
        {"data": {"products": [{"name": "alpha", "quantity": 2}]}},
    )
    write_json(
        candidates_b / "inv_0001.json",
        {"data": {"products": [{"name": "wrong", "quantity": 2}]}},
    )
    write_json(decisions / "inv_0001.json", {"decision": "candidate_b"})
    report = tmp_path / "report.json"
    write_json(
        report,
        {
            "split": "validation",
            "samples": [
                {
                    "sample_id": "inv_0001",
                    "prediction_contract_valid": True,
                    "prediction_validation_errors": [],
                    "evidence_localization_accuracy": 0.5,
                },
                {
                    "sample_id": "inv_0002",
                    "prediction_contract_valid": True,
                    "prediction_validation_errors": [],
                    "evidence_localization_accuracy": 1.0,
                },
            ],
        },
    )

    result = analyze_failures.analyze(
        dataset,
        predictions,
        "validation",
        report_path=report,
        routing_decisions=decisions,
        candidate_a=candidates_a,
        candidate_b=candidates_b,
    )

    assert result["summary"]["product_exact_samples"] == 1
    assert result["summary"]["fully_correct_samples"] == 1
    assert result["summary"]["category_counts"] == {
        "identity": 1,
        "localization": 1,
        "routing": 1,
    }
    assert result["failures"][0]["routing"]["correct_candidate_discarded"] is True


def test_analyze_refuses_frozen_test_without_explicit_override(tmp_path):
    dataset = build_dataset(tmp_path, split="test")

    with pytest.raises(ValueError, match="frozen test split"):
        analyze_failures.analyze(dataset, tmp_path / "predictions", "test")


def test_stage_summary_reports_cost_and_latency(tmp_path):
    stage = tmp_path / "stage"
    write_json(stage / "inv_0001.json", {"latency_ms": 100, "estimated_cost_usd": 0.01})
    write_json(stage / "inv_0002.json", {"latency_ms": 300, "estimated_cost_usd": 0.03})

    result = analyze_failures.stage_summary(stage, ["inv_0001", "inv_0002"])

    assert result["total_cost_usd"] == 0.04
    assert result["median_cost_usd"] == 0.02
    assert result["latency_p50_ms"] == 200
    assert result["latency_p95_ms"] == 290
