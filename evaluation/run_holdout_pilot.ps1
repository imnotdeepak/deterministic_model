param(
    [int]$Offset = 20,
    [int]$Limit = 10
)

$ErrorActionPreference = "Stop"

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not configured in this PowerShell session."
}
if ($Offset -lt 0) {
    throw "Offset must be zero or greater."
}
if ($Limit -le 0) {
    throw "Limit must be greater than zero."
}

$tag = "holdout-$Offset-$Limit"
$dataset = "evaluation\datasets\inventory-v0"
$references = "evaluation\references\inventory-v0\reference-manifest.json"
$lunaRun = "evaluation\runs\$tag-luna-references-v0"
$terraRun = "evaluation\runs\$tag-terra-references-v0"
$evidenceRun = "evaluation\runs\$tag-luna-terra-evidence-v0"
$resolverRun = "evaluation\runs\$tag-sol-identity-v0"
$routedRun = "evaluation\runs\$tag-routed-policy-v0"
$report = "evaluation\reports\$tag-routed-policy-v0.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/6: Luna aggregate candidates"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split development --offset $Offset --limit $Limit `
    --output $lunaRun --model gpt-5.6-luna `
    --references $references --reference-detail high --fail-fast
Assert-LastCommand "Luna aggregate run"

Write-Output "Stage 2/6: Terra aggregate candidates"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split development --offset $Offset --limit $Limit `
    --output $terraRun --model gpt-5.6-terra `
    --references $references --reference-detail high `
    --input-usd-per-million 2 --output-usd-per-million 12 --fail-fast
Assert-LastCommand "Terra aggregate run"

Write-Output "Stage 3/6: Luna evidence for Terra aggregate data"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split development --offset $Offset --limit $Limit `
    --output $evidenceRun --model gpt-5.6-luna `
    --references $references --reference-detail high `
    --evidence-only --identity-predictions "$terraRun\predictions" --fail-fast
Assert-LastCommand "Luna evidence run"

Write-Output "Stage 4/6: Sol identity-only disagreement adjudication"
& py -3.11 evaluation\src\run_disagreement_resolver.py `
    --dataset $dataset --split development --offset $Offset --limit $Limit `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --references $references --output $resolverRun `
    --model gpt-5.6-sol --reasoning-effort low --fail-fast
Assert-LastCommand "Sol disagreement run"

Write-Output "Stage 5/6: Apply frozen routing policy"
& py -3.11 evaluation\src\compose_routing_policy.py `
    --dataset $dataset --split development --offset $Offset --limit $Limit `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --adjudicated-predictions "$resolverRun\predictions" `
    --output $routedRun
Assert-LastCommand "Routing composition"

Write-Output "Stage 6/6: Evaluate untouched holdout samples"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$routedRun\predictions" `
    --split development --offset $Offset --limit $Limit --output $report | Out-Null
Assert-LastCommand "Holdout evaluation"

$evaluation = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
$conciseSummary = [ordered]@{
    samples = $evaluation.summary.samples
    whole_image_exact_accuracy = $evaluation.summary.whole_image_exact_accuracy
    product_precision = $evaluation.summary.product_precision
    product_recall = $evaluation.summary.product_recall
    evidence_localization_accuracy = $evaluation.summary.mean_evidence_localization_accuracy
    prediction_contract_valid_rate = $evaluation.summary.prediction_contract_valid_rate
    missing_predictions = $evaluation.coverage.missing_predictions
}
Write-Output "Holdout pipeline complete."
Write-Output "Report: $report"
$conciseSummary | ConvertTo-Json
