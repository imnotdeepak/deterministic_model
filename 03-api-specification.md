# World API — API Specification

## 1. API Versioning

Base path:

```text
/v1
```

Breaking changes require a new major API version.

Non-breaking additions may be introduced within `/v1`.

The server should return:

```text
World-API-Version
Model-Bundle-Version
Request-ID
```

headers where useful.

---

## 2. Authentication

All API requests require an API key.

Example:

```http
Authorization: Bearer world_live_...
```

Keys should be scoped by project/workspace.

Future support:

- restricted keys
- environment-specific keys
- key rotation
- service accounts

---

## 3. Core Resources

Recommended resources:

```text
uploads
extractions
schemas
webhooks
```

MVP requires:

```text
POST /v1/uploads
POST /v1/extractions
GET  /v1/extractions/{id}
POST /v1/extractions/{id}/cancel
```

V1 Beta adds:

```text
POST /v1/webhooks
GET  /v1/webhooks
DELETE /v1/webhooks/{id}
```

---

## 4. Upload Flow

World API supports two upload methods that converge on the same upload resource.

### 4.1 Create signed upload

```http
POST /v1/uploads
```

Request:

```json
{
  "filename": "shelf.jpg",
  "content_type": "image/jpeg",
  "content_length": 3812220,
  "method": "signed"
}
```

Response:

```json
{
  "id": "upl_123",
  "status": "created",
  "upload_url": "https://signed-upload-url.example/...",
  "expires_at": "2026-09-17T22:15:00Z"
}
```

The upload URL is short-lived and scoped to one object.

After the client finishes uploading, it confirms completion:

```http
POST /v1/uploads/upl_123/complete
```

The service then validates the object and transitions the upload through:

```text
created
uploaded
validating
validated
rejected
expired
deleted
```

### 4.2 Direct multipart upload

For small images, clients may use:

```http
POST /v1/uploads/multipart
Content-Type: multipart/form-data
```

The API creates the same upload resource internally and returns:

```json
{
  "id": "upl_456",
  "status": "validating"
}
```

The multipart path is a convenience mechanism, not a separate storage model.

### 4.3 Get upload

```http
GET /v1/uploads/{id}
```

Example:

```json
{
  "id": "upl_123",
  "status": "validated",
  "filename": "shelf.jpg",
  "content_type": "image/jpeg",
  "content_length": 3812220,
  "created_at": "2026-09-17T22:00:00Z"
}
```

Extraction creation must reject uploads that are not yet `validated`.

---

## 5. Create Extraction

```http
POST /v1/extractions
```

Request:

```json
{
  "upload_id": "upl_123",
  "schema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
      "products": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "quantity": {"type": "integer", "minimum": 0}
          },
          "required": ["name", "quantity"],
          "additionalProperties": false
        }
      }
    },
    "required": ["products"],
    "additionalProperties": false
  },
  "instruction": "Count only products on the middle shelf.",
  "min_confidence": 0.9
}
```

Recommended request header:

```http
Idempotency-Key: <unique-client-value>
```

---

## 6. Synchronous vs Asynchronous HTTP Behavior

The extraction resource is the same in both synchronous and asynchronous modes.

### 6.1 Default behavior

Without a `Prefer` header:

- image extraction may wait synchronously for up to 8 seconds
- if processing completes within that window, return `201 Created` with the completed extraction resource
- if processing does not complete within that window, return `202 Accepted` with the queued/running extraction resource

The server always returns:

```http
Location: /v1/extractions/{id}
```

### 6.2 Explicit async

Clients may request immediate asynchronous behavior:

```http
Prefer: respond-async
```

The server returns:

```http
202 Accepted
Location: /v1/extractions/{id}
```

with the extraction resource in `queued` or `running` state.

### 6.3 Explicit wait

Clients may request a bounded synchronous wait:

```http
Prefer: wait=8
```

Rules:

- supported wait values are capped by the server
- MVP maximum wait is 8 seconds
- if processing completes within the requested wait, return `201 Created`
- otherwise return `202 Accepted`

The response body uses the same extraction envelope in both cases.

### 6.4 Created resource semantics

`POST /v1/extractions` always creates an extraction resource.

Therefore:

```text
201 Created
```

