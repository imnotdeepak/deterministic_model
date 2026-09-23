from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from PIL import Image
from openai import (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    OpenAI,
    RateLimitError,
)


RUNNER_VERSION = "0.4.0"
PROMPT_VERSION = "inventory-catalog-v1"
REFERENCE_PROMPT_VERSION = "inventory-catalog-visual-references-v1"
INSTANCE_PROMPT_VERSION = "inventory-instance-localization-v1"
INSTANCE_REFERENCE_PROMPT_VERSION = "inventory-instance-localization-references-v1"
TWO_STAGE_PROMPT_VERSION = "inventory-two-stage-localization-v1"
TWO_STAGE_REFERENCE_PROMPT_VERSION = "inventory-two-stage-localization-references-v1"
EVIDENCE_ONLY_PROMPT_VERSION = "inventory-authoritative-evidence-v1"
EVIDENCE_ONLY_REFERENCE_PROMPT_VERSION = "inventory-authoritative-evidence-references-v1"
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_INPUT_USD_PER_MILLION = 0.20
DEFAULT_OUTPUT_USD_PER_MILLION = 1.20
RETRYABLE_ERRORS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc}") from exc
    return rows


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def catalog_names(catalog: list[dict[str, Any]]) -> list[str]:
    names = [item.get("name") for item in catalog]
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("Every catalog item must contain a non-empty string name")
    if len(names) != len(set(names)):
        raise ValueError("Catalog names must be unique")
    return names


def structured_output_schema(
    authoritative_schema: dict[str, Any], allowed_names: list[str]
) -> dict[str, Any]:
    schema = json.loads(json.dumps(authoritative_schema))
    # The API accepts the supported JSON Schema subset itself, not its dialect marker.
    schema.pop("$schema", None)
    try:
        name_schema = schema["properties"]["products"]["items"]["properties"]["name"]
    except (KeyError, TypeError) as exc:
        raise ValueError("inventory-v0 schema does not expose products[].name") from exc
    name_schema["enum"] = allowed_names
    return schema


def instance_output_schema(allowed_names: list[str]) -> dict[str, Any]:
    coordinate = {"type": "integer", "minimum": 0, "maximum": 1000}
    extent = {"type": "integer", "minimum": 1, "maximum": 1000}
    return {
        "type": "object",
        "properties": {
            "instances": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "enum": allowed_names},
                        "bbox": {
                            "type": "object",
                            "properties": {
                                "x": coordinate,
                                "y": coordinate,
                                "width": extent,
                                "height": extent,
                            },
                            "required": ["x", "y", "width", "height"],
                            "additionalProperties": False,
                        },
                        "confidence": {
                            "type": "number",
                            "minimum": 0,
                            "maximum": 1,
                        },
                    },
                    "required": ["name", "bbox", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["instances"],
        "additionalProperties": False,
    }


def image_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError(f"Unsupported baseline image type: {mime_type or path.suffix}")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_reference_sheets(
    manifest_path: Path, allowed_names: set[str]
) -> tuple[list[Path], dict[str, Any]]:
    manifest_path = manifest_path.resolve()
    manifest = read_json(manifest_path)
    entries = manifest.get("entries")
    sheets = manifest.get("sheets")
    if not isinstance(entries, list) or not isinstance(sheets, list) or not sheets:
        raise ValueError("Reference manifest must contain non-empty entries and sheets arrays")
    reference_names = [entry.get("name") for entry in entries]
    if len(reference_names) != len(set(reference_names)):
        raise ValueError("Reference manifest contains duplicate product names")
    missing = sorted(allowed_names - set(reference_names))
    unexpected = sorted(set(reference_names) - allowed_names)
    if missing or unexpected:
        raise ValueError(
            f"Reference catalog does not match dataset catalog; missing={missing}, unexpected={unexpected}"
        )
    paths = []
    for sheet in sheets:
        relative_path = sheet.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("Every reference sheet must contain a path")
        path = (manifest_path.parent / relative_path).resolve()
        if not path.is_file():
            raise ValueError(f"Reference sheet does not exist: {path}")
        expected_hash = sheet.get("sha256")
        if expected_hash and sha256_file(path) != expected_hash:
            raise ValueError(f"Reference sheet hash mismatch: {path}")
        paths.append(path)
    return paths, manifest


