# World API — Inventory Evaluation Starter v2

This benchmark exists **before model selection** so every model or pipeline is judged against the same frozen task.

The locked v2 outcome and honest provisional release-gate assessment are in
[`FINAL_RESULTS.md`](FINAL_RESULTS.md).

## What v2 fixes

The evaluator now:

- counts missing predictions as failures
- reports expected/predicted/missing/unexpected sample counts
- validates annotation and prediction names against `catalog.json`
- rejects duplicate product entries
- validates image existence and actual dimensions with Pillow
- verifies annotation quantities against instance counts
- validates bounding boxes and image bounds
- separates evidence reference coverage from evidence localization correctness
- scores evidence localization using IoU (default threshold: `0.50`)
- uses union-based exact pair accuracy so false-positive products are penalized
- reports both total count error per image and per-product count MAE
- uses canonical API evidence objects in the prediction example
- records dataset/model/evaluator/environment hashes and versions

## Directory layout

```text
evaluation/
├── datasets/
│   └── inventory-v0/
│       ├── images/
│       ├── annotations/
│       ├── catalog.json
│       ├── schema.json
│       ├── dataset_metadata.json
│       └── manifest.jsonl
├── src/
│   └── evaluate.py
├── reports/
├── prediction.example.json
├── requirements.txt
└── COLLECTION_GUIDE.md
```

## Dataset integrity is a prerequisite

The evaluator aborts before model scoring if the benchmark itself is invalid.

It enforces:

- image exists
- image opens successfully
- declared width/height match the actual file
- annotation `sample_id` matches the manifest
- annotation image path matches the manifest
- annotation `data` validates against `schema.json`
- all product names exist in the frozen catalog
- no duplicate product names appear in an annotation
- one valid bounding box exists per annotated visible instance
- declared quantities equal instance counts
- boxes have positive dimensions
- boxes remain within original image bounds

A bad benchmark must not silently produce a model score.

## Missing predictions

A missing prediction is a failure, not an ignored sample.

Reports contain:

```text
expected_samples
predicted_samples
missing_predictions
unexpected_predictions
prediction_coverage
```

Missing samples remain in metric denominators and contribute false negatives/count error.

Unexpected predictions are reported but are not substituted for expected samples.

## Product metrics

### Product precision / recall

Measures whether canonical product types were identified.

### Exact pair accuracy over union

For each image, compare the union of expected and predicted product names.

A pair is correct only when:

```text
product name matches
AND
quantity matches exactly
```

False-positive products therefore lower the score.

### Whole-image exact accuracy

An image is correct only if the complete expected product→quantity mapping matches exactly and the prediction contract is valid.

### Count errors

The report contains:

```text
mean_total_absolute_count_error_per_image
mean_absolute_count_error_per_product
```

The names are intentionally explicit.

## Evidence metrics

Evidence is evaluated in two separate ways.

### 1. Evidence reference coverage

Checks whether returned non-null scalar fields contain non-empty evidence references that resolve to real evidence IDs.

### 2. Evidence localization correctness

For the predicted product at `/products/{i}`, the evaluator gathers unique boxes referenced by its `name` and `quantity` metadata.

It then performs one-to-one matching against ground-truth boxes for the same canonical product.

Default match threshold:

```text
IoU >= 0.50
```

Reported metrics:

```text
evidence_localization_accuracy
evidence_localization_precision
evidence_localization_recall
mean_matched_iou
```

`evidence_localization_accuracy` is:

```text
matched boxes / max(ground-truth boxes, predicted evidence boxes)
```

This penalizes both missing and extra localization evidence.

## Evidence contract

`prediction.example.json` now follows the canonical image evidence contract, including:

```text
id
media_id
source_type
coordinate_space
bbox
rotation_degrees
page_index
frame_index
timestamp_ms
model_version
media_available
```

For `inventory-v0`:

```text
source_type      = image_region
coordinate_space = original_pixels
```

## Reproducibility metadata

Every generated report records:

