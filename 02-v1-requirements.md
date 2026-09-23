# World API — V1 Requirements

## 1. Scope Model

The product will use three explicit release boundaries.

### MVP

**Images only**

Goal: prove schema-driven structured extraction with field-level uncertainty and canonical evidence.

### V1 Beta

**Images + PDFs**

Goal: extend the same extraction contract to multi-page visual documents.

### V1 GA

**Images + PDFs + sampled video**

Goal: support stable production APIs, async jobs, webhooks, rate limits, and production security controls.

---

## 2. MVP Inputs

Supported image formats:

```text
.jpg
.jpeg
.png
.webp
.heic
```

### Maximum limits

Initial implementation should make all limits configurable.

Recommended starting limits:

- maximum upload size: 20 MB
- maximum image dimension: 10,000 × 10,000 px
- maximum decoded pixel count: configurable
- one media object per extraction
- no animated image support in MVP

These are implementation defaults, not permanent product guarantees.

---

## 3. MVP Input Delivery

MVP should support two upload methods that create the same upload resource:

1. **Signed object-storage upload** — primary path for normal production use
2. **Direct multipart upload** — convenience path for small images

Both methods must produce an `upload_id` and enter the same validation lifecycle before extraction.

Extraction requests reference a previously created and validated upload.

MVP should **not** support arbitrary remote URLs.

Remote URLs may be added later only after:

- SSRF protection
- DNS/IP validation
- redirect restrictions
- content-type verification
- content-length limits
- private-network blocking
- malware scanning policy
- fetch timeout policy

---

## 4. Canonical Schema Format

World API should use:

> **JSON Schema Draft 2020-12**

as its canonical internal and external schema representation.

Pydantic models and future SDK-native types should compile to the canonical JSON Schema representation.

### Example

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "type": "object",
  "properties": {
    "products": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": {
            "type": "string"
          },
          "quantity": {
            "type": "integer",
            "minimum": 0
          }
        },
        "required": ["name", "quantity"],
        "additionalProperties": false
      }
    }
  },
  "required": ["products"],
  "additionalProperties": false
}
```

A human-friendly shorthand may be introduced later, but it should be explicitly named **World Schema Shorthand** and compile to JSON Schema.

### Supported MVP JSON Schema subset

The API uses Draft 2020-12 syntax but intentionally supports a constrained subset in the first release.

Supported:

- `type`
- `properties`
- `required`
- `items`
- `enum`
- `const`
- `minimum`
- `maximum`
- `exclusiveMinimum`
- `exclusiveMaximum`
- `minLength`
- `maxLength`
- `pattern` with bounded safe-regex execution
- `minItems`
- `maxItems`
- `uniqueItems`
- `additionalProperties`
- `description`
- `title`
- `$defs`
- local, non-recursive `$ref`
- type arrays containing `null`, such as `["string", "null"]`
- non-ambiguous nullable `anyOf`, such as a single concrete schema plus `{ "type": "null" }`

Limited or rejected in MVP:

- recursive `$ref`
- remote `$ref`
- ambiguous `oneOf`
- ambiguous `anyOf`
- `allOf` compositions that materially alter extraction semantics
- `patternProperties`
- `dependentSchemas`
- unbounded recursion
- schemas exceeding configured depth
- schemas exceeding configured property count
- excessively large enums
- regex patterns exceeding configured complexity limits

The service must reject unsupported or excessively complex schemas before inference begins.

Recommended initial safety limits:

```text
Maximum nesting depth      8
Maximum named properties   200
Maximum enum values        500
Maximum schema size        256 KB
Maximum regex length       512 characters
```

These limits should remain configuration-driven.

---

## 5. Pydantic

Python users may supply a Pydantic model through the SDK.

Example:

```python
from pydantic import BaseModel

class Product(BaseModel):
    name: str
    quantity: int

class Inventory(BaseModel):
    products: list[Product]
