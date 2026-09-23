# Inventory v1 plan

## Objective

Improve the inventory pipeline beyond the frozen `inventory-v0-final` result
without tuning against its 15-image test split.

The v0 test result remains a historical regression result. The one known test
miss (`inv_0062`) may be documented, but it must not be used to choose v1
prompts, models, thresholds, or routing rules.

## Development protocol

1. Diagnose failures on development data and the already-observed v0
   validation data.
2. Categorize each failure as identity, quantity, localization, contract, or
   routing selection error.
3. Change one pipeline behavior at a time and record the hypothesis, run
   configuration, accuracy, latency, and cost.
4. Create a new representative benchmark expansion before declaring v1.
5. Reserve new validation and test samples before inspecting model outputs.
6. Choose the v1 pipeline using development and new validation results only.
7. Run the newly reserved test split once after the pipeline is locked.

Because the v0 validation labels and results have already influenced pipeline
design, they are development evidence for v1 and cannot be treated as a fresh
v1 validation set.

## Initial hypotheses

- Separate identity selection from instance counting. A model can recognize the
  right product while still missing an occluded or partially visible instance.
- Route on uncertainty signals that predict actual errors, not disagreement
  alone.
- Avoid expensive adjudication when the inexpensive candidates agree and have
  consistent instance evidence.
- Measure end-to-end cost and latency across every executed stage.

## Provisional v1 gates

These gates must be evaluated on a newly reserved, representative test split:

| Gate | Target |
|---|---:|
| Prediction coverage | 100% |
| Contract-valid predictions | 100% |
| Whole-image exact accuracy | >= 95% |
| Product precision | >= 98% |
| Product recall | >= 98% |
| Evidence reference coverage | >= 98% |
| Evidence localization accuracy | >= 95% |
| Median end-to-end API cost per image | <= $0.03 |
| End-to-end model-call latency p95 | <= 8 s |

Confidence calibration and unsupported-field hallucination need explicit
metrics before they can become enforceable release gates.

## First work item

Generate a reproducible failure analysis from the observed v0 validation run:

```powershell
py -3.11 evaluation\src\analyze_failures.py `
  --dataset evaluation\datasets\inventory-v0 `
  --predictions evaluation\runs\validation-routed-policy-v2\predictions `
  --split validation `
  --report evaluation\reports\validation-routed-policy-v2.json `
  --routing-decisions evaluation\runs\validation-routed-policy-v2\routing-decisions `
  --candidate-a evaluation\runs\validation-luna-references-v0\predictions `
  --candidate-b evaluation\runs\validation-terra-references-v0\predictions `
  --stage luna=evaluation\runs\validation-luna-references-v0\predictions `
  --stage terra=evaluation\runs\validation-terra-references-v0\predictions `
  --stage evidence=evaluation\runs\validation-luna-terra-evidence-v0\predictions `
  --stage sol=evaluation\runs\validation-sol-full-catalog-v2\predictions `
  --output evaluation\reports\v1-validation-failure-analysis.json
```

The analyzer refuses `--split test` unless the caller supplies the explicit
`--allow-frozen-test` override.

## Confusion-aware pilot

The first v1 experiment supplements the four full-catalog reference sheets
with high-detail crops for catalog-defined visual confusion families. It
reuses the saved Luna, Terra, and evidence outputs, so only the eight identity
disagreements invoke Sol.

Validate every input without making API calls:

```powershell
.\evaluation\run_v1_confusion_pilot.ps1 -DryRun
```

Run the paid development pilot after setting `OPENAI_API_KEY` in the current
PowerShell session:

```powershell
.\evaluation\run_v1_confusion_pilot.ps1
```

This experiment deliberately uses the old `validation` split as observed v1
development evidence. Its result is not fresh validation evidence, and the
script does not access the frozen v0 test split.

## Definition of done

- A written error hypothesis is supported by development-set measurements.
- Unit tests cover every changed routing/counting behavior.
- The new benchmark split is recorded and immutable before v1 runs begin.
- The selected pipeline meets the gates on new validation data.
- A single final run is performed on the new test set.
- Results, configuration, and costs are committed and tagged `inventory-v1-final`.
