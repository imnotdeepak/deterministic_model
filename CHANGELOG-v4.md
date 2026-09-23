# World API Spec v4 — Contract Clarifications

## Pydantic compatibility

The supported JSON Schema subset now explicitly includes:

- `$defs`
- local, non-recursive `$ref`
- type arrays containing `null`
- non-ambiguous nullable `anyOf`

The SDK performs a normalization/compatibility pass before submission.

## JSON Pointer convention

All field pointers are RFC 6901 JSON Pointers relative to the root of `data`.

Correct:

```text
/products/0/quantity
```

Incorrect:

```text
/data/products/0/quantity
```

The same convention applies to metadata, evidence, warnings, and field-scoped errors.

## Schema validity invariant

Every `succeeded` extraction must have `data` that validates against the submitted schema.

```text
Schema-valid rate among succeeded responses = 100%
```

If that cannot be achieved, the extraction fails with:

```text
processing_error / schema_unsatisfied
```

## schema_unsatisfied semantics

Canonical failed resource:

```json
{
  "status": "failed",
  "error": {
    "type": "processing_error",
    "code": "schema_unsatisfied",
    "message": "Required fields could not be resolved without violating the submitted schema.",
    "retryable": false
  }
}
```

## Sync / async HTTP semantics

Supported preference headers:

```http
Prefer: wait=8
Prefer: respond-async
```

Rules:

- completed within wait window → `201 Created`
- still processing → `202 Accepted`
- `Location` always points to `/v1/extractions/{id}`
- response envelope is the same in both modes
