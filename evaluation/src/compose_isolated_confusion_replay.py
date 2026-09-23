from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import run_baseline as baseline
from run_disagreement_resolver import (
    isolated_confusion_reference_names,
    load_confusion_sets,
    resolver_decision,
)


REPLAY_VERSION = "0.1.0"
MODEL_BUNDLE_VERSION = "terra-luna-sol-isolated-confusion-replay-v4"


def run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    manifest = baseline.read_jsonl(dataset / "manifest.jsonl")
    catalog = baseline.read_json(dataset / "catalog.json")
    allowed_names = set(baseline.catalog_names(catalog))
    confusion_version, families = load_confusion_sets(
        args.confusion_sets, allowed_names
    )
    rows = baseline.selected_rows(manifest, args.split, args.limit, args.offset)
    candidate_a = baseline.load_identity_predictions(args.candidate_a)
    candidate_b = baseline.load_identity_predictions(args.candidate_b)
    standard = baseline.load_identity_predictions(args.standard_adjudicated)
    targeted = baseline.load_identity_predictions(args.targeted_adjudicated)

    collections = {
        "candidate A": candidate_a,
        "candidate B": candidate_b,
        "standard adjudication": standard,
        "targeted adjudication": targeted,
    }
    for row in rows:
        sample_id = row["sample_id"]
        for label, collection in collections.items():
            if sample_id not in collection:
                raise ValueError(f"Missing {label} prediction for {sample_id}")

    output = args.output.resolve()
    predictions_dir = output / "predictions"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    selected_targeted: list[str] = []
    selected_standard: list[str] = []
    selections: dict[str, dict[str, Any]] = {}
    counterfactual_adjudicator_cost = 0.0

    for row in rows:
        sample_id = row["sample_id"]
        a_prediction, _ = candidate_a[sample_id]
        b_prediction, _ = candidate_b[sample_id]
        decision = resolver_decision(a_prediction, b_prediction)
        targeted_names: list[str] = []
        matched_families: list[str] = []
        if decision == "sol_identity_adjudication":
            targeted_names, matched_families = isolated_confusion_reference_names(
                a_prediction["data"],
                b_prediction["data"],
                families,
                args.max_targeted_reference_products,
            )
        use_targeted = decision == "sol_identity_adjudication" and bool(
            matched_families
        )
        selected, selected_path = (
            targeted[sample_id] if use_targeted else standard[sample_id]
        )
        prediction = json.loads(json.dumps(selected))
        prediction["model_bundle_version"] = MODEL_BUNDLE_VERSION
        metadata = prediction.setdefault("provider_metadata", {})
        metadata.update(
            {
                "replay_version": REPLAY_VERSION,
                "replay_policy": "isolated_identity_symmetric_difference",
                "replay_source": "targeted_v3" if use_targeted else "standard_v2",
                "replay_source_path": str(selected_path),
                "matched_confusion_families": matched_families,
                "targeted_reference_names": targeted_names,
            }
        )
        baseline.atomic_write_json(
            predictions_dir / f"{sample_id}.json", prediction
        )
        if decision == "sol_identity_adjudication":
            cost = prediction.get("estimated_cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                counterfactual_adjudicator_cost += float(cost)
            if use_targeted:
                selected_targeted.append(sample_id)
            else:
                selected_standard.append(sample_id)
        selections[sample_id] = {
            "decision": decision,
            "source": "targeted_v3" if use_targeted else "standard_v2",
            "matched_confusion_families": matched_families,
            "targeted_reference_names": targeted_names,
        }

    config = {
        "replay_version": REPLAY_VERSION,
        "model_bundle_version": MODEL_BUNDLE_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "split": args.split,
        "candidate_a": str(args.candidate_a.resolve()),
        "candidate_b": str(args.candidate_b.resolve()),
        "standard_adjudicated": str(args.standard_adjudicated.resolve()),
        "targeted_adjudicated": str(args.targeted_adjudicated.resolve()),
        "confusion_sets": str(args.confusion_sets.resolve()),
        "confusion_sets_version": confusion_version,
        "selection_policy": "isolated_identity_symmetric_difference",
        "api_calls": 0,
        "counterfactual_replay": True,
    }
    summary = {
        "selected": len(rows),
        "targeted_adjudication_sample_ids": selected_targeted,
        "standard_adjudication_sample_ids": selected_standard,
        "counterfactual_adjudicator_cost_usd": round(
            counterfactual_adjudicator_cost, 8
        ),
        "api_calls": 0,
        "predictions": str(predictions_dir),
    }
    baseline.atomic_write_json(output / "run-config.json", config)
    baseline.atomic_write_json(output / "run-summary.json", summary)
    baseline.atomic_write_json(output / "selections.json", selections)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay an isolated-confusion policy from saved v2 and v3 outputs."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", default="development")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--candidate-a", required=True, type=Path)
    parser.add_argument("--candidate-b", required=True, type=Path)
    parser.add_argument("--standard-adjudicated", required=True, type=Path)
    parser.add_argument("--targeted-adjudicated", required=True, type=Path)
    parser.add_argument("--confusion-sets", required=True, type=Path)
    parser.add_argument("--max-targeted-reference-products", type=int, default=12)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Isolated-confusion replay failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
