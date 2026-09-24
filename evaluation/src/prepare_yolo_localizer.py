from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import yaml


PREPARER_VERSION = "0.1.0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_names(dataset_yaml: Path) -> list[str]:
    document = yaml.safe_load(dataset_yaml.read_text(encoding="utf-8"))
    raw = document.get("names")
    if isinstance(raw, dict):
        names = [str(raw[index]) for index in sorted(raw)]
    elif isinstance(raw, list):
        names = [str(value) for value in raw]
    else:
        raise ValueError("Dataset YAML lacks an ordered names mapping")
    if not names or len(names) != len(set(names)):
        raise ValueError("Dataset class names must be non-empty and unique")
    return names


def localizer_lines(text: str, class_count: int) -> list[str]:
    output: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"line {line_number}: expected five YOLO fields")
        class_id = int(parts[0])
        if not 0 <= class_id < class_count:
            raise ValueError(f"line {line_number}: class id {class_id} is out of range")
        coordinates = [float(value) for value in parts[1:]]
        if any(not 0.0 <= value <= 1.0 for value in coordinates):
            raise ValueError(f"line {line_number}: normalized coordinate is out of range")
        output.append("0 " + " ".join(parts[1:]))
    return output


def materialize_image(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def prepare(source: Path, output: Path) -> dict[str, Any]:
    source = source.resolve()
    output = output.resolve()
    if output.exists():
        raise ValueError(f"Output already exists: {output}")
    dataset_yaml = source / "dataset.yaml"
    export_metadata = source / "export-metadata.json"
    if not dataset_yaml.is_file() or not export_metadata.is_file():
        raise ValueError("Source is not a complete YOLO export")
    names = read_names(dataset_yaml)
    source_metadata = json.loads(export_metadata.read_text(encoding="utf-8"))

    stage = output
    counts: dict[str, dict[str, int]] = {}
    try:
        stage.mkdir(parents=True)
        for split in ("train", "val"):
            image_dir = source / "images" / split
            label_dir = source / "labels" / split
            if not image_dir.is_dir() or not label_dir.is_dir():
                raise ValueError(f"Source split is incomplete: {split}")
            images = sorted(path for path in image_dir.iterdir() if path.is_file())
            instance_count = 0
            for image_path in images:
                label_path = label_dir / f"{image_path.stem}.txt"
                if not label_path.is_file():
                    raise ValueError(f"Missing label for {image_path}")
                lines = localizer_lines(label_path.read_text(encoding="utf-8"), len(names))
                if not lines:
                    raise ValueError(f"Image has no labeled products: {image_path}")
                materialize_image(image_path, stage / "images" / split / image_path.name)
                target_label = stage / "labels" / split / label_path.name
                target_label.parent.mkdir(parents=True, exist_ok=True)
                target_label.write_text("\n".join(lines) + "\n", encoding="utf-8")
                instance_count += len(lines)
            counts[split] = {"images": len(images), "instances": instance_count}

        (stage / "dataset.yaml").write_text(
            'train: images/train\nval: images/val\nnames:\n  0: "product"\n',
            encoding="utf-8",
        )
        metadata = {
            "preparer_version": PREPARER_VERSION,
            "task": "class_agnostic_product_localization",
            "source": str(source),
            "source_dataset_yaml_sha256": sha256_file(dataset_yaml),
            "source_export_metadata_sha256": sha256_file(export_metadata),
            "protected_scene_count": source_metadata.get("protected_scene_count"),
            "source_category_count": len(names),
            "category_count": 1,
            "splits": counts,
            "output": str(output),
        }
        (stage / "export-metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        return metadata
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert a multiclass YOLO export to class-agnostic localization."
    )
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        result = prepare(**vars(parse_args()))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Localizer preparation failed: {exc}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