```

The SDK converts:

```text
Pydantic model
↓
Pydantic-generated JSON Schema
↓
Normalization / compatibility pass
↓
Supported JSON Schema Draft 2020-12 subset
↓
API request
```

The normalization pass must:

- preserve `$defs` when valid
- preserve local non-recursive `$ref`
- reject recursive references
- normalize nullable types into supported forms
- allow `anyOf` only when it is non-ambiguous and represents a nullable value
- reject unsupported compositions before the request is submitted
- enforce the same schema-size and complexity limits as the API

The server should not maintain a separate Pydantic-specific interpretation path. After SDK normalization, Pydantic-originated schemas and hand-written schemas use the same canonical validation and extraction path.

---

## 6. Extraction Request

Conceptual Python SDK:

```python
result = world.extract(
    input=upload,
    schema=Inventory,
    instruction="Count only products on the middle shelf.",
    min_confidence=0.90
)
```

MVP parameters:

- `input`
- `schema`
- `instruction`
- `min_confidence`
- optional `region`
- optional `idempotency_key`

---

## 7. Schema-Valid Data + Field Metadata Sidecar

`result.data` must validate against the caller's submitted JSON Schema exactly.

Perception metadata must **not** wrap or replace user-declared values.

Example caller data:

```json
{
  "products": [
    {
      "name": "Coca-Cola Zero",
      "quantity": 4
    }
  ]
}
```

Field metadata is returned separately and keyed by RFC 6901 JSON Pointer:

```json
{
  "field_metadata": {
    "/products/0/name": {
      "confidence": 0.98,
      "status": "verified",
      "evidence_refs": ["ev_1"]
    },
    "/products/0/quantity": {
      "confidence": 0.97,
      "status": "verified",
      "evidence_refs": ["ev_1", "ev_2", "ev_3", "ev_4"]
    }
  }
}
```

This preserves all three requirements:

1. `data` remains typed and schema-valid,
2. uncertainty stays field-level,
3. evidence remains attributable to exact output paths.

### Field status enum

Recommended initial values:

```text
verified
unverified
uncertain
not_observed
invalid
```

Definitions:

- `verified`: required deterministic checks and configured evidence/confidence policy passed
- `unverified`: a value exists but verification policy was not satisfied
- `uncertain`: the system has competing or insufficient evidence
- `not_observed`: no supported value could be observed in the input
- `invalid`: a candidate value failed structural or business-rule validation

### Missing or unresolved fields

If the caller's schema allows `null`, unresolved values may be represented as `null`.

If a required field cannot be populated without violating the submitted schema, the extraction must not return schema-invalid `data`. The extraction should fail with a machine-readable error such as `schema_unsatisfied`.

A low-confidence candidate may still be returned when it is schema-valid, but its sidecar status must clearly indicate `uncertain` or `unverified`. The API must never mutate the declared type merely to attach metadata.

---

## 8. Confidence Semantics

### Field confidence

Confidence is attached to a field or object value, not treated as a universal truth score.

A confidence value should only be interpreted within the model/version/task family for which calibration has been measured.

### Aggregate confidence

The response may include a separate aggregate score.

It must not replace field-level confidence.

Example:

```json
{
  "aggregate_confidence": 0.93
}
```

The aggregation algorithm must be documented and versioned.

### Threshold behavior

If a field is below `min_confidence`:

- its `field_metadata` status becomes `uncertain` or `unverified`
- the whole job does not automatically fail
- other fields may still be returned
- the value may be omitted or set to `null` only when allowed by the submitted schema

The caller may optionally configure a future strict mode to fail an extraction when required fields are unresolved.

### Calibration

A value of `0.95` should only be described as “approximately 95% empirical correctness” when calibration testing supports that interpretation for the relevant task family.

Until calibrated, label scores as model confidence rather than probability of correctness.

---

## 9. Verification Model

Verification must be decomposed.

### 9.1 Structural validation

Examples:

- valid JSON
- matches JSON Schema
- correct types
- required fields present
- enum membership
- numeric bounds

### 9.2 Business-rule validation

Optional user-defined or product-defined rules.

Examples:

- quantity ≥ 0
- subtotal + tax = total
- occupied ≤ total
- end_time ≥ start_time

### 9.3 Evidence coverage

Checks whether a field is linked to sufficient source evidence.

### 9.4 Cross-model agreement

May strengthen confidence but does not independently prove truth.

### 9.5 Empirical confidence calibration

Confidence should be calibrated using labeled evaluation data.

### 9.6 Human review

Some workflows may require human review when:

- confidence is below policy
- evidence is ambiguous
- high-value decisions depend on the result
- configured customer rules require review

---

## 10. JSON Pointer Convention

Every field-scoped path in the API uses RFC 6901 JSON Pointer relative to the root of `data`.

Examples:

```text
/products/0/name
/products/0/quantity
/order/total
```

Never:

```text
/data/products/0/name
```

This convention applies consistently to:

- `field_metadata`
- evidence linkage
- warnings
- field-scoped errors
- future correction/feedback APIs

---

## 11. Canonical Evidence Model

Every evidence item receives a stable ID.

Example:

```json
{
  "id": "ev_12",
  "media_id": "med_123",
  "source_type": "image_region",
  "coordinate_space": "original_pixels",
  "bbox": {
    "x": 122,
    "y": 84,
    "width": 80,
    "height": 206
  },
  "rotation_degrees": 0,
  "page_index": null,
  "frame_index": null,
  "timestamp_ms": null,
  "model_version": "detector-1.2.0"
}
```

### Coordinate spaces

MVP supports:

```text
original_pixels
normalized_0_1
```

Rules:

- pixel coordinates are measured against the original uploaded media after orientation metadata is normalized
- bounding boxes use `{x, y, width, height}`
- `x` and `y` identify the top-left edge
- right and bottom edges are **exclusive**
- normalized coordinates are relative to the original oriented media width and height
- normalized values are in the closed range `[0, 1]`
- requested crops do not change the returned coordinate system; evidence is mapped back to original-media coordinates

Every evidence object must declare which coordinate space it uses.

### Transform metadata

If media is resized, rotated, cropped, or normalized before inference, the system must retain transform metadata sufficient to map evidence back to original coordinates.

### Field linkage

Fields refer to evidence using:

```text
evidence_refs[]
```

All field paths use RFC 6901 JSON Pointer **relative to the root of `data`**.

Example:

```text
/products/0/quantity
```

The `/data` prefix is never included. This same convention applies to `field_metadata`, evidence linkage, warnings, and field-scoped errors.

### Indexing and identity

- `page_index` is zero-based
- `frame_index` is zero-based
- `timestamp_ms` is measured from the start of the source media
- evidence IDs are stable within an extraction and opaque to callers
- callers must not infer ordering or global identity from an evidence ID
- `upload_id` identifies the uploaded source object
- `media_id` identifies the normalized logical media artifact used by extraction
- one upload may produce multiple media artifacts, such as rendered PDF pages or sampled video frames

### One-to-many

One field may reference multiple evidence items.

One evidence item may support multiple fields.

### Retention

Evidence metadata may outlive raw media.

If source media has expired or been deleted, the API must indicate:

```text
media_available: false
```

rather than silently breaking evidence references.

---

## 12. MVP Result Envelope

`data` must validate against the submitted schema.

Example:

```json
{
  "id": "ext_123",
  "status": "succeeded",
  "schema_version": "1",
  "model_bundle": "world-image-v1",
  "data": {
    "products": [
      {
        "name": "Coca-Cola Zero",
        "quantity": 4
      }
    ]
  },
  "field_metadata": {
    "/products/0/name": {
      "confidence": 0.98,
      "status": "verified",
      "evidence_refs": ["ev_1"]
    },
    "/products/0/quantity": {
      "confidence": 0.97,
      "status": "verified",
      "evidence_refs": ["ev_1", "ev_2", "ev_3", "ev_4"]
    }
  },
  "aggregate_confidence": 0.96,
  "evidence": [],
  "warnings": [],
  "created_at": "2026-09-17T22:00:00Z"
}
```

`aggregate_confidence` is optional and informational. It must never be interpreted as replacing field-level metadata.

---

## 13. Regions

Optional region input:

```json
{
  "x": 120,
  "y": 200,
  "width": 800,
  "height": 600,
  "coordinate_space": "original_pixels"
}
```

MVP supports rectangular regions only.

Polygonal regions can be added later.

---

## 14. V1 Beta: PDFs

PDF support adds:

- page rendering
- per-page image normalization
- OCR
- layout extraction
- page-index evidence
- asynchronous processing for larger files

Initial configurable limits:

- maximum pages
- maximum file size
- maximum render resolution
- maximum processing time

---

## 15. V1 GA: Sampled Video

Video support adds:

- MP4
- MOV
- WEBM
- frame sampling
- frame-index evidence
- timestamps
- cross-frame aggregation

V1 GA does not promise real-time tracking.

Sampled-video output should clearly distinguish:

```text
observed_in_sampled_frames
```

from:

```text
observed_continuously
```

---

## 16. Nonfunctional Requirements

Initial targets should be treated as engineering goals and revised after benchmarks.

### Availability

- MVP target: best-effort beta
- V1 GA target: define formal SLA before launch

### Latency

Measure:

- p50
- p95
- p99

separately for:

- image extraction
- PDF extraction
- video extraction

### Throughput

Track:

- requests per second
- queued jobs
- GPU utilization
- media bytes processed

### Cost

Track:

- compute cost per extraction
- storage cost per extraction
- model/API cost per extraction

### Accuracy

Accuracy targets must be tied to a named evaluation dataset and use case.

Avoid global claims such as “95% accurate” across arbitrary schemas.

### Provisional MVP gates

```text
Schema-valid rate among succeeded responses = 100%
Exact count accuracy                        ≥ 95% on benchmark images
Evidence coverage                           ≥ 98% of returned non-null scalar fields
Unsupported-field hallucination rate        ≤ 1%
p95 image extraction latency                ≤ 8 seconds
Median image extraction cost                ≤ $0.03
```

These gates become authoritative only after the benchmark dataset and evaluation harness are versioned.

### Hard schema invariant

The 100% schema-valid rate among `succeeded` responses is a contract invariant, not a statistical quality target.

If the system cannot produce `data` that validates against the submitted schema, the extraction must transition to `failed` with `error.code = "schema_unsatisfied"`.

There is no allowed percentage of schema-invalid successful responses.

---

## 17. V1 Exclusions

- live streams
- audio
- remote URL fetches in MVP
- arbitrary robotics control
- facial recognition
- biometric identity
- unrestricted surveillance use cases
- automatic training on customer media by default
