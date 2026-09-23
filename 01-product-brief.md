# World API — Product Brief

## 1. Product Thesis

World API is a perception infrastructure platform that converts real-world visual input into structured, machine-readable data.

The product is not a chatbot and should not optimize for free-form descriptions. Its core job is:

> **Input media + requested schema → structured data + evidence + uncertainty**

Long-term vision:

> **An API that lets software perceive the real world.**

The initial product should remain much narrower than that vision.

---

## 2. Initial Customer

### Primary launch customer

Software teams that need to extract structured information from recurring visual inputs but do not want to assemble and maintain their own OCR, vision, schema-validation, confidence, and evidence stack.

Initial target:

- API-first software companies
- small-to-medium engineering teams
- teams already sending images or documents through manual or brittle extraction workflows
- developers who need typed, machine-readable results rather than natural-language descriptions

### Initial buyer

Depending on company size:

- CTO
- Head of Engineering
- AI/ML Lead
- Product Engineering Lead
- Technical Founder

---

## 3. Initial Wedge

The platform architecture should remain horizontal, but the first launch wedge should be constrained.

### Recommended wedge

**Structured extraction from product and inventory images under controlled conditions.**

Examples:

- shelf inventory
- storage-bin contents
- product counting
- package identification
- stock-state extraction

Why this wedge:

- clear structured outputs
- repeatable image conditions
- measurable counting and classification accuracy
- easy visual evidence through bounding boxes
- obtainable evaluation datasets
- natural path from image extraction into video and live monitoring later

### Secondary benchmark use case

**Document/menu extraction** can be used as a secondary evaluation track because OCR and layout extraction are easier to benchmark independently.

It should not expand the commercial launch scope until the image wedge is stable.

---

## 4. Problem

Developers can already call general-purpose multimodal models, but production use creates additional work:

- prompt engineering
- output parsing
- schema enforcement
- confidence handling
- evidence mapping
- bounding-box extraction
- OCR
- retries
- failure handling
- model routing
- evaluation
- abstention logic
- version tracking

World API packages those concerns behind one interface.

Instead of:

```text
Vision model
+ OCR
+ object detector
+ segmentation model
+ prompt
+ output parser
+ validation
+ confidence heuristics
```

developers call:

```python
world.extract(...)
```

---

## 5. Product Promise

For the initial release:

> **Send an image, define the schema you want, and receive structured fields with evidence and explicit uncertainty.**

The product must not promise that model-generated values are mathematically proven correct.

Instead, it should promise:

- schema-valid output
- source-linked evidence where available
- calibrated confidence where supported
- narrow verification statuses
- explicit unresolved fields
- no silent guessing when thresholds are not met

---

## 6. Positioning

### Short positioning

> **Turn images into structured data you can inspect.**

### Developer positioning

> **Define the data you want. We return typed fields, confidence, and evidence.**

### Long-term positioning

> **An API that lets software perceive the real world.**

---

## 7. What “Verified” Means

“Verified” must be used narrowly.

A field may be marked `verified` only when all required deterministic checks for that field have passed and the evidence/confidence policy for that extraction profile has been satisfied.

It does **not** mean that software has logically proven the real-world fact is true.

Verification can include:

- structural validation
- business-rule validation
- evidence coverage
- calibrated-confidence threshold
- optional cross-model agreement

Cross-model agreement by itself is not verification.

---

## 8. Initial Use Case

### Inventory extraction

Input:

```text
Controlled image of a shelf, bin, counter, or storage area
```

Requested schema:

```text
products[]
  name
  quantity
```

Output:

```json
{
  "products": [
    {
      "name": {
        "value": "Coca-Cola Zero",
        "confidence": 0.98,
        "status": "verified",
        "evidence_refs": ["ev_1", "ev_2", "ev_3", "ev_4"]
      },
      "quantity": {
        "value": 4,
        "confidence": 0.97,
        "status": "verified",
        "evidence_refs": ["ev_1", "ev_2", "ev_3", "ev_4"]
      }
    }
  ]
}
```

---

## 9. MVP Boundary

### MVP

Images only.

Supported:

- JPG
- JPEG
- PNG
- WEBP
- HEIC

Delivery:

- signed object-storage upload (primary)
- direct multipart upload for small images (convenience path)
- previously created upload object

Both upload methods converge on the same upload resource and validation lifecycle.

MVP deliberately excludes arbitrary remote URL ingestion until SSRF and media-security controls are complete.

### V1 Beta

- images
- PDFs
- page-level evidence
- async extraction jobs
- webhook delivery

### V1 General Availability

- images
- PDFs
- sampled video
- frame/timestamp evidence
- production rate limits
- billing
- formal API versioning policy

### V2

- audio
- speech understanding
- environmental audio
- audio + video fusion
- stronger temporal reasoning

### V3

- RTSP
- webcams
- live microphones
- continuous state
- event streams
- real-time webhooks

---

## 10. What Is Explicitly Out of Scope for MVP

- arbitrary real-world understanding
- live video
- audio
- robotics
- autonomous control
- LiDAR
- GPS
- IoT telemetry
- biometric identification
- unrestricted remote URL fetches
- custom foundation-model training
- fully automatic retraining from customer data
- unsupported claims of universal confidence calibration

---

## 11. Success Metrics and Provisional Release Gates

The MVP should be judged on one narrow, versioned evaluation set for the launch wedge.

Required metrics:

- field accuracy
- count accuracy
- precision
- recall
- schema-valid response rate
- evidence coverage
- abstention precision
- calibration error
- p50 latency
- p95 latency
- cost per extraction

### Provisional MVP release gates

These are engineering targets, not permanent product guarantees. They should be revised once the first benchmark dataset is frozen.

```text
Schema-valid rate among succeeded responses = 100%
Exact count accuracy          ≥ 95% on benchmark images
Evidence coverage             ≥ 98% of returned non-null scalar fields
Unsupported-field hallucination rate ≤ 1%
p95 image extraction latency  ≤ 8 seconds
Median image extraction cost  ≤ $0.03
```

There is no tolerated schema-invalid success path. Any extraction that cannot satisfy the submitted schema must fail with `schema_unsatisfied`.

A release must not be declared complete until:

1. the benchmark dataset is versioned,
2. each metric has a reproducible evaluation script,
3. the release candidate meets the published gates,
4. confidence calibration is reported separately from raw model confidence.

---

## 12. Long-Term Expansion

Once the core extraction contract is strong, the product can grow into:

```text
Image
↓
Video
↓
Audio
↓
Live streams
↓
Continuous real-world state
```

The long-term moat should come from:

- schema-driven perception
- evidence system
- confidence calibration
- evaluation infrastructure
- specialized routing
- customer-specific extraction profiles
- video tracking
- multimodal fusion
- developer tooling
- correction datasets
- inference infrastructure
