# World API — System Architecture

## 1. Architecture Goals

The system must support:

- secure media ingestion
- asynchronous inference
- GPU workloads
- model routing
- deterministic validation
- evidence persistence
- request tracing
- tenant isolation
- usage metering
- evaluation
- configurable retention
- future PDF/video/audio expansion

The system architecture should be separated from the perception/model pipeline.

---

## 2. High-Level System Architecture

```text
                        ┌─────────────────────┐
                        │ Developer / SDK     │
                        └──────────┬──────────┘
                                   │
                                   ▼
                        ┌─────────────────────┐
                        │ API Gateway         │
                        │ Auth + Rate Limits  │
                        └──────────┬──────────┘
                                   │
               ┌───────────────────┼───────────────────┐
               ▼                   ▼                   ▼
        ┌─────────────┐     ┌─────────────┐    ┌─────────────┐
        │ Upload API  │     │ Extraction  │    │ Webhook API │
        └──────┬──────┘     │ API         │    └──────┬──────┘
               │            └──────┬──────┘           │
               ▼                   │                  ▼
        ┌─────────────┐            ▼           ┌─────────────┐
        │ Object      │     ┌─────────────┐    │ Webhook     │
        │ Storage     │     │ Metadata DB │    │ Delivery    │
        └──────┬──────┘     └──────┬──────┘    └─────────────┘
               │                   │
               └──────────┬────────┘
                          ▼
                   ┌─────────────┐
                   │ Job Queue   │
                   └──────┬──────┘
                          ▼
                ┌────────────────────┐
                │ Orchestrator       │
                │ / Scheduler        │
                └─────────┬──────────┘
                          │
          ┌───────────────┼────────────────┐
          ▼               ▼                ▼
   ┌────────────┐   ┌────────────┐   ┌────────────┐
   │ CPU Worker │   │ GPU Worker │   │ PDF/Video  │
   │ Pool       │   │ Pool       │   │ Worker     │
   └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
         │                │                │
         └────────────────┼────────────────┘
                          ▼
                 ┌──────────────────┐
                 │ Perception       │
                 │ Pipeline         │
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │ Validation +     │
                 │ Confidence       │
                 └────────┬─────────┘
                          ▼
                 ┌──────────────────┐
                 │ Result Store     │
                 └──────────────────┘
```

Cross-cutting components:

```text
Observability
Model Registry
Evaluation Pipeline
Usage Metering
Billing
Audit Logs
Secrets Management
Security Monitoring
```

---

## 3. Perception Pipeline

The inference pipeline is separate from the application architecture.

```text
Normalized Media
      ↓
Task Router
      ↓
┌──────────────┬──────────────┬──────────────┐
│ Detection    │ OCR          │ Segmentation │
└──────┬───────┴──────┬───────┴──────┬───────┘
       │              │              │
       └──────────────┼──────────────┘
                      ↓
             Candidate Extraction
                      ↓
               Schema Mapping
                      ↓
              Structural Validation
                      ↓
             Business-Rule Validation
                      ↓
              Evidence Association
                      ↓
              Confidence Policy
                      ↓
        Field Results + Evidence + Status
```

---

## 4. Core Services

### 4.1 API Gateway

Responsibilities:

- TLS termination
- authentication
- project identification
- rate limiting
- request-size enforcement
- request IDs
- abuse controls

### 4.2 Upload Service

Responsibilities:

- create signed upload URLs
- accept direct multipart uploads for small media
- confirm signed-upload completion
- expose upload status
- validate declared metadata
- track upload lifecycle
- invoke malware/media validation
- enforce size/type policies
- schedule secure deletion

### 4.3 Extraction Service

Responsibilities:

- validate request
- validate JSON Schema
- create extraction record
- enforce idempotency
- enqueue jobs
- expose extraction status

### 4.4 Job Orchestrator

Responsibilities:

- select worker class
- route by media type
- enforce processing timeout
- retry transient failures
- track job state
- cancel work where possible

### 4.5 Inference Workers

Worker pools should be specialized:

```text
CPU workers
GPU workers
PDF render workers
Video decode workers
```

Avoid coupling every workload to the same GPU runtime.

### 4.6 Validation Service

Responsibilities:

- validate schema compatibility before inference
- validate `data` directly against the caller's JSON Schema as a hard invariant for every `succeeded` extraction
- business-rule validation
- evidence-coverage checks
- confidence policy
- build `field_metadata` sidecar keyed by JSON Pointer
- aggregate informational confidence
- never mutate caller-declared field types to attach metadata

### 4.7 Evidence Service

Responsibilities:

- canonical evidence IDs scoped to an extraction
- zero-based page/frame indices
- original-media coordinate mapping
- exclusive right/bottom box edges
- transform mapping after resize/rotation/crop
- frame/page/timestamp references
- media-availability state
- linkage from JSON Pointer field paths to evidence IDs
- all field pointers are relative to the root of `data`; `/data` is never included

---

## 5. Storage

### Metadata Database

Recommended:

```text
PostgreSQL
```

Store:

- users/projects
- API keys
- uploads
- extraction jobs
- schemas
- model bundle versions
- results
- evidence metadata
- webhook configurations
- usage records
- audit events

### Object Storage

Recommended:

```text
S3-compatible object storage
```

Store:

- raw uploads
- normalized derivatives
- rendered PDF pages
- sampled video frames
- optional debugging artifacts

