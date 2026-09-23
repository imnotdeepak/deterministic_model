from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ANALYZER_VERSION = "0.1.0"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def product_counts(document: dict[str, Any] | None) -> dict[str, int]:
    if document is None:
        return {}
    data = document.get("data", document)
    products = data.get("products", []) if isinstance(data, dict) else []
    counts: dict[str, int] = {}
    for product in products:
        if not isinstance(product, dict):
            continue
        name = product.get("name")
        quantity = product.get("quantity")
        if isinstance(name, str) and isinstance(quantity, int) and not isinstance(quantity, bool):
            counts[name] = quantity
    return counts


def percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def load_optional_prediction(directory: Path | None, sample_id: str) -> dict[str, Any] | None:
    if directory is None:
        return None
    path = directory / f"{sample_id}.json"
    return load_json(path) if path.exists() else None


def parse_stage(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("stage must use NAME=PATH")
    name, raw_path = value.split("=", 1)
    if not name.strip() or not raw_path.strip():
        raise argparse.ArgumentTypeError("stage must use non-empty NAME=PATH")
    return name.strip(), Path(raw_path.strip())


def stage_summary(stage_dir: Path, sample_ids: list[str]) -> dict[str, Any]:
    latencies: list[float] = []
    costs: list[float] = []
    present = 0
    for sample_id in sample_ids:
        prediction = load_optional_prediction(stage_dir, sample_id)
        if prediction is None:
            continue
        present += 1
        latency = prediction.get("latency_ms")
        cost = prediction.get("estimated_cost_usd")
        if isinstance(latency, (int, float)) and not isinstance(latency, bool):
            latencies.append(float(latency))
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            costs.append(float(cost))
    return {
        "expected_samples": len(sample_ids),
        "present_predictions": present,
        "total_cost_usd": round(sum(costs), 8),
        "mean_cost_usd": round(statistics.mean(costs), 8) if costs else None,
        "median_cost_usd": round(statistics.median(costs), 8) if costs else None,
        "latency_p50_ms": round(percentile(latencies, 0.50), 3) if latencies else None,
        "latency_p95_ms": round(percentile(latencies, 0.95), 3) if latencies else None,
    }


def analyze(
    dataset: Path,
    predictions: Path,
    split: str,
    *,
    report_path: Path | None = None,
    routing_decisions: Path | None = None,
    candidate_a: Path | None = None,
    candidate_b: Path | None = None,
    stages: list[tuple[str, Path]] | None = None,
    allow_frozen_test: bool = False,
) -> dict[str, Any]:
    if split == "test" and not allow_frozen_test:
        raise ValueError(
            "Refusing to analyze the frozen test split for tuning; "
            "pass --allow-frozen-test only for an explicitly authorized audit"
        )

    manifest = load_jsonl(dataset / "manifest.jsonl")
    rows = sorted(
        (row for row in manifest if row.get("split") == split),
        key=lambda row: row["sample_id"],
    )
    if not rows:
        raise ValueError(f"No manifest samples found for split {split!r}")

    report_samples: dict[str, dict[str, Any]] = {}
    if report_path is not None:
        report = load_json(report_path)
        if report.get("split") != split:
            raise ValueError(
                f"Report split {report.get('split')!r} does not match requested split {split!r}"
            )
        report_samples = {
            item["sample_id"]: item
            for item in report.get("samples", [])
            if isinstance(item, dict) and isinstance(item.get("sample_id"), str)
        }

    failures: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    product_exact_samples = 0
    fully_correct_samples = 0

    for row in rows:
        sample_id = row["sample_id"]
        annotation = load_json(dataset / row["annotation"])
        prediction = load_optional_prediction(predictions, sample_id)
        expected = product_counts(annotation)
        predicted = product_counts(prediction)
        report_sample = report_samples.get(sample_id, {})

        categories: list[str] = []
        if prediction is None:
            categories.append("missing_prediction")
        if prediction is not None and report_sample.get("prediction_contract_valid") is False:
            categories.append("contract")
        expected_names = set(expected)
        predicted_names = set(predicted)
        if expected_names != predicted_names:
            categories.append("identity")
        if any(expected[name] != predicted[name] for name in expected_names & predicted_names):
            categories.append("quantity")
        localization = report_sample.get("evidence_localization_accuracy")
        if isinstance(localization, (int, float)) and localization < 1.0:
            categories.append("localization")

        routing: dict[str, Any] | None = None
        decision_doc = load_optional_prediction(routing_decisions, sample_id)
        if decision_doc is not None:
            decision = decision_doc.get("decision", "unknown")
            decision_counts[str(decision)] += 1
            a_counts = product_counts(load_optional_prediction(candidate_a, sample_id))
            b_counts = product_counts(load_optional_prediction(candidate_b, sample_id))
            final_exact = predicted == expected and prediction is not None
            a_exact = a_counts == expected if candidate_a is not None else None
            b_exact = b_counts == expected if candidate_b is not None else None
            correct_candidate_discarded = not final_exact and (a_exact is True or b_exact is True)
            if correct_candidate_discarded:
                categories.append("routing")
            routing = {
                "decision": decision,
                "candidate_a_exact": a_exact,
                "candidate_b_exact": b_exact,
                "correct_candidate_discarded": correct_candidate_discarded,
            }

        product_exact = (
            prediction is not None
            and predicted == expected
            and report_sample.get("prediction_contract_valid", True) is not False
        )
        if product_exact:
            product_exact_samples += 1
        if not categories:
            fully_correct_samples += 1
            continue

        categories = list(dict.fromkeys(categories))
        category_counts.update(categories)
        deltas = [
            {
                "product": name,
                "expected": expected.get(name, 0),
                "predicted": predicted.get(name, 0),
                "delta": predicted.get(name, 0) - expected.get(name, 0),
            }
            for name in sorted(set(expected) | set(predicted))
            if expected.get(name, 0) != predicted.get(name, 0)
        ]
        failure: dict[str, Any] = {
            "sample_id": sample_id,
            "categories": categories,
            "expected": expected,
            "predicted": predicted,
            "count_deltas": deltas,
            "tags": row.get("tags", []),
            "prediction_validation_errors": report_sample.get(
                "prediction_validation_errors", []
            ),
            "evidence_localization_accuracy": localization,
        }
        if routing is not None:
            failure["routing"] = routing
        failures.append(failure)

    sample_ids = [row["sample_id"] for row in rows]
    stage_results = {
        name: stage_summary(stage_dir, sample_ids) for name, stage_dir in (stages or [])
    }

    return {
        "analyzer_version": ANALYZER_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset.resolve()),
        "split": split,
        "tuning_safety": {
            "frozen_test_override_used": split == "test" and allow_frozen_test,
            "warning": (
                "Frozen test data was explicitly opened; do not use this analysis for tuning."
                if split == "test"
                else None
            ),
        },
        "summary": {
            "samples": len(rows),
            "product_exact_samples": product_exact_samples,
            "fully_correct_samples": fully_correct_samples,
            "failed_samples": len(failures),
            "whole_image_exact_accuracy": product_exact_samples / len(rows),
            "all_dimensions_correct_rate": fully_correct_samples / len(rows),
            "category_counts": dict(sorted(category_counts.items())),
            "routing_decision_counts": dict(sorted(decision_counts.items())),
        },
        "stage_metrics": stage_results,
        "failures": failures,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify inventory pipeline failures without model API calls."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--split", required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--routing-decisions", type=Path)
    parser.add_argument("--candidate-a", type=Path)
    parser.add_argument("--candidate-b", type=Path)
    parser.add_argument("--stage", action="append", type=parse_stage, default=[])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-frozen-test", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = analyze(
        args.dataset,
        args.predictions,
        args.split,
        report_path=args.report,
        routing_decisions=args.routing_decisions,
        candidate_a=args.candidate_a,
        candidate_b=args.candidate_b,
        stages=args.stage,
        allow_frozen_test=args.allow_frozen_test,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    summary = result["summary"]
    print(
        f"{args.split}: {summary['product_exact_samples']}/{summary['samples']} "
        f"product-exact; {summary['failed_samples']} samples with a failure dimension"
    )
    print(f"Categories: {json.dumps(summary['category_counts'], sort_keys=True)}")
    print(f"Report: {args.output}")


if __name__ == "__main__":
    main()
