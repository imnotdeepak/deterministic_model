import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema import Draft202012Validator


SRC = Path(__file__).parents[1] / "src"
SPEC = importlib.util.spec_from_file_location("run_baseline", SRC / "run_baseline.py")
run_baseline = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = run_baseline
SPEC.loader.exec_module(run_baseline)
import evaluate


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


def test_structured_output_schema_adds_catalog_enum_without_mutating_source():
    output = run_baseline.structured_output_schema(SCHEMA, ["product_one", "product_two"])

    assert "$schema" not in output
    assert output["properties"]["products"]["items"]["properties"]["name"]["enum"] == [
        "product_one",
        "product_two",
    ]
    assert "enum" not in SCHEMA["properties"]["products"]["items"]["properties"]["name"]


def test_selected_rows_is_sorted_limited_and_split_filtered():
    manifest = [
        {"sample_id": "inv_0003", "split": "development"},
        {"sample_id": "inv_0001", "split": "test"},
        {"sample_id": "inv_0002", "split": "development"},
    ]

    rows = run_baseline.selected_rows(manifest, "development", 1)

    assert [row["sample_id"] for row in rows] == ["inv_0002"]


def test_selected_rows_applies_offset_before_limit():
    manifest = [
        {"sample_id": f"inv_{index:04d}", "split": "development"}
        for index in range(1, 6)
    ]

    rows = run_baseline.selected_rows(manifest, "development", 2, offset=2)

    assert [row["sample_id"] for row in rows] == ["inv_0003", "inv_0004"]
    with pytest.raises(ValueError, match="offset"):
        run_baseline.selected_rows(manifest, "development", 1, offset=-1)


def test_validate_model_data_rejects_unknown_and_duplicate_products():
    validator = Draft202012Validator(SCHEMA)
    with pytest.raises(ValueError, match="outside the catalog"):
        run_baseline.validate_model_data(
            {"products": [{"name": "unknown", "quantity": 1}]},
            validator,
            {"product_one"},
        )
    with pytest.raises(ValueError, match="duplicate"):
        run_baseline.validate_model_data(
            {
                "products": [
                    {"name": "product_one", "quantity": 1},
                    {"name": "product_one", "quantity": 2},
                ]
            },
            validator,
            {"product_one"},
        )


def test_call_model_uses_image_input_strict_schema_and_no_storage():
    class Responses:
        def __init__(self):
            self.request = None

        def create(self, **request):
            self.request = request
            return {"ok": True}

    responses = Responses()
    client = SimpleNamespace(responses=responses)
    output_schema = run_baseline.structured_output_schema(SCHEMA, ["product_one"])

    run_baseline.call_model(
        client,
        model="test-model",
        prompt="extract inventory",
        target_image_url="data:image/jpeg;base64,target",
        reference_image_urls=["data:image/jpeg;base64,reference"],
        output_schema=output_schema,
        detail="high",
        reference_detail="low",
        reasoning_effort="none",
        timeout_seconds=30,
    )

    request = responses.request
    content = request["input"][0]["content"]
    images = [item for item in content if item["type"] == "input_image"]
    assert images == [
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,reference",
            "detail": "low",
        },
        {
            "type": "input_image",
            "image_url": "data:image/jpeg;base64,target",
            "detail": "high",
        },
    ]
    assert "REFERENCE SHEET" in content[1]["text"]
    assert "TARGET IMAGE" in content[-2]["text"]
    assert request["text"]["format"]["strict"] is True
    assert request["text"]["format"]["schema"] == output_schema
    assert request["store"] is False
    assert request["reasoning"] == {"effort": "none"}


def test_dry_run_reads_real_dataset_without_api_key(tmp_path):
    dataset = Path(__file__).parents[1] / "datasets" / "inventory-v0"
    args = run_baseline.parse_args(
        [
            "--dataset",
            str(dataset),
            "--split",
            "development",
            "--limit",
            "2",
            "--output",
            str(tmp_path / "run"),
            "--dry-run",
        ]
    )

    result = run_baseline.run(args)

    assert result["dry_run"] is True
    assert len(result["sample_ids"]) == 2
    assert not (tmp_path / "run").exists()


def test_estimated_cost_uses_configured_token_rates():
    assert run_baseline.estimated_cost(1_000_000, 1_000_000, 0.2, 1.2) == 1.4


