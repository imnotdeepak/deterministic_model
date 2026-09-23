# Inventory-v1 benchmark

This is the newly reserved benchmark for evaluating the v1 pipeline. It was
created before running v1 models on its validation or test splits.

## Composition

- 300 MVTec D2S images from 116 physical scenes
- 60-product frozen catalog
- development: 209 images / 81 scenes / 38 represented classes
- validation: 45 images / 17 scenes / 52 represented classes
- test: 46 images / 18 scenes / 49 represented classes
- combined validation and test coverage: 59 of 60 catalog classes

The existing inventory-v0 development split remains available as development
evidence and covers all 60 classes. Inventory-v1 validation and test are fresh
held-out scenes.

## Leakage controls

The import excludes:

- every physical scene used anywhere in inventory-v0;
- every source scene used to construct the visual reference catalog.

The resulting v1 scene overlap with both sources is zero. All captures from a
physical scene stay in one split.

## Reproduce

```powershell
py -3.11 evaluation\src\import_d2s.py `
  --source data `
  --output evaluation\datasets\inventory-v1 `
  --schema evaluation\datasets\inventory-v0\schema.json `
  --dataset-id inventory-v1 --dataset-version 1.0.0 `
  --limit 300 --seed 104729 `
  --split-strategy coverage_balanced `
  --exclude-manifest evaluation\datasets\inventory-v0\manifest.jsonl `
  --exclude-reference-manifest evaluation\references\inventory-v0\reference-manifest.json
```

## Evaluation protocol

1. Use development data for implementation and prompt changes.
2. Run validation only after a candidate pipeline is defined.
3. Lock the selected pipeline after reviewing validation.
4. Run test once without making subsequent pipeline changes.
5. Treat any post-test change as a new benchmark/model version.

Verify the dataset before every run:

```powershell
py -3.11 evaluation\src\lock_dataset.py `
  --dataset evaluation\datasets\inventory-v1 --verify
```

The source dataset is licensed CC BY-NC-SA 4.0. See the repository-level
`DATASET_LICENSE.md` for attribution and restrictions.
