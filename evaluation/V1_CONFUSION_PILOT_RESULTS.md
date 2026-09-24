# V1 confusion-aware pilot results

## Decision

Do **not** promote the broad confusion-aware v3 policy. It did not improve
whole-image exact accuracy and increased end-to-end cost.

This was a development experiment on the previously observed v0 validation
split. It is not fresh validation evidence. The frozen v0 test split was not
used.

## Comparison

| Metric | Full-catalog v2 | Confusion-aware v3 | Change |
|---|---:|---:|---:|
| Whole-image exact accuracy | 66.67% (10/15) | 66.67% (10/15) | 0 |
| Product precision | 88.57% | 88.24% | -0.34 pp |
| Product recall | 86.11% | 83.33% | -2.78 pp |
| Mean exact-pair accuracy | 76.08% | 81.08% | +5.00 pp |
| Mean count error per image | 1.0000 | 1.0667 | +0.0667 |
| Evidence localization accuracy | 81.71% | 83.37% | +1.67 pp |
| End-to-end API cost | $1.043470 | $1.140226 | +9.27% |
| Mean end-to-end cost per image | $0.069565 | $0.076015 | +9.27% |
| Parallel-candidate latency p95 | 25.595 s | 23.323 s | -8.88% |

The latency change is run-to-run observational evidence, not proof that adding
reference crops reduces latency. Both results remain far above the provisional
8-second p95 gate.

## Sample-level effect

Only two final predictions changed:

- `inv_0021` improved from incorrect to exact. The Adelholzener family crops
  helped recover two instances of `adelholzener_classic_naturell_02`.
- `inv_0071` regressed from exact to incorrect. The broad set of 12 targeted
  crops changed a correct `douwe_egberts_professional_ground_coffee` instance
  into two incorrect `gepa_bio_caffe_crema` instances.

The remaining 13 final predictions were unchanged. The exchange of one fix for
one regression left exact accuracy unchanged.

## Interpretation

High-detail references can help a narrow within-family disagreement, but
broadly adding every family triggered by a complex multi-product scene creates
anchoring and counting risk. More reference images are not automatically more
useful.

The next experiment should restrict targeted crops to **isolated within-family
identity disagreements**. Multi-family scenes should retain the v2 full-catalog
path. This hypothesis is based on development data and must eventually be
tested against a newly reserved validation split.

## Artifacts

- `reports/validation-routed-policy-v2-pipeline-summary.json`
- `reports/validation-routed-policy-v3.json`
- `reports/validation-routed-policy-v3-pipeline-summary.json`
- `reports/v1-confusion-aware-failure-analysis.json`
- `runs/validation-sol-confusion-aware-v3/run-config.json`
- `runs/validation-sol-confusion-aware-v3/run-summary.json`
- `runs/validation-routed-policy-v3/run-config.json`
- `runs/validation-routed-policy-v3/run-summary.json`
