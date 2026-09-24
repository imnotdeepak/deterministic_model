# V1 exhaustive-instance v6 results

## Decision

Reject the v6 exhaustive-instance prompt. Do not run it on a broader slice,
validation, or test.

V6 was evaluated on 14 development images covering the v5 failure clusters
and nearby correct controls. It increased recall by forcing uncertain objects
into catalog classes, but the resulting false identities reduced precision too
far. Validation and test remained untouched.

## Results

| Slice | Samples | Exact accuracy | Precision | Recall | Localization | Median cost | p95 latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| offset 24 | 8 | 75.00% | 72.73% | 100% | 87.50% | $0.028736 | 7.2161 s |
| offset 54 | 6 | 66.67% | 85.71% | 100% | 83.33% | $0.029270 | 4.2228 s |
| combined | 14 | 71.43% (10/14) | — | 100% | — | — | — |

The two runs cost `$0.408076` in total. Coverage, contract validity, and
evidence reference coverage were 100% in both runs.

## Comparison with v5 on the same images

V5 produced 9/14 exact images. V6 produced 10/14, a net gain of one:

- repaired `inv_0031`, a rear/side-facing coffee package;
- repaired `inv_0068`, the first two-bottle Apfelschorle view;
- regressed `inv_0066` by changing one correct bottle identity;
- preserved the other ten sample outcomes;
- left `inv_0025`, `inv_0026`, and `inv_0069` incorrect.

For the tea scene, v6 found all three physical packages but assigned the
rear-facing packages to different tea SKUs. This converted omission errors
into false identities and increased total count error. The exhaustive rule
therefore addressed object enumeration but not fine-grained classification.

## Conclusion

Prompt-only changes have now exposed a stable limitation: the general vision
model can either omit uncertain views or force them into incorrect fine-grained
classes. The result remains well below the 95% exact, 98% precision, and 95%
localization gates.

The next technically justified direction is a supervised detector trained on
the boxed D2S examples, with strict scene exclusions for the locked validation
and test splits. The repository already has the raw D2S annotations and a
CPU-only Ultralytics/PyTorch environment. Dataset export and training
configuration can be built locally, but practical model training should use a
GPU if available.
