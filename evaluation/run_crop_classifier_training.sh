#!/usr/bin/env bash
set -euo pipefail

data="data/yolo-inventory-v2"
output="evaluation/training-runs/inventory-v2-crop-classifier-efficientnetv2s-staged"
dry_run=false

export CUBLAS_WORKSPACE_CONFIG=:4096:8

if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
elif [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--dry-run]" >&2
  exit 2
fi

[[ -f "$data/dataset.yaml" ]] || {
  echo "Classifier dataset is missing: $data" >&2
  exit 1
}

python - "$data/export-metadata.json" <<'PY'
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if metadata.get("protected_scene_count") != 65:
    raise SystemExit("Expected exactly 65 protected external-benchmark scenes.")
if metadata["splits"]["train"] != {
    "images": 5130, "instances": 10770, "scenes": 171, "categories": 60
}:
    raise SystemExit("Unexpected classifier training split")
if metadata["splits"]["val"] != {
    "images": 900, "instances": 3480, "scenes": 30, "categories": 59
}:
    raise SystemExit("Unexpected classifier internal-validation split")
PY

arguments=(
  --data "$data"
  --output "$output"
  --epochs 12
  --patience 6
  --batch 32
  --image-size 384
  --margin 0.05
  --learning-rate 0.0003
  --backbone-learning-rate 0.00001
  --freeze-backbone-epochs 2
  --weight-decay 0.05
  --device cuda
  --workers 8
  --seed 20260924
)

if [[ "$dry_run" == true ]]; then
  python evaluation/src/train_crop_classifier.py "${arguments[@]}" --dry-run
  echo "Crop-classifier dry run complete. No training was started."
  exit 0
fi

python evaluation/src/train_crop_classifier.py "${arguments[@]}"
