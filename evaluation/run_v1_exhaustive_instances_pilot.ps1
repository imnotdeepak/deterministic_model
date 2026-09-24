param(
    [ValidateRange(1, 209)]
    [int]$Limit = 8,

    [ValidateRange(0, 208)]
    [int]$Offset = 24,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if ($Offset + $Limit -gt 209) {
    throw "Offset + Limit must not exceed the 209-image development split."
}

if (-not $DryRun -and -not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not configured in this PowerShell session."
}

$dataset = "evaluation\datasets\inventory-v1"
$references = "evaluation\references\inventory-v0\reference-manifest.json"
$slice = "offset-$Offset-limit-$Limit"
$run = "evaluation\runs\v1-development-terra-exhaustive-instances-v6-$slice"
$report = "evaluation\reports\v1-development-terra-exhaustive-instances-v6-$slice.json"
$failureAnalysis = "evaluation\reports\v1-development-terra-exhaustive-instances-v6-$slice-failure-analysis.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/4: Verify the locked inventory-v1 benchmark"
& py -3.11 evaluation\src\lock_dataset.py --dataset $dataset --verify
Assert-LastCommand "Dataset lock verification"

$runArguments = @(
    "evaluation\src\run_baseline.py",
    "--dataset", $dataset,
    "--split", "development",
    "--offset", $Offset,
    "--limit", $Limit,
    "--output", $run,
    "--model", "gpt-5.6-terra",
    "--references", $references,
    "--reference-detail", "high",
    "--instance-localization",
    "--exhaustive-instance-search",
    "--input-usd-per-million", "2",
    "--output-usd-per-million", "12",
    "--fail-fast"
)
if ($DryRun) {
    $runArguments += "--dry-run"
}

Write-Output "Stage 2/4: Terra exhaustive instance localization"
& py -3.11 @runArguments
Assert-LastCommand "Terra exhaustive instance localization"

if ($DryRun) {
    Write-Output "Dry run complete. V6 selected development offset $Offset and limit $Limit."
    Write-Output "Validation and test were untouched. No API calls or output files were created."
    exit 0
}

Write-Output "Stage 3/4: Evaluate v6"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$run\predictions" `
    --split development --offset $Offset --limit $Limit `
    --output $report | Out-Null
Assert-LastCommand "V6 evaluation"

Write-Output "Stage 4/4: Analyze v6 failures"
& py -3.11 evaluation\src\analyze_failures.py `
    --dataset $dataset --predictions "$run\predictions" `
    --split development --offset $Offset --limit $Limit `
    --report $report --output $failureAnalysis | Out-Null
Assert-LastCommand "V6 failure analysis"

$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
$cost = ($result.samples.estimated_cost_usd | Measure-Object -Sum).Sum
$summary = [ordered]@{
    split = "development"
    offset = $Offset
    samples = $result.summary.samples
    exact_accuracy = $result.summary.whole_image_exact_accuracy
    product_precision = $result.summary.product_precision
    product_recall = $result.summary.product_recall
    evidence_reference_coverage = $result.summary.mean_evidence_reference_coverage
    evidence_localization_accuracy = $result.summary.mean_evidence_localization_accuracy
    median_cost_per_image_usd = $result.summary.median_cost_usd
    total_cost_usd = $cost
    latency_p95_ms = $result.summary.p95_latency_ms
    contract_valid_rate = $result.summary.prediction_contract_valid_rate
    missing_predictions = $result.coverage.missing_predictions
}

Write-Output "V6 exhaustive-instance development pilot complete."
Write-Output "Validation and test were untouched."
Write-Output "Report: $report"
$summary | ConvertTo-Json
