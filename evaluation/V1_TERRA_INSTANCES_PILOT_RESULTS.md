# V1 Terra instance-localization pilot results

## Decision

Advance the one-call Terra instance-localization design to a larger untouched
development slice. Do not return to validation or access test yet.

The fixed 20-image pilot compared Terra aggregate output with Terra
instance-localization output on identical `inventory-v1` development samples
`inv_0001` through `inv_0020`.

## Results

| Metric | Aggregate control | Instance localization |
|---|---:|---:|
| Whole-image exact accuracy | 95.00% (19/20) | 95.00% (19/20) |
| Product precision | 95.00% | **100%** |
| Product recall | 95.00% | **100%** |
| Evidence reference coverage | 0% | **100%** |
| Evidence localization accuracy | 0% | **98.33%** |
| Prediction contract validity | 100% | 100% |
| Median API cost per image | $0.028052 | $0.028548 |
| Model-call latency p95 | 4.4678 s | **3.6589 s** |
| Total cost | $0.561064 | $0.575784 |

The instance arm cost `$0.01472` more over 20 images (2.62%) while producing
canonical evidence in the same call. It met every provisional release gate on
this small development slice.

Latency was measured in separate sequential runs and is not a controlled
benchmark, but both arms were comfortably below the eight-second gate.

## Error analysis

The two arms each missed one different sample:

- Aggregate `inv_0008`: one wrong product identity.
- Instance `inv_0017`: correct product identity, but two of three visible
  `ethiquable_gruener_tee_ceylon` packages were localized and counted.

The instance miss was therefore a pure quantity/localization error caused by
one omitted physical instance. Product identity precision and recall remained
100% across the slice.

## Interpretation

This pilot supports the v5 hypothesis: a single instance-localization call can
replace separate aggregate and evidence calls without sacrificing exact
accuracy. It materially improves evidence quality and stays within the cost
and latency gates.

The sample is too small and was used to select the design, so it is development
evidence rather than validation evidence. The next step is to evaluate only
the instance arm on a larger, non-overlapping development slice beginning at
offset 20. The validation and test splits remain untouched.