- dataset ID/version
- manifest SHA-256
- schema SHA-256
- catalog SHA-256
- evaluator version
- model/bundle version(s)
- UTC evaluation timestamp
- exact command
- Python version
- platform
- dependency versions
- IoU threshold

## Run

Install:

```bash
pip install -r evaluation/requirements.txt
```

Run:

```bash
python evaluation/src/evaluate.py \
  --dataset evaluation/datasets/inventory-v0 \
  --predictions evaluation/prediction.example.json \
  --split development \
  --output evaluation/reports/inventory-v0-baseline.json
```

Change the localization threshold if needed:

```bash
--iou-threshold 0.50
```

## First implementation milestone

Still keep the first vertical slice tiny:

```text
one image
→ one model
→ schema-valid data
→ field_metadata
→ canonical evidence
→ evaluator
→ reproducible report
```

Do not build the dashboard, queues, PDFs, video, billing, or multi-model routing yet.


### Precision when nothing is predicted

If a split contains expected products but the model returns no predictions, product precision is reported as `0.0` rather than `1.0`. This avoids making a zero-coverage model look artificially strong.

The report separates:

```text
schema_valid_rate_over_expected_samples
schema_valid_rate_among_present_predictions
```

so coverage failures and schema-contract failures remain distinguishable.

## Malformed evidence behavior

Evidence that fails bounding-box or coordinate validation:

- remains listed in `prediction_validation_errors`
- makes the prediction contract invalid
- is excluded from `evidence_by_id`
- is not used for IoU/localization scoring

This prevents malformed evidence from crashing the evaluator while still penalizing the prediction.

## Import MVTec D2S

The importer uses the public annotated D2S training and validation splits, keeps
source files immutable, and creates a deterministic scene-grouped benchmark:

```bash
python evaluation/src/import_d2s.py \
  --source ./data \
  --output ./evaluation/datasets/inventory-v0 \
  --limit 100 \
  --seed 42 \
  --dry-run
```

Remove `--dry-run` to generate the benchmark. If generated output already
exists, the importer refuses to replace it unless `--force` is supplied.

See `datasets/inventory-v0/D2S_IMPORT_NOTES.md` for source-format, grouping,
conversion, and licensing details. D2S is licensed CC BY-NC-SA 4.0 and is not
licensed for commercial use.

## Run the OpenAI vision baseline

Keep the API key in the environment rather than a source file:

```powershell
$env:OPENAI_API_KEY = "your-key"
```

Verify sample selection without making API calls:

```powershell
py -3.11 evaluation\src\run_baseline.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 5 `
  --output evaluation\runs\baseline-v0 `
  --dry-run
```

Remove `--dry-run` to create predictions. The default model is
`gpt-5.6-luna`; use `--model` to compare another image-capable model. The
runner uses strict structured output, stores raw responses separately, resumes
completed samples, records usage/latency, and sends requests with `store=false`.

## Build and use visual catalog references

Generate two opposite-view product examples per catalog item from source scenes
that do not occur anywhere in the benchmark manifest:

```powershell
py -3.11 evaluation\src\build_reference_catalog.py `
  --source data `
  --dataset evaluation\datasets\inventory-v0 `
  --output evaluation\references\inventory-v0
```

The generated `reference-manifest.json` records every source image, crop, sheet
hash, and excluded benchmark scene. Run a separate experiment with the sheets:

```powershell
py -3.11 evaluation\src\run_baseline.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 5 `
  --output evaluation\runs\luna-references-v0 `
  --model gpt-5.6-luna `
  --references evaluation\references\inventory-v0\reference-manifest.json `
  --reference-detail high `
  --fail-fast
```

Reference sheets precede the separately labeled target image in the request.
They increase image-token usage, so compare their measured accuracy and cost
against the no-reference baseline before scaling up.

## Instance localization and evidence

Use `--instance-localization` to request one normalized box per visible product
instance. The runner validates those instances, converts boxes to original-image
pixels, derives quantities by counting instances, and emits canonical evidence
and field metadata:

