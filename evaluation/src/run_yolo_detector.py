from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


RUNNER_VERSION = "0.1.0"
ALLOWED_SPLIT = "development"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def selected_rows(
    manifest: list[dict[str, Any]], split: str, offset: int, limit: int | None
) -> list[dict[str, Any]]:
    if split != ALLOWED_SPLIT:
        raise ValueError(
            "The YOLO detector runner is development-only; locked validation and test "
            "splits are intentionally unavailable."
        )
    if offset < 0:
        raise ValueError("--offset must be zero or greater")
    if limit is not None and limit <= 0:
        raise ValueError("--limit must be greater than zero")
    rows = sorted(
        (row for row in manifest if row.get("split") == split),
        key=lambda row: row["sample_id"],
    )[offset:]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise ValueError(f"No manifest samples selected for split {split!r}")
    return rows


def catalog_names(catalog: list[dict[str, Any]]) -> list[str]:
    names = [item.get("name") for item in catalog]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Every catalog item must have a non-empty name")
    if len(names) != len(set(names)):
        raise ValueError("Catalog names must be unique")
    return names


def checkpoint_names(names: Any) -> list[str]:
    if isinstance(names, dict):
        keys = sorted(names, key=lambda key: int(key))
        return [str(names[key]) for key in keys]
    if isinstance(names, (list, tuple)):
        return [str(name) for name in names]
    raise ValueError("Checkpoint does not expose an ordered class-name mapping")


def validate_checkpoint_catalog(model_names: Any, expected_names: list[str]) -> None:
    actual = checkpoint_names(model_names)
    if actual != expected_names:
        missing = sorted(set(expected_names) - set(actual))
        unexpected = sorted(set(actual) - set(expected_names))
        raise ValueError(
            "Checkpoint classes do not match the frozen catalog; "
            f"missing={missing}, unexpected={unexpected}, order_matches={actual == expected_names}"
        )


def pixel_bbox(
    xyxy: Iterable[float], image_width: int, image_height: int
) -> dict[str, int]:
    x1, y1, x2, y2 = (float(value) for value in xyxy)
    left = max(0, min(image_width - 1, math.floor(x1)))
    top = max(0, min(image_height - 1, math.floor(y1)))
    right = max(left + 1, min(image_width, math.ceil(x2)))
    bottom = max(top + 1, min(image_height, math.ceil(y2)))
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def build_prediction(
    *,
    sample_id: str,
    image_width: int,
    image_height: int,
    detections: list[dict[str, Any]],
    model_version: str,
    checkpoint_sha256: str,
    confidence_threshold: float,
    iou_threshold: float,
    latency_ms: float,
) -> dict[str, Any]:
    ordered = sorted(
        detections,
        key=lambda item: (
            str(item["name"]),
            float(item["xyxy"][1]),
            float(item["xyxy"][0]),
        ),
    )
    groups: dict[str, list[tuple[str, float]]] = defaultdict(list)
    evidence: list[dict[str, Any]] = []
    for index, detection in enumerate(ordered, start=1):
        evidence_id = f"ev_{index:03d}"
        name = str(detection["name"])
        confidence = float(detection["confidence"])
        groups[name].append((evidence_id, confidence))
        evidence.append(
            {
                "id": evidence_id,
                "media_id": f"med_{sample_id}",
                "source_type": "image_region",
                "coordinate_space": "original_pixels",
                "bbox": pixel_bbox(detection["xyxy"], image_width, image_height),
                "rotation_degrees": 0,
                "page_index": None,
                "frame_index": None,
                "timestamp_ms": None,
                "model_version": model_version,
                "media_available": True,
            }
        )

    products: list[dict[str, Any]] = []
    field_metadata: dict[str, Any] = {}
    for product_index, name in enumerate(sorted(groups)):
        instances = groups[name]
        refs = [evidence_id for evidence_id, _ in instances]
        metadata = {
            "confidence": min(confidence for _, confidence in instances),
            "status": "unverified",
            "evidence_refs": refs,
        }
        products.append({"name": name, "quantity": len(instances)})
        field_metadata[f"/products/{product_index}/name"] = dict(metadata)
        field_metadata[f"/products/{product_index}/quantity"] = dict(metadata)

    return {
        "sample_id": sample_id,
        "model_bundle_version": model_version,
        "data": {"products": products},
        "field_metadata": field_metadata,
        "evidence": evidence,
        "latency_ms": round(float(latency_ms), 3),
        "estimated_cost_usd": 0.0,
        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        "provider_metadata": {
            "provider": "local_ultralytics",
            "runner_version": RUNNER_VERSION,
            "output_mode": "instances",
            "checkpoint_sha256": checkpoint_sha256,
            "confidence_threshold": confidence_threshold,
            "iou_threshold": iou_threshold,
            "detected_instance_count": len(evidence),
            "image_width": image_width,
            "image_height": image_height,
        },
    }


