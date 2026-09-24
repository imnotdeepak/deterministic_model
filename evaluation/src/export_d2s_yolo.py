from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    from .import_d2s import Candidate, find_source_root, load_source
except ImportError:
    from import_d2s import Candidate, find_source_root, load_source


EXPORTER_VERSION = "0.1.0"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protected_sessions(locked_dataset: Path) -> set[str]:
    rows = read_jsonl(locked_dataset / "manifest.jsonl")
    protected = {
        str(row["session_id"])
        for row in rows
        if row.get("split") in {"validation", "test"}
    }
    if not protected:
        raise ValueError("Locked dataset has no validation/test sessions to protect")
    return protected


def scene_categories(candidates: list[Candidate]) -> dict[str, set[int]]:
    result: dict[str, set[int]] = defaultdict(set)
    for candidate in candidates:
        result[candidate.session_id].update(
            int(annotation["category_id"]) for annotation in candidate.annotations
        )
    return dict(result)


def assign_training_splits(
    candidates: list[Candidate], seed: int, validation_fraction: float
) -> dict[str, str]:
    if not 0 < validation_fraction < 0.5:
        raise ValueError("validation_fraction must be greater than 0 and less than 0.5")
    categories_by_scene = scene_categories(candidates)
    scene_ids = sorted(categories_by_scene)
    if len(scene_ids) < 2:
        raise ValueError("At least two eligible scenes are required")

    category_scene_counts = Counter(
        category_id
        for categories in categories_by_scene.values()
        for category_id in categories
    )
    target = max(1, round(len(scene_ids) * validation_fraction))
    rng = random.Random(seed)
    shuffled = list(scene_ids)
    rng.shuffle(shuffled)
    rank = {scene_id: index for index, scene_id in enumerate(shuffled)}
    remaining = set(scene_ids)
    validation: set[str] = set()
    validation_categories: set[int] = set()

    while len(validation) < target:
        feasible = [
            scene_id
            for scene_id in remaining
            if all(
                category_scene_counts[category_id] > 1
                for category_id in categories_by_scene[scene_id]
            )
        ]
        if not feasible:
            break

        def score(scene_id: str) -> tuple[float, int, int]:
            new_categories = categories_by_scene[scene_id] - validation_categories
            rarity = sum(
                1.0 / category_scene_counts[category_id]
                for category_id in new_categories
            )
            return rarity, len(new_categories), -rank[scene_id]

        chosen = max(feasible, key=score)
        validation.add(chosen)
        remaining.remove(chosen)
        validation_categories.update(categories_by_scene[chosen])
        for category_id in categories_by_scene[chosen]:
            category_scene_counts[category_id] -= 1

    if len(validation) != target:
        raise ValueError(
            f"Could select only {len(validation)} of {target} model-validation scenes "
            "without removing a category from training"
        )
    return {
        scene_id: "val" if scene_id in validation else "train"
        for scene_id in scene_ids
    }


def yolo_lines(
    candidate: Candidate, category_index: dict[int, int]
) -> list[str]:
    width = float(candidate.image["width"])
    height = float(candidate.image["height"])
    lines: list[str] = []
    for annotation in candidate.annotations:
        x, y, box_width, box_height = (float(value) for value in annotation["bbox"])
        if box_width <= 0 or box_height <= 0:
            raise ValueError(f"Invalid bbox in annotation {annotation.get('id')}")
        if x < 0 or y < 0 or x + box_width > width or y + box_height > height:
            raise ValueError(f"Out-of-bounds bbox in annotation {annotation.get('id')}")
        values = (
            category_index[int(annotation["category_id"])],
            (x + box_width / 2) / width,
            (y + box_height / 2) / height,
            box_width / width,
            box_height / height,
        )
        lines.append(f"{values[0]} {values[1]:.8f} {values[2]:.8f} {values[3]:.8f} {values[4]:.8f}")
    return lines


def materialize_image(source: Path, destination: Path, link_mode: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if link_mode == "copy":
        shutil.copy2(source, destination)
        return
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def export(args: argparse.Namespace) -> dict[str, Any]:
    source_root = find_source_root(args.source)
    locked_dataset = args.locked_dataset.resolve()
    output = args.output.resolve()
    if output.exists() and not args.dry_run:
        raise ValueError(f"Output already exists: {output}")

    candidates, categories, source_info = load_source(source_root)
    protected = protected_sessions(locked_dataset)
    eligible = [candidate for candidate in candidates if candidate.session_id not in protected]
    if not eligible:
        raise ValueError("No source images remain after protected-scene exclusion")
    assignments = assign_training_splits(eligible, args.seed, args.validation_fraction)
    category_ids = sorted(int(category["id"]) for category in categories)
    category_index = {category_id: index for index, category_id in enumerate(category_ids)}
    names_by_id = {int(category["id"]): str(category["name"]) for category in categories}

    split_images: Counter[str] = Counter()
    split_instances: Counter[str] = Counter()
    split_scenes: dict[str, set[str]] = defaultdict(set)
    split_class_instances: dict[str, Counter[int]] = defaultdict(Counter)
    for candidate in eligible:
        split = assignments[candidate.session_id]
        split_images[split] += 1
        split_instances[split] += len(candidate.annotations)
        split_scenes[split].add(candidate.session_id)
        split_class_instances[split].update(
            int(annotation["category_id"]) for annotation in candidate.annotations
        )

    missing_train = [
        names_by_id[category_id]
        for category_id in category_ids
        if split_class_instances["train"][category_id] == 0
    ]
    if missing_train:
        raise ValueError(f"Training split lacks categories: {missing_train}")

    summary = {
        "exporter_version": EXPORTER_VERSION,
        "source": str(source_root),
        "locked_dataset": str(locked_dataset),
        "locked_manifest_sha256": sha256_file(locked_dataset / "manifest.jsonl"),
        "protected_splits": ["validation", "test"],
        "protected_scene_count": len(protected),
        "seed": args.seed,
        "model_validation_fraction": args.validation_fraction,
        "category_count": len(category_ids),
        "splits": {
            split: {
                "images": split_images[split],
                "instances": split_instances[split],
                "scenes": len(split_scenes[split]),
                "categories": sum(
                    1 for count in split_class_instances[split].values() if count > 0
                ),
            }
            for split in ("train", "val")
        },
        "source_info": source_info,
        "output": str(output),
    }
    if args.dry_run:
        return {"dry_run": True, **summary}

    for candidate in eligible:
        split = assignments[candidate.session_id]
        filename = str(candidate.image["file_name"])
        source_image = source_root / "images" / filename
        materialize_image(source_image, output / "images" / split / filename, args.link_mode)
        label_path = output / "labels" / split / f"{Path(filename).stem}.txt"
        label_path.parent.mkdir(parents=True, exist_ok=True)
        label_path.write_text("\n".join(yolo_lines(candidate, category_index)) + "\n", encoding="utf-8")

    yaml_names = "\n".join(
        f"  {category_index[category_id]}: {json.dumps(names_by_id[category_id])}"
        for category_id in category_ids
    )
    (output / "dataset.yaml").write_text(
        "train: images/train\n"
        "val: images/val\n"
        "names:\n"
        f"{yaml_names}\n",
        encoding="utf-8",
    )
    (output / "export-metadata.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a leakage-safe D2S YOLO dataset.")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--locked-dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=104729)
    parser.add_argument("--validation-fraction", type=float, default=0.15)
    parser.add_argument("--link-mode", choices=["hardlink", "copy"], default="hardlink")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    try:
        result = export(parse_args())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"YOLO export failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
