param(
    [ValidateRange(1, 209)]
    [int]$Limit = 20,

    [ValidateRange(0, 208)]
    [int]$Offset = 0,

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
$aggregateRun = "evaluation\runs\v1-development-terra-aggregate-v5-$slice"
$instancesRun = "evaluation\runs\v1-development-terra-instances-v5-$slice"
$aggregateReport = "evaluation\reports\v1-development-terra-aggregate-v5-$slice.json"
$instancesReport = "evaluation\reports\v1-development-terra-instances-v5-$slice.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/5: Verify the locked inventory-v1 benchmark"
& py -3.11 evaluation\src\lock_dataset.py --dataset $dataset --verify
Assert-LastCommand "Dataset lock verification"

$commonArguments = @(
    "evaluation\src\run_baseline.py",
    "--dataset", $dataset,
    "--split", "development",
    "--offset", $Offset,
    "--limit", $Limit,
    "--model", "gpt-5.6-terra",
    "--references", $references,
    "--reference-detail", "high",
    "--input-usd-per-million", "2",
    "--output-usd-per-million", "12",
    "--fail-fast"
)

$aggregateArguments = $commonArguments + @("--output", $aggregateRun)
$instancesArguments = $commonArguments + @(
    "--output", $instancesRun,
    "--instance-localization"
)
if ($DryRun) {
    $aggregateArguments += "--dry-run"
    $instancesArguments += "--dry-run"
}

Write-Output "Stage 2/5: Terra aggregate control on locked development slice"
& py -3.11 @aggregateArguments
Assert-LastCommand "Terra aggregate control"

Write-Output "Stage 3/5: Terra one-call instance localization on the same slice"
& py -3.11 @instancesArguments
Assert-LastCommand "Terra instance-localization pilot"

if ($DryRun) {
    Write-Output "Dry run complete. Both selections used development offset $Offset and limit $Limit."
    Write-Output "Validation and test were untouched. No API calls or output files were created."
    exit 0
}

Write-Output "Stage 4/5: Evaluate the aggregate control"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$aggregateRun\predictions" `
    --split development --offset $Offset --limit $Limit `
    --output $aggregateReport | Out-Null
Assert-LastCommand "Aggregate control evaluation"

Write-Output "Stage 5/5: Evaluate the instance-localization pilot"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$instancesRun\predictions" `
    --split development --offset $Offset --limit $Limit `
    --output $instancesReport | Out-Null
Assert-LastCommand "Instance-localization evaluation"

$aggregate = Get-Content -LiteralPath $aggregateReport -Raw | ConvertFrom-Json
$instances = Get-Content -LiteralPath $instancesReport -Raw | ConvertFrom-Json
$aggregateCost = ($aggregate.samples.estimated_cost_usd | Measure-Object -Sum).Sum
$instancesCost = ($instances.samples.estimated_cost_usd | Measure-Object -Sum).Sum
$summary = [ordered]@{
    split = "development"
    offset = $Offset
    samples = $instances.summary.samples
    aggregate = [ordered]@{
        exact_accuracy = $aggregate.summary.whole_image_exact_accuracy
        product_precision = $aggregate.summary.product_precision
        product_recall = $aggregate.summary.product_recall
        evidence_localization_accuracy = $aggregate.summary.mean_evidence_localization_accuracy
        median_cost_per_image_usd = $aggregate.summary.median_cost_usd
        total_cost_usd = $aggregateCost
        latency_p95_ms = $aggregate.summary.p95_latency_ms
    }
    instance_localization = [ordered]@{
        exact_accuracy = $instances.summary.whole_image_exact_accuracy
        product_precision = $instances.summary.product_precision
        product_recall = $instances.summary.product_recall
        evidence_reference_coverage = $instances.summary.mean_evidence_reference_coverage
        evidence_localization_accuracy = $instances.summary.mean_evidence_localization_accuracy
        median_cost_per_image_usd = $instances.summary.median_cost_usd
        total_cost_usd = $instancesCost
        latency_p95_ms = $instances.summary.p95_latency_ms
        contract_valid_rate = $instances.summary.prediction_contract_valid_rate
        missing_predictions = $instances.coverage.missing_predictions
    }
}

Write-Output "Terra instance-localization development pilot complete."
Write-Output "Validation and test were untouched."
Write-Output "Instance report: $instancesReport"
$summary | ConvertTo-Json -Depth 4
