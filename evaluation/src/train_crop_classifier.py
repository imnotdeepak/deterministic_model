from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


@dataclass(frozen=True)
class CropRecord:
    image: Path
    class_id: int
    box: tuple[float, float, float, float]


def read_names(dataset_yaml: Path) -> list[str]:
    import yaml

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


def load_records(dataset: Path, split: str, class_count: int) -> list[CropRecord]:
    image_dir = dataset / "images" / split
    label_dir = dataset / "labels" / split
    records: list[CropRecord] = []
    for image in sorted(path for path in image_dir.iterdir() if path.is_file()):
        label = label_dir / f"{image.stem}.txt"
        if not label.is_file():
            raise ValueError(f"Missing label for {image}")
        for line_number, line in enumerate(label.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) != 5:
                raise ValueError(f"{label}:{line_number}: expected five fields")
            class_id = int(parts[0])
            if not 0 <= class_id < class_count:
                raise ValueError(f"{label}:{line_number}: class id out of range")
            box = tuple(float(value) for value in parts[1:])
            if any(not 0.0 <= value <= 1.0 for value in box) or box[2] <= 0 or box[3] <= 0:
                raise ValueError(f"{label}:{line_number}: invalid normalized box")
            records.append(CropRecord(image, class_id, box))
    if not records:
        raise ValueError(f"No crop records found for split {split!r}")
    return records


def crop_bounds(
    box: tuple[float, float, float, float],
    width: int,
    height: int,
    margin: float,
) -> tuple[int, int, int, int]:
    center_x, center_y, box_width, box_height = box
    expanded_width = box_width * width * (1.0 + 2.0 * margin)
    expanded_height = box_height * height * (1.0 + 2.0 * margin)
    left = max(0, math.floor(center_x * width - expanded_width / 2.0))
    top = max(0, math.floor(center_y * height - expanded_height / 2.0))
    right = min(width, math.ceil(center_x * width + expanded_width / 2.0))
    bottom = min(height, math.ceil(center_y * height + expanded_height / 2.0))
    if right <= left or bottom <= top:
        raise ValueError("Crop collapsed after conversion to pixels")
    return left, top, right, bottom


def pad_square(image: Image.Image) -> Image.Image:
    width, height = image.size
    side = max(width, height)
    horizontal = side - width
    vertical = side - height
    return ImageOps.expand(
        image,
        border=(horizontal // 2, vertical // 2, horizontal - horizontal // 2, vertical - vertical // 2),
        fill=(114, 114, 114),
    )


class ProductCropDataset:
    def __init__(self, records: list[CropRecord], transform: Any, margin: float = 0.05):
        self.records = records
        self.transform = transform
        self.margin = margin

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, index: int):
        record = self.records[index]
        with Image.open(record.image) as source:
            image = source.convert("RGB")
        crop = image.crop(crop_bounds(record.box, *image.size, self.margin))
        return self.transform(pad_square(crop)), record.class_id


def class_sample_weights(records: list[CropRecord]) -> list[float]:
    counts = Counter(record.class_id for record in records)
    return [1.0 / counts[record.class_id] for record in records]


def build_transforms(image_size: int):
    from torchvision import transforms
    from torchvision.transforms import InterpolationMode

    normalize = transforms.Normalize(
        mean=(0.485, 0.456, 0.406), std=(0.229, 0.224, 0.225)
    )
    train = transforms.Compose(
        [
            transforms.Resize((image_size, image_size), InterpolationMode.BICUBIC),
            transforms.RandomHorizontalFlip(),
            transforms.RandomRotation(5, interpolation=InterpolationMode.BILINEAR),
            transforms.ColorJitter(brightness=0.1, contrast=0.1, saturation=0.1, hue=0.01),
            transforms.ToTensor(),
            normalize,
        ]
    )
    validation = transforms.Compose(
        [
            transforms.Resize((image_size, image_size), InterpolationMode.BICUBIC),
            transforms.ToTensor(),
            normalize,
        ]
    )
    return train, validation


def evaluate_model(model, loader, device, class_count: int) -> dict[str, float]:
    import torch

    model.eval()
    correct = 0
    top3_correct = 0
    total = 0
    class_correct = [0] * class_count
    class_total = [0] * class_count
    loss_sum = 0.0
    criterion = torch.nn.CrossEntropyLoss()
    with torch.inference_mode():
        for images, labels in loader:
            images = images.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            logits = model(images)
            loss_sum += float(criterion(logits, labels)) * labels.numel()
            predictions = logits.argmax(dim=1)
            correct += int((predictions == labels).sum())
            top3_correct += int(
                (logits.topk(min(3, class_count), dim=1).indices == labels[:, None])
                .any(dim=1)
                .sum()
            )
            total += labels.numel()
            for label, matched in zip(labels.tolist(), (predictions == labels).tolist()):
                class_total[label] += 1
                class_correct[label] += int(matched)
    present = [index for index, count in enumerate(class_total) if count]
    return {
        "loss": loss_sum / total,
        "top1_accuracy": correct / total,
        "top3_accuracy": top3_correct / total,
        "macro_recall": sum(class_correct[i] / class_total[i] for i in present) / len(present),
        "present_classes": len(present),
    }