```powershell
py -3.11 evaluation\src\run_baseline.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 5 `
  --output evaluation\runs\luna-instance-references-v0 `
  --model gpt-5.6-luna `
  --references evaluation\references\inventory-v0\reference-manifest.json `
  --reference-detail high `
  --instance-localization `
  --fail-fast
```

Score the same limited sample set, including evidence IoU metrics:

```powershell
py -3.11 evaluation\src\evaluate.py `
  --dataset evaluation\datasets\inventory-v0 `
  --predictions evaluation\runs\luna-instance-references-v0\predictions `
  --split development `
  --limit 5 `
  --output evaluation\reports\luna-instance-references-v0-5.json
```

Vision-model localization and counting are experimental: the official OpenAI
vision guidance notes that precise spatial localization can be difficult and
object counts may be approximate. Always use evaluator IoU and count metrics
rather than treating generated boxes as ground truth.

### Two-stage identity and localization

Use `--two-stage` to run an aggregate identity pass first and constrain the
instance-localization schema to those product candidates. The option implies
instance localization. Both responses are stored in the sample's raw-response
file, and prediction usage/cost includes per-stage and combined totals:

```powershell
py -3.11 evaluation\src\run_baseline.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 5 `
  --output evaluation\runs\luna-two-stage-references-v0 `
  --model gpt-5.6-luna `
  --references evaluation\references\inventory-v0\reference-manifest.json `
  --reference-detail high `
  --two-stage `
  --fail-fast
```

If stage one returns no candidates, stage two falls back to the full catalog
instead of being forced to return no instances. This fallback is recorded in
`provider_metadata.identity_candidate_fallback_to_full_catalog`.

### Authoritative aggregate data with evidence

Use `--evidence-only` when an existing aggregate run already has the product
identity and quantity accuracy you want to preserve. The runner copies that
aggregate `data` unchanged and makes exactly one new API call per image to add
bounding-box evidence. Candidate products are prompt hints, not schema
restrictions, so localization can still use the full catalog:

```powershell
py -3.11 evaluation\src\run_baseline.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 5 `
  --output evaluation\runs\luna-authoritative-evidence-v0 `
  --model gpt-5.6-luna `
  --references evaluation\references\inventory-v0\reference-manifest.json `
  --reference-detail high `
  --evidence-only `
  --identity-predictions evaluation\runs\luna-references-v0\predictions `
  --fail-fast
```

Then evaluate the same five samples:

```powershell
py -3.11 evaluation\src\evaluate.py `
  --dataset evaluation\datasets\inventory-v0 `
  --predictions evaluation\runs\luna-authoritative-evidence-v0\predictions `
  --split development `
  --limit 5 `
  --output evaluation\reports\luna-authoritative-evidence-v0-5.json
```

For a single-product aggregate prediction, every localized box is attached to
that authoritative product even if the localization pass chooses a different
catalog label. For multi-product predictions, boxes attach only on exact name
matches; unmatched boxes are omitted rather than assigned speculatively.

### Resolve Luna/Terra disagreements with Sol

`run_disagreement_resolver.py` compares two completed aggregate runs. Samples
with identical product/quantity output are copied from an existing evidence
run without an API call. Only disagreements are sent to Sol for independent
identity, count, and box adjudication. Each adjudication includes the target
image and two in-memory reference crops per candidate product, rather than the
full four-sheet catalog:

```powershell
py -3.11 evaluation\src\run_disagreement_resolver.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 20 `
  --candidate-a evaluation\runs\luna-references-v0\predictions `
  --candidate-b evaluation\runs\terra-references-v0\predictions `
  --base-predictions evaluation\runs\luna-terra-authoritative-evidence-v0\predictions `
  --references evaluation\references\inventory-v0\reference-manifest.json `
  --output evaluation\runs\sol-disagreement-v0 `
  --model gpt-5.6-sol `
  --reasoning-effort low `
  --fail-fast