def test_instance_output_validation_rejects_boxes_outside_normalized_image():
    output = {
        "instances": [
            {
                "name": "product_one",
                "bbox": {"x": 900, "y": 10, "width": 200, "height": 100},
                "confidence": 0.8,
            }
        ]
    }

    with pytest.raises(ValueError, match="normalized width"):
        run_baseline.validate_instance_output(output, {"product_one"})


def test_instance_predictions_aggregate_counts_and_emit_canonical_evidence():
    model_output = {
        "instances": [
            {
                "name": "product_two",
                "bbox": {"x": 500, "y": 100, "width": 200, "height": 300},
                "confidence": 0.9,
            },
            {
                "name": "product_one",
                "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
                "confidence": 0.8,
            },
            {
                "name": "product_one",
                "bbox": {"x": 450, "y": 250, "width": 100, "height": 200},
                "confidence": 0.7,
            },
        ]
    }

    data, metadata, evidence = run_baseline.prediction_from_instances(
        model_output,
        sample_id="inv_0001",
        image_width=2000,
        image_height=1000,
        model="test-model",
    )

    assert data == {
        "products": [
            {"name": "product_one", "quantity": 2},
            {"name": "product_two", "quantity": 1},
        ]
    }
    assert metadata["/products/0/quantity"]["evidence_refs"] == ["ev_002", "ev_003"]
    assert metadata["/products/0/name"]["confidence"] == 0.7
    assert evidence[1]["bbox"] == {"x": 200, "y": 200, "width": 600, "height": 400}
    assert evidence[0] == {
        "id": "ev_001",
        "media_id": "med_inv_0001",
        "source_type": "image_region",
        "coordinate_space": "original_pixels",
        "bbox": {"x": 1000, "y": 100, "width": 400, "height": 300},
        "rotation_degrees": 0,
        "page_index": None,
        "frame_index": None,
        "timestamp_ms": None,
        "model_version": "test-model",
        "media_available": True,
    }


def test_authoritative_single_product_keeps_data_and_attaches_all_boxes():
    authoritative = {"products": [{"name": "product_one", "quantity": 2}]}
    model_output = {
        "instances": [
            {
                "name": "wrong_visual_label",
                "bbox": {"x": 100, "y": 100, "width": 200, "height": 300},
                "confidence": 0.7,
            },
            {
                "name": "product_one",
                "bbox": {"x": 500, "y": 100, "width": 200, "height": 300},
                "confidence": 0.9,
            },
        ]
    }

    data, metadata, evidence = run_baseline.evidence_for_authoritative_data(
        authoritative,
        model_output,
        sample_id="inv_test",
        image_width=1000,
        image_height=500,
        model="test-model",
    )

    assert data == authoritative
    assert data is not authoritative
    assert metadata["/products/0/quantity"]["evidence_refs"] == ["ev_001", "ev_002"]
    assert metadata["/products/0/name"]["confidence"] == 0.7
    assert len(evidence) == 2


def test_authoritative_multi_product_attaches_only_exact_name_matches():
    authoritative = {
        "products": [
            {"name": "product_one", "quantity": 1},
            {"name": "product_two", "quantity": 1},
        ]
    }
    model_output = {
        "instances": [
            {
                "name": "product_one",
                "bbox": {"x": 100, "y": 100, "width": 200, "height": 300},
                "confidence": 0.8,
            },
            {
                "name": "unmatched_product",
                "bbox": {"x": 500, "y": 100, "width": 200, "height": 300},
                "confidence": 0.9,
            },
        ]
    }

    data, metadata, evidence = run_baseline.evidence_for_authoritative_data(
        authoritative,
        model_output,
        sample_id="inv_test",
        image_width=1000,
        image_height=500,
        model="test-model",
    )

    assert data == authoritative
    assert len(evidence) == 1
    assert metadata["/products/0/name"]["evidence_refs"] == ["ev_001"]
    assert metadata["/products/1/name"] == {
        "confidence": 0.0,
        "status": "unverified",
        "evidence_refs": [],
    }


def test_instance_prompt_distinguishes_references_from_target():
    prompt = run_baseline.build_prompt(
        ["product_one"], has_visual_references=True, instance_localization=True
    )

    assert "Do not aggregate quantities" in prompt
    assert "normalized 0-1000 TARGET IMAGE coordinates" in prompt
    assert "REFERENCE SHEET" in prompt


