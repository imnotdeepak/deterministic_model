from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

import run_baseline as baseline
from run_disagreement_resolver import canonical_products


COMPOSER_VERSION = "0.2.0"
POLICY_VERSION = "terra-count-sol-identity-v1"
MODEL_BUNDLE_VERSION = "terra-luna-sol-routed-v1"
POLICY_VERSION_V2 = "terra-count-sol-full-catalog-v2"
MODEL_BUNDLE_VERSION_V2 = "terra-luna-sol-full-catalog-routed-v2"


def identity_signature(data: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(item["name"] for item in data["products"]))


def routing_decision(
    candidate_a: dict[str, Any], candidate_b: dict[str, Any]
) -> str:
    if canonical_products(candidate_a["data"]) == canonical_products(
        candidate_b["data"]
    ):
        return "agreement"
    if identity_signature(candidate_a["data"]) == identity_signature(
        candidate_b["data"]
    ):
        return "terra_count_guard"
    return "sol_identity_adjudication"


def compose_prediction(
    source: dict[str, Any],
    *,
    decision: str,
    candidate_a_path: Path,
    candidate_b_path: Path,
    base_path: Path,
    adjudicated_path: Path,
    policy_version: str = POLICY_VERSION,
    model_bundle_version: str = MODEL_BUNDLE_VERSION,
    identity_adjudication_scope: str = "candidate_union",
) -> dict[str, Any]:
    prediction = json.loads(json.dumps(source))
    prediction["model_bundle_version"] = model_bundle_version
    metadata = prediction.setdefault("provider_metadata", {})
    metadata.update(
        {
            "routing_policy_version": policy_version,
            "routing_decision": decision,
            "identity_adjudication_scope": identity_adjudication_scope,
            "candidate_a_prediction_path": str(candidate_a_path),
            "candidate_b_prediction_path": str(candidate_b_path),
            "base_prediction_path": str(base_path),
            "adjudicated_prediction_path": str(adjudicated_path),
            "component_models": {
                "candidate_a": "gpt-5.6-luna",
                "candidate_b": "gpt-5.6-terra",
                "base_evidence": "gpt-5.6-luna",
                "identity_adjudicator": "gpt-5.6-sol",
            },
        }
    )
    return prediction


