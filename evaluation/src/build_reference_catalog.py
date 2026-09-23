from __future__ import annotations

import argparse
import hashlib
import json
import math
import textwrap
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps


BUILDER_VERSION = "0.1.0"
SOURCE_FILES = ("D2S_training.json", "D2S_validation.json")
PREFERRED_ROTATIONS = (0, 5)


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excluded_scene_ids(manifest: list[dict[str, Any]]) -> set[int]:
    scenes = set()
    for row in manifest:
        image_id = row.get("source_image_id")
        if not isinstance(image_id, int):
            raise ValueError(f"Manifest row {row.get('sample_id')!r} lacks integer source_image_id")
        scenes.add(image_id // 100)
    return scenes


def load_source(source: Path) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    images: dict[int, dict[str, Any]] = {}
    annotations: list[dict[str, Any]] = []
    for filename in SOURCE_FILES:
        path = source / "annotations" / filename
        payload = read_json(path)
        for image in payload["images"]:
            image_id = int(image["id"])
            if image_id in images:
                raise ValueError(f"Duplicate source image id: {image_id}")
            images[image_id] = image
        annotations.extend(payload.get("annotations", []))
    return images, annotations


def circular_rotation_distance(rotation: int, preferred: int) -> int:
    difference = abs(rotation - preferred)
    return min(difference, 10 - difference)


def select_references(
    images: dict[int, dict[str, Any]],
    annotations: list[dict[str, Any]],
    category_ids: list[int],
    excluded_scenes: set[int],
    references_per_product: int,
) -> dict[int, list[dict[str, Any]]]:
    candidates: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for annotation in annotations:
        if int(annotation.get("iscrowd", 0)) != 0:
            continue
        image_id = int(annotation["image_id"])
        if image_id // 100 in excluded_scenes:
            continue
        image = images.get(image_id)
        if image is None:
            continue
        bbox = [float(value) for value in annotation["bbox"]]
        if len(bbox) != 4 or bbox[2] <= 0 or bbox[3] <= 0:
            continue
        area_ratio = bbox[2] * bbox[3] / (int(image["width"]) * int(image["height"]))
        candidates[int(annotation["category_id"])].append(
            {
                "annotation": annotation,
                "image": image,
                "area_ratio": area_ratio,
                "rotation": image_id % 10,
            }
        )

    selected: dict[int, list[dict[str, Any]]] = {}
    for category_id in category_ids:
        pool = candidates.get(category_id, [])
        picks: list[dict[str, Any]] = []
        used_images: set[int] = set()
        for index in range(references_per_product):
            preferred = PREFERRED_ROTATIONS[index % len(PREFERRED_ROTATIONS)]
            available = [
                item for item in pool if int(item["image"]["id"]) not in used_images
            ]
            if not available:
                break
            choice = min(
                available,
                key=lambda item: (
                    circular_rotation_distance(item["rotation"], preferred),
                    -item["area_ratio"],
                    int(item["image"]["id"]),
                    int(item["annotation"]["id"]),
                ),
            )
            picks.append(choice)
            used_images.add(int(choice["image"]["id"]))
        if len(picks) != references_per_product:
            raise ValueError(
                f"Category {category_id} has {len(picks)} usable leak-free references; "
                f"{references_per_product} required"
            )
        selected[category_id] = picks
    return selected


def padded_crop_box(
    bbox: list[float], image_width: int, image_height: int, padding_ratio: float = 0.08
) -> tuple[int, int, int, int]:
    x, y, width, height = bbox
    padding = max(width, height) * padding_ratio
    left = max(0, math.floor(x - padding))
    top = max(0, math.floor(y - padding))
    right = min(image_width, math.ceil(x + width + padding))
    bottom = min(image_height, math.ceil(y + height + padding))
    if right <= left or bottom <= top:
        raise ValueError(f"Invalid crop generated from bbox {bbox}")
    return left, top, right, bottom


def load_font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def render_sheet(
    entries: list[dict[str, Any]],
    source: Path,
    destination: Path,
    columns: int = 4,
) -> None:
    cell_width = 400
    cell_height = 320
    crop_width = 184
    crop_height = 220
    rows = math.ceil(len(entries) / columns)
    sheet = Image.new("RGB", (columns * cell_width, rows * cell_height), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = load_font(24)
    label_font = load_font(19)

    for index, entry in enumerate(entries):
        column = index % columns
        row = index // columns
        origin_x = column * cell_width
        origin_y = row * cell_height
        draw.rectangle(
            (origin_x, origin_y, origin_x + cell_width - 1, origin_y + cell_height - 1),
            outline="#b8b8b8",
            width=2,
        )
        draw.text((origin_x + 10, origin_y + 8), entry["catalog_id"], fill="black", font=title_font)
        for crop_index, reference in enumerate(entry["references"]):
            source_path = source / "images" / reference["source_filename"]
            with Image.open(source_path) as original:
                original = ImageOps.exif_transpose(original).convert("RGB")
                crop = original.crop(tuple(reference["crop_xyxy"]))
                fitted = ImageOps.contain(crop, (crop_width, crop_height))
            paste_x = origin_x + 8 + crop_index * (crop_width + 8)
            paste_y = origin_y + 46 + (crop_height - fitted.height) // 2
            sheet.paste(fitted, (paste_x + (crop_width - fitted.width) // 2, paste_y))

        lines = textwrap.wrap(entry["name"].replace("_", " "), width=34)[:2]
        draw.multiline_text(
            (origin_x + 10, origin_y + 272),
            "\n".join(lines),
            fill="black",
            font=label_font,
            spacing=2,
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(destination, format="JPEG", quality=92, optimize=True)


def build(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve()
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    if args.references_per_product <= 0:
        raise ValueError("--references-per-product must be greater than zero")
    if args.products_per_sheet <= 0:
        raise ValueError("--products-per-sheet must be greater than zero")

    manifest = read_jsonl(dataset / "manifest.jsonl")
    catalog = read_json(dataset / "catalog.json")
    excluded = excluded_scene_ids(manifest)
    images, annotations = load_source(source)
    category_ids = [int(item["source_category_id"]) for item in catalog]
    selected = select_references(
        images, annotations, category_ids, excluded, args.references_per_product
    )

    entries = []
    for item in catalog:
        category_id = int(item["source_category_id"])
        references = []
        for selected_item in selected[category_id]:
            image = selected_item["image"]
            annotation = selected_item["annotation"]
            image_id = int(image["id"])
            source_path = source / "images" / str(image["file_name"])
            if not source_path.is_file():
                raise ValueError(f"Missing source image: {source_path}")
            crop = padded_crop_box(
                [float(value) for value in annotation["bbox"]],
                int(image["width"]),
                int(image["height"]),
            )
            references.append(
                {
                    "source_image_id": image_id,
                    "source_scene_id": image_id // 100,
                    "source_filename": str(image["file_name"]),
                    "source_annotation_id": int(annotation["id"]),
                    "rotation": image_id % 10,
                    "crop_xyxy": list(crop),
                }
            )
        entries.append(
            {
                "catalog_id": item["id"],
                "name": item["name"],
                "source_category_id": category_id,
                "references": references,
            }
        )

    sheets = []
    for sheet_index, start in enumerate(range(0, len(entries), args.products_per_sheet), 1):
        sheet_entries = entries[start : start + args.products_per_sheet]
        filename = f"reference-sheet-{sheet_index:02d}.jpg"
        destination = output / filename
        render_sheet(sheet_entries, source, destination)
        sheets.append(
            {
                "path": filename,
                "sha256": sha256_file(destination),
                "catalog_ids": [entry["catalog_id"] for entry in sheet_entries],
                "product_names": [entry["name"] for entry in sheet_entries],
            }
        )

    manifest_output = {
        "reference_catalog_version": "0.1.0",
        "builder_version": BUILDER_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_dataset": "MVTec D2S 1.1",
        "source_license": "CC BY-NC-SA 4.0",
        "dataset": str(dataset),
        "source": str(source),
        "leakage_policy": "All scenes present in the benchmark manifest are excluded.",
        "excluded_source_scene_ids": sorted(excluded),
        "references_per_product": args.references_per_product,
        "product_count": len(entries),
        "sheets": sheets,
        "entries": entries,
    }
    write_json(output / "reference-manifest.json", manifest_output)
    return {
        "products": len(entries),
        "references": sum(len(entry["references"]) for entry in entries),
        "sheets": len(sheets),
        "output": str(output),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a leak-free D2S visual reference catalog.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--references-per-product", type=int, default=2)
    parser.add_argument("--products-per-sheet", type=int, default=15)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        summary = build(parse_args(argv))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Reference catalog build failed: {exc}")
        return 1
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
