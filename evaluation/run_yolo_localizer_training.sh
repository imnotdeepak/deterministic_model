#!/usr/bin/env bash
set -euo pipefail

source_data="data/yolo-inventory-v2"
localizer_data="data/yolo-inventory-v2-localizer"
run_name="inventory-yolo-v2-localizer-small-1280"
dry_run=false

if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
elif [[ $# -ne 0 ]]; then
  echo "Usage: $0 [--dry-run]" >&2
  exit 2
fi

[[ -f "$source_data/dataset.yaml" ]] || {
  echo "Source dataset is missing: $source_data" >&2
  exit 1
}

if [[ ! -f "$localizer_data/dataset.yaml" ]]; then
  python evaluation/src/prepare_yolo_localizer.py \
    --source "$source_data" --output "$localizer_data"
fi

python - "$localizer_data/export-metadata.json" <<'PY'
import json
import sys
from pathlib import Path

metadata = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
expected = {
    "protected_scene_count": 65,
    "source_category_count": 60,
    "category_count": 1,
}
for key, value in expected.items():
    if metadata.get(key) != value:
        raise SystemExit(f"Expected {key}={value}, got {metadata.get(key)}")
if metadata["splits"]["train"] != {"images": 5130, "instances": 10770}:
    raise SystemExit("Unexpected class-agnostic training split")
if metadata["splits"]["val"] != {"images": 900, "instances": 3480}:
    raise SystemExit("Unexpected class-agnostic internal-validation split")
print(json.dumps(metadata["splits"], indent=2))
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
  echo "Class-agnostic localizer dry run complete. No training was started."
  exit 0
fi

python evaluation/src/train_yolo_detector.py \
  --data "$localizer_data/dataset.yaml" \
  --model yolo11s.pt --epochs 20 --imgsz 1280 --batch 8 \
  --device 0 --workers 4 --patience 7 --seed 20260924 \
  --name "$run_name"
