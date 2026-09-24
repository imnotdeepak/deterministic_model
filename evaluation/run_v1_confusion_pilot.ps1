param(
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

if (-not $DryRun -and -not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not configured in this PowerShell session."
}

$dataset = "evaluation\datasets\inventory-v0"
$references = "evaluation\references\inventory-v0\reference-manifest.json"
$confusionSets = "evaluation\confusion_sets.json"
$lunaRun = "evaluation\runs\validation-luna-references-v0"
$terraRun = "evaluation\runs\validation-terra-references-v0"
$evidenceRun = "evaluation\runs\validation-luna-terra-evidence-v0"
$resolverRun = "evaluation\runs\validation-sol-confusion-aware-v3"
$routedRun = "evaluation\runs\validation-routed-policy-v3"
$routedReport = "evaluation\reports\validation-routed-policy-v3.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

$resolverArguments = @(
    "evaluation\src\run_disagreement_resolver.py",
    "--dataset", $dataset,
    "--split", "validation",
    "--candidate-a", "$lunaRun\predictions",
    "--candidate-b", "$terraRun\predictions",
    "--base-predictions", "$evidenceRun\predictions",
    "--references", $references,
    "--confusion-sets", $confusionSets,
    "--output", $resolverRun,
    "--model", "gpt-5.6-sol",
    "--reasoning-effort", "low",
    "--full-catalog-adjudication",
    "--fail-fast"
)
if ($DryRun) {
    $resolverArguments += "--dry-run"
}

Write-Output "Stage 1/3: Confusion-aware Sol adjudication on v1 development evidence"
& py -3.11 @resolverArguments
Assert-LastCommand "Confusion-aware adjudication"

if ($DryRun) {
    Write-Output "Dry run complete. No API calls or output files were created."
    exit 0
}

Write-Output "Stage 2/3: Compose the experimental v3 routing policy"
& py -3.11 evaluation\src\compose_routing_policy.py `
    --dataset $dataset --split validation `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --adjudicated-predictions "$resolverRun\predictions" `
    --output $routedRun `
    --full-catalog-adjudication --confusion-aware-adjudication
Assert-LastCommand "V3 routing composition"

Write-Output "Stage 3/3: Evaluate the experimental v3 pipeline"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$routedRun\predictions" `
    --split validation --output $routedReport | Out-Null
Assert-LastCommand "V3 development evaluation"

$report = Get-Content -LiteralPath $routedReport -Raw | ConvertFrom-Json
$summary = [ordered]@{
    samples = $report.summary.samples
    whole_image_exact_accuracy = $report.summary.whole_image_exact_accuracy
    product_precision = $report.summary.product_precision
    product_recall = $report.summary.product_recall
    evidence_localization_accuracy = $report.summary.mean_evidence_localization_accuracy
    contract_valid_rate = $report.summary.prediction_contract_valid_rate
    missing_predictions = $report.coverage.missing_predictions
}
Write-Output "Confusion-aware v1 development pilot complete. The frozen test split was untouched."
Write-Output "Report: $routedReport"
$summary | ConvertTo-Json
