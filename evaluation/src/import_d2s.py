from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import sys
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from PIL import Image

try:
    from .evaluate import catalog_names, validate_annotation, validate_manifest
except ImportError:
    from evaluate import catalog_names, validate_annotation, validate_manifest


IMPORTER_VERSION = "0.1.0"
SOURCE_FILES = {
    "training": "D2S_training.json",
    "validation": "D2S_validation.json",
}
SPLIT_RATIOS = {
    "development": 0.70,
    "validation": 0.15,
    "test": 0.15,
}


@dataclass(frozen=True)
class Candidate:
    source_split: str
    image: dict[str, Any]
    annotations: tuple[dict[str, Any], ...]

    @property
    def image_id(self) -> int:
        return int(self.image["id"])

    @property
    def scene_id(self) -> int:
        return self.image_id // 100

    @property
    def capture_index(self) -> int:
        return self.image_id % 100

    @property
    def session_id(self) -> str:
        return f"d2s_scene_{self.scene_id:04d}"


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_source_root(source: Path) -> Path:
    source = source.resolve()
    candidates = [source]
    candidates.extend(path for path in source.iterdir() if path.is_dir())
    for candidate in candidates:
        images = candidate / "images"
        annotations = candidate / "annotations"
        if images.is_dir() and annotations.is_dir() and all(
            (annotations / filename).is_file() for filename in SOURCE_FILES.values()
        ):
            return candidate
    raise ValueError(
        f"Could not find D2S images/ and annotations/ beneath {source}. "
        f"Required annotation files: {sorted(SOURCE_FILES.values())}"
    )


def load_source(
    source_root: Path,
) -> tuple[list[Candidate], list[dict[str, Any]], dict[str, Any]]:
    canonical_categories: dict[int, dict[str, Any]] | None = None
    candidates: list[Candidate] = []
    seen_image_ids: set[int] = set()
    source_info: dict[str, Any] = {}

    for source_split, filename in SOURCE_FILES.items():
        annotation_path = source_root / "annotations" / filename
        payload = read_json(annotation_path)
        categories = {int(item["id"]): item for item in payload["categories"]}
        if canonical_categories is None:
            canonical_categories = categories
        elif categories != canonical_categories:
            raise ValueError(f"Category metadata differs in {annotation_path}")

        annotations_by_image: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for annotation in payload.get("annotations", []):
            annotations_by_image[int(annotation["image_id"])].append(annotation)

        for image in payload["images"]:
            image_id = int(image["id"])
            if image_id in seen_image_ids:
                raise ValueError(f"Duplicate D2S image id across source files: {image_id}")
            seen_image_ids.add(image_id)
            if image_id % 100 >= 30:
                raise ValueError(
                    f"Image id {image_id} does not follow the documented 30-capture scene layout"
                )
            image_annotations = tuple(
                sorted(annotations_by_image.get(image_id, []), key=lambda item: int(item["id"]))
            )
            if not image_annotations:
                raise ValueError(
                    f"Public annotated split {source_split} contains no annotation for image {image_id}"
                )
            candidates.append(Candidate(source_split, image, image_annotations))

        source_info[source_split] = {
            "annotation_file": f"annotations/{filename}",
            "annotation_sha256": sha256_file(annotation_path),
            "images": len(payload["images"]),
            "annotations": len(payload.get("annotations", [])),
            "dataset_info": payload.get("info", {}),
            "licenses": payload.get("licenses", []),
        }

    assert canonical_categories is not None
    category_list = [canonical_categories[key] for key in sorted(canonical_categories)]
    return sorted(candidates, key=lambda item: (item.source_split, item.image_id)), category_list, source_info