def detections_from_result(result: Any, names: list[str]) -> list[dict[str, Any]]:
    if result.boxes is None:
        return []
    coordinates = result.boxes.xyxy.detach().cpu().tolist()
    classes = result.boxes.cls.detach().cpu().tolist()
    confidences = result.boxes.conf.detach().cpu().tolist()
    return [
        {
            "name": names[int(class_id)],
            "confidence": float(confidence),
            "xyxy": [float(value) for value in xyxy],
        }
        for xyxy, class_id, confidence in zip(coordinates, classes, confidences)
    ]


def chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def run(args: argparse.Namespace, model: Any | None = None) -> dict[str, Any]:
    dataset = args.dataset.resolve()
    checkpoint = args.checkpoint.resolve()
    output = args.output.resolve()
    rows = selected_rows(
        read_jsonl(dataset / "manifest.jsonl"), args.split, args.offset, args.limit
    )
    expected_names = catalog_names(read_json(dataset / "catalog.json"))
    if not checkpoint.is_file():
        raise ValueError(f"Checkpoint does not exist: {checkpoint}")
    checkpoint_hash = sha256_file(checkpoint)
    model_version = f"yolo11s-d2s-pilot5-{checkpoint_hash[:12]}"

    if model is None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise SystemExit(
                "Ultralytics is required. Install ultralytics==8.3.95."
            ) from exc
        model = YOLO(str(checkpoint))
    validate_checkpoint_catalog(model.names, expected_names)

    predictions_dir = output / "predictions"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    selected: list[tuple[dict[str, Any], Path]] = []
    resumed = 0
    for row in rows:
        prediction_path = predictions_dir / f"{row['sample_id']}.json"
        if prediction_path.exists() and not args.overwrite:
            resumed += 1
            continue
        image_path = dataset / row["image"]
        if not image_path.is_file():
            raise ValueError(f"Image does not exist: {image_path}")
        selected.append((row, image_path))

    completed = 0
    total_started = time.perf_counter()
    if selected:
        for batch_items in chunks(selected, args.batch):
            results = model.predict(
                source=[str(image_path) for _, image_path in batch_items],
                imgsz=args.imgsz,
                conf=args.confidence,
                iou=args.iou,
                device=args.device,
                batch=len(batch_items),
                workers=args.workers,
                max_det=args.max_detections,
                stream=False,
                verbose=False,
            )
            for (row, _), result in zip(batch_items, results, strict=True):
                sample_id = row["sample_id"]
                image_height, image_width = (int(value) for value in result.orig_shape)
                speed = getattr(result, "speed", {}) or {}
                latency_ms = sum(
                    float(speed.get(stage, 0.0))
                    for stage in ("preprocess", "inference", "postprocess")
                )
                prediction = build_prediction(
                    sample_id=sample_id,
                    image_width=image_width,
                    image_height=image_height,
                    detections=detections_from_result(result, expected_names),
                    model_version=model_version,
                    checkpoint_sha256=checkpoint_hash,
                    confidence_threshold=args.confidence,
                    iou_threshold=args.iou,
                    latency_ms=latency_ms,
                )
                atomic_write_json(predictions_dir / f"{sample_id}.json", prediction)
                completed += 1
                print(
                    f"{sample_id}: completed ({latency_ms:.1f} ms, "
                    f"{len(prediction['evidence'])} detections)"
                )

    summary = {
        "runner_version": RUNNER_VERSION,
        "split": args.split,
        "offset": args.offset,
        "selected": len(rows),
        "completed_this_run": completed,
        "resumed": resumed,
        "checkpoint": str(checkpoint),
        "checkpoint_sha256": checkpoint_hash,
        "model_bundle_version": model_version,
        "confidence_threshold": args.confidence,
        "iou_threshold": args.iou,
        "image_size": args.imgsz,
        "batch": args.batch,
        "device": args.device,
        "elapsed_seconds": round(time.perf_counter() - total_started, 3),
        "predictions": str(predictions_dir),
    }
    atomic_write_json(output / "run-summary.json", summary)
    print(json.dumps(summary, indent=2))
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the leakage-safe YOLO detector on inventory-v1 development samples."
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--split", default=ALLOWED_SPLIT)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.70)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument(
        "--batch",
        type=int,
        default=1,
        help="Inference batch size. The CPU-safe default is 1; increase on a GPU.",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-detections", type=int, default=300)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if not 0.0 < args.confidence < 1.0:
        parser.error("--confidence must be greater than 0 and less than 1")
    if not 0.0 < args.iou < 1.0:
        parser.error("--iou must be greater than 0 and less than 1")
    if args.imgsz <= 0 or args.batch <= 0 or args.workers < 0:
        parser.error("--imgsz and --batch must be positive; --workers cannot be negative")
    return args


def main() -> int:
    run(parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
