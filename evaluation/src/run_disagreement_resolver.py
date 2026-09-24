from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from openai import OpenAI
from PIL import Image

import run_baseline as baseline


RUNNER_VERSION = "0.3.0"
PROMPT_VERSION = "inventory-disagreement-adjudication-v1"
MODEL_BUNDLE_VERSION = "terra-luna-sol-disagreement-v1"
PROMPT_VERSION_V2 = "inventory-full-catalog-adjudication-v2"
MODEL_BUNDLE_VERSION_V2 = "terra-luna-sol-full-catalog-v2"
PROMPT_VERSION_V3 = "inventory-confusion-aware-adjudication-v3"
MODEL_BUNDLE_VERSION_V3 = "terra-luna-sol-confusion-aware-v3"
PROMPT_VERSION_V4 = "inventory-isolated-confusion-adjudication-v4"
MODEL_BUNDLE_VERSION_V4 = "terra-luna-sol-isolated-confusion-v4"
DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_INPUT_USD_PER_MILLION = 4.0
DEFAULT_OUTPUT_USD_PER_MILLION = 20.0


def canonical_products(data: dict[str, Any]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted((item["name"], item["quantity"]) for item in data["products"]))


def predictions_agree(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return canonical_products(a["data"]) == canonical_products(b["data"])


def identity_signature(prediction: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(item["name"] for item in prediction["data"]["products"]))


def resolver_decision(a: dict[str, Any], b: dict[str, Any]) -> str:
    if predictions_agree(a, b):
        return "agreement"
    if identity_signature(a) == identity_signature(b):
        return "terra_count_guard"
    return "sol_identity_adjudication"


def reference_index(manifest_path: Path) -> tuple[dict[str, list[dict[str, Any]]], Path]:
    manifest = baseline.read_json(manifest_path.resolve())
    source = Path(manifest["source"]).resolve()
    if not source.is_dir():
        raise ValueError(f"Reference source directory does not exist: {source}")
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise ValueError("Reference manifest must contain an entries array")
    indexed: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        name = entry.get("name")
        references = entry.get("references")
        if not isinstance(name, str) or not isinstance(references, list) or not references:
            raise ValueError("Every reference entry must contain a name and references")
        indexed[name] = references
    return indexed, source


def load_confusion_sets(
    path: Path, allowed_names: set[str]
) -> tuple[str, list[dict[str, Any]]]:
    document = baseline.read_json(path.resolve())
    version = document.get("version")
    families = document.get("families")
    if not isinstance(version, str) or not version:
        raise ValueError("Confusion sets must contain a non-empty version")
    if not isinstance(families, list) or not families:
        raise ValueError("Confusion sets must contain a non-empty families array")

    seen_ids: set[str] = set()
    seen_members: set[str] = set()
    validated: list[dict[str, Any]] = []
    for family in families:
        family_id = family.get("id") if isinstance(family, dict) else None
        members = family.get("members") if isinstance(family, dict) else None
        if not isinstance(family_id, str) or not family_id:
            raise ValueError("Every confusion family must contain a non-empty id")
        if family_id in seen_ids:
            raise ValueError(f"Duplicate confusion family id: {family_id}")
        if (
            not isinstance(members, list)
            or len(members) < 2
            or not all(isinstance(name, str) and name for name in members)
        ):
            raise ValueError(
                f"Confusion family {family_id!r} must contain at least two product names"
            )
        if len(set(members)) != len(members):
            raise ValueError(f"Confusion family {family_id!r} contains duplicate members")
        unknown = sorted(set(members) - allowed_names)
        if unknown:
            raise ValueError(
                f"Confusion family {family_id!r} contains unknown catalog names: {unknown}"
            )
        overlap = sorted(set(members) & seen_members)
        if overlap:
            raise ValueError(
                "A product may belong to only one confusion family; repeated: "
                + ", ".join(overlap)
            )
        seen_ids.add(family_id)
        seen_members.update(members)
        validated.append({"id": family_id, "members": list(members)})
    return version, validated


def targeted_reference_names(
    candidate_names: list[str],
    families: list[dict[str, Any]],
    max_products: int,
) -> tuple[list[str], list[str]]:
    if max_products <= 0:
        raise ValueError("--max-targeted-reference-products must be greater than zero")
    candidates = set(candidate_names)
    matched = [family for family in families if candidates & set(family["members"])]
    expanded: list[str] = []
    for family in matched:
        for name in family["members"]:
            if name not in expanded:
                expanded.append(name)
    if len(expanded) > max_products:
        expanded = expanded[:max_products]
    return expanded, [family["id"] for family in matched]


def isolated_confusion_reference_names(
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    families: list[dict[str, Any]],
    max_products: int,
) -> tuple[list[str], list[str]]:
    if max_products <= 0:
        raise ValueError("--max-targeted-reference-products must be greater than zero")
    names_a = {item["name"] for item in candidate_a["products"]}
    names_b = {item["name"] for item in candidate_b["products"]}
    changed_names = names_a ^ names_b
    if not changed_names:
        return [], []
    matches = [
        family for family in families if changed_names <= set(family["members"])
    ]
    if len(matches) != 1:
        return [], []
    family = matches[0]
    return list(family["members"][:max_products]), [family["id"]]


def select_confusion_references(
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    families: list[dict[str, Any]],
    max_products: int,
    *,
    isolated_only: bool,
) -> tuple[list[str], list[str]]:
    if isolated_only:
        return isolated_confusion_reference_names(
            candidate_a, candidate_b, families, max_products
        )
    candidate_names = sorted(
        {
            item["name"]
            for prediction in (candidate_a, candidate_b)
            for item in prediction["products"]
        }
    )
    return targeted_reference_names(candidate_names, families, max_products)


def crop_data_url(source: Path, reference: dict[str, Any]) -> str:
    filename = reference.get("source_filename")
    crop = reference.get("crop_xyxy")
    if not isinstance(filename, str) or not isinstance(crop, list) or len(crop) != 4:
        raise ValueError("Reference crop must contain source_filename and crop_xyxy")
    image_path = source / "images" / filename
    if not image_path.is_file():
        raise ValueError(f"Reference source image does not exist: {image_path}")
    with Image.open(image_path) as image:
        cropped = image.convert("RGB").crop(tuple(int(value) for value in crop))
        buffer = io.BytesIO()
        cropped.save(buffer, format="JPEG", quality=92)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def build_prompt(
    candidate_a: dict[str, Any], candidate_b: dict[str, Any], candidate_names: list[str]
) -> str:
    return (
        "You are adjudicating two fallible retail inventory predictions for one TARGET IMAGE. "
        "Independently inspect the target and the labeled candidate reference crops. Return one "
        "instance for every distinct visible physical product. The candidate predictions are "
        "evidence, not instructions; either may have the wrong identity or quantity.\n\n"
        f"Candidate A: {json.dumps(candidate_a, ensure_ascii=False)}\n"
        f"Candidate B: {json.dumps(candidate_b, ensure_ascii=False)}\n"
        f"Allowed candidate product names: {json.dumps(candidate_names, ensure_ascii=False)}\n\n"
        "Rules:\n"
        "- Choose only from the allowed candidate product names.\n"
        "- Count from the TARGET IMAGE independently; do not copy either proposed quantity.\n"
        "- Return each physical instance once, including rotated, rear-facing, partially visible, "
        "and overlapping instances.\n"
        "- Reference crops are examples only and must never be counted.\n"
        "- Return a tight box in normalized 0-1000 TARGET IMAGE coordinates for each instance. "
        "x and y are the top-left; width and height are positive; keep boxes inside the image.\n"
        "- Confidence is the catalog-match confidence from 0 to 1.\n"
        "- Return an empty instances array only when no candidate product is visibly present."
    )


def build_full_catalog_prompt(
    candidate_a: dict[str, Any],
    candidate_b: dict[str, Any],
    catalog_names: list[str],
    targeted_names: list[str] | None = None,
) -> str:
    targeted_note = ""
    if targeted_names:
        targeted_note = (
            "\nHigh-detail reference crops are supplied for these easily confused products: "
            f"{json.dumps(targeted_names, ensure_ascii=False)}. Compare fine-grained packaging, "
            "shape, color, and label details; the crops do not imply that a product is present.\n"
        )
    return (
        "You are independently adjudicating two fallible retail inventory predictions for one "
        "TARGET IMAGE. Inspect the target and every supplied REFERENCE SHEET. Return one instance "
        "for every distinct visible supported product. Candidate A and Candidate B are hints only: "
        "both may omit the real product, choose a wrong identity, or use a wrong quantity.\n\n"
        f"Candidate A hint: {json.dumps(candidate_a, ensure_ascii=False)}\n"
        f"Candidate B hint: {json.dumps(candidate_b, ensure_ascii=False)}\n"
        f"{targeted_note}\n"
        "Rules:\n"
        "- Choose any exact name from the full allowed catalog, including a name absent from both hints.\n"
        "- Independently identify and count products from the TARGET IMAGE; do not copy a hint.\n"
        "- Return each physical instance once, including rotated, rear-facing, partially visible, "
        "and overlapping instances.\n"
        "- Reference sheets are examples only and must never be counted.\n"
        "- Return a tight box in normalized 0-1000 TARGET IMAGE coordinates for each instance. "
        "x and y are the top-left; width and height are positive; keep boxes inside the image.\n"
        "- Confidence is the catalog-match confidence from 0 to 1.\n"
        "- Omit unsupported or unidentifiable products. Return an empty instances array only when "
        "no supported catalog product is visibly present.\n\n"
        f"Full allowed catalog names:\n{json.dumps(catalog_names, ensure_ascii=False)}"
    )


def call_adjudicator(
    client: OpenAI,
    *,
    model: str,
    prompt: str,
    candidate_references: list[tuple[str, str]],
    target_image_url: str,
    output_schema: dict[str, Any],
    detail: str,
    reference_detail: str,
    reasoning_effort: str,
    timeout_seconds: float,
    reference_sheet_urls: list[str] | None = None,
) -> Any:
    content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
    for index, image_url in enumerate(reference_sheet_urls or [], start=1):
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": (
                        f"FULL CATALOG REFERENCE SHEET {index}. Use for matching only; "
                        "do not count it."
                    ),
                },
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": reference_detail,
                },
            ]
        )
    for name, image_url in candidate_references:
        content.extend(
            [
                {
                    "type": "input_text",
                    "text": f"REFERENCE CROP for {name}. Use for matching only; do not count it.",
                },
                {
                    "type": "input_image",
                    "image_url": image_url,
                    "detail": reference_detail,
                },
            ]
        )
    content.extend(
        [
            {
                "type": "input_text",
                "text": "TARGET IMAGE. Identify, count, and localize products only here.",
            },
            {"type": "input_image", "image_url": target_image_url, "detail": detail},
        ]
    )
    request: dict[str, Any] = {
        "model": model,
        "input": [{"role": "user", "content": content}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "inventory_adjudication",
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


def call_with_retries(client: OpenAI, *, max_retries: int, retry_base_seconds: float, **request: Any) -> Any:
    for attempt in range(max_retries + 1):
        try:
            return call_adjudicator(client, **request)
        except baseline.RETRYABLE_ERRORS:
            if attempt == max_retries:
                raise
            time.sleep(retry_base_seconds * (2**attempt))
    raise AssertionError("retry loop exhausted")


def routed_base_prediction(
    base_prediction: dict[str, Any],
    *,
    sample_id: str,
    candidate_a_path: Path,
    candidate_b_path: Path,
    decision: str,
    prompt_version: str = PROMPT_VERSION,
    model_bundle_version: str = MODEL_BUNDLE_VERSION,
    adjudication_scope: str = "candidate_union",
) -> dict[str, Any]:
    prediction = json.loads(json.dumps(base_prediction))
    prediction["sample_id"] = sample_id
    prediction["model_bundle_version"] = model_bundle_version
    metadata = prediction.setdefault("provider_metadata", {})
    metadata.update(
        {
            "resolver_prompt_version": prompt_version,
            "resolver_decision": decision,
            "resolver_model": None,
            "adjudication_scope": adjudication_scope,
            "candidate_a_prediction_path": str(candidate_a_path),
            "candidate_b_prediction_path": str(candidate_b_path),
            "component_models": {
                "candidate_a": "gpt-5.6-luna",
                "candidate_b": "gpt-5.6-terra",
                "evidence": "gpt-5.6-luna",
                "adjudicator": None,
            },
        }
    )
    return prediction


def run(args: argparse.Namespace, client: OpenAI | None = None) -> dict[str, Any]:
    if args.targeted_references_per_product <= 0:
        raise ValueError("--targeted-references-per-product must be greater than zero")
    if args.max_targeted_reference_products <= 0:
        raise ValueError("--max-targeted-reference-products must be greater than zero")
    dataset = args.dataset.resolve()
    manifest = baseline.read_jsonl(dataset / "manifest.jsonl")
    schema = baseline.read_json(dataset / "schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    catalog = baseline.read_json(dataset / "catalog.json")
    catalog_names = baseline.catalog_names(catalog)
    allowed_names = set(catalog_names)
    full_catalog = args.full_catalog_adjudication
    confusion_aware = args.confusion_sets is not None
    isolated_confusion = args.isolated_confusion_only
    if confusion_aware and not full_catalog:
        raise ValueError("--confusion-sets requires --full-catalog-adjudication")
    if isolated_confusion and not confusion_aware:
        raise ValueError("--isolated-confusion-only requires --confusion-sets")
    if isolated_confusion:
        prompt_version = PROMPT_VERSION_V4
        model_bundle_version = MODEL_BUNDLE_VERSION_V4
        adjudication_scope = "full_catalog_isolated_confusion"
    elif confusion_aware:
        prompt_version = PROMPT_VERSION_V3
        model_bundle_version = MODEL_BUNDLE_VERSION_V3
        adjudication_scope = "full_catalog_confusion_aware"
    elif full_catalog:
        prompt_version = PROMPT_VERSION_V2
        model_bundle_version = MODEL_BUNDLE_VERSION_V2
        adjudication_scope = "full_catalog"
    else:
        prompt_version = PROMPT_VERSION
        model_bundle_version = MODEL_BUNDLE_VERSION
        adjudication_scope = "candidate_union"
    rows = baseline.selected_rows(manifest, args.split, args.limit, args.offset)

    candidate_a = baseline.load_identity_predictions(args.candidate_a)
    candidate_b = baseline.load_identity_predictions(args.candidate_b)
    base_predictions = baseline.load_identity_predictions(args.base_predictions)
    if full_catalog:
        reference_sheet_paths, _ = baseline.load_reference_sheets(
            args.references, allowed_names
        )
        reference_sheet_urls = [
            baseline.image_data_url(path) for path in reference_sheet_paths
        ]
        if confusion_aware:
            references, reference_source = reference_index(args.references)
            confusion_version, confusion_families = load_confusion_sets(
                args.confusion_sets, allowed_names
            )
        else:
            references = {}
            reference_source = None
            confusion_version = None
            confusion_families = []
    else:
        references, reference_source = reference_index(args.references)
        reference_sheet_paths = []
        reference_sheet_urls = []
        confusion_version = None
        confusion_families = []

    missing: list[str] = []
    for row in rows:
        sample_id = row["sample_id"]
        if sample_id not in candidate_a or sample_id not in candidate_b or sample_id not in base_predictions:
            missing.append(sample_id)
            continue
        baseline.validate_model_data(candidate_a[sample_id][0]["data"], validator, allowed_names)
        baseline.validate_model_data(candidate_b[sample_id][0]["data"], validator, allowed_names)
        baseline.validate_model_data(base_predictions[sample_id][0]["data"], validator, allowed_names)
        if canonical_products(base_predictions[sample_id][0]["data"]) != canonical_products(
            candidate_b[sample_id][0]["data"]
        ):
            raise ValueError(
                f"Base prediction for {sample_id} does not preserve candidate B data"
            )
    if missing:
        raise ValueError("Missing predictions for selected samples: " + ", ".join(missing))

    decisions = {
        row["sample_id"]: resolver_decision(
            candidate_a[row["sample_id"]][0], candidate_b[row["sample_id"]][0]
        )
        for row in rows
    }
    agreement_ids = [sample_id for sample_id, value in decisions.items() if value == "agreement"]
    count_guard_ids = [
        sample_id for sample_id, value in decisions.items() if value == "terra_count_guard"
    ]
    identity_disagreement_ids = [
        sample_id
        for sample_id, value in decisions.items()
        if value == "sol_identity_adjudication"
    ]
    targeted_preview: dict[str, dict[str, Any]] = {}
    if confusion_aware:
        assert reference_source is not None
        for sample_id in identity_disagreement_ids:
            targeted_names, matched_families = select_confusion_references(
                candidate_a[sample_id][0]["data"],
                candidate_b[sample_id][0]["data"],
                confusion_families,
                args.max_targeted_reference_products,
                isolated_only=isolated_confusion,
            )
            crop_count = 0
            for name in targeted_names:
                if name not in references:
                    raise ValueError(
                        f"Reference manifest lacks confusion-set product {name!r}"
                    )
                selected_references = references[name][
                    : args.targeted_references_per_product
                ]
                crop_count += len(selected_references)
                for reference in selected_references:
                    filename = reference.get("source_filename")
                    if not isinstance(filename, str) or not (
                        reference_source / "images" / filename
                    ).is_file():
                        raise ValueError(
                            f"Reference source image does not exist for {name!r}: {filename!r}"
                        )
            targeted_preview[sample_id] = {
                "matched_confusion_families": matched_families,
                "targeted_reference_names": targeted_names,
                "targeted_reference_crop_count": crop_count,
            }
    run_config = {
        "runner_version": RUNNER_VERSION,
        "prompt_version": prompt_version,
        "model_bundle_version": model_bundle_version,
        "adjudication_scope": adjudication_scope,
        "reference_sheet_count": len(reference_sheet_paths),
        "confusion_sets": str(args.confusion_sets.resolve()) if confusion_aware else None,
        "confusion_sets_version": confusion_version,
        "confusion_targeting_policy": (
            "isolated_identity_symmetric_difference"
            if isolated_confusion
            else "any_candidate_family"
            if confusion_aware
            else None
        ),
        "targeted_references_per_product": args.targeted_references_per_product,
        "max_targeted_reference_products": args.max_targeted_reference_products,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(dataset),
        "split": args.split,
        "requested_limit": args.limit,
        "requested_offset": args.offset,
        "selected_samples": len(rows),
        "agreement_samples": len(agreement_ids),
        "count_guard_samples": len(count_guard_ids),
        "identity_disagreement_samples": len(identity_disagreement_ids),
        "candidate_a": str(args.candidate_a.resolve()),
        "candidate_b": str(args.candidate_b.resolve()),
        "base_predictions": str(args.base_predictions.resolve()),
        "reference_manifest": str(args.references.resolve()),
        "model": args.model,
        "image_detail": args.detail,
        "reference_detail": args.reference_detail,
        "reasoning_effort": args.reasoning_effort,
        "input_usd_per_million_tokens": args.input_usd_per_million,
        "output_usd_per_million_tokens": args.output_usd_per_million,
        "store": False,
    }
    if args.dry_run:
        return {
            "dry_run": True,
            "config": run_config,
            "agreement_sample_ids": agreement_ids,
            "count_guard_sample_ids": count_guard_ids,
            "identity_disagreement_sample_ids": identity_disagreement_ids,
            "targeted_references_by_sample": targeted_preview,
        }
    if client is None and not os.environ.get("OPENAI_API_KEY"):
        raise ValueError(
            "OPENAI_API_KEY is not configured. Set it in the current shell; do not place it in source files."
        )

    output = args.output.resolve()
    predictions_dir = output / "predictions"
    raw_dir = output / "raw-responses"
    errors_dir = output / "errors"
    predictions_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    errors_dir.mkdir(parents=True, exist_ok=True)
    baseline.atomic_write_json(output / "run-config.json", run_config)
    client = client or OpenAI(timeout=args.timeout_seconds)

    copied = 0
    count_guarded = 0
    adjudicated = 0
    resumed = 0
    failed = 0
    total_estimated_cost = 0.0
    for row in rows:
        sample_id = row["sample_id"]
        prediction_path = predictions_dir / f"{sample_id}.json"
        if not args.overwrite and baseline.prediction_is_complete(
            prediction_path, sample_id, prompt_version
        ):
            resumed += 1
            continue
        try:
            a_prediction, a_path = candidate_a[sample_id]
            b_prediction, b_path = candidate_b[sample_id]
            decision = decisions[sample_id]
            if decision in {"agreement", "terra_count_guard"}:
                base_prediction = base_predictions[sample_id][0]
                prediction = routed_base_prediction(
                    base_prediction,
                    sample_id=sample_id,
                    candidate_a_path=a_path,
                    candidate_b_path=b_path,
                    decision=decision,
                    prompt_version=prompt_version,
                    model_bundle_version=model_bundle_version,
                    adjudication_scope=adjudication_scope,
                )
                baseline.atomic_write_json(
                    raw_dir / f"{sample_id}.json",
                    {
                        "resolver_decision": decision,
                        "candidate_a": a_prediction["data"],
                        "candidate_b": b_prediction["data"],
                        "model_called": False,
                    },
                )
                if decision == "agreement":
                    copied += 1
                else:
                    count_guarded += 1
            else:
                candidate_names = sorted(
                    {
                        item["name"]
                        for prediction in (a_prediction, b_prediction)
                        for item in prediction["data"]["products"]
                    }
                )
                if not candidate_names:
                    raise ValueError(
                        f"Disagreement for {sample_id} has no candidate product names"
                    )
                if full_catalog:
                    schema_names = catalog_names
                    if confusion_aware:
                        targeted_names, matched_confusion_families = select_confusion_references(
                            a_prediction["data"],
                            b_prediction["data"],
                            confusion_families,
                            args.max_targeted_reference_products,
                            isolated_only=isolated_confusion,
                        )
                        assert reference_source is not None
                        missing_targeted_references = [
                            name for name in targeted_names if name not in references
                        ]
                        if missing_targeted_references:
                            raise ValueError(
                                "Reference manifest lacks confusion-set products for "
                                f"{sample_id}: {missing_targeted_references}"
                            )
                        candidate_reference_images = [
                            (name, crop_data_url(reference_source, reference))
                            for name in targeted_names
                            for reference in references[name][
                                : args.targeted_references_per_product
                            ]
                        ]
                    else:
                        targeted_names = []
                        matched_confusion_families = []
                        candidate_reference_images = []
                    prompt = build_full_catalog_prompt(
                        a_prediction["data"],
                        b_prediction["data"],
                        catalog_names,
                        targeted_names,
                    )
                else:
                    missing_references = [
                        name for name in candidate_names if name not in references
                    ]
                    if missing_references:
                        raise ValueError(
                            f"Reference manifest lacks candidates for {sample_id}: {missing_references}"
                        )
                    assert reference_source is not None
                    candidate_reference_images = [
                        (name, crop_data_url(reference_source, reference))
                        for name in candidate_names
                        for reference in references[name]
                    ]
                    schema_names = candidate_names
                    targeted_names = candidate_names
                    matched_confusion_families = []
                    prompt = build_prompt(
                        a_prediction["data"], b_prediction["data"], candidate_names
                    )
                image_path = dataset / row["image"]
                with Image.open(image_path) as image:
                    image_width, image_height = image.size
                started = time.perf_counter()
                request = {
                    "client": client,
                    "max_retries": args.max_retries,
                    "retry_base_seconds": args.retry_base_seconds,
                    "model": args.model,
                    "prompt": prompt,
                    "target_image_url": baseline.image_data_url(image_path),
                    "output_schema": baseline.instance_output_schema(schema_names),
                    "detail": args.detail,
                    "reference_detail": args.reference_detail,
                    "reasoning_effort": args.reasoning_effort,
                    "timeout_seconds": args.timeout_seconds,
                }
                response = call_with_retries(
                    candidate_references=candidate_reference_images,
                    reference_sheet_urls=reference_sheet_urls,
                    **request,
                )
                latency_ms = round((time.perf_counter() - started) * 1_000)
                output_text = getattr(response, "output_text", None)
                if not output_text:
                    raise ValueError("Adjudicator response did not contain output_text")
                model_output = json.loads(output_text)
                baseline.validate_instance_output(model_output, set(schema_names))
                data, field_metadata, evidence = baseline.prediction_from_instances(
                    model_output,
                    sample_id=sample_id,
                    image_width=image_width,
                    image_height=image_height,
                    model=args.model,
                )
                baseline.validate_model_data(data, validator, allowed_names)
                usage = getattr(response, "usage", None)
                input_tokens = baseline.usage_value(usage, "input_tokens")
                output_tokens = baseline.usage_value(usage, "output_tokens")
                cost = baseline.estimated_cost(
                    input_tokens,
                    output_tokens,
                    args.input_usd_per_million,
                    args.output_usd_per_million,
                )
                total_estimated_cost += cost
                prediction = {
                    "sample_id": sample_id,
                    "model_bundle_version": model_bundle_version,
                    "data": data,
                    "field_metadata": field_metadata,
                    "evidence": evidence,
                    "latency_ms": latency_ms,
                    "estimated_cost_usd": cost,
                    "usage": {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "total_tokens": input_tokens + output_tokens,
                        "by_stage": {
                            "adjudication": {
                                "input_tokens": input_tokens,
                                "output_tokens": output_tokens,
                                "total_tokens": input_tokens + output_tokens,
                            }
                        },
                    },
                    "estimated_cost_usd_by_stage": {"adjudication": cost},
                    "provider_metadata": {
                        "provider": "openai",
                        "response_id": getattr(response, "id", None),
                        "prompt_version": prompt_version,
                        "resolver_prompt_version": prompt_version,
                        "resolver_decision": "sol_identity_adjudication",
                        "resolver_model": args.model,
                        "adjudication_scope": adjudication_scope,
                        "candidate_a_prediction_path": str(a_path),
                        "candidate_b_prediction_path": str(b_path),
                        "candidate_a_data": a_prediction["data"],
                        "candidate_b_data": b_prediction["data"],
                        "candidate_names": candidate_names,
                        "reference_crop_count": len(candidate_reference_images),
                        "reference_sheet_count": len(reference_sheet_paths),
                        "targeted_reference_names": targeted_names,
                        "matched_confusion_families": matched_confusion_families,
                        "confusion_sets_version": confusion_version,
                        "image_detail": args.detail,
                        "reference_detail": args.reference_detail,
                        "reasoning_effort": args.reasoning_effort,
                        "output_mode": "adjudicated_instances",
                        "component_models": {
                            "candidate_a": "gpt-5.6-luna",
                            "candidate_b": "gpt-5.6-terra",
                            "evidence": args.model,
                            "adjudicator": args.model,
                        },
                    },
                }
                baseline.atomic_write_json(
                    raw_dir / f"{sample_id}.json",
                    {
                        "resolver_decision": "sol_identity_adjudication",
                        "candidate_a": a_prediction["data"],
                        "candidate_b": b_prediction["data"],
                        "response": baseline.response_dump(response),
                    },
                )
                adjudicated += 1

            prediction.setdefault("provider_metadata", {})["prompt_version"] = prompt_version
            baseline.atomic_write_json(prediction_path, prediction)
            error_path = errors_dir / f"{sample_id}.json"
            if error_path.exists():
                error_path.unlink()
            print(
                f"{sample_id}: "
                + (
                    "copied agreement"
                    if decision == "agreement"
                    else "kept Terra count"
                    if decision == "terra_count_guard"
                    else "adjudicated identity"
                )
            )
        except Exception as exc:
            failed += 1
            baseline.atomic_write_json(
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
        "agreements_copied_this_run": copied,
        "count_guards_copied_this_run": count_guarded,
        "identity_disagreements_adjudicated_this_run": adjudicated,
        "disagreements_adjudicated_this_run": adjudicated,
        "resumed": resumed,
        "failed": failed,
        "estimated_cost_usd_this_run": round(total_estimated_cost, 8),
        "predictions": str(predictions_dir),
    }
    baseline.atomic_write_json(output / "run-summary.json", summary)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve disagreements between two inventory prediction runs."
    )
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--split", default="development")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--candidate-a", required=True, type=Path)
    parser.add_argument("--candidate-b", required=True, type=Path)
    parser.add_argument("--base-predictions", required=True, type=Path)
    parser.add_argument("--references", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--full-catalog-adjudication",
        action="store_true",
        help=(
            "For identity disagreements, give the adjudicator the full catalog and all "
            "reference sheets; candidate predictions remain non-binding hints."
        ),
    )
    parser.add_argument(
        "--confusion-sets",
        type=Path,
        help=(
            "Add high-detail crops for catalog-defined confusion families during full-catalog "
            "adjudication. This versions the resolver as the v3 experimental path."
        ),
    )
    parser.add_argument(
        "--isolated-confusion-only",
        action="store_true",
        help=(
            "Attach targeted crops only when the identity symmetric difference is fully "
            "contained in exactly one confusion family. Requires --confusion-sets."
        ),
    )
    parser.add_argument("--targeted-references-per-product", type=int, default=1)
    parser.add_argument("--max-targeted-reference-products", type=int, default=12)
    parser.add_argument("--detail", choices=["low", "high", "auto"], default="high")
    parser.add_argument(
        "--reference-detail", choices=["low", "high", "auto"], default="high"
    )
    parser.add_argument(
        "--reasoning-effort",
        choices=["default", "none", "low", "medium", "high"],
        default="low",
    )
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-base-seconds", type=float, default=1.0)
    parser.add_argument(
        "--input-usd-per-million", type=float, default=DEFAULT_INPUT_USD_PER_MILLION
    )
    parser.add_argument(
        "--output-usd-per-million", type=float, default=DEFAULT_OUTPUT_USD_PER_MILLION
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
        print(f"Disagreement resolver failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