def build_catalog(categories: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    catalog = []
    seen_names: set[str] = set()
    for category in sorted(categories, key=lambda item: int(item["id"])):
        category_id = int(category["id"])
        name = str(category["name"])
        if name in seen_names:
            raise ValueError(f"Duplicate D2S category name: {name!r}")
        seen_names.add(name)
        catalog.append(
            {
                "id": f"d2s_{category_id:03d}",
                "name": name,
                "source_category_id": category_id,
                "supercategory": category.get("supercategory"),
            }
        )
    return catalog


def deterministic_candidate_order(
    candidates: Iterable[Candidate], seed: int
) -> list[Candidate]:
    by_scene: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in sorted(candidates, key=lambda item: (item.session_id, item.image_id)):
        by_scene[candidate.session_id].append(candidate)

    rng = random.Random(seed)
    scene_ids = sorted(by_scene)
    rng.shuffle(scene_ids)
    for scene_id in scene_ids:
        rng.shuffle(by_scene[scene_id])

    ordered: list[Candidate] = []
    max_scene_size = max((len(items) for items in by_scene.values()), default=0)
    for capture_round in range(max_scene_size):
        for scene_id in scene_ids:
            items = by_scene[scene_id]
            if capture_round < len(items):
                ordered.append(items[capture_round])
    return ordered


def compact_number(value: float) -> int | float:
    value = float(value)
    return int(value) if value.is_integer() else value


def convert_bbox(raw_bbox: Any, width: int, height: int) -> dict[str, int | float]:
    if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
        raise ValueError("COCO bbox must be [x, y, width, height]")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in raw_bbox):
        raise ValueError("COCO bbox values must be numeric")
    x, y, box_width, box_height = (float(value) for value in raw_bbox)
    if box_width <= 0 or box_height <= 0:
        raise ValueError("bbox width and height must be positive")
    if x < 0 or y < 0 or x + box_width > width or y + box_height > height:
        raise ValueError("bbox extends outside image bounds")
    return {
        "x": compact_number(x),
        "y": compact_number(y),
        "width": compact_number(box_width),
        "height": compact_number(box_height),
    }