def test_exhaustive_instance_prompt_counts_objects_with_hidden_labels():
    prompt = run_baseline.build_prompt(
        ["product_one"],
        has_visual_references=True,
        instance_localization=True,
        exhaustive_instance_search=True,
    )

    assert "showing only a cap, side, rear, bottom" in prompt
    assert "Do not omit a physical object merely because its label is unreadable" in prompt
    assert "lower confidence instead of omitting the object" in prompt
    assert "Omit products that cannot be matched confidently" not in prompt


def test_instance_prediction_satisfies_evaluator_evidence_contract():
    model_output = {
        "instances": [
            {
                "name": "product_one",
                "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
                "confidence": 0.9,
            }
        ]
    }
    data, metadata, evidence_items = run_baseline.prediction_from_instances(
        model_output,
        sample_id="inv_test",
        image_width=1000,
        image_height=1000,
        model="test-model",
    )
    prediction = {
        "data": data,
        "field_metadata": metadata,
        "evidence": evidence_items,
    }
    truth = {
        "data": {"products": [{"name": "product_one", "quantity": 1}]},
        "instances": [
            {
                "product_name": "product_one",
                "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
            }
        ],
    }

    result = evaluate.evaluate_prediction(
        truth,
        prediction,
        Draft202012Validator(SCHEMA),
        {"product_one"},
        1000,
        1000,
        0.5,
    )

    assert result["prediction_contract_valid"] is True
    assert result["whole_image_exact"] is True
    assert result["evidence_reference_coverage"] == 1.0
    assert result["evidence_localization_accuracy"] == 1.0


def test_instance_mode_runs_end_to_end_with_fake_client(tmp_path):
    dataset = Path(__file__).parents[1] / "datasets" / "inventory-v0"
    catalog = json.loads((dataset / "catalog.json").read_text(encoding="utf-8"))
    name = catalog[0]["name"]

    class FakeResponse:
        id = "resp_test"
        usage = {"input_tokens": 100, "output_tokens": 20}
        output_text = json.dumps(
            {
                "instances": [
                    {
                        "name": name,
                        "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
                        "confidence": 0.8,
                    }
                ]
            }
        )

        def model_dump(self, mode="json"):
            return {"id": self.id, "output_text": self.output_text, "usage": self.usage}

    client = SimpleNamespace(
        responses=SimpleNamespace(create=lambda **request: FakeResponse())
    )
    output = tmp_path / "instance-run"
    args = run_baseline.parse_args(
        [
            "--dataset",
            str(dataset),
            "--split",
            "development",
            "--limit",
            "1",
            "--output",
            str(output),
            "--instance-localization",
        ]
    )

    summary = run_baseline.run(args, client=client)
    prediction = json.loads(
        next((output / "predictions").glob("*.json")).read_text(encoding="utf-8")
    )

    assert summary["completed_this_run"] == 1
    assert prediction["data"] == {"products": [{"name": name, "quantity": 1}]}
    assert prediction["field_metadata"]["/products/0/name"]["status"] == "unverified"
    assert prediction["evidence"][0]["coordinate_space"] == "original_pixels"
    assert prediction["provider_metadata"]["output_mode"] == "instances"


