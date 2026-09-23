param()

$ErrorActionPreference = "Stop"

$dataset = "evaluation\datasets\inventory-v0"
$confusionSets = "evaluation\confusion_sets.json"
$lunaRun = "evaluation\runs\validation-luna-references-v0"
$terraRun = "evaluation\runs\validation-terra-references-v0"
$evidenceRun = "evaluation\runs\validation-luna-terra-evidence-v0"
$standardRun = "evaluation\runs\validation-sol-full-catalog-v2"
$targetedRun = "evaluation\runs\validation-sol-confusion-aware-v3"
$replayRun = "evaluation\runs\validation-sol-isolated-confusion-replay-v4"
$routedRun = "evaluation\runs\validation-routed-policy-v4-replay"
$report = "evaluation\reports\validation-routed-policy-v4-replay.json"
$pipelineSummary = "evaluation\reports\validation-routed-policy-v4-replay-pipeline-summary.json"

function Assert-LastCommand {
    param([string]$Stage)
    if ($LASTEXITCODE -ne 0) {
        throw "$Stage failed with exit code $LASTEXITCODE."
    }
}

Write-Output "Stage 1/4: Select saved adjudications using the isolated-family rule"
& py -3.11 evaluation\src\compose_isolated_confusion_replay.py `
    --dataset $dataset --split validation `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --standard-adjudicated "$standardRun\predictions" `
    --targeted-adjudicated "$targetedRun\predictions" `
    --confusion-sets $confusionSets --output $replayRun
Assert-LastCommand "Isolated-confusion replay"

Write-Output "Stage 2/4: Compose the v4 replay routing policy"
& py -3.11 evaluation\src\compose_routing_policy.py `
    --dataset $dataset --split validation `
    --candidate-a "$lunaRun\predictions" `
    --candidate-b "$terraRun\predictions" `
    --base-predictions "$evidenceRun\predictions" `
    --adjudicated-predictions "$replayRun\predictions" `
    --output $routedRun `
    --full-catalog-adjudication --isolated-confusion-adjudication --overwrite
Assert-LastCommand "V4 replay routing composition"

Write-Output "Stage 3/4: Evaluate the v4 counterfactual replay"
& py -3.11 evaluation\src\evaluate.py `
    --dataset $dataset --predictions "$routedRun\predictions" `
    --split validation --output $report | Out-Null
Assert-LastCommand "V4 replay evaluation"

Write-Output "Stage 4/4: Summarize end-to-end replay economics"
& py -3.11 evaluation\src\summarize_pipeline.py `
    --report $report `
    --luna "$lunaRun\predictions" `
    --terra "$terraRun\predictions" `
    --evidence "$evidenceRun\predictions" `
    --sol "$replayRun\predictions" `
    --decisions "$routedRun\routing-decisions" `
    --output $pipelineSummary | Out-Null
Assert-LastCommand "V4 replay pipeline summary"

$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
$economics = Get-Content -LiteralPath $pipelineSummary -Raw | ConvertFrom-Json
$summary = [ordered]@{
    samples = $result.summary.samples
    whole_image_exact_accuracy = $result.summary.whole_image_exact_accuracy
    product_precision = $result.summary.product_precision
    product_recall = $result.summary.product_recall
    evidence_localization_accuracy = $result.summary.mean_evidence_localization_accuracy
    end_to_end_cost_usd = $economics.end_to_end_model_cost.total_usd
    parallel_candidate_latency_p95_ms = $economics.model_call_latency.parallel_candidates_p95_ms
    api_calls_this_replay = 0
}
Write-Output "V4 counterfactual replay complete. No API calls were made."
Write-Output "Report: $report"
$summary | ConvertTo-Json
