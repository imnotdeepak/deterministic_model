import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


SRC = Path(__file__).parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

if "run_baseline" not in sys.modules:
    baseline_spec = importlib.util.spec_from_file_location(
        "run_baseline", SRC / "run_baseline.py"
    )
    run_baseline = importlib.util.module_from_spec(baseline_spec)
    sys.modules[baseline_spec.name] = run_baseline
    baseline_spec.loader.exec_module(run_baseline)
else:
    run_baseline = sys.modules["run_baseline"]

resolver_spec = importlib.util.spec_from_file_location(
    "run_disagreement_resolver", SRC / "run_disagreement_resolver.py"
)
resolver = importlib.util.module_from_spec(resolver_spec)
sys.modules[resolver_spec.name] = resolver
resolver_spec.loader.exec_module(resolver)


def write_predictions(directory: Path, values: dict[str, dict]) -> None:
    directory.mkdir(parents=True)
    for sample_id, data in values.items():
        (directory / f"{sample_id}.json").write_text(
            json.dumps({"sample_id": sample_id, "data": data}), encoding="utf-8"
        )


def test_predictions_agree_ignores_product_order():
    a = {
        "data": {
            "products": [
                {"name": "one", "quantity": 1},
                {"name": "two", "quantity": 2},
            ]
        }
    }
    b = {
        "data": {
            "products": [
                {"name": "two", "quantity": 2},
                {"name": "one", "quantity": 1},
            ]
        }
    }

    assert resolver.predictions_agree(a, b)


