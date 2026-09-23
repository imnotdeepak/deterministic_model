param()

$ErrorActionPreference = "Stop"

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not configured in this PowerShell session."
}

$dataset = "evaluation\datasets\inventory-v0"
$references = "evaluation\references\inventory-v0\reference-manifest.json"
$lunaRun = "evaluation\runs\test-luna-references-v0"
$terraRun = "evaluation\runs\test-terra-references-v0"
$evidenceRun = "evaluation\runs\test-luna-terra-evidence-v0"
$resolverRun = "evaluation\runs\test-sol-full-catalog-v2"
$routedRun = "evaluation\runs\test-routed-policy-v2"
$terraReport = "evaluation\reports\test-terra-references-v0.json"
$routedReport = "evaluation\reports\test-routed-policy-v2.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/6: Luna aggregate candidates on the final test split"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split test `
    --output $lunaRun --model gpt-5.6-luna `
    --references $references --reference-detail high --fail-fast
Assert-LastCommand "Luna final test run"

Write-Output "Stage 2/6: Terra aggregate candidates on the final test split"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split test `
    --output $terraRun --model gpt-5.6-terra `
    --references $references --reference-detail high `
    --input-usd-per-million 2 --output-usd-per-million 12 --fail-fast
Assert-LastCommand "Terra final test run"

Write-Output "Stage 3/6: Luna evidence for Terra aggregate data"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split test `
    --output $evidenceRun --model gpt-5.6-luna `
    --references $references --reference-detail high `
    --evidence-only --identity-predictions "$terraRun\predictions" --fail-fast
Assert-LastCommand "Luna final test evidence run"

Write-Output "Stage 4/6: Sol full-catalog identity adjudication"
& py -3.11 evaluation\src\run_disagreement_resolver.py `
    --dataset $dataset --split test `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --references $references --output $resolverRun `
    --model gpt-5.6-sol --reasoning-effort low `
    --full-catalog-adjudication --fail-fast
Assert-LastCommand "Sol full-catalog final test run"

Write-Output "Stage 5/6: Apply the frozen v2 routing policy"
& py -3.11 evaluation\src\compose_routing_policy.py `
    --dataset $dataset --split test `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --adjudicated-predictions "$resolverRun\predictions" `
    --output $routedRun --full-catalog-adjudication
Assert-LastCommand "Frozen v2 routing composition"

Write-Output "Stage 6/6: Evaluate Terra and routed v2 on the final test split"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$terraRun\predictions" `
    --split test --output $terraReport | Out-Null
Assert-LastCommand "Terra final test evaluation"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$routedRun\predictions" `
    --split test --output $routedReport | Out-Null
Assert-LastCommand "Routed v2 final test evaluation"

$terra = Get-Content -LiteralPath $terraReport -Raw | ConvertFrom-Json
$routed = Get-Content -LiteralPath $routedReport -Raw | ConvertFrom-Json
$summary = [ordered]@{
    samples = $routed.summary.samples
    terra_exact_accuracy = $terra.summary.whole_image_exact_accuracy
    routed_v2_exact_accuracy = $routed.summary.whole_image_exact_accuracy
    routed_v2_product_precision = $routed.summary.product_precision
    routed_v2_product_recall = $routed.summary.product_recall
    routed_v2_mean_count_error = $routed.summary.mean_total_absolute_count_error_per_image
    routed_v2_evidence_localization_accuracy = $routed.summary.mean_evidence_localization_accuracy
    routed_v2_contract_valid_rate = $routed.summary.prediction_contract_valid_rate
    routed_v2_missing_predictions = $routed.coverage.missing_predictions
}
Write-Output "Final test v2 pipeline complete. Treat this result as the locked report card."
Write-Output "Routed report: $routedReport"
$summary | ConvertTo-Json
