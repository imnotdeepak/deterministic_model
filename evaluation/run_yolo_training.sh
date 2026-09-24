#!/usr/bin/env bash
set -euo pipefail

device="0"
model="yolo11s.pt"
epochs=100
image_size=960
batch=8
workers=4
run_name="inventory-yolo-v1"
dry_run=false

usage() {
  cat <<'EOF'
Usage: ./evaluation/run_yolo_training.sh [options]

Options:
  --device VALUE       CUDA device (default: 0)
  --model PATH         Model checkpoint (default: yolo11s.pt)
  --epochs NUMBER      Training epochs (default: 100)
  --image-size NUMBER  Input image size (default: 960)
  --batch NUMBER       Batch size (default: 8)
  --workers NUMBER     Data-loader workers (default: 4)
  --name VALUE         Output run name (default: inventory-yolo-v1)
  --dry-run            Validate configuration without training
  -h, --help           Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) device="$2"; shift 2 ;;
    --model) model="$2"; shift 2 ;;
    --epochs) epochs="$2"; shift 2 ;;
    --image-size) image_size="$2"; shift 2 ;;
    --batch) batch="$2"; shift 2 ;;
    --workers) workers="$2"; shift 2 ;;
    --name) run_name="$2"; shift 2 ;;
    --dry-run) dry_run=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

data="data/yolo-inventory-v1/dataset.yaml"
metadata="data/yolo-inventory-v1/export-metadata.json"

[[ -f "$data" ]] || { echo "YOLO dataset is missing: $data" >&2; exit 1; }
[[ -f "$metadata" ]] || { echo "YOLO export metadata is missing: $metadata" >&2; exit 1; }

python - "$metadata" <<'PY'
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if metadata["protected_scene_count"] != 35:
    raise SystemExit("Expected exactly 35 protected validation/test scenes.")
for split in ("train", "val"):
    if metadata["splits"][split]["categories"] != 60:
        raise SystemExit(f"Expected all 60 categories in the {split} split.")
print(json.dumps({
    "protected_scene_count": metadata["protected_scene_count"],
    "train_images": metadata["splits"]["train"]["images"],
    "validation_images": metadata["splits"]["val"]["images"],
}, indent=2))
PY

python - <<'PY'
import json
import torch
print(json.dumps({
    "torch_version": torch.__version__,
    "cuda_available": torch.cuda.is_available(),
    "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
}, indent=2))
PY

if [[ "$dry_run" == true ]]; then
  echo "YOLO training dry run complete. No training was started."
  exit 0
fi

python evaluation/src/train_yolo_detector.py \
  --data "$data" \
  --model "$model" \
  --epochs "$epochs" \
  --imgsz "$image_size" \
  --batch "$batch" \
  --device "$device" \
  --workers "$workers" \
  --name "$run_name"
