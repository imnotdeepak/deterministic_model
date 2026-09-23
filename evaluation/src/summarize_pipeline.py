from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluate import percentile


SUMMARY_VERSION = "0.1.0"


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def prediction(path: Path, sample_id: str) -> dict[str, Any]:
    data = read_json(path / f"{sample_id}.json")
    if data.get("sample_id") != sample_id:
        raise ValueError(f"Prediction sample_id mismatch: {path / f'{sample_id}.json'}")
    for field in ("estimated_cost_usd", "latency_ms"):
        value = data.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise ValueError(f"Prediction has invalid {field}: {path / f'{sample_id}.json'}")
    return data


def run(args: argparse.Namespace) -> dict[str, Any]:
    report = read_json(args.report.resolve())
    samples = report.get("samples")
    if not isinstance(samples, list) or not samples:
        raise ValueError("Evaluation report must contain a non-empty samples array")

    decisions_dir = args.decisions.resolve()
    prediction_dirs = {
        "luna_aggregate": args.luna.resolve(),
        "terra_aggregate": args.terra.resolve(),
        "luna_evidence": args.evidence.resolve(),
        "sol_adjudication": args.sol.resolve(),
    }
    rows = []
    decision_counts: Counter[str] = Counter()
    for sample in samples:
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("Every report sample must have a sample_id")
        decision_data = read_json(decisions_dir / f"{sample_id}.json")
        decision = decision_data.get("decision")
        if decision not in {
            "agreement",
            "terra_count_guard",
            "sol_identity_adjudication",
        }:
            raise ValueError(f"Unknown routing decision for {sample_id}: {decision}")
        decision_counts[decision] += 1

        stage_predictions = {
            name: prediction(directory, sample_id)
            for name, directory in prediction_dirs.items()
            if name != "sol_adjudication" or decision == "sol_identity_adjudication"
        }
        luna_latency = float(stage_predictions["luna_aggregate"]["latency_ms"])
        terra_latency = float(stage_predictions["terra_aggregate"]["latency_ms"])
        evidence_latency = float(stage_predictions["luna_evidence"]["latency_ms"])
        sol_latency = float(
            stage_predictions.get("sol_adjudication", {}).get("latency_ms", 0)
        )
        rows.append(
            {
                "sample_id": sample_id,
                "decision": decision,
                "estimated_cost_usd": sum(
                    float(item["estimated_cost_usd"])
                    for item in stage_predictions.values()
                ),
                "serial_model_latency_ms": (
                    luna_latency + terra_latency + evidence_latency + sol_latency
                ),
                "parallel_candidate_model_latency_ms": (
                    max(luna_latency, terra_latency) + evidence_latency + sol_latency
                ),
            }
        )

    costs = [row["estimated_cost_usd"] for row in rows]
    serial_latencies = [row["serial_model_latency_ms"] for row in rows]
    parallel_latencies = [row["parallel_candidate_model_latency_ms"] for row in rows]
    result = {
        "summary_version": SUMMARY_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "report": str(args.report.resolve()),
        "samples": len(rows),
        "routing_decisions": dict(sorted(decision_counts.items())),
        "evaluation_metrics": report.get("summary"),
        "end_to_end_model_cost": {
            "total_usd": round(sum(costs), 8),
            "mean_per_image_usd": round(statistics.mean(costs), 8),
            "median_per_image_usd": round(statistics.median(costs), 8),
        },
        "model_call_latency": {
            "serial_p50_ms": percentile(serial_latencies, 0.50),
            "serial_p95_ms": percentile(serial_latencies, 0.95),
            "parallel_candidates_p50_ms": percentile(parallel_latencies, 0.50),
            "parallel_candidates_p95_ms": percentile(parallel_latencies, 0.95),
            "notes": (
                "Model-call latency only. Serial sums all stage latencies. Parallel-candidates "
                "uses max(Luna aggregate, Terra aggregate), then adds Luna evidence and Sol when routed."
            ),
        },
        "per_sample": rows,
    }
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize end-to-end cost and model-call latency for a routed pipeline."
    )
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--luna", required=True, type=Path)
    parser.add_argument("--terra", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--sol", required=True, type=Path)
    parser.add_argument("--decisions", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Pipeline summary failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
