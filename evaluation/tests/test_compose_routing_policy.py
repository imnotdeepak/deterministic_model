import importlib.util
import sys
from pathlib import Path


SRC = Path(__file__).parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

spec = importlib.util.spec_from_file_location(
    "compose_routing_policy", SRC / "compose_routing_policy.py"
)
composer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = composer
spec.loader.exec_module(composer)


def prediction(*products):
    return {"data": {"products": list(products)}}


def test_routing_policy_distinguishes_agreement_count_and_identity():
    same = prediction({"name": "one", "quantity": 1})
    count_a = prediction({"name": "one", "quantity": 1})
    count_b = prediction({"name": "one", "quantity": 2})
    identity_b = prediction({"name": "two", "quantity": 1})

    assert composer.routing_decision(same, same) == "agreement"
    assert composer.routing_decision(count_a, count_b) == "terra_count_guard"
    assert (
        composer.routing_decision(count_a, identity_b)
        == "sol_identity_adjudication"
    )


def test_real_inputs_route_to_expected_policy_counts(tmp_path):
    evaluation = Path(__file__).parents[1]
    args = composer.parse_args(
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
            "--adjudicated-predictions",
            str(evaluation / "runs" / "sol-disagreement-v0" / "predictions"),
            "--output",
            str(tmp_path / "unused"),
            "--dry-run",
        ]
    )

    result = composer.run(args)

    assert result["config"]["decision_counts"] == {
        "agreement": 12,
        "terra_count_guard": 3,
        "sol_identity_adjudication": 5,
    }
    assert result["config"]["api_calls"] == 0
    assert not (tmp_path / "unused").exists()


def test_full_catalog_mode_versions_the_policy_separately(tmp_path):
    evaluation = Path(__file__).parents[1]
    args = composer.parse_args(
        [
            "--dataset", str(evaluation / "datasets" / "inventory-v0"),
            "--split", "development",
            "--limit", "20",
            "--candidate-a",
            str(evaluation / "runs" / "luna-references-v0" / "predictions"),
            "--candidate-b",
            str(evaluation / "runs" / "terra-references-v0" / "predictions"),
            "--base-predictions",
            str(evaluation / "runs" / "luna-terra-authoritative-evidence-v0" / "predictions"),
            "--adjudicated-predictions",
            str(evaluation / "runs" / "sol-disagreement-v0" / "predictions"),
            "--output", str(tmp_path / "unused-v2"),
            "--full-catalog-adjudication",
            "--dry-run",
        ]
    )

    result = composer.run(args)

    assert result["config"]["routing_policy_version"] == composer.POLICY_VERSION_V2
    assert result["config"]["model_bundle_version"] == composer.MODEL_BUNDLE_VERSION_V2
    assert result["config"]["identity_adjudication_scope"] == "full_catalog"
