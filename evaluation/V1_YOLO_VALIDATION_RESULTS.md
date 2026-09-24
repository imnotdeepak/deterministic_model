# YOLO validation results

## Decision

Reject the five-epoch YOLO candidate. The frozen configuration was evaluated
once on all 45 validation images:

- checkpoint SHA-256:
  `30d76b9b5dbea84097f21de6991d6e31d4586f1713e92b35b4ada9253ad0b08e`
- confidence: 0.35
- IoU threshold: 0.70
- image size: 960

The dataset lock was verified immediately before inference. The test split was
not accessed.

## Gate results

| Gate | Target | Result | Pass |
| --- | ---: | ---: | :---: |
| Prediction coverage | 100% | 100% (45/45) | Yes |
| Contract-valid predictions | 100% | 100% | Yes |
| Whole-image exact accuracy | >= 95% | 66.67% (30/45) | No |
| Product precision | >= 98% | 86.52% | No |
| Product recall | >= 98% | 79.74% | No |
| Evidence reference coverage | >= 98% | 100% | Yes |
| Evidence localization accuracy | >= 95% | 80.90% | No |
| Median cost per image | <= $0.03 | $0.00 | Yes |
| Model latency p95 | <= 8 s | 327 ms | Yes |

## Failure structure

Fifteen images were non-exact. Eleven belong to repeated-view identity
confusion clusters: Golden Delicious versus other apple classes, orange versus
clementine, closely related Adelholzener water variants, and one tea-class
confusion. The remaining four are dense multi-product scenes with substantial
under-detection. Because localization is class-aware, these identity errors
also reduce the reported localization score even when a box overlaps the
physical object.

The candidate passes the operational gates but does not generalize at the
required recognition quality. The configuration must not be adjusted using
these validation labels. Any new candidate requires training/development-only
selection and a newly reserved validation slice; the 46-image test split stays
sealed.

The machine-readable evaluation and failure analysis are:

- `reports/v1-validation-yolo-pilot5-conf035.json`
- `reports/v1-validation-yolo-pilot5-conf035-failure-analysis.json`
