# V1 validation v4 results

## Decision

Reject isolated-confusion v4 as the v1 release candidate. Keep the 46-image
test split sealed.

The workflow completed successfully on all 45 locked validation samples, but
only prediction coverage, contract validity, and evidence coverage met their
provisional gates. Exact accuracy improved over Terra alone, but the remaining
quality, cost, and latency gaps are too large to justify a final test run.

## Locked benchmark

- dataset: `inventory-v1` version `1.0.0`
- split: `validation`
- samples: 45
- immutable content SHA-256:
  `7d5852644d069cb7163b6242677fc1ff7f863be950d4b4bfa7908cf3a9b19863`
- missing predictions: 0
- test samples accessed: 0

## Candidate comparison

| Metric | Luna | Terra | Routed v4 |
|---|---:|---:|---:|
| Whole-image exact accuracy | 62.22% | 73.33% | **80.00%** |
| Product precision | 86.13% | 94.20% | **95.14%** |
| Product recall | 77.12% | 84.97% | **89.54%** |
| Mean exact-pair accuracy | 78.66% | 91.52% | **92.29%** |
| Mean count error per image | 1.3778 | 0.7333 | **0.5778** |

Routed v4 produced 36 exact images, three more than Terra alone.

## Release-gate assessment

| Gate | Target | Result | Status |
|---|---:|---:|---|
| Prediction coverage | 100% | 100% | pass |
| Contract-valid predictions | 100% | 100% | pass |
| Whole-image exact accuracy | >= 95% | 80.00% | fail |
| Product precision | >= 98% | 95.14% | fail |
| Product recall | >= 98% | 89.54% | fail |
| Evidence reference coverage | >= 98% | 100% | pass |
| Evidence localization accuracy | >= 95% | 93.39% | fail |
| Median end-to-end API cost per image | <= $0.03 | $0.0340724 | fail |
| End-to-end model-call latency p95 | <= 8 s | 23.5106 s | fail |

Total model cost for the 45-image run was `$2.5537442`.

## Routing behavior

The policy copied 30 Luna/Terra agreements and sent 15 identity disagreements
to Sol. Within those 15 adjudications:

- five correct Terra results were preserved;
- one Luna-only correct result was selected;
- three cases that both candidates missed were rescued;
- one correct Terra result was replaced by an incorrect adjudication;
- six cases remained incorrect.

The net improvement over Terra was three exact images.

Three of the 30 candidate agreements were incorrect. Consequently, even a
perfect resolver operating only on disagreements could reach at most 42/45
exact images (93.33%), below the 95% gate. The evidence pass reported matching
instance counts and high confidence on all three hidden agreement errors, so
the current runtime signals cannot identify them reliably.

## Failure profile

The routed output had ten samples with at least one evaluated failure:

- identity: 9
- localization: 10
- quantity: 2
- routing selection: 1

The hardest failures clustered around dense scenes and recurring look-alike
families, including Adelholzener bottles and cucumber versus zucchini. Dense
12- and 13-product scenes showed substantial omission errors.

## Next hypothesis

The always-on four-stage pipeline cannot meet the cost or latency gates, and
adding more adjudication cannot overcome incorrect candidate agreements. The
next development experiment should test a one-call Terra instance-localization
baseline. It derives counts and canonical evidence from detected instances in
the same response, potentially replacing the aggregate candidate, separate
evidence, and routine adjudication calls.

Run the new design first on a small, fixed development slice. Do not revisit
validation until the development result justifies it, and do not access the
test split until a pipeline is frozen.
