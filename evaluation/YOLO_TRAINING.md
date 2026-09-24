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

The current local Python environment has CPU-only PyTorch. On the GPU machine,
install the appropriate GPU-enabled PyTorch build for that hardware, then:

```powershell
py -3.11 -m pip install ultralytics==8.3.95
```

Copy these items to the GPU machine while preserving their relative paths:

- the repository;
- `data\yolo-inventory-v1`.

## Verify without training

```powershell
.\evaluation\run_yolo_training.ps1 -DryRun
```

## Train

The default uses YOLO11s, 960-pixel inputs, batch size 8, and CUDA device 0:

```powershell
.\evaluation\run_yolo_training.ps1
```

For a smaller GPU, reduce the batch size:

```powershell
.\evaluation\run_yolo_training.ps1 -Batch 4
```

For a larger GPU, increase it:

```powershell
.\evaluation\run_yolo_training.ps1 -Batch 16
```

Training artifacts are written to
`evaluation\training-runs\inventory-yolo-v1`. Do not evaluate the locked v1
validation or test split until training and internal model-validation choices
are complete.