means the extraction resource was created and completed within the synchronous wait window.

```text
202 Accepted
```

means the extraction resource was created but processing continues asynchronously.

A later terminal job failure is retrieved through the same extraction resource.

---

## 7. Extraction States

Canonical job states:

```text
queued
running
succeeded
failed
cancel_requested
cancelled
```

Field uncertainty does not change the job state. It is represented through `field_metadata`, warnings, and result-quality metadata.

A future `partially_succeeded` state may be introduced only for partial media-processing failures, such as processing 8 of 10 PDF pages. It must not be used for ordinary low-confidence or unresolved fields.

---

## 8. Get Extraction

```http
GET /v1/extractions/{id}
```

Example:

```json
{
  "id": "ext_123",
  "status": "succeeded",
  "data": {},
  "field_metadata": {},
  "aggregate_confidence": 0.96,
  "evidence": [],
  "warnings": [],
  "error": null
}
```

---

## 9. Cancel Extraction

```http
POST /v1/extractions/{id}/cancel
```

Cancellation is best-effort.

A job already completing on a worker may still finish.

---

## 10. Error Model

All API errors should follow one canonical structure.

```json
{
  "error": {
    "type": "invalid_request",
    "code": "unsupported_media_type",
    "message": "HEIF sequence files are not supported.",
    "param": "upload_id",
    "request_id": "req_123"
  }
}
```

Initial error types:

```text
authentication_error
authorization_error
invalid_request
rate_limit_error
media_validation_error
schema_error
processing_error
timeout_error
internal_error
```

### `schema_unsatisfied`

If required output cannot be resolved without violating the submitted schema, the extraction transitions to:

```json
{
  "id": "ext_123",
  "status": "failed",
  "data": null,
  "field_metadata": {},
  "error": {
    "type": "processing_error",
    "code": "schema_unsatisfied",
    "message": "Required fields could not be resolved without violating the submitted schema.",
    "retryable": false
  }
}
```

Semantics:

- job state: `failed`
- error type: `processing_error`
- error code: `schema_unsatisfied`
- default retryability: `false`
- `data` must not contain schema-invalid partial output
- field-level diagnostic metadata may be returned only if it does not create ambiguity about job failure

For a request that was being synchronously awaited:

- if `schema_unsatisfied` is reached within the wait window, return the failed extraction resource with HTTP `201 Created`
- the HTTP status reflects creation of the extraction resource; the resource's own `status` reflects processing outcome
- clients must inspect the extraction resource `status`

For asynchronous requests, the client receives `202 Accepted` initially and later observes `status: "failed"` via polling or webhook.

A retry may be useful only if the caller changes the input, schema, instructions, or model version. Repeating the identical extraction is not expected to succeed.

---

## 11. Idempotency

`POST /v1/extractions` should support an idempotency key.

Rules:

- key scoped to API project
- same key + same payload returns same logical extraction
- same key + different payload returns conflict
- retention window documented

---

## 12. Pagination

List endpoints should use cursor pagination.

Example:

```http
GET /v1/extractions?limit=50&after=ext_123
```

Response:

```json
{
  "data": [],
  "has_more": true,
  "next_cursor": "..."
}
```

---

## 13. Rate Limits

Rate limits should be project-scoped.

Expose headers such as:

```text
X-RateLimit-Limit
X-RateLimit-Remaining
X-RateLimit-Reset
```

Separate limits may exist for:

- requests per minute
- concurrent jobs
- bytes per minute
- GPU-heavy workloads

---

## 14. Webhooks

V1 Beta should support webhooks for async completion.

Events:

```text
extraction.succeeded
extraction.failed
extraction.cancelled
```

`extraction.failed` includes failures such as `schema_unsatisfied`; clients inspect `error.code` on the extraction resource for details.

If partial media-processing states are introduced in a later API revision, a corresponding webhook event may be added then.

Webhook payload example:

```json
{
  "id": "evt_123",
  "type": "extraction.succeeded",
  "created_at": "2026-09-17T22:04:00Z",
  "data": {
    "extraction_id": "ext_123"
  }
}
```

Requirements:

- signed payloads
- replay protection
- retry policy
- delivery logs
- event IDs
- idempotent consumer guidance

---

## 15. Batch Behavior

