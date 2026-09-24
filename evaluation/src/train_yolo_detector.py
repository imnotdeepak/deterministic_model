from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch
from ultralytics import YOLO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train the inventory YOLO detector.")
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--model", default="yolo11s.pt")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=960)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="0")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--patience", type=int, default=20)
    parser.add_argument("--seed", type=int, default=104729)
    parser.add_argument("--project", type=Path, default=Path("evaluation/training-runs"))
    parser.add_argument("--name", default="inventory-yolo-v1")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.device != "cpu" and not torch.cuda.is_available():
        raise SystemExit(
            "CUDA is not available in this environment. Run on the GPU machine or pass --device cpu."
        )
    model = YOLO(args.model)
    results = model.train(
        data=str(args.data.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=args.device,
        workers=args.workers,
        patience=args.patience,
        seed=args.seed,
        deterministic=True,
        project=str(args.project.resolve()),
        name=args.name,
        exist_ok=False,
    )
    print(json.dumps({"save_dir": str(results.save_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