def build_prompt(
    allowed_names: list[str],
    has_visual_references: bool = False,
    instance_localization: bool = False,
    identity_candidates: list[dict[str, Any]] | None = None,
    restrict_identity_candidates: bool = True,
) -> str:
    catalog = json.dumps(allowed_names, ensure_ascii=False)
    reference_rules = ""
    if has_visual_references:
        reference_rules = (
            "\n- The images labeled REFERENCE SHEET show catalog examples; never count them."
            "\n- Match the separate TARGET IMAGE against those examples, including rotated, "
            "rear-facing, and partially occluded products."
        )
    task = (
        "Inspect the supplied TARGET IMAGE and return one entry for every distinct visible "
        "supported product instance. Do not aggregate quantities."
        if instance_localization
        else "Inspect the supplied image and return every visible supported product with its count."
    )
    instance_rules = ""
    if instance_localization:
        instance_rules = (
            "\n- Return each physical instance separately, including partially visible and overlapping items."
            "\n- Do not return the same physical instance twice."
            "\n- For each instance, return a tight bounding box in normalized 0-1000 TARGET IMAGE "
            "coordinates: x and y are the top-left; width and height are positive extents."
            "\n- Ensure x + width <= 1000 and y + height <= 1000."
            "\n- Confidence is a number from 0 to 1 for the catalog match."
        )
    candidate_rules = ""
    if identity_candidates:
        if restrict_identity_candidates:
            candidate_rules = (
                "\n- Stage one identified the following candidate products. Quantities are hints, "
                "not ground truth; independently locate every distinct physical instance: "
                f"{json.dumps(identity_candidates, ensure_ascii=False)}"
                "\n- Do not choose product names outside these stage-one candidates, even if a "
                "reference sheet contains other catalog items."
            )
        else:
            candidate_rules = (
                "\n- A previously scored aggregate prediction is authoritative for final product "
                "names and quantities. Use it as a strong visual hint while focusing only on "
                "locating visible physical instances: "
                f"{json.dumps(identity_candidates, ensure_ascii=False)}"
                "\n- You may return any allowed catalog name when visually necessary. This pass "
                "supplies evidence only and cannot change the authoritative aggregate output."
            )
    output_rules = (
        "- Return an empty instances array when no supported product can be identified."
        if instance_localization
        else (
            "- Count distinct visible physical product instances.\n"
            "- Include each product name at most once.\n"
            "- Return an empty products array when no supported product can be identified."
        )
    )
    return (
        "You are evaluating a controlled retail inventory extraction system. "
        f"{task}\n\n"
        "Rules:\n"
        "- Use only an exact product name from the allowed catalog.\n"
        "- Omit products that cannot be matched confidently to the catalog.\n"
        "- Do not infer products that are not visibly present.\n"
        f"{output_rules}"
        f"{instance_rules}"
        f"{candidate_rules}"
        f"{reference_rules}\n\n"
        f"Allowed catalog names:\n{catalog}"
    )


def validate_instance_output(data: Any, allowed_names: set[str]) -> None:
    validator = Draft202012Validator(instance_output_schema(sorted(allowed_names)))
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        raise ValueError(
            "Model instance output schema error: "
            + "; ".join(error.message for error in errors)
        )
    for index, instance in enumerate(data["instances"]):
        bbox = instance["bbox"]
        if bbox["x"] + bbox["width"] > 1000:
            raise ValueError(f"instances[{index}] bbox extends beyond normalized width")
        if bbox["y"] + bbox["height"] > 1000:
            raise ValueError(f"instances[{index}] bbox extends beyond normalized height")


