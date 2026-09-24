param()

$ErrorActionPreference = "Stop"

if (-not $env:OPENAI_API_KEY) {
    throw "OPENAI_API_KEY is not configured in this PowerShell session."
}

$dataset = "evaluation\datasets\inventory-v1"
$references = "evaluation\references\inventory-v0\reference-manifest.json"
$confusionSets = "evaluation\confusion_sets.json"
$lunaRun = "evaluation\runs\v1-validation-luna-v4"
$terraRun = "evaluation\runs\v1-validation-terra-v4"
$evidenceRun = "evaluation\runs\v1-validation-evidence-v4"
$resolverRun = "evaluation\runs\v1-validation-sol-isolated-confusion-v4"
$routedRun = "evaluation\runs\v1-validation-routed-v4"
$terraReport = "evaluation\reports\v1-validation-terra-v4.json"
$routedReport = "evaluation\reports\v1-validation-routed-v4.json"
$pipelineSummary = "evaluation\reports\v1-validation-routed-v4-pipeline-summary.json"
$failureAnalysis = "evaluation\reports\v1-validation-routed-v4-failure-analysis.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/9: Verify the locked inventory-v1 benchmark"
& py -3.11 evaluation\src\lock_dataset.py --dataset $dataset --verify
Assert-LastCommand "Dataset lock verification"

Write-Output "Stage 2/9: Luna candidates on fresh validation"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split validation `
    --output $lunaRun --model gpt-5.6-luna `
    --references $references --reference-detail high --fail-fast
Assert-LastCommand "Luna v1 validation run"

Write-Output "Stage 3/9: Terra candidates on fresh validation"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split validation `
    --output $terraRun --model gpt-5.6-terra `
    --references $references --reference-detail high `
    --input-usd-per-million 2 --output-usd-per-million 12 --fail-fast
Assert-LastCommand "Terra v1 validation run"

Write-Output "Stage 4/9: Luna evidence for Terra candidate data"
& py -3.11 evaluation\src\run_baseline.py `
    --dataset $dataset --split validation `
    --output $evidenceRun --model gpt-5.6-luna `
    --references $references --reference-detail high `
    --evidence-only --identity-predictions "$terraRun\predictions" --fail-fast
Assert-LastCommand "Luna v1 validation evidence run"

Write-Output "Stage 5/9: Sol v4 isolated-confusion adjudication"
& py -3.11 evaluation\src\run_disagreement_resolver.py `
    --dataset $dataset --split validation `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --references $references --confusion-sets $confusionSets `
    --isolated-confusion-only --full-catalog-adjudication `
    --output $resolverRun --model gpt-5.6-sol `
    --reasoning-effort low --fail-fast
Assert-LastCommand "Sol v4 validation adjudication"

Write-Output "Stage 6/9: Compose frozen v4 routing"
& py -3.11 evaluation\src\compose_routing_policy.py `
    --dataset $dataset --split validation `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --adjudicated-predictions "$resolverRun\predictions" `
    --output $routedRun `
    --full-catalog-adjudication --isolated-confusion-adjudication
Assert-LastCommand "V4 validation routing composition"

Write-Output "Stage 7/9: Evaluate Terra and routed v4"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$terraRun\predictions" `
    --split validation --output $terraReport | Out-Null
Assert-LastCommand "Terra v1 validation evaluation"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$routedRun\predictions" `
    --split validation --output $routedReport | Out-Null
Assert-LastCommand "Routed v4 validation evaluation"

Write-Output "Stage 8/9: Summarize end-to-end economics"
& py -3.11 evaluation\src\summarize_pipeline.py `
    --report $routedReport `
    --luna "$lunaRun\predictions" `
    --terra "$terraRun\predictions" `
    --evidence "$evidenceRun\predictions" `
    --sol "$resolverRun\predictions" `
    --decisions "$routedRun\routing-decisions" `
    --output $pipelineSummary | Out-Null
Assert-LastCommand "V4 validation pipeline summary"

Write-Output "Stage 9/9: Generate validation failure analysis"
& py -3.11 evaluation\src\analyze_failures.py `
    --dataset $dataset --predictions "$routedRun\predictions" `
    --split validation --report $routedReport `
    --routing-decisions "$routedRun\routing-decisions" `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --stage "luna=$lunaRun\predictions" `
    --stage "terra=$terraRun\predictions" `
    --stage "evidence=$evidenceRun\predictions" `
    --stage "sol=$resolverRun\predictions" `
    --output $failureAnalysis | Out-Null
Assert-LastCommand "V4 validation failure analysis"

$terra = Get-Content -LiteralPath $terraReport -Raw | ConvertFrom-Json
$routed = Get-Content -LiteralPath $routedReport -Raw | ConvertFrom-Json
$economics = Get-Content -LiteralPath $pipelineSummary -Raw | ConvertFrom-Json
$summary = [ordered]@{
    samples = $routed.summary.samples
    terra_exact_accuracy = $terra.summary.whole_image_exact_accuracy
    routed_v4_exact_accuracy = $routed.summary.whole_image_exact_accuracy
    routed_v4_product_precision = $routed.summary.product_precision
    routed_v4_product_recall = $routed.summary.product_recall
    routed_v4_evidence_localization_accuracy = $routed.summary.mean_evidence_localization_accuracy
    routed_v4_contract_valid_rate = $routed.summary.prediction_contract_valid_rate
    end_to_end_cost_usd = $economics.end_to_end_model_cost.total_usd
    median_end_to_end_cost_per_image_usd = $economics.end_to_end_model_cost.median_per_image_usd
    parallel_candidate_latency_p95_ms = $economics.model_call_latency.parallel_candidates_p95_ms
    missing_predictions = $routed.coverage.missing_predictions
}
Write-Output "V1 validation complete. The test split was untouched."
Write-Output "Routed report: $routedReport"
$summary | ConvertTo-Json
