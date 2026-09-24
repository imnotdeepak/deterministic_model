param()

$ErrorActionPreference = "Stop"

$dataset = "evaluation\datasets\inventory-v1"
$checkpoint = "evaluation\training-runs\inventory-yolo-v1-pilot-5e\weights\best.pt"
$run = "evaluation\runs\v1-validation-yolo-pilot5-conf035"
$report = "evaluation\reports\v1-validation-yolo-pilot5-conf035.json"
$failureAnalysis = "evaluation\reports\v1-validation-yolo-pilot5-conf035-failure-analysis.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/4: Verify the locked inventory-v1 benchmark"
& py -3.11 evaluation\src\lock_dataset.py --dataset $dataset --verify
Assert-LastCommand "Dataset lock verification"

Write-Output "Stage 2/4: Run the frozen YOLO configuration on all validation images"
& py -3.11 evaluation\src\run_yolo_detector.py `
    --dataset $dataset --checkpoint $checkpoint --output $run `
    --split validation --allow-locked-validation `
    --confidence 0.35 --iou 0.70 --imgsz 960 --device cpu
Assert-LastCommand "Frozen YOLO validation inference"

Write-Output "Stage 3/4: Evaluate validation predictions"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$run\predictions" `
    --split validation --output $report | Out-Null
Assert-LastCommand "YOLO validation evaluation"

Write-Output "Stage 4/4: Record validation failures without changing configuration"
& py -3.11 evaluation\src\analyze_failures.py `
    --dataset $dataset --predictions "$run\predictions" `
    --split validation --report $report --stage "yolo=$run\predictions" `
    --output $failureAnalysis | Out-Null
Assert-LastCommand "YOLO validation failure analysis"

$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
$summary = $result.summary
$gates = [ordered]@{
    prediction_coverage = ($result.coverage.missing_predictions -eq 0)
    contract_valid = ($summary.prediction_contract_valid_rate -ge 1.0)
    exact_accuracy = ($summary.whole_image_exact_accuracy -ge 0.95)
    product_precision = ($summary.product_precision -ge 0.98)
    product_recall = ($summary.product_recall -ge 0.98)
    evidence_reference_coverage = ($summary.mean_evidence_reference_coverage -ge 0.98)
    evidence_localization_accuracy = ($summary.mean_evidence_localization_accuracy -ge 0.95)
    median_cost = ($summary.median_cost_usd -le 0.03)
    latency_p95 = ($summary.p95_latency_ms -le 8000)
}
$output = [ordered]@{
    samples = $summary.samples
    exact_accuracy = $summary.whole_image_exact_accuracy
    product_precision = $summary.product_precision
    product_recall = $summary.product_recall
    evidence_reference_coverage = $summary.mean_evidence_reference_coverage
    evidence_localization_accuracy = $summary.mean_evidence_localization_accuracy
    median_cost_usd = $summary.median_cost_usd
    latency_p95_ms = $summary.p95_latency_ms
    contract_valid_rate = $summary.prediction_contract_valid_rate
    missing_predictions = $result.coverage.missing_predictions
    gates = $gates
    all_gates_pass = -not ($gates.Values -contains $false)
}
Write-Output "Frozen YOLO validation complete. The test split was untouched."
Write-Output "Report: $report"
$output | ConvertTo-Json -Depth 4
