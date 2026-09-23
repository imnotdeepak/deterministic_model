from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import math
import platform
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from jsonschema import Draft202012Validator

EVALUATOR_VERSION = "0.4.0"
IOU_THRESHOLD = 0.50

CANONICAL_EVIDENCE_KEYS = {
    "id",
    "media_id",
    "source_type",
    "coordinate_space",
    "bbox",
    "rotation_degrees",
    "page_index",
    "frame_index",
    "timestamp_ms",
    "model_version",
    "media_available",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dependency_versions() -> dict[str, str]:
    result = {}
    for dist in ["jsonschema", "Pillow"]:
        try:
            result[dist] = importlib.metadata.version(dist)
        except importlib.metadata.PackageNotFoundError:
            result[dist] = "not-installed"
    return result


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    idx = (len(values) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return values[lo]
    return values[lo] + (values[hi] - values[lo]) * (idx - lo)


def select_manifest_rows(
    manifest: list[dict[str, Any]],
    split: str,
    limit: int | None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = sorted(
        (row for row in manifest if row.get("split") == split),
        key=lambda row: row["sample_id"],
    )
    if offset < 0:
        raise ValueError("--offset must be zero or greater")
    rows = rows[offset:]
    if limit is not None:
        if limit <= 0:
            raise ValueError("--limit must be greater than zero")
        rows = rows[:limit]
    if not rows:
        raise ValueError(f"No manifest samples found for split {split!r}")
    return rows


def bbox_valid(bbox: dict[str, Any], width: int, height: int) -> tuple[bool, str | None]:
    required = {"x", "y", "width", "height"}
    if not isinstance(bbox, dict) or not required.issubset(bbox):
        return False, "bbox must contain x, y, width, height"

    vals = {}
    for key in required:
        value = bbox[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False, f"bbox.{key} must be numeric"
        vals[key] = float(value)

    if vals["width"] <= 0 or vals["height"] <= 0:
        return False, "bbox width and height must be > 0"
    if vals["x"] < 0 or vals["y"] < 0:
        return False, "bbox x and y must be >= 0"
    if vals["x"] + vals["width"] > width:
        return False, "bbox extends beyond image width"
    if vals["y"] + vals["height"] > height:
        return False, "bbox extends beyond image height"
    return True, None


def bbox_iou(a: dict[str, Any], b: dict[str, Any]) -> float:
    ax1, ay1 = float(a["x"]), float(a["y"])
    ax2, ay2 = ax1 + float(a["width"]), ay1 + float(a["height"])
    bx1, by1 = float(b["x"]), float(b["y"])
    bx2, by2 = bx1 + float(b["width"]), by1 + float(b["height"])

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    intersection = iw * ih
    if intersection <= 0:
        return 0.0

    a_area = (ax2 - ax1) * (ay2 - ay1)
    b_area = (bx2 - bx1) * (by2 - by1)
    union = a_area + b_area - intersection
    return intersection / union if union > 0 else 0.0


def greedy_iou_matches(
    gt_boxes: list[dict[str, Any]],
    pred_boxes: list[dict[str, Any]],
    threshold: float,
) -> tuple[int, list[float]]:
    candidates = []
    for gi, gt in enumerate(gt_boxes):
        for pi, pred in enumerate(pred_boxes):
            score = bbox_iou(gt, pred)
            if score >= threshold:
                candidates.append((score, gi, pi))

    candidates.sort(reverse=True)
    used_gt: set[int] = set()
    used_pred: set[int] = set()
    matched_ious: list[float] = []

    for score, gi, pi in candidates:
        if gi in used_gt or pi in used_pred:
            continue
        used_gt.add(gi)
        used_pred.add(pi)
        matched_ious.append(score)

    return len(matched_ious), matched_ious


def validate_manifest(manifest: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids = [row.get("sample_id") for row in manifest]
    duplicates = [k for k, v in Counter(ids).items() if k is not None and v > 1]
    if duplicates:
        errors.append(f"duplicate manifest sample_id values: {duplicates}")

    for idx, row in enumerate(manifest):
        for key in ["sample_id", "image", "annotation", "split"]:
            if not row.get(key):
                errors.append(f"manifest row {idx + 1} missing required field {key!r}")
    return errors


def catalog_names(catalog: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    ids: set[str] = set()
    for idx, item in enumerate(catalog):
        if not item.get("id") or not item.get("name"):
            raise ValueError(f"catalog item {idx} must contain id and name")
        if item["id"] in ids:
            raise ValueError(f"duplicate catalog id: {item['id']}")
        if item["name"] in names:
            raise ValueError(f"duplicate catalog name: {item['name']}")
        ids.add(item["id"])
        names.add(item["name"])
    return names


def product_array_issues(data: dict[str, Any], allowed_names: set[str]) -> list[str]:
    issues: list[str] = []
    products = data.get("products", [])
    if not isinstance(products, list):
        return ["products must be an array"]

    names: list[str] = []
    for idx, item in enumerate(products):
        if not isinstance(item, dict):
            issues.append(f"products[{idx}] must be an object")
            continue
        name = item.get("name")
        if isinstance(name, str):
            names.append(name)
            if name not in allowed_names:
                issues.append(f"products[{idx}].name is not in frozen catalog: {name!r}")

    duplicate_names = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicate_names:
        issues.append(
            "duplicate product names are not allowed: "
            + ", ".join(repr(x) for x in duplicate_names)
        )
    return issues


def validate_annotation(
    dataset_dir: Path,
    row: dict[str, Any],
    annotation: dict[str, Any],
    validator: Draft202012Validator,
    allowed_names: set[str],
) -> list[str]:
    errors: list[str] = []

    sample_id = row["sample_id"]
    if annotation.get("sample_id") != sample_id:
        errors.append(
            f"annotation sample_id {annotation.get('sample_id')!r} "
            f"does not match manifest {sample_id!r}"
        )

    if annotation.get("image") != row["image"]:
        errors.append(
            f"annotation image {annotation.get('image')!r} "
            f"does not match manifest {row['image']!r}"
        )

    image_path = dataset_dir / row["image"]
    if not image_path.exists():
        errors.append(f"image does not exist: {image_path}")
        return errors

    try:
        with Image.open(image_path) as img:
            actual_width, actual_height = img.size
    except Exception as exc:
        errors.append(f"image could not be opened: {exc}")
        return errors

    if annotation.get("width") != actual_width or annotation.get("height") != actual_height:
        errors.append(
            f"declared dimensions {annotation.get('width')}x{annotation.get('height')} "
            f"do not match actual {actual_width}x{actual_height}"
        )

    data = annotation.get("data", {})
    schema_errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
    errors.extend(f"annotation data schema error: {e.message}" for e in schema_errors)
    errors.extend(f"annotation data contract error: {e}" for e in product_array_issues(data, allowed_names))

    instances = annotation.get("instances")
    if not isinstance(instances, list):
        errors.append("instances must be an array")
        return errors

    instance_counts: Counter[str] = Counter()
    for idx, instance in enumerate(instances):
        if not isinstance(instance, dict):
            errors.append(f"instances[{idx}] must be an object")
            continue
        product_name = instance.get("product_name")
        if product_name not in allowed_names:
            errors.append(
                f"instances[{idx}].product_name not in frozen catalog: {product_name!r}"
            )
        else:
            instance_counts[product_name] += 1

        valid, reason = bbox_valid(
            instance.get("bbox", {}),
            actual_width,
            actual_height,
        )
        if not valid:
            errors.append(f"instances[{idx}] invalid bbox: {reason}")

    declared_counts = {
        item["name"]: int(item["quantity"])
        for item in data.get("products", [])
        if isinstance(item, dict)
        and item.get("name") in allowed_names
        and isinstance(item.get("quantity"), int)
        and not isinstance(item.get("quantity"), bool)
    }

    all_names = set(declared_counts) | set(instance_counts)
    for name in sorted(all_names):
        if declared_counts.get(name, 0) != instance_counts.get(name, 0):
            errors.append(
                f"quantity/instance mismatch for {name!r}: "
                f"data={declared_counts.get(name, 0)}, "
                f"instances={instance_counts.get(name, 0)}"
            )

    return errors


def validate_evidence(
    pred: dict[str, Any],
    width: int,
    height: int,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    evidence = pred.get("evidence", [])
    if not isinstance(evidence, list):
        return ["evidence must be an array"], {}

    evidence_by_id: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"evidence[{idx}] must be an object")
            continue

        missing = sorted(CANONICAL_EVIDENCE_KEYS - set(item))
        if missing:
            errors.append(f"evidence[{idx}] missing canonical keys: {missing}")

        evidence_id = item.get("id")
        if not isinstance(evidence_id, str) or not evidence_id:
            errors.append(f"evidence[{idx}].id must be a non-empty string")
            continue
        if evidence_id in evidence_by_id:
            errors.append(f"duplicate evidence id: {evidence_id}")
            continue

        if item.get("source_type") != "image_region":
            errors.append(
                f"evidence[{idx}].source_type must be 'image_region' for inventory-v0"
            )
        if item.get("coordinate_space") != "original_pixels":
            errors.append(
                f"evidence[{idx}].coordinate_space must be 'original_pixels'"
            )

        valid, reason = bbox_valid(item.get("bbox", {}), width, height)
        if not valid:
            errors.append(f"evidence[{idx}] invalid bbox: {reason}")
            # Keep the contract violation in prediction_validation_errors,
            # but exclude malformed evidence from all downstream localization
            # scoring so IoU never receives an invalid bbox.
            continue

        evidence_by_id[evidence_id] = item

    return errors, evidence_by_id


def safe_product_sets(data: dict[str, Any]) -> tuple[set[str], dict[str, int] | None]:
    products = data.get("products", [])
    names: list[str] = []
    counts: dict[str, int] = {}
    duplicate = False

    if not isinstance(products, list):
        return set(), None

    for item in products:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        qty = item.get("quantity")
        if isinstance(name, str):
            names.append(name)
            if name in counts:
                duplicate = True
            elif isinstance(qty, int) and not isinstance(qty, bool):
                counts[name] = qty

    return set(names), None if duplicate else counts


def gather_product_evidence_refs(
    pred: dict[str, Any],
    product_index: int,
) -> list[str]:
    metadata = pred.get("field_metadata", {})
    refs: list[str] = []
    for field in ["name", "quantity"]:
        ptr = f"/products/{product_index}/{field}"
        field_meta = metadata.get(ptr, {})
        for ref in field_meta.get("evidence_refs", []) or []:
            if ref not in refs:
                refs.append(ref)
    return refs


def evidence_metrics(
    truth: dict[str, Any],
    pred: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    iou_threshold: float,
) -> dict[str, Any]:
    pred_products = pred.get("data", {}).get("products", [])
    metadata = pred.get("field_metadata", {})

    # Reference coverage: returned scalar fields with non-empty, resolvable refs.
    scalar_paths: list[str] = []
    if isinstance(pred_products, list):
        for i, item in enumerate(pred_products):
            if not isinstance(item, dict):
                continue
            if "name" in item and item["name"] is not None:
                scalar_paths.append(f"/products/{i}/name")
            if "quantity" in item and item["quantity"] is not None:
                scalar_paths.append(f"/products/{i}/quantity")

    covered = 0
    for ptr in scalar_paths:
        refs = metadata.get(ptr, {}).get("evidence_refs", []) or []
        if refs and all(ref in evidence_by_id for ref in refs):
            covered += 1

    reference_coverage = (
        covered / len(scalar_paths) if scalar_paths else 0.0
    )

    gt_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for instance in truth.get("instances", []):
        if isinstance(instance, dict) and isinstance(instance.get("product_name"), str):
            gt_by_product[instance["product_name"]].append(instance["bbox"])

    pred_boxes_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen_refs_by_product: dict[str, set[str]] = defaultdict(set)

    if isinstance(pred_products, list):
        for i, item in enumerate(pred_products):
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str):
                continue
            for ref in gather_product_evidence_refs(pred, i):
                if ref in seen_refs_by_product[name]:
                    continue
                seen_refs_by_product[name].add(ref)
                evidence = evidence_by_id.get(ref)
                if evidence and evidence.get("source_type") == "image_region":
                    pred_boxes_by_product[name].append(evidence["bbox"])

    matched = 0
    total_gt = 0
    total_pred_boxes = 0
    matched_ious: list[float] = []

    for name in sorted(set(gt_by_product) | set(pred_boxes_by_product)):
        gt_boxes = gt_by_product.get(name, [])
        pred_boxes = pred_boxes_by_product.get(name, [])
        m, ious = greedy_iou_matches(gt_boxes, pred_boxes, iou_threshold)
        matched += m
        total_gt += len(gt_boxes)
        total_pred_boxes += len(pred_boxes)
        matched_ious.extend(ious)

    loc_precision = matched / total_pred_boxes if total_pred_boxes else (1.0 if total_gt == 0 else 0.0)
    loc_recall = matched / total_gt if total_gt else (1.0 if total_pred_boxes == 0 else 0.0)
    loc_accuracy = (
        matched / max(total_gt, total_pred_boxes)
        if max(total_gt, total_pred_boxes) > 0
        else 1.0
    )

    return {
        "evidence_reference_coverage": reference_coverage,
        "evidence_localization_accuracy": loc_accuracy,
        "evidence_localization_precision": loc_precision,
        "evidence_localization_recall": loc_recall,
        "mean_matched_iou": statistics.mean(matched_ious) if matched_ious else None,
        "matched_instances": matched,
        "ground_truth_instances": total_gt,
        "predicted_evidence_boxes": total_pred_boxes,
    }


def evaluate_prediction(
    truth: dict[str, Any],
    pred: dict[str, Any] | None,
    validator: Draft202012Validator,
    allowed_names: set[str],
    image_width: int,
    image_height: int,
    iou_threshold: float,
) -> dict[str, Any]:
    truth_data = truth["data"]
    expected_names, truth_counts = safe_product_sets(truth_data)
    assert truth_counts is not None, "ground-truth duplicates must be blocked by dataset validation"

    if pred is None:
        expected_pair_count = len(expected_names)
        return {
            "prediction_present": False,
            "schema_valid": False,
            "prediction_contract_valid": False,
            "prediction_validation_errors": ["missing prediction"],
            "tp": 0,
            "fp": 0,
            "fn": len(expected_names),
            "exact_pair_accuracy_union": 0.0 if expected_pair_count else 1.0,
            "whole_image_exact": False if expected_pair_count else True,
            "total_absolute_count_error": sum(truth_counts.values()),
            "per_product_absolute_errors": [
                abs(qty) for qty in truth_counts.values()
            ],
            "evidence_reference_coverage": 0.0 if expected_pair_count else 1.0,
            "evidence_localization_accuracy": 0.0 if sum(truth_counts.values()) else 1.0,
            "evidence_localization_precision": 0.0 if sum(truth_counts.values()) else 1.0,
            "evidence_localization_recall": 0.0 if sum(truth_counts.values()) else 1.0,
            "mean_matched_iou": None,
            "latency_ms": None,
            "estimated_cost_usd": None,
        }

    pred_data = pred.get("data", {})
    schema_errors = list(validator.iter_errors(pred_data))
    schema_valid = len(schema_errors) == 0

    contract_errors = [
        f"schema: {e.message}" for e in schema_errors
    ]
    contract_errors.extend(product_array_issues(pred_data, allowed_names))

    evidence_errors, evidence_by_id = validate_evidence(
        pred, image_width, image_height
    )
    contract_errors.extend(evidence_errors)

    predicted_names, pred_counts = safe_product_sets(pred_data)
    contract_valid = len(contract_errors) == 0

    tp = len(expected_names & predicted_names)
    fp = len(predicted_names - expected_names)
    fn = len(expected_names - predicted_names)

    union_names = expected_names | predicted_names

    if pred_counts is None:
        # Duplicate names make quantity semantics invalid.
        exact_pair_accuracy_union = 0.0 if union_names else 1.0
        whole_image_exact = False
        # Aggregate duplicate quantities only for penalty computation, never as normalization.
        aggregated: Counter[str] = Counter()
        for item in pred_data.get("products", []) if isinstance(pred_data.get("products", []), list) else []:
            if (
                isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and isinstance(item.get("quantity"), int)
                and not isinstance(item.get("quantity"), bool)
            ):
                aggregated[item["name"]] += int(item["quantity"])
        scoring_counts = dict(aggregated)
    else:
        scoring_counts = pred_counts
        correct_pairs = sum(
            1 for name in union_names
            if name in truth_counts
            and name in scoring_counts
            and truth_counts[name] == scoring_counts[name]
        )
        exact_pair_accuracy_union = (
            correct_pairs / len(union_names) if union_names else 1.0
        )
        whole_image_exact = truth_counts == scoring_counts and contract_valid

    per_product_errors = [
        abs(truth_counts.get(name, 0) - scoring_counts.get(name, 0))
        for name in sorted(union_names)
    ]
    total_abs_error = sum(per_product_errors)

    ev = evidence_metrics(
        truth, pred, evidence_by_id, iou_threshold
    )

    return {
        "prediction_present": True,
        "schema_valid": schema_valid,
        "prediction_contract_valid": contract_valid,
        "prediction_validation_errors": contract_errors,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "exact_pair_accuracy_union": exact_pair_accuracy_union,
        "whole_image_exact": whole_image_exact,
        "total_absolute_count_error": total_abs_error,
        "per_product_absolute_errors": per_product_errors,
        **ev,
        "latency_ms": pred.get("latency_ms"),
        "estimated_cost_usd": pred.get("estimated_cost_usd"),
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"samples": 0}

    tp = sum(r["tp"] for r in results)
    fp = sum(r["fp"] for r in results)
    fn = sum(r["fn"] for r in results)

    precision = (
        tp / (tp + fp)
        if tp + fp
        else (0.0 if fn > 0 else 1.0)
    )
    recall = tp / (tp + fn) if tp + fn else 1.0

    latencies = [
        float(r["latency_ms"])
        for r in results
        if r.get("latency_ms") is not None
    ]
    costs = [
        float(r["estimated_cost_usd"])
        for r in results
        if r.get("estimated_cost_usd") is not None
    ]
    product_abs_errors = [
        value
        for r in results
        for value in r.get("per_product_absolute_errors", [])
    ]

    return {
        "samples": len(results),
        "schema_valid_rate_over_expected_samples": statistics.mean(
            1.0 if r["schema_valid"] else 0.0 for r in results
        ),
        "schema_valid_rate_among_present_predictions": (
            statistics.mean(
                1.0 if r["schema_valid"] else 0.0
                for r in results
                if r["prediction_present"]
            )
            if any(r["prediction_present"] for r in results)
            else None
        ),
        "prediction_contract_valid_rate": statistics.mean(
            1.0 if r["prediction_contract_valid"] else 0.0 for r in results
        ),
        "product_precision": precision,
        "product_recall": recall,
        "mean_exact_pair_accuracy_over_union": statistics.mean(
            r["exact_pair_accuracy_union"] for r in results
        ),
        "whole_image_exact_accuracy": statistics.mean(
            1.0 if r["whole_image_exact"] else 0.0 for r in results
        ),
        "mean_total_absolute_count_error_per_image": statistics.mean(
            r["total_absolute_count_error"] for r in results
        ),
        "mean_absolute_count_error_per_product": (
            statistics.mean(product_abs_errors) if product_abs_errors else 0.0
        ),
        "mean_evidence_reference_coverage": statistics.mean(
            r["evidence_reference_coverage"] for r in results
        ),
        "mean_evidence_localization_accuracy": statistics.mean(
            r["evidence_localization_accuracy"] for r in results
        ),
        "mean_evidence_localization_precision": statistics.mean(
            r["evidence_localization_precision"] for r in results
        ),
        "mean_evidence_localization_recall": statistics.mean(
            r["evidence_localization_recall"] for r in results
        ),
        "p50_latency_ms": percentile(latencies, 0.50),
        "p95_latency_ms": percentile(latencies, 0.95),
        "median_cost_usd": statistics.median(costs) if costs else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--split", default="development")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--iou-threshold", type=float, default=IOU_THRESHOLD)
    args = parser.parse_args()

    if not 0.0 <= args.iou_threshold <= 1.0:
        raise SystemExit("--iou-threshold must be between 0 and 1")

    schema_path = args.dataset / "schema.json"
    catalog_path = args.dataset / "catalog.json"
    metadata_path = args.dataset / "dataset_metadata.json"
    manifest_path = args.dataset / "manifest.jsonl"
    if not manifest_path.exists():
        manifest_path = args.dataset / "manifest.example.jsonl"

    schema = load_json(schema_path)
    catalog = load_json(catalog_path)
    dataset_metadata = load_json(metadata_path) if metadata_path.exists() else {
        "dataset_id": args.dataset.name,
        "dataset_version": "unknown",
    }

    validator = Draft202012Validator(schema)
    allowed_names = catalog_names(catalog)

    manifest_all = load_jsonl(manifest_path)
    manifest_errors = validate_manifest(manifest_all)
    if manifest_errors:
        raise SystemExit(
            "Manifest validation failed:\n- " + "\n- ".join(manifest_errors)
        )

    try:
        manifest = select_manifest_rows(
            manifest_all, args.split, args.limit, args.offset
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    expected_ids = {row["sample_id"] for row in manifest}

    # Validate dataset integrity before scoring any model.
    dataset_errors: list[str] = []
    truth_by_id: dict[str, dict[str, Any]] = {}
    dims_by_id: dict[str, tuple[int, int]] = {}

    for row in manifest:
        annotation_path = args.dataset / row["annotation"]
        if not annotation_path.exists() and annotation_path.name == "inv_0001.json":
            example_path = args.dataset / "annotations" / "inv_0001.example.json"
            if example_path.exists():
                annotation_path = example_path

        if not annotation_path.exists():
            dataset_errors.append(
                f"{row['sample_id']}: annotation does not exist: {annotation_path}"
            )
            continue

        annotation = load_json(annotation_path)
        errors = validate_annotation(
            args.dataset,
            row,
            annotation,
            validator,
            allowed_names,
        )
        if errors:
            dataset_errors.extend(
                f"{row['sample_id']}: {error}" for error in errors
            )
            continue

        truth_by_id[row["sample_id"]] = annotation
        dims_by_id[row["sample_id"]] = (
            int(annotation["width"]),
            int(annotation["height"]),
        )

    if dataset_errors:
        raise SystemExit(
            "Dataset integrity validation failed:\n- "
            + "\n- ".join(dataset_errors)
        )

    predictions: dict[str, dict[str, Any]] = {}
    prediction_files = (
        list(args.predictions.glob("*.json"))
        if args.predictions.is_dir()
        else [args.predictions]
    )

    duplicate_prediction_ids: list[str] = []
    for pred_file in prediction_files:
        pred = load_json(pred_file)
        sample_id = pred.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise SystemExit(
                f"Prediction file missing non-empty sample_id: {pred_file}"
            )
        if sample_id in predictions:
            duplicate_prediction_ids.append(sample_id)
        predictions[sample_id] = pred

    if duplicate_prediction_ids:
        raise SystemExit(
            "Duplicate prediction sample_id values: "
            + ", ".join(sorted(set(duplicate_prediction_ids)))
        )

    predicted_ids = set(predictions)
    missing_ids = sorted(expected_ids - predicted_ids)
    unexpected_ids = sorted(predicted_ids - expected_ids)

    sample_results: list[dict[str, Any]] = []
    by_tag: dict[str, list[dict[str, Any]]] = defaultdict(list)

    rows_by_id = {row["sample_id"]: row for row in manifest}

    for sample_id in sorted(expected_ids):
        row = rows_by_id[sample_id]
        truth = truth_by_id[sample_id]
        width, height = dims_by_id[sample_id]
        pred = predictions.get(sample_id)

        result = evaluate_prediction(
            truth,
            pred,
            validator,
            allowed_names,
            width,
            height,
            args.iou_threshold,
        )
        result["sample_id"] = sample_id
        result["tags"] = row.get("tags", [])
        sample_results.append(result)

        for tag in row.get("tags", []):
            by_tag[tag].append(result)

    model_versions = sorted({
        str(pred.get("model_bundle_version", "unknown"))
        for sample_id, pred in predictions.items()
        if sample_id in expected_ids
    })

    expected_samples = len(expected_ids)
    predicted_samples = len(expected_ids & predicted_ids)
    prediction_coverage = (
        predicted_samples / expected_samples if expected_samples else 1.0
    )

    report = {
        "reproducibility": {
            "dataset_id": dataset_metadata.get("dataset_id", args.dataset.name),
            "dataset_version": dataset_metadata.get("dataset_version", "unknown"),
            "manifest_sha256": sha256_file(manifest_path),
            "schema_sha256": sha256_file(schema_path),
            "catalog_sha256": sha256_file(catalog_path),
            "evaluator_version": EVALUATOR_VERSION,
            "model_bundle_versions": model_versions,
            "mixed_model_bundle_versions": len(model_versions) > 1,
            "evaluation_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "command": " ".join(sys.argv),
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "dependency_versions": dependency_versions(),
            "iou_threshold": args.iou_threshold,
            "requested_limit": args.limit,
            "requested_offset": args.offset,
        },
        "coverage": {
            "expected_samples": expected_samples,
            "predicted_samples": predicted_samples,
            "missing_predictions": len(missing_ids),
            "missing_prediction_ids": missing_ids,
            "unexpected_predictions": len(unexpected_ids),
            "unexpected_prediction_ids": unexpected_ids,
            "prediction_coverage": prediction_coverage,
        },
        "dataset": args.dataset.name,
        "split": args.split,
        "summary": summarize(sample_results),
        "by_tag": {
            tag: summarize(rows)
            for tag, rows in sorted(by_tag.items())
        },
        "samples": sample_results,
    }

    rendered = json.dumps(report, indent=2)
    print(rendered)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
