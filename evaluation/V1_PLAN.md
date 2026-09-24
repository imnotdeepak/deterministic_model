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

Result: the broad v3 strategy was rejected. It exchanged one corrected sample
for one regression, leaving exact accuracy at 10/15 while increasing
end-to-end cost by 9.27%. See `V1_CONFUSION_PILOT_RESULTS.md`. The next
hypothesis restricts targeted crops to isolated within-family disagreements.

The isolated-confusion v4 counterfactual replay improved exact accuracy from
10/15 to 11/15 with no API calls. It is the leading development candidate, but
the replay is not independent validation evidence. See
`V1_ISOLATED_CONFUSION_REPLAY_RESULTS.md`.

## Locked v1 benchmark

`datasets/inventory-v1` is now locked before any v1 validation or test model
run. It contains 300 images from 116 scenes with zero scene overlap against
inventory-v0 and the visual reference sources.

- development: 209 images / 81 scenes
- validation: 45 images / 17 scenes
- test: 46 images / 18 scenes
- immutable content SHA-256:
  `7d5852644d069cb7163b6242677fc1ff7f863be950d4b4bfa7908cf3a9b19863`

The next model action is a v4 validation run. The 46-image test split must stay
untouched until the pipeline is selected and frozen from validation results.

Run validation from a PowerShell session containing `OPENAI_API_KEY`:

```powershell
.\evaluation\run_v1_validation_v4.ps1
```

The script verifies the dataset lock, processes only the 45-image validation
split, and produces evaluation, end-to-end economics, and failure-analysis
reports. It does not access the test split.

Result: isolated-confusion v4 was rejected as the release candidate. It
improved whole-image exact accuracy from Terra's 73.33% to 80.00%, but missed
the accuracy, precision, recall, localization, cost, and latency gates. Three
incorrect Luna/Terra agreements also cap any disagreement-only resolver below
the 95% exact-accuracy gate. See `V1_VALIDATION_V4_RESULTS.md`.

The next experiment is a development-only comparison of Terra aggregate output
against a one-call Terra instance-localization design. Validation and test stay
sealed during this pilot.

Preview the fixed 20-image development pilot without API calls:

```powershell
.\evaluation\run_v1_terra_instances_pilot.ps1 -DryRun
```

Run the paid comparison after configuring `OPENAI_API_KEY`:

```powershell
.\evaluation\run_v1_terra_instances_pilot.ps1
```

Both arms use the same development offset and limit. Custom development slices
can be selected with `-Offset` and `-Limit`; the runner rejects ranges outside
the 209-image development split.

Result: the first 20-image pilot supported the hypothesis. Both arms reached
19/20 exact images, while instance localization improved product precision and
recall to 100%, achieved 98.33% evidence localization accuracy, and stayed
inside the cost and latency gates. See
`V1_TERRA_INSTANCES_PILOT_RESULTS.md`.

The next check runs only the promising instance arm on a larger, non-overlapping
development slice:

```powershell
.\evaluation\run_v1_terra_instances_pilot.ps1 `
  -Offset 20 -Limit 40 -InstancesOnly
```

Result: v5 failed to generalize at the required quality level. On the larger
40-image development slice it reached 87.50% exact accuracy, 95.12% precision,
97.50% recall, and 92.50% localization accuracy. Cost and latency passed. The
dominant error was omission of separately visible objects whose identifying
face was hidden. See `V1_TERRA_INSTANCES_SCALE_RESULTS.md`.

V6 tests an exhaustive-instance prompt on the observed failure clusters and
nearby correct controls. It remains a one-call Terra design and uses only
development data:

```powershell
.\evaluation\run_v1_exhaustive_instances_pilot.ps1 -Offset 24 -Limit 8
.\evaluation\run_v1_exhaustive_instances_pilot.ps1 -Offset 54 -Limit 6
```

Result: v6 was rejected. It improved the same-sample exact result from 9/14 to
10/14 and reached 100% product recall, but precision fell to 72.73% and 85.71%
on the two slices because uncertain objects were forced into incorrect catalog
classes. See `V1_EXHAUSTIVE_INSTANCES_RESULTS.md`.

Further prompt-only API experiments are paused. The next proposed architecture
is a supervised detector trained from D2S bounding-box annotations with locked
validation/test scene exclusions. This is a deliberate architecture change and
must be selected before implementation.

## Definition of done

- A written error hypothesis is supported by development-set measurements.
- Unit tests cover every changed routing/counting behavior.
- The new benchmark split is recorded and immutable before v1 runs begin.
- The selected pipeline meets the gates on new validation data.
- A single final run is performed on the new test set.
- Results, configuration, and costs are committed and tagged `inventory-v1-final`.
