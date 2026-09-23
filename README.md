# World API — Authoritative Specification Set

This directory is the authoritative specification for the current World API design.

## Documents

1. `01-product-brief.md`
   - customer
   - launch wedge
   - positioning
   - MVP/V1/V2/V3 scope
   - provisional release gates

2. `02-v1-requirements.md`
   - supported media
   - canonical JSON Schema format
   - supported schema subset
   - field-level uncertainty
   - canonical evidence
   - release requirements

3. `03-api-specification.md`
   - uploads
   - signed and multipart upload flows
   - upload completion
   - extraction jobs
   - job states
   - errors
   - idempotency
   - rate limits
   - webhooks
   - deletion
   - evidence and metadata contracts

4. `04-system-architecture.md`
   - services
   - queues
   - workers
   - inference
   - validation
   - storage
   - model registry
   - evaluation
   - observability
   - security and privacy

## Locked decisions

- MVP = images only
- V1 beta = images + PDFs
- V1 GA = images + PDFs + sampled video
- JSON Schema Draft 2020-12 is canonical, with a constrained supported subset
- Pydantic compiles to the same canonical schema
- `result.data` always preserves caller-declared types
- confidence, status, and evidence are returned in a `field_metadata` sidecar keyed by JSON Pointer
- job status is separate from result quality
- signed uploads are primary; multipart is a convenience path for small images
- arbitrary URL ingestion is excluded from MVP
- deletion endpoints are required
- evidence indices are zero-based
- evidence coordinates map back to original oriented media
- the launch wedge is constrained product/inventory extraction under controlled visual conditions

## Deprecated document

The earlier `world-api-product-architecture.md` is deprecated and retained only for historical context.

The numbered specification set in this directory is authoritative.

## Evaluation result

The frozen inventory pipeline's held-out test result, end-to-end cost and
latency accounting, and provisional release-gate assessment are documented in
[`evaluation/FINAL_RESULTS.md`](evaluation/FINAL_RESULTS.md).

## Dataset licensing

The included inventory benchmark and visual reference sheets are derived from
MVTec D2S and are restricted to non-commercial use under CC BY-NC-SA 4.0. See
[`DATASET_LICENSE.md`](DATASET_LICENSE.md) before using or redistributing them.


## v4 contract clarifications

- Pydantic-generated schemas are normalized into the supported Draft 2020-12 subset
- `$defs` and local non-recursive `$ref` are supported
- nullable type arrays and non-ambiguous nullable `anyOf` are supported
- every field JSON Pointer is relative to `data`
- schema validity is a 100% invariant for `succeeded` extractions
- `schema_unsatisfied` is a terminal `processing_error`
- sync/async behavior uses standard `Prefer: wait=N` and `Prefer: respond-async`
- both synchronous and asynchronous modes return the same extraction resource envelope
