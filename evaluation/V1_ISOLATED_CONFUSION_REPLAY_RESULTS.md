# V1 isolated-confusion replay results

## Decision

Keep the isolated-confusion v4 rule as the leading v1 development candidate.
Do not treat this replay as independent validation evidence.

The replay deterministically combined already-saved v2 and v3 adjudications:

- use targeted v3 adjudication only when the Luna/Terra identity symmetric
  difference is contained in exactly one confusion family;
- use standard full-catalog v2 adjudication for all complex or multi-family
  disagreements.

No API calls were made.

## Comparison with v2

| Metric | Full-catalog v2 | Isolated-confusion v4 replay | Change |
|---|---:|---:|---:|
| Whole-image exact accuracy | 66.67% (10/15) | **73.33% (11/15)** | +6.67 pp |
| Product precision | 88.57% | **91.18%** | +2.61 pp |
| Product recall | 86.11% | 86.11% | 0 |
| Mean exact-pair accuracy | 76.08% | **82.75%** | +6.67 pp |
| Mean count error per image | 1.0000 | **0.8667** | -0.1333 |
| Evidence localization accuracy | 81.71% | **85.04%** | +3.33 pp |
| Counterfactual end-to-end cost | $1.043470 | $1.059622 | +1.55% |

Latency and cost combine calls made at different times, so they are useful for
accounting and rough planning but are not controlled performance measurements.

## Selection behavior

Targeted v3 outputs were selected for:

- `inv_0003` — coffee package disagreement
- `inv_0021` — Adelholzener bottle disagreement
- `inv_0025` — apple disagreement

Standard v2 outputs were retained for the five complex disagreements:

- `inv_0010`
- `inv_0068`
- `inv_0070`
- `inv_0071`
- `inv_0076`

This preserved the correct v2 result for `inv_0071` while retaining the v3
correction for `inv_0021`.

## Interpretation

The replay supports the hypothesis that targeted references are useful only
when the ambiguity is narrow and structurally isolated. It does not establish
generalization because both the rule and the component outputs were selected
using observed development data.

The next valid step is to create a newly reserved v1 benchmark split, lock its
manifest before running models, and evaluate v4 without further tuning on those
labels.