```

Preview the exact routing without making API calls by adding `--dry-run`. The
current 20-sample inputs route 12 agreements without a call and eight
disagreements to Sol. Interrupted runs resume completed output unless
`--overwrite` is supplied.

Evaluate the resolved bundle with:

```powershell
py -3.11 evaluation\src\evaluate.py `
  --dataset evaluation\datasets\inventory-v0 `
  --predictions evaluation\runs\sol-disagreement-v0\predictions `
  --split development `
  --limit 20 `
  --output evaluation\reports\sol-disagreement-v0-20.json
```

The resolver records a single pipeline bundle version on every prediction and
adds component-model and routing metadata, fixing the incomplete provenance in
the earlier hybrid report.

### Apply the frozen routing policy

After adjudication, compose the locked development policy without any API
calls. Exact Luna/Terra agreements use the existing hybrid prediction,
same-identity quantity disagreements retain Terra, and product-identity
disagreements use the existing Sol result:

```powershell
py -3.11 evaluation\src\compose_routing_policy.py `
  --dataset evaluation\datasets\inventory-v0 `
  --split development `
  --limit 20 `
  --candidate-a evaluation\runs\luna-references-v0\predictions `
  --candidate-b evaluation\runs\terra-references-v0\predictions `
  --base-predictions evaluation\runs\luna-terra-authoritative-evidence-v0\predictions `
  --adjudicated-predictions evaluation\runs\sol-disagreement-v0\predictions `
  --output evaluation\runs\routed-policy-v0
```

Evaluate the composed predictions:

```powershell
py -3.11 evaluation\src\evaluate.py `
  --dataset evaluation\datasets\inventory-v0 `
  --predictions evaluation\runs\routed-policy-v0\predictions `
  --split development `
  --limit 20 `
  --output evaluation\reports\routed-policy-v0-20.json
```

This rule was selected from the initial 20 development samples. Treat its
score there as tuned development performance and measure generalization only
on samples that were not used to choose the policy.

### Untouched holdout pilot

All generation and evaluation commands accept `--offset`, applied after stable
sample-ID sorting and before `--limit`. The included PowerShell workflow uses
offset 20 and limit 10, so none of the 20 samples used to select the routing
policy are included. It runs both aggregate candidates, Luna evidence, Sol
adjudication for identity disagreements only, the frozen composer, and the
evaluator:

```powershell
.\evaluation\run_holdout_pilot.ps1
```

Custom untouched ranges can be selected explicitly:

```powershell
.\evaluation\run_holdout_pilot.ps1 -Offset 30 -Limit 10
```

The script stops immediately if a stage fails and every paid runner resumes
completed predictions. The default report is written to
`evaluation/reports/holdout-20-10-routed-policy-v0.json`. Do not revise the
frozen `terra-count-sol-identity-v1` policy based on this holdout result if the
goal is an unbiased measurement.

### Full-catalog resolver v2

The v1 failure analysis showed that many identity disagreements could not be
recovered because Sol was restricted to the union of Luna and Terra names.
Add `--full-catalog-adjudication` to give Sol all four reference sheets and a
structured-output schema containing every catalog name. Luna and Terra remain
non-binding hints. Agreement samples still avoid a Sol call, and same-identity
quantity disagreements still retain Terra.

Run the complete v2 workflow on the 15-sample validation split with:

```powershell
.\evaluation\run_validation_v2.ps1
```

The script evaluates Terra and routed v2 side by side, writes the routed report
to `evaluation/reports/validation-routed-policy-v2.json`, and prints only a
compact summary. Paid stages resume existing outputs. Keep the 15-sample test
split untouched until the v2 policy has been accepted or rejected from this
validation result; do not tune further after inspecting test performance.

### Frozen v2 final test

After accepting v2 from validation, run the unchanged configuration exactly
once on the untouched 15-sample test split:

```powershell
.\evaluation\run_final_test_v2.ps1
```

The workflow uses separate `test-*` run directories, compares the Terra
baseline with routed v2, and writes the final report to
`evaluation/reports/test-routed-policy-v2.json`. It is resumable after an
interruption. Treat the resulting score as the final report card rather than
another prompt-tuning signal.