def test_two_stage_mode_constrains_localization_and_combines_accounting(tmp_path):
    dataset = Path(__file__).parents[1] / "datasets" / "inventory-v0"
    catalog = json.loads((dataset / "catalog.json").read_text(encoding="utf-8"))
    name = catalog[0]["name"]

    class FakeResponse:
        usage = {"input_tokens": 100, "output_tokens": 20}

        def __init__(self, response_id, payload):
            self.id = response_id
            self.output_text = json.dumps(payload)

        def model_dump(self, mode="json"):
            return {"id": self.id, "output_text": self.output_text, "usage": self.usage}

    responses = [
        FakeResponse(
            "resp_identity", {"products": [{"name": name, "quantity": 1}]}
        ),
        FakeResponse(
            "resp_localization",
            {
                "instances": [
                    {
                        "name": name,
                        "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
                        "confidence": 0.9,
                    }
                ]
            },
        ),
    ]

    class FakeResponses:
        def __init__(self):
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            return responses[len(self.requests) - 1]

    endpoint = FakeResponses()
    output = tmp_path / "two-stage-run"
    args = run_baseline.parse_args(
        [
            "--dataset",
            str(dataset),
            "--split",
            "development",
            "--limit",
            "1",
            "--output",
            str(output),
            "--two-stage",
        ]
    )

    summary = run_baseline.run(args, client=SimpleNamespace(responses=endpoint))
    prediction_path = next((output / "predictions").glob("*.json"))
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    raw = json.loads(
        (output / "raw-responses" / prediction_path.name).read_text(encoding="utf-8")
    )

    assert summary["completed_this_run"] == 1
    assert len(endpoint.requests) == 2
    assert "products" in endpoint.requests[0]["text"]["format"]["schema"]["properties"]
    second_schema = endpoint.requests[1]["text"]["format"]["schema"]
    assert second_schema["properties"]["instances"]["items"]["properties"]["name"][
        "enum"
    ] == [name]
    assert name in endpoint.requests[1]["input"][0]["content"][0]["text"]
    assert set(raw) == {"identity", "localization"}
    assert prediction["estimated_cost_usd"] == 0.000088
    assert prediction["usage"]["input_tokens"] == 200
    assert prediction["provider_metadata"]["response_ids"] == {
        "identity": "resp_identity",
        "localization": "resp_localization",
    }
    assert prediction["provider_metadata"]["identity_candidates"] == [name]
    assert prediction["provider_metadata"]["output_mode"] == "two_stage_instances"


def test_evidence_only_mode_reuses_frozen_identity_and_calls_api_once(tmp_path):
    dataset = Path(__file__).parents[1] / "datasets" / "inventory-v0"
    catalog = json.loads((dataset / "catalog.json").read_text(encoding="utf-8"))
    authoritative_name = catalog[0]["name"]
    localization_name = catalog[1]["name"]
    manifest = run_baseline.read_jsonl(dataset / "manifest.jsonl")
    sample_id = run_baseline.selected_rows(manifest, "development", 1)[0]["sample_id"]

    identity_dir = tmp_path / "identity-predictions"
    identity_dir.mkdir()
    frozen_data = {"products": [{"name": authoritative_name, "quantity": 3}]}
    (identity_dir / f"{sample_id}.json").write_text(
        json.dumps({"sample_id": sample_id, "data": frozen_data}), encoding="utf-8"
    )

    class FakeResponse:
        id = "resp_localization"
        usage = {"input_tokens": 100, "output_tokens": 20}
        output_text = json.dumps(
            {
                "instances": [
                    {
                        "name": localization_name,
                        "bbox": {"x": 100, "y": 200, "width": 300, "height": 400},
                        "confidence": 0.8,
                    }
                ]
            }
        )

        def model_dump(self, mode="json"):
            return {"id": self.id, "output_text": self.output_text, "usage": self.usage}

    class FakeResponses:
        def __init__(self):
            self.requests = []

        def create(self, **request):
            self.requests.append(request)
            return FakeResponse()

    endpoint = FakeResponses()
    output = tmp_path / "evidence-run"
    args = run_baseline.parse_args(
        [
            "--dataset",
            str(dataset),
            "--split",
            "development",
            "--limit",
            "1",
            "--output",
            str(output),
            "--evidence-only",
            "--identity-predictions",
            str(identity_dir),
        ]
    )

    summary = run_baseline.run(args, client=SimpleNamespace(responses=endpoint))
    prediction_path = output / "predictions" / f"{sample_id}.json"
    prediction = json.loads(prediction_path.read_text(encoding="utf-8"))
    raw = json.loads(
        (output / "raw-responses" / f"{sample_id}.json").read_text(encoding="utf-8")
    )

    assert summary["completed_this_run"] == 1
    assert len(endpoint.requests) == 1
    schema_names = endpoint.requests[0]["text"]["format"]["schema"]["properties"][
        "instances"
    ]["items"]["properties"]["name"]["enum"]
    assert schema_names == [item["name"] for item in catalog]
    assert "may return any allowed catalog name" in endpoint.requests[0]["input"][0][
        "content"
    ][0]["text"]
    assert prediction["data"] == frozen_data
    assert prediction["evidence"][0]["id"] == "ev_001"
    assert prediction["usage"]["by_stage"].keys() == {"localization"}
    assert prediction["provider_metadata"]["output_mode"] == "aggregate_with_evidence"
    assert prediction["provider_metadata"]["identity_source"] == "predictions"
    assert set(raw) == {"identity_source", "localization"}