### Cache / Queue

Recommended categories:

```text
Redis or managed queue
```

Exact technology should be selected after workload requirements are known.

---

## 6. Model Registry

The model registry should track:

- model name
- semantic version
- task
- artifact hash
- training/evaluation dataset version
- deployment status
- calibration version
- rollback target

Every extraction should record the model bundle used.

Example:

```text
world-image-v1
├── detector 1.4.2
├── OCR 2.1.0
├── segmenter 1.0.5
└── schema-mapper 0.9.8
```

---

## 7. Evaluation Pipeline

Production model releases should pass automated evaluation before deployment.

```text
Candidate model
      ↓
Fixed benchmark datasets
      ↓
Task metrics
      ↓
Calibration metrics
      ↓
Regression checks
      ↓
Release gate
```

Evaluation should track:

- field accuracy
- count accuracy
- precision/recall
- OCR error rate
- evidence coverage
- calibration error
- abstention behavior
- latency
- cost

---

## 8. Observability

Required signals:

### Logs

- structured
- request ID
- extraction ID
- project ID
- worker ID
- model version

### Metrics

- API request rate
- queue depth
- job latency
- inference latency
- GPU utilization
- error rate
- timeout rate
- extraction status distribution
- cost per extraction

### Traces

Distributed tracing should cover:

```text
API
→ queue
→ worker
→ model
→ validation
→ persistence
→ webhook
```

---

## 9. Usage Metering and Billing

Meter:

- extraction count
- uploaded bytes
- processed megapixels
- PDF pages
- video seconds
- GPU seconds
- storage duration

Pricing does not need to use all metrics, but internal metering should support cost analysis.

---

## 10. Security and Privacy

Security must be part of the architecture before remote ingestion or production launch.

### 10.1 Authentication

- hashed API keys
- key rotation
- project scoping
- least-privilege service credentials

### 10.2 Tenant Isolation

All database and storage access must be tenant-scoped.

Never trust client-provided tenant IDs without authenticated project context.

### 10.3 Encryption

- TLS in transit
- encrypted object storage
- encrypted database storage
- managed secrets

### 10.4 File Validation

Uploads should be validated using:

- magic-byte detection
- content-type verification
- extension mismatch detection
- file-size limits
- decompression-bomb protections
- image dimension limits
- malware scanning where appropriate

### 10.5 SSRF

Do not implement arbitrary URL fetching until protections exist.

Required controls:

- block loopback
- block private networks
- block link-local networks
- resolve and validate DNS
- re-check redirects
- restrict protocols
- enforce response-size limits
- enforce timeouts

### 10.6 PII

Real-world media may contain:

- faces
- names
- addresses
- account numbers
- license plates
- documents
- other personal information

The platform needs:

- retention controls
- deletion API
- access logging
- data minimization
- customer-configurable storage policies

### 10.7 Biometric Data

MVP should not perform biometric identification.

Face presence may be technically detectable later, but identity recognition should require a separate legal/security review.

### 10.8 Training Data Policy

Default recommendation:

> Customer media is not used for model training unless the customer explicitly opts in.

Corrections may be retained for service operation only under documented terms unless training opt-in is enabled.

### 10.9 Signed Media Access

Raw media access should use short-lived signed URLs.

Never expose permanent public object-storage URLs.

### 10.10 Audit Logs

Track:

- key creation/revocation
- upload access
- extraction creation
- media deletion
- retention changes
- admin actions

### 10.11 Regional Storage

Not required for prototype MVP, but architecture should avoid making future regional storage impossible.

---

## 11. Retention and Deletion

Each project should eventually support:

```text
delete media immediately after extraction
retain for N hours/days
retain until manually deleted
```

Deletion should cover:

- original upload
- derivatives
- cached frames/pages
- generated thumbnails

Metadata may be retained separately where legally and contractually allowed.

---

## 12. Failure Isolation

The system should isolate:

- malformed media
- model failure
- validation failure
- storage failure
- queue failure
- webhook failure

A webhook delivery failure should never change a successful extraction into a failed extraction.

---

## 13. Deployment Strategy

Early prototype:

```text
Frontend: Next.js
API: FastAPI
Database: PostgreSQL
Storage: S3/R2
Queue: managed queue or Redis
Inference: GPU platform or AWS GPU instance
```

Do not overcommit to infrastructure before measuring:

- request volume
- media size
- GPU utilization
- model memory requirements
- latency targets

---

## 14. Scaling Path

### Stage 1

Single API service + one worker pool.

### Stage 2

Separate CPU/GPU workers.

### Stage 3

Task-specific queues and autoscaling.

### Stage 4

Regional inference and edge options.

### Stage 5

Live-stream processing infrastructure.

---

## 15. V2 Audio Architecture

V2 adds:

```text
audio ingestion
speech recognition
speaker diarization
environmental sound classification
timestamp evidence
multimodal fusion
```

Audio evidence should use the same canonical evidence system with:

```text
source_type: audio_segment
start_ms
end_ms
speaker_id
```

---

## 16. V3 Live Perception Architecture

V3 introduces:

- RTSP/WebRTC ingestion
- stream session manager
- state store
- event detector
- event deduplication
- webhook/event bus
- continuous inference scheduler

The V1 extraction architecture should not assume all requests are one-shot forever, but live-stream infrastructure should not be implemented early.
