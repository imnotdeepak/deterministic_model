param(
    [string]$Device = "0",
    [string]$Model = "yolo11s.pt",
    [ValidateRange(1, 1000)]
    [int]$Epochs = 100,
    [ValidateRange(320, 2048)]
    [int]$ImageSize = 960,
    [ValidateRange(1, 256)]
    [int]$Batch = 8,
    [ValidateRange(0, 64)]
    [int]$Workers = 4,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$data = "data\yolo-inventory-v1\dataset.yaml"
$metadata = "data\yolo-inventory-v1\export-metadata.json"

if (-not (Test-Path -LiteralPath $data)) {
    throw "YOLO dataset is missing. Run the export command in evaluation/YOLO_TRAINING.md first."
}
if (-not (Test-Path -LiteralPath $metadata)) {
    throw "YOLO export metadata is missing: $metadata"
}

$export = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
if ($export.protected_scene_count -ne 35) {
    throw "Expected 35 protected validation/test scenes, found $($export.protected_scene_count)."
}
if ($export.splits.train.categories -ne 60 -or $export.splits.val.categories -ne 60) {
    throw "Both detector training splits must cover all 60 catalog classes."
}

$configuration = [ordered]@{
    data = $data
    protected_scene_count = $export.protected_scene_count
    train_images = $export.splits.train.images
    validation_images = $export.splits.val.images
    model = $Model
    epochs = $Epochs
    image_size = $ImageSize
    batch = $Batch
    device = $Device
    workers = $Workers
}

if ($DryRun) {
    Write-Output "YOLO training dry run. No training was started."
    $configuration | ConvertTo-Json
    exit 0
}

Write-Output "Starting leakage-safe YOLO training."
$configuration | ConvertTo-Json
& py -3.11 evaluation\src\train_yolo_detector.py `
    --data $data --model $Model --epochs $Epochs `
    --imgsz $ImageSize --batch $Batch --device $Device `
    --workers $Workers --name inventory-yolo-v1
if ($LASTEXITCODE -ne 0) {
    throw "YOLO training failed with exit code $LASTEXITCODE."
}