def validate_candidate(
    candidate: Candidate,
    source_root: Path,
    categories_by_id: dict[int, dict[str, Any]],
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    image_path = source_root / "images" / str(candidate.image["file_name"])
    if not image_path.is_file():
        return False, [f"source image does not exist: {image_path}"]
    try:
        with Image.open(image_path) as image:
            actual_size = image.size
    except Exception as exc:
        return False, [f"source image cannot be opened: {exc}"]

    declared_size = (int(candidate.image["width"]), int(candidate.image["height"]))
    if actual_size != declared_size:
        errors.append(f"declared dimensions {declared_size} do not match actual {actual_size}")
    for annotation in candidate.annotations:
        category_id = int(annotation["category_id"])
        if category_id not in categories_by_id:
            errors.append(f"unknown category_id {category_id}")
        if int(annotation.get("iscrowd", 0)) != 0:
            errors.append(f"annotation {annotation.get('id')} is marked iscrowd")
        try:
            convert_bbox(annotation.get("bbox"), *actual_size)
        except ValueError as exc:
            errors.append(f"annotation {annotation.get('id')} invalid bbox: {exc}")
    return not errors, errors


def select_valid_candidates(
    candidates: list[Candidate],
    source_root: Path,
    categories_by_id: dict[int, dict[str, Any]],
    limit: int,
    seed: int,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    if limit <= 0:
        raise ValueError("--limit must be greater than zero")
    selected: list[Candidate] = []
    skipped: list[dict[str, Any]] = []
    for candidate in deterministic_candidate_order(candidates, seed):
        valid, errors = validate_candidate(candidate, source_root, categories_by_id)
        if not valid:
            skipped.append(
                {
                    "source_image": f"images/{candidate.image['file_name']}",
                    "source_split": candidate.source_split,
                    "errors": errors,
                }
            )
            continue
        selected.append(candidate)
        if len(selected) == limit:
            break
    if len(selected) < limit:
        raise ValueError(
            f"Requested {limit} samples, but only {len(selected)} valid annotated images are available"
        )
    return selected, skipped


def target_split_counts(total: int) -> dict[str, int]:
    raw = {name: total * ratio for name, ratio in SPLIT_RATIOS.items()}
    counts = {name: int(value) for name, value in raw.items()}
    remainder = total - sum(counts.values())
    order = sorted(
        SPLIT_RATIOS,
        key=lambda name: (-(raw[name] - counts[name]), list(SPLIT_RATIOS).index(name)),
    )
    for name in order[:remainder]:
        counts[name] += 1
    return counts


def assign_scene_splits(selected: list[Candidate], seed: int) -> dict[str, str]:
    by_scene: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in selected:
        by_scene[candidate.session_id].append(candidate)
    rng = random.Random(seed ^ 0xD2A5)
    randomized = list(by_scene)
    rng.shuffle(randomized)
    rank = {scene_id: index for index, scene_id in enumerate(randomized)}
    scene_ids = sorted(by_scene, key=lambda scene_id: (-len(by_scene[scene_id]), rank[scene_id]))
    targets = target_split_counts(len(selected))
    counts = {name: 0 for name in SPLIT_RATIOS}
    assignments: dict[str, str] = {}
    split_order = list(SPLIT_RATIOS)
    for scene_id in scene_ids:
        size = len(by_scene[scene_id])
        chosen = max(
            split_order,
            key=lambda name: (targets[name] - counts[name], -split_order.index(name)),
        )
        assignments[scene_id] = chosen
        counts[chosen] += size
    return assignments


def build_annotation(
    sample_id: str,
    image_relative_path: str,
    candidate: Candidate,
    categories_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    width = int(candidate.image["width"])
    height = int(candidate.image["height"])
    instances = []
    counts: Counter[str] = Counter()
    for source_annotation in candidate.annotations:
        category = categories_by_id[int(source_annotation["category_id"])]
        name = str(category["name"])
        counts[name] += 1
        instances.append(
            {
                "product_name": name,
                "bbox": convert_bbox(source_annotation["bbox"], width, height),
                "source_annotation_id": int(source_annotation["id"]),
                "source_category_id": int(source_annotation["category_id"]),
            }
        )
    products = [
        {"name": name, "quantity": quantity}
        for name, quantity in sorted(counts.items())
    ]
    return {
        "sample_id": sample_id,
        "image": image_relative_path,
        "width": width,
        "height": height,
        "data": {"products": products},
        "instances": instances,
    }


def output_conflicts(output: Path) -> list[Path]:
    conflicts = []
    manifest = output / "manifest.jsonl"
    if manifest.exists():
        conflicts.append(manifest)
    for directory, pattern in [(output / "images", "inv_*"), (output / "annotations", "inv_*.json")]:
        if not directory.exists():
            continue
        conflicts.extend(
            path for path in directory.glob(pattern)
            if path.is_file() and ".example." not in path.name
        )
    return sorted(set(conflicts))


def validate_staged_dataset(stage: Path) -> None:
    schema = read_json(stage / "schema.json")
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    catalog = read_json(stage / "catalog.json")
    allowed_names = catalog_names(catalog)
    manifest = []
    with (stage / "manifest.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                manifest.append(json.loads(line))
    errors = validate_manifest(manifest)
    for row in manifest:
        annotation_path = stage / row["annotation"]
        if not annotation_path.is_file():
            errors.append(f"{row['sample_id']}: annotation missing: {annotation_path}")
            continue
        annotation = read_json(annotation_path)
        errors.extend(
            f"{row['sample_id']}: {error}"
            for error in validate_annotation(stage, row, annotation, validator, allowed_names)
        )
    if errors:
        raise ValueError("Generated dataset validation failed:\n- " + "\n- ".join(errors))


def generate_stage(
    stage: Path,
    output: Path,
    source_root: Path,
    selected: list[Candidate],
    skipped: list[dict[str, Any]],
    categories: list[dict[str, Any]],
    source_info: dict[str, Any],
    seed: int,
    requested_limit: int,
) -> dict[str, Any]:
    stage.mkdir(parents=True)
    (stage / "images").mkdir()
    (stage / "annotations").mkdir()
    schema_source = output / "schema.json"
    if not schema_source.is_file():
        raise ValueError(f"Authoritative schema is missing: {schema_source}")
    shutil.copy2(schema_source, stage / "schema.json")

    catalog = build_catalog(categories)
    write_json(stage / "catalog.json", catalog)
    categories_by_id = {int(item["id"]): item for item in categories}
    assignments = assign_scene_splits(selected, seed)
    ordered_selected = sorted(selected, key=lambda item: (item.source_split, item.image_id))
    manifest_rows = []
    for index, candidate in enumerate(ordered_selected, start=1):
        sample_id = f"inv_{index:04d}"
        suffix = Path(str(candidate.image["file_name"])).suffix.lower() or ".jpg"
        generated_name = f"{sample_id}{suffix}"
        image_relative = f"images/{generated_name}"
        annotation_relative = f"annotations/{sample_id}.json"
        shutil.copy2(
            source_root / "images" / str(candidate.image["file_name"]),
            stage / image_relative,
        )
        annotation = build_annotation(sample_id, image_relative, candidate, categories_by_id)
        write_json(stage / annotation_relative, annotation)
        capture_index = candidate.capture_index
        manifest_rows.append(
            {
                "sample_id": sample_id,
                "source_dataset": "d2s",
                "source_image_id": candidate.image_id,
                "source_image": f"images/{candidate.image['file_name']}",
                "image": image_relative,
                "annotation": annotation_relative,
                "split": assignments[candidate.session_id],
                "source_split": candidate.source_split,
                "session_id": candidate.session_id,
                "tags": [
                    "d2s",
                    f"d2s_{candidate.source_split}",
                    f"rotation_{capture_index % 10}",
                    f"lighting_{capture_index // 10}",
                ],
            }
        )
    with (stage / "manifest.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in manifest_rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    split_counts = Counter(row["split"] for row in manifest_rows)
    source_split_counts = Counter(row["source_split"] for row in manifest_rows)
    metadata = {
        "dataset_id": "inventory-v0",
        "dataset_version": "0.1.0",
        "task": "controlled_inventory_image_extraction",
        "annotation_spec_version": "1.0.0",
        "bbox_format": "xywh",
        "coordinate_space": "original_pixels",
        "bbox_edges": "right_bottom_exclusive",
        "json_pointer_root": "data",
        "source_dataset": "MVTec D2S",
        "source_dataset_version": "1.1",
        "source_license": "CC BY-NC-SA 4.0",
        "source_license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
        "importer_version": IMPORTER_VERSION,
        "seed": seed,
        "requested_limit": requested_limit,
        "imported_samples": len(manifest_rows),
        "skipped_invalid_samples": len(skipped),
        "split_strategy": "deterministic scene-grouped 70/15/15",
        "split_counts": dict(sorted(split_counts.items())),
        "source_split_counts": dict(sorted(source_split_counts.items())),
        "category_count": len(catalog),
        "source_files": source_info,
        "skipped": skipped,
    }
    write_json(stage / "dataset_metadata.json", metadata)
    validate_staged_dataset(stage)
    return metadata


def commit_stage(stage: Path, output: Path, force: bool) -> None:
    conflicts = output_conflicts(output)
    if conflicts and not force:
        rendered = "\n".join(f"- {path}" for path in conflicts[:20])
        raise ValueError(
            "Generated output already exists. Re-run with --force to replace it:\n" + rendered
        )
    output.mkdir(parents=True, exist_ok=True)
    (output / "images").mkdir(exist_ok=True)
    (output / "annotations").mkdir(exist_ok=True)
    if force:
        for path in output_conflicts(output):
            path.unlink()
    for path in sorted((stage / "images").iterdir()):
        shutil.copy2(path, output / "images" / path.name)
    for path in sorted((stage / "annotations").iterdir()):
        shutil.copy2(path, output / "annotations" / path.name)
    for filename in ["catalog.json", "manifest.jsonl", "dataset_metadata.json"]:
        shutil.copy2(stage / filename, output / filename)


def dry_run_summary(
    source_root: Path,
    candidates: list[Candidate],
    categories: list[dict[str, Any]],
    selected: list[Candidate],
    skipped: list[dict[str, Any]],
    output: Path,
    seed: int,
) -> dict[str, Any]:
    assignments = assign_scene_splits(selected, seed)
    split_counts = Counter(assignments[item.session_id] for item in selected)
    return {
        "source": str(source_root),
        "source_images_found": len(list((source_root / "images").glob("*.jpg"))),
        "annotated_images_found": len(candidates),
        "product_classes_found": len(categories),
        "selected_sample_count": len(selected),
        "expected_split_sizes": dict(sorted(split_counts.items())),
        "skipped_invalid_samples": len(skipped),
        "output": str(output.resolve()),
    }


def import_dataset(args: argparse.Namespace) -> dict[str, Any]:
    source_root = find_source_root(args.source)
    candidates, categories, source_info = load_source(source_root)
    categories_by_id = {int(item["id"]): item for item in categories}
    selected, skipped = select_valid_candidates(
        candidates, source_root, categories_by_id, args.limit, args.seed
    )
    summary = dry_run_summary(
        source_root, candidates, categories, selected, skipped, args.output, args.seed
    )
    if args.dry_run:
        return summary
    conflicts = output_conflicts(args.output)
    if conflicts and not args.force:
        rendered = "\n".join(f"- {path}" for path in conflicts[:20])
        raise ValueError(
            "Generated output already exists. Re-run with --force to replace it:\n" + rendered
        )
    stage = args.output.parent / f".{args.output.name}-import-{uuid.uuid4().hex}"
    try:
        metadata = generate_stage(
            stage, args.output, source_root, selected, skipped, categories,
            source_info, args.seed, args.limit,
        )
        commit_stage(stage, args.output, args.force)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    summary["split_counts"] = metadata["split_counts"]
    summary["source_split_counts"] = metadata["source_split_counts"]
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import public annotated MVTec D2S images into inventory-v0."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = import_dataset(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"D2S import failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
