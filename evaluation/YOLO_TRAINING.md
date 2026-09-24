# Leakage-safe YOLO training

## Why this path

The v4-v6 API experiments show a stable fine-grained detection limit: uncertain
views are either omitted or assigned to the wrong catalog class. D2S provides
bounding boxes for supervised detector training, which directly matches the
inventory task and removes per-image model API cost.

The generated detector dataset excludes every scene in the locked
`inventory-v1` validation and test splits.

## Export

From the repository root:

```powershell
py -3.11 evaluation\src\export_d2s_yolo.py `
  --source data `
  --locked-dataset evaluation\datasets\inventory-v1 `
  --output data\yolo-inventory-v1
```

The current deterministic export contains:

- training: 5,880 images, 14,340 instances, 196 scenes, 60 classes;
- internal model validation: 1,050 images, 4,374 instances, 35 scenes, 60 classes;
- protected benchmark: 35 `inventory-v1` validation/test scenes, completely
  excluded from both detector splits.

The default export uses hardlinks to avoid duplicating the raw images. Copying
the exported directory to another drive or computer produces normal files.
The source and generated dataset remain subject to D2S's CC BY-NC-SA 4.0
non-commercial license.

## GPU environment

The current workstation has an AMD Radeon RX 6600 with 8 GB VRAM. It is not a
supported accelerator for this Ultralytics training workflow on Windows:

- Ultralytics' Python package does not provide a DirectML training backend.
- Official PyTorch ROCm wheels are Linux-only.
- The RX 6600 is not in AMD's current officially supported Radeon ROCm compute
  list for native Linux training.

Do not launch the default `device=0` command in the current CPU-only Python
environment. It will stop before training rather than silently fall back to
CPU.

Use a supported NVIDIA CUDA or AMD ROCm GPU environment, locally or in the
cloud. Install the matching GPU-enabled PyTorch build first, then:

```powershell
py -3.11 -m pip install ultralytics==8.3.95
```

Copy these items to the GPU machine while preserving their relative paths:

- the repository;
- `data\yolo-inventory-v1`.

The default YOLO11s configuration targets a supported GPU with roughly 8 GB or
more of usable VRAM. Start with `-Batch 4`; increase to 8 only after confirming
memory headroom.

## Verify without training

```powershell
.\evaluation\run_yolo_training.ps1 -DryRun
```

## Train

The default uses YOLO11s, 960-pixel inputs, and GPU device 0. For an 8 GB
supported accelerator, begin with batch size 4:

```powershell
.\evaluation\run_yolo_training.ps1 -Batch 4
```

If that runs out of memory, reduce the image size before switching to CPU:

```powershell
.\evaluation\run_yolo_training.ps1 -Batch 4 -ImageSize 768
```

For a larger GPU, increase it:

```powershell
.\evaluation\run_yolo_training.ps1 -Batch 16
```

Training artifacts are written to
`evaluation\training-runs\inventory-yolo-v1`. Do not evaluate the locked v1
validation or test split until training and internal model-validation choices
are complete.

## RunPod A5000 pilot

Create a RunPod GPU Pod with an RTX A5000 and a current PyTorch template. Give
the pod at least 20 GB of persistent volume storage. In its terminal:

```bash
git clone https://github.com/imnotdeepak/deterministic_model.git worldao
cd worldao
git switch inventory-v1
python -m pip install ultralytics==8.3.95
```

Upload the local `data/yolo-inventory-v1` directory into the cloned repository's
`data` directory. The resulting cloud path must be
`worldao/data/yolo-inventory-v1/dataset.yaml`.

Validate the GPU, dataset, and leakage guard without starting training:

```bash
chmod +x evaluation/run_yolo_training.sh
./evaluation/run_yolo_training.sh --dry-run
```

The output must identify the A5000, report CUDA as available, report 5,880
training images and 1,050 internal-validation images, and confirm 35 protected
scenes. Then run the timing pilot:

```bash
./evaluation/run_yolo_training.sh \
  --epochs 5 \
  --batch 16 \
  --name inventory-yolo-v1-pilot-5e
```

The pilot writes to
`evaluation/training-runs/inventory-yolo-v1-pilot-5e`. Download that directory
before stopping or deleting the pod. Stop the pod as soon as the artifacts are
safe; persistent storage may continue accruing charges until it is deleted.

## Development detector evaluation

The five-epoch pilot checkpoint is the selected detector. A longer batch-16
run stopped after 39 epochs but regressed internal mAP50-95 from 0.860 to 0.822
and collapsed several class recalls, so it is retained only as a rejected
experiment.

Run the selected detector against development only:

```powershell
py -3.11 evaluation\src\run_yolo_detector.py `
  --dataset evaluation\datasets\inventory-v1 `
  --checkpoint evaluation\training-runs\inventory-yolo-v1-pilot-5e\weights\best.pt `
  --output evaluation\runs\v1-development-yolo-pilot5-conf035 `
  --split development --confidence 0.35 --imgsz 960 --device cpu
```

The runner deliberately refuses the locked validation and test splits. It
converts every detection to an original-pixel evidence box and aggregates
instances into schema-valid product quantities.

Confidence `0.35` is frozen from a development-only sweep. It reached 201/209
whole-image exact matches (96.17%), 97.13% product precision, 97.93% product
recall, and 98.21% evidence-localization accuracy. Confidence `0.40` produced
identical task metrics, so the lower edge of the plateau was selected. See
`V1_YOLO_DEVELOPMENT_RESULTS.md`.

## Frozen validation result

The selected configuration was run once on the complete 45-image validation
split. It was rejected after reaching 66.67% whole-image exact accuracy, 86.52%
product precision, 79.74% product recall, and 80.90% evidence-localization
accuracy. No threshold or checkpoint was changed after observing validation.
The test split remains untouched. See `V1_YOLO_VALIDATION_RESULTS.md`.

## V2 training candidate

The rejected v1 validation result must not be used for detector tuning. A fresh
inventory-v2 benchmark was therefore locked before new training. The v2 export
protects validation/test scenes from both inventory-v1 and inventory-v2 and
contains 5,130 training images plus 900 internal-validation images.

The predeclared v2 candidate uses `yolo11m.pt`, image size 1280, at most 30
epochs, batch size 8, and early-stopping patience 10. See `V2_PLAN.md` for the
exact RunPod commands and leakage protocol.
