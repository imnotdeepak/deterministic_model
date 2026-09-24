# Inventory v2 plan

## Objective

Train a stronger supervised detector without tuning against the observed
inventory-v1 validation labels. Both inventory-v1's 46-image test split and
inventory-v2's 45-image test split remain sealed.

## Fresh benchmark

`datasets/inventory-v2` was reserved and locked before v2 training. Its 300
images come from 100 D2S scenes that do not occur anywhere in inventory-v1 or
the inventory-v0 reference catalog.

- development: 210 images / 70 scenes
- validation: 45 images / 15 scenes
- test: 45 images / 15 scenes
- immutable content SHA-256:
  `7e8bb2fd9a6a18a123d03de0eda2a03bdfc1e01ce6506d0d562beb4693fdbbf3`

The development scenes are declared training material for v2 and are not used
as held-out evaluation. The v2 validation and test scenes are excluded from
training. Inventory-v1 validation and test scenes remain excluded as well.

## Leakage-safe training export

The v2 YOLO export protects 65 scenes in total: 35 inventory-v1 validation/test
scenes and 30 inventory-v2 validation/test scenes. Its internal split contains:

- train: 5,130 images / 171 scenes / all 60 classes
- internal validation: 900 images / 30 scenes / 59 classes

The one class absent from internal validation occurs in only one eligible
scene, so that scene stays in training to preserve complete training coverage.
External inventory-v2 validation remains the generalization gate.

## Predeclared candidate

Train `yolo11m.pt` for at most 30 epochs at image size 1280, batch size 8,
patience 10, and seed 20260924. Ultralytics selects `best.pt` using only the
internal model-validation loss and metrics. This configuration is fixed before
inventory-v2 validation is accessed.

Run the GPU dry run, then training:

```bash
bash evaluation/run_yolo_training.sh \
  --data data/yolo-inventory-v2/dataset.yaml \
  --model yolo11m.pt --epochs 30 --image-size 1280 \
  --batch 8 --patience 10 --name inventory-yolo-v2-medium-1280 \
  --expected-protected-scenes 65 --dry-run

nohup env PYTHONUNBUFFERED=1 bash evaluation/run_yolo_training.sh \
  --data data/yolo-inventory-v2/dataset.yaml \
  --model yolo11m.pt --epochs 30 --image-size 1280 \
  --batch 8 --patience 10 --name inventory-yolo-v2-medium-1280 \
  --expected-protected-scenes 65 > /root/inventory-yolo-v2-medium-1280.log 2>&1 &
```

Do not run inventory-v2 validation until the selected checkpoint and inference
configuration have been frozen from training/internal-validation evidence.
