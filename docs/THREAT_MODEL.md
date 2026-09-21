# Threat Model — Prompt-Memory Document Extraction (Sample)

This threat model documents the security analysis for the `sample-prompt-correction-memory`
sample code. It follows a STRIDE-based approach aligned with AWS threat modeling
guidance. Because this is **sample/reference code**, the model focuses on the
architecture demonstrated by `infrastructure/template.yaml` and the two Lambda
handlers, and on the guidance adopters need before running it in their own
accounts.

> Scope note: This is a demonstration sample, not a production service. It is
> intended to be deployed by a developer into their own AWS account to explore
> the prompt-memory pattern. It does not host multi-tenant data or expose a
> public network interface.

---

## 1. System Overview

Prompt-Memory extracts structured fields from documents using Amazon Bedrock,
and improves over time by storing human QA corrections and reusing them as
few-shot examples. Recurring corrections graduate into deterministic rules.

### Components

| Component | AWS Resource | Purpose |
|-----------|--------------|---------|
| Document/correction store | Amazon S3 (`DocumentBucket`) | Holds uploaded documents (`documents/`) and QA correction files (`corrections/`) |
| Access logs | Amazon S3 (`AccessLogBucket`) | S3 server access logs |
| Event routing | Amazon EventBridge | Routes S3 `Object Created` events to the queue / feedback function |
| Throttling | Amazon SQS (`ExtractionQueue` + `ExtractionDLQ`) | Buffers extraction work; DLQ captures failures |
| Async failure capture | Amazon SQS (`LambdaDLQ`) | Dead-letter queue for failed async Lambda invocations |
| Extraction compute | AWS Lambda (`ExtractionFunction`) | Reads documents, calls Bedrock, writes results |
| Feedback compute | AWS Lambda (`FeedbackFunction`) | Validates and ingests correction files |
| Correction memory | Amazon DynamoDB (`CorrectionLogTable`) | Field-level correction log |
| Results store | Amazon DynamoDB (`ExtractionResultsTable`) | Extraction outputs |
| Inference | Amazon Bedrock | LLM extraction + self-healing retries |
| Encryption | AWS KMS (`EncryptionKey`) | CMK encrypting DynamoDB tables and SQS queues |

### Data Flow

```
                    ┌──────────────────────────────────────────────┐
                    │            Developer's AWS Account            │
                    │                                               │
  Uploader ──put──► │  S3 DocumentBucket                            │
 (documents/,       │      │  Object Created                        │
  corrections/)     │      ▼                                        │
                    │  EventBridge ── documents/ ──► SQS ExtractionQueue
                    │      │                              │          │
                    │      └── corrections/ ──► FeedbackFunction     │
                    │                                  │             │
                    │                       validate + write         │
                    │                                  ▼             │
                    │                       DynamoDB CorrectionLog   │
                    │                                               │
                    │  SQS ──► ExtractionFunction                    │
                    │              │  read doc, read corrections     │
                    │              │  call Bedrock (Converse)        │
                    │              ▼                                 │
                    │        DynamoDB ExtractionResults              │
                    │              │                                 │
                    │              ▼  (low confidence)               │
                    │        Bedrock self-heal retry                 │
                    └──────────────────────────────────────────────┘
```

---

## 2. Trust Boundaries

- **TB1 — External uploader → S3.** Whoever can write to `DocumentBucket`
  supplies both document content and correction files. This is the primary
  untrusted-input boundary.
- **TB2 — Document/correction content → Lambda → Bedrock.** Document text and
  correction reasons are attacker-influenceable data that flow into LLM prompts.
- **TB3 — AWS service-to-service.** EventBridge → SQS → Lambda → DynamoDB /
  Bedrock, governed by IAM and resource policies within the account.
- **TB4 — KMS.** Encryption/decryption of data at rest for DynamoDB and SQS.

---

## 3. Assumptions

1. The stack is deployed into a single owner's AWS account; there is no
   multi-tenant isolation requirement in the sample.
2. Only trusted principals are granted write access to `DocumentBucket`.
   The sample blocks all public access but does not itself authenticate
   uploaders.
3. Bedrock model access is enabled and the deployer accepts model provider terms.
4. Adopters review IAM scoping before any production use.

---

## 4. Threats and Mitigations (STRIDE)

### Spoofing
- **T-S1: Unauthorized upload of documents/corrections.**
  A spoofed or unauthorized principal writes to the bucket and thereby poisons
  the correction memory or triggers extraction.
  - *Mitigations:* S3 `PublicAccessBlockConfiguration` blocks all public access;
    `BucketOwnerEnforced` ownership disables ACLs. EventBridge/Lambda invocation
    is restricted via resource policies and `SourceArn`/`SourceAccount`
    conditions.
  - *Adopter guidance:* Restrict `s3:PutObject` on `DocumentBucket` to a known
    role/identity; consider a separate upload approval path for corrections.