def train(args: argparse.Namespace) -> dict[str, Any]:
    import torch
    from torch.utils.data import DataLoader, WeightedRandomSampler
    from torchvision.models import EfficientNet_V2_S_Weights, efficientnet_v2_s

    dataset = args.data.resolve()
    names = read_names(dataset / "dataset.yaml")
    train_records = load_records(dataset, "train", len(names))
    validation_records = load_records(dataset, "val", len(names))
    summary = {
        "classes": len(names),
        "train_crops": len(train_records),
        "validation_crops": len(validation_records),
        "train_classes": len({record.class_id for record in train_records}),
        "validation_classes": len({record.class_id for record in validation_records}),
    }
    if summary["train_classes"] != len(names):
        raise ValueError("Training crops do not cover every class")
    if args.dry_run:
        print(json.dumps({"dry_run": True, **summary}, indent=2))
        return summary
    if args.output.exists():
        raise ValueError(f"Output already exists: {args.output}")
    if args.device != "cpu" and not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable; use --device cpu or a CUDA machine")

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    torch.use_deterministic_algorithms(True, warn_only=True)
    device = torch.device(args.device)
    train_transform, validation_transform = build_transforms(args.image_size)
    train_dataset = ProductCropDataset(train_records, train_transform, args.margin)
    validation_dataset = ProductCropDataset(
        validation_records, validation_transform, args.margin
    )
    generator = torch.Generator().manual_seed(args.seed)
    sampler = WeightedRandomSampler(
        class_sample_weights(train_records),
        num_samples=len(train_records),
        replacement=True,
        generator=generator,
    )
    train_loader = DataLoader(
        train_dataset,
        batch_size=args.batch,
        sampler=sampler,
        num_workers=args.workers,
        pin_memory=device.type == "cuda",
        persistent_workers=args.workers > 0,
    )
    validation_loader = DataLoader(
        validation_dataset,
        batch_size=args.batch,
        shuffle=False,
        num_workers=args.workers,
        pin_memory=device.type == "cuda",
        persistent_workers=args.workers > 0,
    )

    model = efficientnet_v2_s(weights=EfficientNet_V2_S_Weights.DEFAULT)
    model.classifier[1] = torch.nn.Linear(model.classifier[1].in_features, len(names))
    model.to(device)
    criterion = torch.nn.CrossEntropyLoss(label_smoothing=0.05)
    for parameter in model.features.parameters():
        parameter.requires_grad = False
    optimizer = torch.optim.AdamW(
        [
            {"params": model.features.parameters(), "lr": args.backbone_learning_rate},
            {"params": model.classifier.parameters(), "lr": args.learning_rate},
        ],
        weight_decay=args.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs)
    scaler = torch.amp.GradScaler("cuda", enabled=device.type == "cuda")
    args.output.mkdir(parents=True)
    history: list[dict[str, float | int]] = []
    best_score = -1.0
    stale_epochs = 0

    for epoch in range(1, args.epochs + 1):
        if epoch == args.freeze_backbone_epochs + 1:
            for parameter in model.features.parameters():
                parameter.requires_grad = True
        model.train()
        if epoch <= args.freeze_backbone_epochs:
            model.features.eval()
        train_loss = 0.0
        seen = 0
        for images, labels in train_loader:
            images = images.to(device, non_blocking=True)
            labels = labels.to(device, non_blocking=True)
            optimizer.zero_grad(set_to_none=True)
            with torch.amp.autocast("cuda", enabled=device.type == "cuda"):
                logits = model(images)
                loss = criterion(logits, labels)
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
            train_loss += float(loss.detach()) * labels.numel()
            seen += labels.numel()
        scheduler.step()
        metrics = evaluate_model(model, validation_loader, device, len(names))
        row = {
            "epoch": epoch,
            "train_loss": train_loss / seen,
            "backbone_learning_rate": optimizer.param_groups[0]["lr"],
            "head_learning_rate": optimizer.param_groups[1]["lr"],
            "backbone_frozen": epoch <= args.freeze_backbone_epochs,
            **metrics,
        }
        history.append(row)
        print(json.dumps(row))
        score = float(metrics["macro_recall"])
        if score > best_score:
            best_score = score
            stale_epochs = 0
            torch.save(
                {
                    "architecture": "efficientnet_v2_s",
                    "model_state_dict": model.state_dict(),
                    "class_names": names,
                    "image_size": args.image_size,
                    "margin": args.margin,
                    "epoch": epoch,
                    "metrics": metrics,
                },
                args.output / "best.pt",
            )
        else:
            stale_epochs += 1
        if stale_epochs >= args.patience:
            print(f"Early stopping after {epoch} epochs; best macro recall={best_score:.6f}")
            break

    with (args.output / "history.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(history[0]))
        writer.writeheader()
        writer.writerows(history)
    run_summary = {
        **summary,
        "architecture": "efficientnet_v2_s",
        "epochs_completed": len(history),
        "best_macro_recall": best_score,
        "best_checkpoint": str((args.output / "best.pt").resolve()),
    }
    (args.output / "run-summary.json").write_text(
        json.dumps(run_summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(run_summary, indent=2))
    return run_summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the product crop classifier.")
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--patience", type=int, default=4)
    parser.add_argument("--batch", type=int, default=64)
    parser.add_argument("--image-size", type=int, default=384)
    parser.add_argument("--margin", type=float, default=0.05)
    parser.add_argument("--learning-rate", type=float, default=3e-4)
    parser.add_argument("--backbone-learning-rate", type=float, default=1e-5)
    parser.add_argument("--freeze-backbone-epochs", type=int, default=2)
    parser.add_argument("--weight-decay", type=float, default=0.05)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260924)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.epochs <= 0 or args.patience <= 0 or args.batch <= 0 or args.image_size <= 0:
        parser.error("epochs, patience, batch, and image size must be positive")
    if not 0 <= args.freeze_backbone_epochs < args.epochs:
        parser.error("freeze-backbone-epochs must be non-negative and less than epochs")
    if not 0.0 <= args.margin <= 0.5:
        parser.error("margin must be between 0 and 0.5")
    return args


def main() -> int:
    try:
        train(parse_args())
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"Crop-classifier training failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
