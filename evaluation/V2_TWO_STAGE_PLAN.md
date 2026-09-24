# Inventory v2 two-stage detector plan

## Hypothesis

The rejected multiclass detectors mix two different tasks: finding every
physical product and choosing among 60 visually similar identities. Their
internal-validation confusion matrices show both background misses and class
confusions. Training longer did not solve either problem, and the smaller model
was substantially worse.

The next candidate separates these tasks:

1. A class-agnostic YOLO model detects and localizes every product instance.
2. An EfficientNet-V2-S classifier assigns one of the 60 catalog identities to
   each detected crop.
3. Classified instances are aggregated into inventory quantities while keeping
   the localizer boxes as evidence.

This architecture is developed exclusively on the leakage-safe YOLO training
and internal-validation splits. Inventory-v2 validation and both test sets stay
sealed.

## Frozen component configurations

### Localizer

- model: `yolo11s.pt`
- one output class: `product`
- image size: 1280
- epochs: at most 20
- batch: 8 (RTX 3090-safe)
- early-stopping patience: 7
- seed: 20260924

The localizer export preserves every original box but replaces all class IDs
with zero. `prepare_yolo_localizer.py` validates all source labels and writes a
new guarded dataset without accessing an external benchmark split.

### Crop classifier

- model: ImageNet-pretrained EfficientNet-V2-S
- input size: 384 square pixels with aspect-preserving padding
- crop context margin: 5%
- epochs: at most 12
- batch: 32 (RTX 3090-safe)
- optimizer: AdamW, learning rate 0.0003, weight decay 0.05
- early-stopping metric: macro recall
- early-stopping patience: 4
- seed: 20260924

Training uses inverse-frequency sampling so every catalog class contributes
equal expected sampling weight. Color augmentation is deliberately mild because
color distinguishes several product variants.

## GPU commands

After restoring `data/yolo-inventory-v2`, verify both configurations:

```bash
bash evaluation/run_yolo_localizer_training.sh --dry-run
bash evaluation/run_crop_classifier_training.sh --dry-run
```

Train the components sequentially, writing logs to the container disk:

```bash
nohup env PYTHONUNBUFFERED=1 \
  bash evaluation/run_yolo_localizer_training.sh \
  > /root/inventory-v2-localizer.log 2>&1 &
```

After the localizer finishes:

```bash
nohup env PYTHONUNBUFFERED=1 \
  bash evaluation/run_crop_classifier_training.sh \
  > /root/inventory-v2-crop-classifier.log 2>&1 &
```

Do not access inventory-v2 validation until both components and the combined
inference policy have been selected from internal-validation evidence.
