# Inventory-v0 Final Evaluation Results

## Decision

The frozen `terra-luna-sol-full-catalog-routed-v2` pipeline achieved strong
held-out test performance, but it does **not** satisfy every provisional MVP
release gate. Treat this as a successful evaluation milestone, not a completed
MVP release declaration.

No prompt, routing, model, reference, or evaluator changes were made after the
15-sample validation result was reviewed. The frozen pipeline was then run once
on the 15-sample test split.

## Final test result

| Metric | Terra baseline | Routed v2 |
|---|---:|---:|
| Whole-image exact accuracy | 73.33% (11/15) | **93.33% (14/15)** |
| Product precision | 95.65% | **100%** |
| Product recall | 91.67% | **100%** |
| Mean exact pair accuracy | 77.33% | **93.33%** |
| Mean total count error per image | 0.4000 | **0.0667** |
| Evidence reference coverage | 0% | **100%** |
| Evidence localization accuracy | 0% | **96.67%** |
| Prediction contract validity | 100% | **100%** |

Coverage was complete: 15 expected predictions, 15 present, zero missing, and
zero unexpected. All outputs used the single bundle version
`terra-luna-sol-full-catalog-routed-v2`.

The routing policy sent six identity disagreements to Sol. Five were exactly
correct. All nine Luna/Terra agreements were exactly correct. Relative to
Terra, routed v2 corrected `inv_0019`, `inv_0045`, and `inv_0065` without
regressing an exact sample.

The sole remaining error was `inv_0062`: the identity
`franken_tafelreiniger` was correct, but the pipeline predicted quantity 3
instead of the ground-truth quantity 4.

## End-to-end pipeline economics

The evaluator's per-prediction cost and latency fields describe the selected
final component. They are not end-to-end figures for this multi-stage pipeline.
Summing the actual model stages gives:

| Measure | Result |
|---|---:|
| Total API cost for 15 test images | $0.884347 |
| Mean API cost per image | $0.05895647 |
| Median API cost per image | $0.03384440 |
| Serial model-call latency p50 | 9.367 s |
| Serial model-call latency p95 | 21.064 s |
| Parallel Luna/Terra candidate latency p50 | 6.714 s |
| Parallel Luna/Terra candidate latency p95 | 18.385 s |

These latency calculations cover model-call time, not network orchestration or
other production service overhead. The parallel estimate assumes Luna and
Terra aggregate calls run concurrently; subsequent evidence and conditional
Sol calls remain sequential.

## Provisional release-gate assessment

The closest implemented benchmark metric is used where a specification term
does not map one-to-one to an evaluator field.

| Provisional gate | Observed | Status |
|---|---:|---|
| Schema-valid succeeded responses = 100% | 100% | Pass |
| Exact count accuracy >= 95% | 93.33% whole-image exact | **Fail** |
| Evidence coverage >= 98% | 100% | Pass |
| Unsupported-field hallucination <= 1% | Not directly measured | Not established |
| p95 extraction latency <= 8 s | 18.385 s optimistic model-call estimate | **Fail** |
| Median extraction cost <= $0.03 | $0.033844 end-to-end API cost | **Fail** |
| Calibration reported separately | No calibration metric implemented | Incomplete |

The exact-accuracy miss is one image. That does not justify changing the locked
pipeline against the test set. Future work should start with a new benchmark
version or a newly reserved evaluation split.

## Reproduce

Run the frozen workflow:

```powershell
.\evaluation\run_final_test_v2.ps1
```

Regenerate the end-to-end cost and latency summary from the stored outputs:

```powershell
py -3.11 evaluation\src\summarize_pipeline.py `
  --report evaluation\reports\test-routed-policy-v2.json `
  --luna evaluation\runs\test-luna-references-v0\predictions `
  --terra evaluation\runs\test-terra-references-v0\predictions `
  --evidence evaluation\runs\test-luna-terra-evidence-v0\predictions `
  --sol evaluation\runs\test-sol-full-catalog-v2\predictions `
  --decisions evaluation\runs\test-routed-policy-v2\routing-decisions `
  --output evaluation\reports\test-routed-policy-v2-pipeline-summary.json
```

Primary artifacts:

- `reports/test-routed-policy-v2.json`
- `reports/test-terra-references-v0.json`
- `reports/test-routed-policy-v2-pipeline-summary.json`
- `runs/test-routed-policy-v2/run-config.json`
- `runs/test-routed-policy-v2/run-summary.json`

## Interpretation

The test set contains only 15 images, so 93.33% should not be presented as a
precise production-wide accuracy estimate. It is evidence that the routing
strategy generalized well to this frozen split. Production readiness still
requires a larger representative benchmark, explicit confidence calibration,
and meeting the published latency, cost, and exact-count gates.