def normalized_bbox_to_pixels(
    bbox: dict[str, int], image_width: int, image_height: int
) -> dict[str, int]:
    left = max(0, min(image_width - 1, round(bbox["x"] * image_width / 1000)))
    top = max(0, min(image_height - 1, round(bbox["y"] * image_height / 1000)))
    right = max(
        left + 1,
        min(image_width, round((bbox["x"] + bbox["width"]) * image_width / 1000)),
    )
    bottom = max(
        top + 1,
        min(image_height, round((bbox["y"] + bbox["height"]) * image_height / 1000)),
    )
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def prediction_from_instances(
    model_output: dict[str, Any],
    *,
    sample_id: str,
    image_width: int,
    image_height: int,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    groups: dict[str, list[tuple[str, float]]] = {}
    evidence: list[dict[str, Any]] = []
    for index, instance in enumerate(model_output["instances"], start=1):
        evidence_id = f"ev_{index:03d}"
        name = instance["name"]
        groups.setdefault(name, []).append((evidence_id, float(instance["confidence"])))
        evidence.append(
            {
                "id": evidence_id,
                "media_id": f"med_{sample_id}",
                "source_type": "image_region",
                "coordinate_space": "original_pixels",
                "bbox": normalized_bbox_to_pixels(
                    instance["bbox"], image_width, image_height
                ),
                "rotation_degrees": 0,
                "page_index": None,
                "frame_index": None,
                "timestamp_ms": None,
                "model_version": model,
                "media_available": True,
            }
        )

    products = []
    field_metadata: dict[str, Any] = {}
    for product_index, name in enumerate(sorted(groups)):
        instances = groups[name]
        refs = [evidence_id for evidence_id, _ in instances]
        confidence = min(confidence for _, confidence in instances)
        products.append({"name": name, "quantity": len(instances)})
        metadata = {
            "confidence": confidence,
            "status": "unverified",
            "evidence_refs": refs,
        }
        field_metadata[f"/products/{product_index}/name"] = dict(metadata)
        field_metadata[f"/products/{product_index}/quantity"] = dict(metadata)
    return {"products": products}, field_metadata, evidence


def evidence_for_authoritative_data(
    authoritative_data: dict[str, Any],
    model_output: dict[str, Any],
    *,
    sample_id: str,
    image_width: int,
    image_height: int,
    model: str,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Attach localization evidence without changing authoritative products or counts."""
    data = json.loads(json.dumps(authoritative_data))
    products = data["products"]
    sole_product_name = products[0]["name"] if len(products) == 1 else None
    attached: dict[str, list[tuple[str, float]]] = {
        product["name"]: [] for product in products
    }
    evidence: list[dict[str, Any]] = []

    for instance in model_output["instances"]:
        target_name = sole_product_name or instance["name"]
        if target_name not in attached:
            continue
        evidence_id = f"ev_{len(evidence) + 1:03d}"
        attached[target_name].append((evidence_id, float(instance["confidence"])))
        evidence.append(
            {
                "id": evidence_id,
                "media_id": f"med_{sample_id}",
                "source_type": "image_region",
                "coordinate_space": "original_pixels",
                "bbox": normalized_bbox_to_pixels(
                    instance["bbox"], image_width, image_height
                ),
                "rotation_degrees": 0,
                "page_index": None,
                "frame_index": None,
                "timestamp_ms": None,
                "model_version": model,
                "media_available": True,
            }
        )

    field_metadata: dict[str, Any] = {}
    for product_index, product in enumerate(products):
        matches = attached[product["name"]]
        metadata = {
            "confidence": min((confidence for _, confidence in matches), default=0.0),
            "status": "unverified",
            "evidence_refs": [evidence_id for evidence_id, _ in matches],
        }
        field_metadata[f"/products/{product_index}/name"] = dict(metadata)
        field_metadata[f"/products/{product_index}/quantity"] = dict(metadata)
    return data, field_metadata, evidence


def load_identity_predictions(
    path: Path,
) -> dict[str, tuple[dict[str, Any], Path]]:
    resolved = path.resolve()
    if resolved.is_dir():
        paths = sorted(resolved.glob("*.json"))
    elif resolved.is_file():
        paths = [resolved]
    else:
        raise ValueError(f"Identity predictions path does not exist: {resolved}")
    if not paths:
        raise ValueError(f"No identity prediction JSON files found in: {resolved}")

    predictions: dict[str, tuple[dict[str, Any], Path]] = {}
    for prediction_path in paths:
        prediction = read_json(prediction_path)
        sample_id = prediction.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError(f"Identity prediction has no sample_id: {prediction_path}")
        if sample_id in predictions:
            raise ValueError(f"Duplicate identity prediction for sample {sample_id}")
        if not isinstance(prediction.get("data"), dict):
            raise ValueError(f"Identity prediction has no data object: {prediction_path}")
        predictions[sample_id] = (prediction, prediction_path)
    return predictions


def selected_rows(
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


def validate_model_data(
    data: Any,
    validator: Draft202012Validator,
    allowed_names: set[str],
) -> None:
    errors = sorted(validator.iter_errors(data), key=lambda error: list(error.path))
    if errors:
        raise ValueError("Model output schema error: " + "; ".join(error.message for error in errors))
    names = [item["name"] for item in data["products"]]
    unknown = sorted(set(names) - allowed_names)
    duplicates = sorted(name for name in set(names) if names.count(name) > 1)
    if unknown:
        raise ValueError(f"Model returned names outside the catalog: {unknown}")
    if duplicates:
        raise ValueError(f"Model returned duplicate product rows: {duplicates}")


def usage_value(usage: Any, name: str) -> int:
    value = getattr(usage, name, None)
    if value is None and isinstance(usage, dict):
        value = usage.get(name)
    return int(value or 0)


def estimated_cost(
    input_tokens: int,
    output_tokens: int,
    input_usd_per_million: float,
    output_usd_per_million: float,
) -> float:
    return round(
        input_tokens / 1_000_000 * input_usd_per_million
        + output_tokens / 1_000_000 * output_usd_per_million,
        8,
    )


def response_dump(response: Any) -> dict[str, Any]:
    if hasattr(response, "model_dump"):
        return response.model_dump(mode="json")
    if isinstance(response, dict):
        return response
    raise TypeError("OpenAI response cannot be serialized")


def call_model(
    client: OpenAI,
    *,
    model: str,
    prompt: str,
    target_image_url: str,
    reference_image_urls: list[str],
    output_schema: dict[str, Any],
    detail: str,
    reference_detail: str,
    reasoning_effort: str,
    timeout_seconds: float,
) -> Any:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for index, reference_image_url in enumerate(reference_image_urls, start=1):
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": f"REFERENCE SHEET {index}. Use only for visual matching; do not count it.",
                },
                {
                    "type": "input_image",
                    "image_url": reference_image_url,
                    "detail": reference_detail,
                },
            ]
        )
    content.extend(
        [
            {
                "type": "input_text",
                "text": "TARGET IMAGE. Identify and count products only in this image.",
            },
            {"type": "input_image", "image_url": target_image_url, "detail": detail},
        ]
    )
    request: dict[str, Any] = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "inventory_extraction",
                "schema": output_schema,
                "strict": True,
            }
        },
        "max_output_tokens": 2_000,
        "store": False,
        "timeout": timeout_seconds,
    }
    if reasoning_effort != "default":
        request["reasoning"] = {"effort": reasoning_effort}
    return client.responses.create(**request)


def call_with_retries(
    client: OpenAI,
    *,
    max_retries: int,
    retry_base_seconds: float,
    **request: Any,
) -> Any:
    for attempt in range(max_retries + 1):
        try:
            return call_model(client, **request)
        except RETRYABLE_ERRORS:
            if attempt == max_retries:
                raise
            delay = retry_base_seconds * (2**attempt) + random.random() * 0.25
            time.sleep(delay)
    raise AssertionError("retry loop exhausted")


def prediction_is_complete(
    path: Path, sample_id: str, prompt_version: str | None = None
) -> bool:
    if not path.is_file():
        return False
    try:
        prediction = read_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    if prediction.get("sample_id") != sample_id or not isinstance(prediction.get("data"), dict):
        return False
    if prompt_version is not None:
        actual = prediction.get("provider_metadata", {}).get("prompt_version")
        return actual == prompt_version
    return True


def run(args: argparse.Namespace, client: OpenAI | None = None) -> dict[str, Any]:
    if args.evidence_only and args.identity_predictions is None:
        raise ValueError("--evidence-only requires --identity-predictions")
    if args.identity_predictions is not None and not args.evidence_only:
        raise ValueError("--identity-predictions requires --evidence-only")
    if args.evidence_only and (args.two_stage or args.instance_localization):
        raise ValueError(
            "--evidence-only cannot be combined with --two-stage or --instance-localization"
        )

    dataset = args.dataset.resolve()
    manifest_path = dataset / "manifest.jsonl"
    schema_path = dataset / "schema.json"
    catalog_path = dataset / "catalog.json"
    manifest = read_jsonl(manifest_path)
    schema = read_json(schema_path)
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    catalog = read_json(catalog_path)
    names = catalog_names(catalog)
    allowed_names = set(names)
    instance_localization = args.instance_localization or args.two_stage or args.evidence_only
    aggregate_schema = structured_output_schema(schema, names)
    output_schema = (
        instance_output_schema(names)
        if instance_localization and not args.two_stage
        else aggregate_schema
    )
    reference_paths: list[Path] = []
    reference_manifest: dict[str, Any] | None = None
    if args.references is not None:
        reference_paths, reference_manifest = load_reference_sheets(
            args.references, allowed_names
        )
    reference_image_urls = [image_data_url(path) for path in reference_paths]
    if args.evidence_only:
        prompt_version = (
            EVIDENCE_ONLY_REFERENCE_PROMPT_VERSION
            if reference_paths
            else EVIDENCE_ONLY_PROMPT_VERSION
        )
    elif args.two_stage:
        prompt_version = (
            TWO_STAGE_REFERENCE_PROMPT_VERSION
            if reference_paths
            else TWO_STAGE_PROMPT_VERSION
        )
    elif instance_localization:
        prompt_version = (
            INSTANCE_REFERENCE_PROMPT_VERSION if reference_paths else INSTANCE_PROMPT_VERSION
        )
    else:
        prompt_version = REFERENCE_PROMPT_VERSION if reference_paths else PROMPT_VERSION
    prompt = build_prompt(
        names,
        has_visual_references=bool(reference_paths),
        instance_localization=instance_localization and not args.two_stage,
    )
    rows = selected_rows(manifest, args.split, args.limit, args.offset)
    identity_predictions: dict[str, tuple[dict[str, Any], Path]] = {}
    if args.identity_predictions is not None:
        identity_predictions = load_identity_predictions(args.identity_predictions)
        missing = [row["sample_id"] for row in rows if row["sample_id"] not in identity_predictions]
        if missing:
            raise ValueError(
                "Missing authoritative identity predictions for selected samples: "
                + ", ".join(missing)
            )
        for row in rows:
            frozen_data = identity_predictions[row["sample_id"]][0]["data"]
            validate_model_data(frozen_data, validator, allowed_names)

    output = args.output.resolve()
    predictions_dir = output / "predictions"
    raw_dir = output / "raw-responses"
    errors_dir = output / "errors"
    run_config = {
        "runner_version": RUNNER_VERSION,
        "prompt_version": prompt_version,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "split": args.split,
        "requested_limit": args.limit,
        "requested_offset": args.offset,
        "selected_samples": len(rows),
        "model": args.model,
        "image_detail": args.detail,
        "reasoning_effort": args.reasoning_effort,
        "output_mode": (
            "aggregate_with_evidence"
            if args.evidence_only
            else "two_stage_instances"
            if args.two_stage
            else "instances" if instance_localization else "aggregate"
        ),
        "two_stage": args.two_stage,
        "evidence_only": args.evidence_only,
        "identity_predictions": (
            str(args.identity_predictions.resolve()) if args.identity_predictions else None
        ),
        "bbox_input_coordinate_space": (
            "normalized_0_1000" if instance_localization else None
        ),
        "bbox_output_coordinate_space": (
            "original_pixels" if instance_localization else None
        ),
        "reference_manifest": str(args.references.resolve()) if args.references else None,
        "reference_catalog_version": (
            reference_manifest.get("reference_catalog_version") if reference_manifest else None
        ),
        "reference_sheet_count": len(reference_paths),
        "reference_detail": args.reference_detail if reference_paths else None,
        "input_usd_per_million_tokens": args.input_usd_per_million,
        "output_usd_per_million_tokens": args.output_usd_per_million,
        "store": False,
    }

    if args.dry_run:
        return {
            "dry_run": True,
            "config": run_config,
            "sample_ids": [row["sample_id"] for row in rows],
        }

    if not os.environ.get("OPENAI_API_KEY") and client is None:
        raise ValueError(
            "OPENAI_API_KEY is not configured. Set it in the current shell; do not place it in source files."
        )

    predictions_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    errors_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(output / "run-config.json", run_config)
    client = client or OpenAI(timeout=args.timeout_seconds)

    completed = 0
    resumed = 0
    failed = 0
    total_estimated_cost = 0.0

    for row in rows:
        sample_id = row["sample_id"]
        prediction_path = predictions_dir / f"{sample_id}.json"
        if not args.overwrite and prediction_is_complete(
            prediction_path, sample_id, prompt_version
        ):
            resumed += 1
            continue

        image_path = dataset / row["image"]
        started = time.perf_counter()
        try:
            with Image.open(image_path) as image:
                image_width, image_height = image.size
            target_image_url = image_data_url(image_path)
            stage_responses: dict[str, Any] = {}
            identity_data: dict[str, Any] | None = None
            candidate_names: list[str] | None = None
            candidate_fallback = False
            identity_prediction_path: Path | None = None

            if args.evidence_only:
                frozen_prediction, identity_prediction_path = identity_predictions[sample_id]
                identity_data = json.loads(json.dumps(frozen_prediction["data"]))
                candidate_names = [item["name"] for item in identity_data["products"]]
                localization_prompt = build_prompt(
                    names,
                    has_visual_references=bool(reference_paths),
                    instance_localization=True,
                    identity_candidates=identity_data["products"],
                    restrict_identity_candidates=False,
                )
                response = call_with_retries(
                    client,
                    max_retries=args.max_retries,
                    retry_base_seconds=args.retry_base_seconds,
                    model=args.model,
                    prompt=localization_prompt,
                    target_image_url=target_image_url,
                    reference_image_urls=reference_image_urls,
                    output_schema=instance_output_schema(names),
                    detail=args.detail,
                    reference_detail=args.reference_detail,
                    reasoning_effort=args.reasoning_effort,
                    timeout_seconds=args.timeout_seconds,
                )
                stage_responses["localization"] = response
                raw = {
                    "identity_source": {
                        "prediction_path": str(identity_prediction_path),
                        "sample_id": sample_id,
                        "data": identity_data,
                    },
                    "localization": response_dump(response),
                }
            elif args.two_stage:
                identity_response = call_with_retries(
                    client,
                    max_retries=args.max_retries,
                    retry_base_seconds=args.retry_base_seconds,
                    model=args.model,
                    prompt=prompt,
                    target_image_url=target_image_url,
                    reference_image_urls=reference_image_urls,
                    output_schema=aggregate_schema,
                    detail=args.detail,
                    reference_detail=args.reference_detail,
                    reasoning_effort=args.reasoning_effort,
                    timeout_seconds=args.timeout_seconds,
                )
                stage_responses["identity"] = identity_response
                atomic_write_json(
                    raw_dir / f"{sample_id}.json",
                    {"identity": response_dump(identity_response)},
                )
                identity_text = getattr(identity_response, "output_text", None)
                if not identity_text:
                    raise ValueError("Identity-stage response did not contain output_text")
                identity_data = json.loads(identity_text)
                validate_model_data(identity_data, validator, allowed_names)
                candidate_names = [item["name"] for item in identity_data["products"]]
                if candidate_names:
                    localization_names = candidate_names
                    identity_candidates = identity_data["products"]
                else:
                    localization_names = names
                    identity_candidates = None
                    candidate_fallback = True
                localization_prompt = build_prompt(
                    localization_names,
                    has_visual_references=bool(reference_paths),
                    instance_localization=True,
                    identity_candidates=identity_candidates,
                )
                response = call_with_retries(
                    client,
                    max_retries=args.max_retries,
                    retry_base_seconds=args.retry_base_seconds,
                    model=args.model,
                    prompt=localization_prompt,
                    target_image_url=target_image_url,
                    reference_image_urls=reference_image_urls,
                    output_schema=instance_output_schema(localization_names),
                    detail=args.detail,
                    reference_detail=args.reference_detail,
                    reasoning_effort=args.reasoning_effort,
                    timeout_seconds=args.timeout_seconds,
                )
                stage_responses["localization"] = response
                raw = {
                    "identity": response_dump(identity_response),
                    "localization": response_dump(response),
                }
            else:
                response = call_with_retries(
                    client,
                    max_retries=args.max_retries,
                    retry_base_seconds=args.retry_base_seconds,
                    model=args.model,
                    prompt=prompt,
                    target_image_url=target_image_url,
                    reference_image_urls=reference_image_urls,
                    output_schema=output_schema,
                    detail=args.detail,
                    reference_detail=args.reference_detail,
                    reasoning_effort=args.reasoning_effort,
                    timeout_seconds=args.timeout_seconds,
                )
                stage_responses["localization" if instance_localization else "aggregate"] = response
                raw = response_dump(response)

            latency_ms = round((time.perf_counter() - started) * 1_000)
            atomic_write_json(raw_dir / f"{sample_id}.json", raw)

            output_text = getattr(response, "output_text", None)
            if not output_text:
                raise ValueError("OpenAI response did not contain output_text")
            model_output = json.loads(output_text)
            if args.evidence_only:
                validate_instance_output(model_output, allowed_names)
                assert identity_data is not None
                data, field_metadata, evidence = evidence_for_authoritative_data(
                    identity_data,
                    model_output,
                    sample_id=sample_id,
                    image_width=image_width,
                    image_height=image_height,
                    model=args.model,
                )
            elif instance_localization:
                validate_instance_output(model_output, allowed_names)
                data, field_metadata, evidence = prediction_from_instances(
                    model_output,
                    sample_id=sample_id,
                    image_width=image_width,
                    image_height=image_height,
                    model=args.model,
                )
            else:
                data = model_output
                field_metadata = {}
                evidence = []
            validate_model_data(data, validator, allowed_names)

            usage_by_stage = {}
            cost_by_stage = {}
            input_tokens = 0
            output_tokens = 0
            for stage_name, stage_response in stage_responses.items():
                stage_input = usage_value(
                    getattr(stage_response, "usage", None), "input_tokens"
                )
                stage_output = usage_value(
                    getattr(stage_response, "usage", None), "output_tokens"
                )
                input_tokens += stage_input
                output_tokens += stage_output
                usage_by_stage[stage_name] = {
                    "input_tokens": stage_input,
                    "output_tokens": stage_output,
                    "total_tokens": stage_input + stage_output,
                }
                cost_by_stage[stage_name] = estimated_cost(
                    stage_input,
                    stage_output,
                    args.input_usd_per_million,
                    args.output_usd_per_million,
                )
            cost = round(sum(cost_by_stage.values()), 8)
            prediction = {
                "sample_id": sample_id,
                "model_bundle_version": args.model,
                "data": data,
                "field_metadata": field_metadata,
                "evidence": evidence,
                "latency_ms": latency_ms,
                "estimated_cost_usd": cost,
                "usage": {
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens,
                    "by_stage": usage_by_stage,
                },
                "estimated_cost_usd_by_stage": cost_by_stage,
                "provider_metadata": {
                    "provider": "openai",
                    "response_id": getattr(response, "id", None),
                    "response_ids": {
                        stage_name: getattr(stage_response, "id", None)
                        for stage_name, stage_response in stage_responses.items()
                    },
                    "prompt_version": prompt_version,
                    "image_detail": args.detail,
                    "reference_sheet_count": len(reference_paths),
                    "reference_detail": args.reference_detail if reference_paths else None,
                    "reasoning_effort": args.reasoning_effort,
                    "output_mode": (
                        "aggregate_with_evidence"
                        if args.evidence_only
                        else "two_stage_instances"
                        if args.two_stage
                        else "instances" if instance_localization else "aggregate"
                    ),
                    "model_instance_count": (
                        len(model_output["instances"])
                        if instance_localization
                        else None
                    ),
                    "identity_candidates": candidate_names,
                    "identity_candidate_fallback_to_full_catalog": candidate_fallback,
                    "identity_source": (
                        "predictions" if args.evidence_only else "api" if args.two_stage else None
                    ),
                    "identity_prediction_path": (
                        str(identity_prediction_path) if identity_prediction_path else None
                    ),
                },
            }
            atomic_write_json(prediction_path, prediction)
            error_path = errors_dir / f"{sample_id}.json"
            if error_path.exists():
                error_path.unlink()
            total_estimated_cost += cost
            completed += 1
            print(f"{sample_id}: completed ({latency_ms} ms, estimated ${cost:.6f})")
        except Exception as exc:
            failed += 1
            atomic_write_json(
                errors_dir / f"{sample_id}.json",
                {
                    "sample_id": sample_id,
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                    "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                },
            )
            print(f"{sample_id}: failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            if args.fail_fast:
                raise

    summary = {
        "selected": len(rows),
        "completed_this_run": completed,
        "resumed": resumed,
        "failed": failed,
        "estimated_cost_usd_this_run": round(total_estimated_cost, 8),
        "predictions": str(predictions_dir),
    }
    atomic_write_json(output / "run-summary.json", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an OpenAI vision baseline on inventory-v0.")
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", default="development")
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip this many sorted samples within the selected split.",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--detail", choices=["low", "high", "auto"], default="high")
    parser.add_argument(
        "--references",
        type=Path,
        help="Path to a visual reference-manifest.json generated by build_reference_catalog.py",
    )
    parser.add_argument(
        "--reference-detail", choices=["low", "high", "auto"], default="high"
    )
    parser.add_argument(
        "--instance-localization",
        action="store_true",
        help="Return one normalized bounding box per instance and derive counts/evidence.",
    )
    parser.add_argument(
        "--two-stage",
        action="store_true",
        help=(
            "First identify aggregate product candidates, then constrain instance "
            "localization to those candidates. Implies --instance-localization."
        ),
    )
    parser.add_argument(
        "--evidence-only",
        action="store_true",
        help=(
            "Keep aggregate product data from --identity-predictions authoritative and "
            "make one model call per image only to attach localization evidence."
        ),
    )
    parser.add_argument(
        "--identity-predictions",
        type=Path,
        help=(
            "Prediction JSON file or directory used as authoritative aggregate data by "
            "--evidence-only."
        ),
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["default", "none", "low", "medium", "high"],
        default="none",
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=float, default=1.0)
    parser.add_argument(
        "--input-usd-per-million",
        type=float,
        default=DEFAULT_INPUT_USD_PER_MILLION,
    )
    parser.add_argument(
        "--output-usd-per-million",
        type=float,
        default=DEFAULT_OUTPUT_USD_PER_MILLION,
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = run(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Baseline run failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
