# World API Spec v3 — Changes

## Critical contract fix

`data` now remains exactly schema-valid.

Old:

```json
{
  "name": {
    "value": "Coca-Cola Zero",
    "confidence": 0.98
  }
}
```

New:

```json
{
  "data": {
    "name": "Coca-Cola Zero"
  },
  "field_metadata": {
    "/name": {
      "confidence": 0.98,
      "status": "verified",
      "evidence_refs": ["ev_1"]
    }
  }
}
```

## Other fixes

- added signed-upload + multipart-upload contract
- added upload completion and upload-status endpoints
- separated job state from result quality
- constrained supported JSON Schema subset
- added schema-complexity limits
- added upload/extraction deletion endpoints
- clarified evidence indexing and coordinate semantics
- added provisional MVP release gates
- marked original architecture document deprecated

- removed `extraction.partially_succeeded` from the current webhook contract
- defined unresolved required fields as `schema_unsatisfied` rather than returning schema-invalid data