def run(args: argparse.Namespace) -> dict[str, Any]:
    full_catalog = args.full_catalog_adjudication
    policy_version = POLICY_VERSION_V2 if full_catalog else POLICY_VERSION
    model_bundle_version = (
        MODEL_BUNDLE_VERSION_V2 if full_catalog else MODEL_BUNDLE_VERSION
    )
    identity_adjudication_scope = "full_catalog" if full_catalog else "candidate_union"
    dataset = args.dataset.resolve()
    manifest = baseline.read_jsonl(dataset / "manifest.jsonl")
    schema = baseline.read_json(dataset / "schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    catalog = baseline.read_json(dataset / "catalog.json")
    allowed_names = set(baseline.catalog_names(catalog))
    rows = baseline.selected_rows(manifest, args.split, args.limit, args.offset)

    candidate_a = baseline.load_identity_predictions(args.candidate_a)
    candidate_b = baseline.load_identity_predictions(args.candidate_b)
    base_predictions = baseline.load_identity_predictions(args.base_predictions)
    adjudicated_predictions = baseline.load_identity_predictions(
        args.adjudicated_predictions
    )
    collections = {
        "candidate A": candidate_a,
        "candidate B": candidate_b,
        "base": base_predictions,
        "adjudicated": adjudicated_predictions,
    }
    for row in rows:
        sample_id = row["sample_id"]
        for label, collection in collections.items():
            if sample_id not in collection:
                raise ValueError(f"Missing {label} prediction for {sample_id}")
        for collection in collections.values():
            baseline.validate_model_data(
                collection[sample_id][0]["data"], validator, allowed_names
            )
        if canonical_products(base_predictions[sample_id][0]["data"]) != canonical_products(
            candidate_b[sample_id][0]["data"]
        ):
            raise ValueError(
                f"Base prediction for {sample_id} does not preserve candidate B data"
            )

    decisions = {
        row["sample_id"]: routing_decision(
            candidate_a[row["sample_id"]][0], candidate_b[row["sample_id"]][0]
        )
        for row in rows
    }
    counts = {
        decision: sum(value == decision for value in decisions.values())
        for decision in (
            "agreement",
            "terra_count_guard",
            "sol_identity_adjudication",
        )
    }
    config = {
        "composer_version": COMPOSER_VERSION,
        "routing_policy_version": policy_version,
        "model_bundle_version": model_bundle_version,
        "identity_adjudication_scope": identity_adjudication_scope,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "split": args.split,
        "requested_limit": args.limit,
        "requested_offset": args.offset,
        "selected_samples": len(rows),
        "candidate_a": str(args.candidate_a.resolve()),
        "candidate_b": str(args.candidate_b.resolve()),
        "base_predictions": str(args.base_predictions.resolve()),
        "adjudicated_predictions": str(args.adjudicated_predictions.resolve()),
        "decision_counts": counts,
        "api_calls": 0,
    }
    if args.dry_run:
        return {"dry_run": True, "config": config, "decisions": decisions}

    output = args.output.resolve()
    predictions_dir = output / "predictions"
    decisions_dir = output / "routing-decisions"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    decisions_dir.mkdir(parents=True, exist_ok=True)
    baseline.atomic_write_json(output / "run-config.json", config)

    written = 0
    resumed = 0
    for row in rows:
        sample_id = row["sample_id"]
        output_path = predictions_dir / f"{sample_id}.json"
        if not args.overwrite and baseline.prediction_is_complete(output_path, sample_id):
            resumed += 1
            continue
        decision = decisions[sample_id]
        a_prediction, a_path = candidate_a[sample_id]
        b_prediction, b_path = candidate_b[sample_id]
        base_prediction, base_path = base_predictions[sample_id]
        adjudicated_prediction, adjudicated_path = adjudicated_predictions[sample_id]
        source = (
            adjudicated_prediction
            if decision == "sol_identity_adjudication"
            else base_prediction
        )
        prediction = compose_prediction(
            source,
            decision=decision,
            candidate_a_path=a_path,
            candidate_b_path=b_path,
            base_path=base_path,
            adjudicated_path=adjudicated_path,
            policy_version=policy_version,
            model_bundle_version=model_bundle_version,
            identity_adjudication_scope=identity_adjudication_scope,
        )
        baseline.atomic_write_json(output_path, prediction)
        baseline.atomic_write_json(
            decisions_dir / f"{sample_id}.json",
            {
                "sample_id": sample_id,
                "routing_policy_version": policy_version,
                "decision": decision,
                "candidate_a_data": a_prediction["data"],
                "candidate_b_data": b_prediction["data"],
                "selected_source": (
                    str(adjudicated_path)
                    if decision == "sol_identity_adjudication"
                    else str(base_path)
                ),
                "selected_data": prediction["data"],
            },
        )
        written += 1

    summary = {
        "selected": len(rows),
        "written_this_run": written,
        "resumed": resumed,
        "decision_counts": counts,
        "api_calls": 0,
        "estimated_cost_usd_this_run": 0.0,
        "predictions": str(predictions_dir),
    }
    baseline.atomic_write_json(output / "run-summary.json", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compose a frozen Terra-count/Sol-identity routing policy."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", default="development")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--candidate-a", required=True, type=Path)
    parser.add_argument("--candidate-b", required=True, type=Path)
    parser.add_argument("--base-predictions", required=True, type=Path)
    parser.add_argument("--adjudicated-predictions", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--full-catalog-adjudication",
        action="store_true",
        help="Record the v2 policy that uses full-catalog Sol identity adjudication.",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Routing composition failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