def test_confusion_sets_expand_candidate_to_full_visual_family():
    evaluation = Path(__file__).parents[1]
    catalog = json.loads(
        (evaluation / "datasets" / "inventory-v0" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    allowed_names = {item["name"] for item in catalog}
    version, families = resolver.load_confusion_sets(
        evaluation / "confusion_sets.json", allowed_names
    )

    names, family_ids = resolver.targeted_reference_names(
        ["gepa_bio_caffe_crema"], families, max_products=12
    )

    assert version == "inventory-confusion-sets-v1"
    assert family_ids == ["coffee_packages"]
    assert names == [
        "cafe_wunderbar_espresso",
        "douwe_egberts_professional_ground_coffee",
        "gepa_bio_caffe_crema",
        "gepa_italienischer_bio_espresso",
    ]


def test_isolated_confusion_targets_one_family_and_rejects_multi_family_scene():
    families = [
        {"id": "coffee", "members": ["coffee_a", "coffee_b"]},
        {"id": "fruit", "members": ["apple_a", "apple_b"]},
    ]
    coffee_a = {
        "products": [
            {"name": "shared", "quantity": 1},
            {"name": "coffee_a", "quantity": 1},
        ]
    }
    coffee_b = {
        "products": [
            {"name": "shared", "quantity": 1},
            {"name": "coffee_b", "quantity": 1},
        ]
    }
    names, family_ids = resolver.isolated_confusion_reference_names(
        coffee_a, coffee_b, families, max_products=12
    )
    assert names == ["coffee_a", "coffee_b"]
    assert family_ids == ["coffee"]

    multi_a = {
        "products": [
            {"name": "coffee_a", "quantity": 1},
            {"name": "apple_a", "quantity": 1},
        ]
    }
    multi_b = {
        "products": [
            {"name": "coffee_b", "quantity": 1},
            {"name": "apple_b", "quantity": 1},
        ]
    }
    assert resolver.isolated_confusion_reference_names(
        multi_a, multi_b, families, max_products=12
    ) == ([], [])


def test_adjudicator_request_combines_catalog_sheets_targeted_crops_and_target():
    class FakeResponses:
        def __init__(self):
            self.request = None

        def create(self, **request):
            self.request = request
            return SimpleNamespace(output_text='{"instances": []}')

    endpoint = FakeResponses()
    resolver.call_adjudicator(
        SimpleNamespace(responses=endpoint),
        model="gpt-5.6-sol",
        prompt="prompt",
        candidate_references=[("product_a", "data:image/jpeg;base64,crop")],
        reference_sheet_urls=["data:image/jpeg;base64,sheet"],
        target_image_url="data:image/jpeg;base64,target",
        output_schema={"type": "object"},
        detail="high",
        reference_detail="high",
        reasoning_effort="low",
        timeout_seconds=30,
    )

    content = endpoint.request["input"][0]["content"]
    labels = [item["text"] for item in content if item["type"] == "input_text"]
    images = [item["image_url"] for item in content if item["type"] == "input_image"]
    assert any("FULL CATALOG REFERENCE SHEET" in label for label in labels)
    assert any("REFERENCE CROP for product_a" in label for label in labels)
    assert images == [
        "data:image/jpeg;base64,sheet",
        "data:image/jpeg;base64,crop",
        "data:image/jpeg;base64,target",
    ]


def test_real_run_dry_run_applies_count_guard_before_identity_adjudication(tmp_path):
    evaluation = Path(__file__).parents[1]
    args = resolver.parse_args(
        [
            "--dataset",
            str(evaluation / "datasets" / "inventory-v0"),
            "--split",
            "development",
            "--limit",
            "20",
            "--candidate-a",
            str(evaluation / "runs" / "luna-references-v0" / "predictions"),
            "--candidate-b",
            str(evaluation / "runs" / "terra-references-v0" / "predictions"),
            "--base-predictions",
            str(
                evaluation
                / "runs"
                / "luna-terra-authoritative-evidence-v0"
                / "predictions"
            ),
            "--references",
            str(evaluation / "references" / "inventory-v0" / "reference-manifest.json"),
            "--output",
            str(tmp_path / "unused"),
            "--dry-run",
        ]
    )

    result = resolver.run(args)

    assert len(result["agreement_sample_ids"]) == 12
    assert result["count_guard_sample_ids"] == [
        "inv_0004",
        "inv_0007",
        "inv_0020",
    ]
    assert result["identity_disagreement_sample_ids"] == [
        "inv_0002",
        "inv_0012",
        "inv_0016",
        "inv_0022",
        "inv_0023",
    ]
    assert not (tmp_path / "unused").exists()


def test_resolver_copies_agreement_and_calls_model_once_for_disagreement(tmp_path):
    evaluation = Path(__file__).parents[1]
    dataset = evaluation / "datasets" / "inventory-v0"
    catalog = json.loads((dataset / "catalog.json").read_text(encoding="utf-8"))
    name_a = catalog[0]["name"]
    name_b = catalog[1]["name"]
    rows = run_baseline.selected_rows(
        run_baseline.read_jsonl(dataset / "manifest.jsonl"), "development", 2
    )
    first_id, second_id = (row["sample_id"] for row in rows)

    candidate_a_dir = tmp_path / "candidate-a"
    candidate_b_dir = tmp_path / "candidate-b"
    base_dir = tmp_path / "base"
    agreement = {"products": [{"name": name_a, "quantity": 1}]}
    a_disagreement = {"products": [{"name": name_a, "quantity": 2}]}
    b_disagreement = {"products": [{"name": name_b, "quantity": 2}]}
    write_predictions(
        candidate_a_dir, {first_id: agreement, second_id: a_disagreement}
    )
    write_predictions(
        candidate_b_dir, {first_id: agreement, second_id: b_disagreement}
    )
    write_predictions(base_dir, {first_id: agreement, second_id: b_disagreement})

    class FakeResponse:
        id = "resp_adjudication"
        usage = {"input_tokens": 100, "output_tokens": 20}
        output_text = json.dumps(
            {
                "instances": [
                    {
                        "name": name_a,
                        "bbox": {"x": 100, "y": 100, "width": 200, "height": 300},
                        "confidence": 0.9,
                    },
                    {
                        "name": name_a,
                        "bbox": {"x": 500, "y": 100, "width": 200, "height": 300},
                        "confidence": 0.8,
                    },
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
    output = tmp_path / "resolved"
    args = resolver.parse_args(
        [
            "--dataset",
            str(dataset),
            "--split",
            "development",
            "--limit",
            "2",
            "--candidate-a",
            str(candidate_a_dir),
            "--candidate-b",
            str(candidate_b_dir),
            "--base-predictions",
            str(base_dir),
            "--references",
            str(evaluation / "references" / "inventory-v0" / "reference-manifest.json"),
            "--output",
            str(output),
        ]
    )

    result = resolver.run(args, client=SimpleNamespace(responses=endpoint))
    copied = json.loads(
        (output / "predictions" / f"{first_id}.json").read_text(encoding="utf-8")
    )
    adjudicated = json.loads(
        (output / "predictions" / f"{second_id}.json").read_text(encoding="utf-8")
    )

    assert result["agreements_copied_this_run"] == 1
    assert result["disagreements_adjudicated_this_run"] == 1
    assert result["estimated_cost_usd_this_run"] == 0.0008
    assert len(endpoint.requests) == 1
    request = endpoint.requests[0]
    assert request["model"] == "gpt-5.6-sol"
    assert request["reasoning"] == {"effort": "low"}
    schema_names = request["text"]["format"]["schema"]["properties"]["instances"][
        "items"
    ]["properties"]["name"]["enum"]
    assert schema_names == sorted([name_a, name_b])
    image_items = [
        item
        for item in request["input"][0]["content"]
        if item["type"] == "input_image"
    ]
    assert len(image_items) == 5  # two crops per candidate plus the target
    assert copied["model_bundle_version"] == resolver.MODEL_BUNDLE_VERSION
    assert copied["provider_metadata"]["resolver_decision"] == "agreement"
    assert copied["provider_metadata"]["resolver_model"] is None
    assert adjudicated["data"] == {
        "products": [{"name": name_a, "quantity": 2}]
    }
    assert len(adjudicated["evidence"]) == 2
    assert (
        adjudicated["provider_metadata"]["resolver_decision"]
        == "sol_identity_adjudication"
    )
    assert adjudicated["provider_metadata"]["component_models"] == {
        "candidate_a": "gpt-5.6-luna",
        "candidate_b": "gpt-5.6-terra",
        "evidence": "gpt-5.6-sol",
        "adjudicator": "gpt-5.6-sol",
    }


def test_full_catalog_v2_can_select_name_outside_candidate_union(tmp_path):
    evaluation = Path(__file__).parents[1]
    dataset = evaluation / "datasets" / "inventory-v0"
    catalog = json.loads((dataset / "catalog.json").read_text(encoding="utf-8"))
    name_a, name_b, recovered_name = (catalog[index]["name"] for index in range(3))
    row = run_baseline.selected_rows(
        run_baseline.read_jsonl(dataset / "manifest.jsonl"), "development", 1
    )[0]
    sample_id = row["sample_id"]

    candidate_a_dir = tmp_path / "candidate-a"
    candidate_b_dir = tmp_path / "candidate-b"
    base_dir = tmp_path / "base"
    write_predictions(
        candidate_a_dir,
        {sample_id: {"products": [{"name": name_a, "quantity": 1}]}},
    )
    write_predictions(
        candidate_b_dir,
        {sample_id: {"products": [{"name": name_b, "quantity": 1}]}},
    )
    write_predictions(
        base_dir,
        {sample_id: {"products": [{"name": name_b, "quantity": 1}]}},
    )

    class FakeResponse:
        id = "resp_full_catalog"
        usage = {"input_tokens": 100, "output_tokens": 20}
        output_text = json.dumps(
            {
                "instances": [
                    {
                        "name": recovered_name,
                        "bbox": {"x": 100, "y": 100, "width": 200, "height": 300},
                        "confidence": 0.9,
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
    output = tmp_path / "resolved-v2"
    args = resolver.parse_args(
        [
            "--dataset", str(dataset),
            "--split", "development",
            "--limit", "1",
            "--candidate-a", str(candidate_a_dir),
            "--candidate-b", str(candidate_b_dir),
            "--base-predictions", str(base_dir),
            "--references",
            str(evaluation / "references" / "inventory-v0" / "reference-manifest.json"),
            "--output", str(output),
            "--full-catalog-adjudication",
        ]
    )

    resolver.run(args, client=SimpleNamespace(responses=endpoint))
    prediction = json.loads(
        (output / "predictions" / f"{sample_id}.json").read_text(encoding="utf-8")
    )
    request = endpoint.requests[0]
    schema_names = request["text"]["format"]["schema"]["properties"]["instances"][
        "items"
    ]["properties"]["name"]["enum"]
    image_items = [
        item for item in request["input"][0]["content"] if item["type"] == "input_image"
    ]

    assert schema_names == [item["name"] for item in catalog]
    assert len(image_items) == 5  # four full reference sheets plus the target
    assert prediction["data"] == {
        "products": [{"name": recovered_name, "quantity": 1}]
    }
    assert prediction["model_bundle_version"] == resolver.MODEL_BUNDLE_VERSION_V2
    assert prediction["provider_metadata"]["adjudication_scope"] == "full_catalog"
    assert prediction["provider_metadata"]["candidate_names"] == sorted([name_a, name_b])
    assert prediction["provider_metadata"]["reference_sheet_count"] == 4
