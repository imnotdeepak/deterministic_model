# V1 Terra instance-localization scale-up results

## Decision

Reject the v5 prompt as a release candidate, but retain the one-call
instance-localization architecture for one targeted prompt revision.

The scale-up evaluated 40 development samples beginning at offset 20. These
samples did not overlap the first 20-image pilot. Validation and test remained
untouched.

## Results

| Metric | Gate | Result | Status |
|---|---:|---:|---|
| Prediction coverage | 100% | 100% | pass |
| Contract-valid predictions | 100% | 100% | pass |
| Whole-image exact accuracy | >= 95% | 87.50% (35/40) | fail |
| Product precision | >= 98% | 95.12% | fail |
| Product recall | >= 98% | 97.50% | fail |
| Evidence reference coverage | >= 98% | 100% | pass |
| Evidence localization accuracy | >= 95% | 92.50% | fail |
| Median API cost per image | <= $0.03 | $0.028500 | pass |
| Model-call latency p95 | <= 8 s | 4.489 s | pass |

Total cost was `$1.145916`.

## Failure analysis

Five images had incorrect product data and one additional image had correct
data with incomplete localization:

- `inv_0025`: omitted one of three peppermint-tea packages.
- `inv_0026`: replaced one peppermint-tea package with fennel tea.
- `inv_0031`: replaced a rear/side-facing coffee package with spaghetti.
- `inv_0068`: omitted one of two Apfelschorle bottles.
- `inv_0069`: omitted one of two Apfelschorle bottles.
- `inv_0071`: product data was exact, but only two of three bottles localized.

Four product failures occur in two repeated physical scenes. Inspection shows
that the omitted instances display a cap, rear, side, bottom, or washed-out
face while another object in the same image exposes the product identity.

## Next hypothesis

The v5 instance prompt contains conflicting incentives: it requests every
physical instance but also tells the model to omit products that cannot be
matched confidently. The observed failures indicate that the model follows the
omission rule for hard views.

V6 keeps the one-call architecture and structured output, but explicitly
requires a best-supported catalog assignment for every plausible retail object
and uses confidence to represent uncertainty. A small development pilot should
cover the two failure clusters plus nearby correct controls before any broader
run.