Batch extraction should not be part of the first MVP endpoint surface unless needed.

When introduced:

```http
POST /v1/batches
```

A batch should consist of references to previously created uploads.

Each item gets an independent extraction status.

---

## 16. Timeouts

Server-side processing timeouts should vary by media type and plan.

Synchronous HTTP timeout should be shorter than backend processing timeout.

Long-running jobs must transition to async rather than holding connections indefinitely.

---

## 17. Media Limits

Limits should be returned in developer documentation and enforced before expensive processing.

Limits may include:

- upload bytes
- decoded pixels
- PDF pages
- video duration
- video resolution
- audio duration in V2

---

## 18. Evidence Contract

Canonical evidence object:

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
  "media_available": true,
  "model_version": "detector-1.2.0"
}
```

Possible future `source_type` values:

```text
image_region
document_region
video_frame_region
audio_segment
text_span
```

---

## 19. Field Metadata Contract

Caller-declared values remain inside `data` exactly as specified by the submitted JSON Schema.

Perception metadata is returned separately and keyed by RFC 6901 JSON Pointer relative to the root of `data`. The `/data` prefix is never included.

Example:

```json
{
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
      "evidence_refs": ["ev_1", "ev_2"]
    }
  }
}
```

Required metadata semantics:

- `confidence`: field-level model or calibrated confidence
- `status`: verification state
- `evidence_refs`: source evidence IDs

The API must never wrap a schema-declared string, number, boolean, array, or object solely to attach perception metadata.

---

## 20. Schema Compatibility Validation

Before inference begins, the API validates the submitted schema against the supported MVP subset.

Supported Pydantic-compatible constructs include:

```text
$defs
local non-recursive $ref
type arrays containing null
non-ambiguous nullable anyOf
```

The API rejects:

- recursive `$ref`
- remote `$ref`
- ambiguous `oneOf` / `anyOf`
- unsupported schema keywords
- excessive nesting
- excessive property counts
- oversized enums
- unsafe or overly complex regular expressions
- schemas above configured byte limits

Example error:

```json
{
  "error": {
    "type": "schema_error",
    "code": "unsupported_schema_construct",
    "message": "Recursive $ref is not supported in v1.",
    "param": "schema"
  }
}
```

Schema-compatibility errors occur before an extraction job is created.

---

## 21. Schema Resource


V1 may allow inline schemas only.

A later schema registry may expose:

```text
POST /v1/schemas
GET  /v1/schemas/{id}
GET  /v1/schemas
```

Stored schemas should be immutable by version.

---

## 22. Deletion

### Delete upload

```http
DELETE /v1/uploads/{id}
```

Semantics:

- schedules deletion of raw media and derived media artifacts
- prevents new extractions from referencing the upload
- returns `202 Accepted` when deletion is asynchronous

### Delete extraction

```http
DELETE /v1/extractions/{id}
```

Semantics:

- schedules deletion of extraction result payloads
- schedules deletion of extraction-specific evidence metadata
- does not automatically delete the source upload unless explicitly requested

### Deletion state

Resources may expose:

```text
deletion_requested
deleted
```

Deletion should cover applicable:

- raw media
- normalized derivatives
- rendered pages
- sampled frames
- result payloads
- evidence metadata
- cached artifacts

Audit and billing records may be retained separately where legally and contractually permitted.

---

## 23. Request Tracing

Every API response should expose a request identifier.

Internally, traces should connect:

```text
API request
→ upload
→ extraction
→ queue
→ worker
→ model calls
→ validation
→ storage
→ webhook
```

---

## 24. Retention

Each extraction should track:

```text
media_retention_policy
result_retention_policy
evidence_retention_policy
```

If raw media expires before result metadata, evidence objects must indicate that source media is no longer retrievable.

---

## 25. Example End-to-End Flow

```text
1. POST /v1/uploads
2. Client uploads to the signed object-storage URL
3. POST /v1/uploads/{id}/complete
4. Server validates media
5. GET /v1/uploads/{id} until `validated`, or wait for SDK helper
6. POST /v1/extractions
7. Extraction enters queue
8. Worker processes media
9. Result validated against caller schema
10. Field metadata and evidence persisted separately
11. GET /v1/extractions/{id}
12. Webhook optionally delivered
```