### Tampering
- **T-T1: Correction-log poisoning.** Malicious correction files bias future
  extractions (few-shot examples) or force incorrect rule graduation.
  - *Mitigations:* `FeedbackFunction` validates every correction via
    `validate_correction` before writing. DynamoDB tables are KMS-encrypted and
    have point-in-time recovery enabled for rollback.
  - *Residual risk:* Validation is schema/shape level, not semantic. Adopters
    should treat corrections as privileged input and gate who can submit them.
- **T-T2: Data-at-rest tampering.** Direct modification of stored data.
  - *Mitigations:* KMS CMK (`SSEType: KMS`) on both DynamoDB tables and all SQS
    queues; S3 versioning enabled on document and log buckets.

### Repudiation
- **T-R1: No record of who uploaded or corrected.**
  - *Mitigations:* S3 server access logging enabled to `AccessLogBucket`.
  - *Adopter guidance:* Enable CloudTrail (data events for S3/DynamoDB) and
    Lambda logging retention per your compliance needs. The sample does not
    configure CloudTrail.

### Information Disclosure
- **T-I1: Sensitive document content exposure.** Documents may contain PII or
  contract terms.
  - *Mitigations:* All buckets block public access and are encrypted (SSE);
    DynamoDB and SQS encrypted with a customer-managed KMS key with rotation
    enabled; least-privilege IAM (`S3ReadPolicy`, scoped `DynamoDB*Policy`,
    `kms:Decrypt`/`GenerateDataKey` limited to the stack CMK).
  - *Residual risk:* Document text is sent to Amazon Bedrock for inference.
    Adopters must confirm this is acceptable for their data classification and
    choose an appropriate Bedrock region/model.
- **T-I2: Log leakage.** Access logs or Lambda logs containing identifiers.
  - *Mitigations:* Handlers log document IDs (SHA-256 prefix) and S3 keys, not
    document contents. Access log bucket is private and encrypted.

### Denial of Service
- **T-D1: Upload flood / cost amplification.** Mass uploads drive Bedrock and
  DynamoDB usage and cost.
  - *Mitigations:* SQS decouples ingestion from processing; Lambda event source
    `MaximumConcurrency: 5` and `ReservedConcurrentExecutions: 5` cap parallel
    Bedrock calls; SQS redrive to DLQ after 3 attempts.
  - *Adopter guidance:* Add S3 request limits / budget alarms; the sample does
    not set AWS Budgets.
- **T-D2: Poison-message loops.** A record that always fails reprocesses.
  - *Mitigations:* `ExtractionDLQ` (maxReceiveCount 3) and `LambdaDLQ` for async
    invocation failures.

### Elevation of Privilege
- **T-E1: Over-broad IAM.** Functions gaining more access than needed.
  - *Mitigations:* SAM policy templates scope DynamoDB/S3 access to named
    resources; KMS access limited to the stack CMK ARN; EventBridge granted only
    `kms:Decrypt`/`GenerateDataKey` for the encrypted queue.
  - *Residual risk:* `bedrock:InvokeModel` uses `Resource: "*"` because model
    ARNs vary by region/model. Adopters should scope this to the specific model
    ARNs they enable.

---

## 5. Prompt-Injection Considerations (LLM-specific)

Because document text and correction reasons flow into LLM prompts (TB2),
adversarial content could attempt to steer extraction output.

- The system uses **structured tool-use output** and per-field prompts, which
  constrains the model to a defined schema rather than free-form responses.
- Extracted values are **typed** (`data_type` on each field) and written to
  DynamoDB as data, not executed.
- **Residual risk:** A crafted document could still influence a field value.
  Adopters handling untrusted documents should add output validation/allow-lists
  for high-stakes fields and human review for low-confidence results (the sample
  already surfaces `confidence_score` and `self_healed`).

---

## 6. Residual Risks Summary

| ID | Residual Risk | Owner Action Before Production |
|----|---------------|--------------------------------|
| RR-1 | `bedrock:InvokeModel` scoped to `*` | Scope to specific model ARNs |
| RR-2 | Document content sent to Bedrock | Confirm data-classification fit |
| RR-3 | No CloudTrail / budget alarms in sample | Add per environment |
| RR-4 | Correction validation is structural only | Gate who can submit corrections |
| RR-5 | Uploader identity not authenticated by sample | Restrict bucket write access |
| RR-6 | Prompt injection via document content | Add output validation + human review for sensitive fields |

---

## 7. Security Controls Already Implemented

- KMS customer-managed key (rotation enabled) for DynamoDB + SQS encryption
- S3: public access fully blocked, SSE enabled, versioning + access logging
- DynamoDB: KMS SSE + point-in-time recovery on both tables
- SQS: KMS encryption on all queues; dead-letter queues for extraction and async
  Lambda failures
- Lambda: environment variables encrypted with the CMK, reserved concurrency,
  DLQ configured
- Least-privilege IAM via SAM policy templates; KMS access limited to stack CMK
- Structured/typed LLM output to constrain model responses

_Validated with `checkov` (0 failed / intentional skips documented) and
`sam validate --lint`._
