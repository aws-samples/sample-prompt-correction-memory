# Comprehensive Threat Model Report

**Generated**: 2026-09-21 15:13:55
**Current Phase**: 9 - Output Generation and Documentation
**Overall Completion**: 100.0%

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [Business Context](#business-context)
3. [System Architecture](#system-architecture)
4. [Threat Actors](#threat-actors)
5. [Trust Boundaries](#trust-boundaries)
6. [Assets and Flows](#assets-and-flows)
7. [Threats](#threats)
8. [Mitigations](#mitigations)
9. [Assumptions](#assumptions)
10. [Phase Progress](#phase-progress)

## Executive Summary

sample-prompt-correction-memory is an open-source AWS sample that extracts structured fields from documents using Amazon Bedrock LLMs, and self-improves over time by storing human QA corrections and reusing them as few-shot examples. Recurring corrections graduate into deterministic rules that eliminate LLM calls. It is reference/sample code intended for a developer to deploy into their own single-owner AWS account to explore the pattern; it is not a multi-tenant production service and exposes no public network interface. Architecture: S3 (documents + correction files) → EventBridge → SQS → Lambda (extraction) → Bedrock, with a second Lambda ingesting QA corrections into a DynamoDB correction log, and results stored in a second DynamoDB table. KMS CMK encrypts DynamoDB and SQS.

### Key Statistics

- **Total Threats**: 11
- **Total Mitigations**: 11
- **Total Assumptions**: 5
- **System Components**: 11
- **Assets**: 3
- **Threat Actors**: 3

## Business Context

**Description**: sample-prompt-correction-memory is an open-source AWS sample that extracts structured fields from documents using Amazon Bedrock LLMs, and self-improves over time by storing human QA corrections and reusing them as few-shot examples. Recurring corrections graduate into deterministic rules that eliminate LLM calls. It is reference/sample code intended for a developer to deploy into their own single-owner AWS account to explore the pattern; it is not a multi-tenant production service and exposes no public network interface. Architecture: S3 (documents + correction files) → EventBridge → SQS → Lambda (extraction) → Bedrock, with a second Lambda ingesting QA corrections into a DynamoDB correction log, and results stored in a second DynamoDB table. KMS CMK encrypts DynamoDB and SQS.

### Business Features

- **Industry Sector**: Technology
- **Data Sensitivity**: Confidential
- **User Base Size**: Small
- **Geographic Scope**: Global
- **Regulatory Requirements**: None
- **System Criticality**: Low
- **Financial Impact**: Low
- **Authentication Requirement**: Federated
- **Deployment Environment**: Cloud-Public
- **Integration Complexity**: Moderate

## System Architecture

### Components

| ID | Name | Type | Service Provider | Description |
|---|---|---|---|---|
| C001 | DocumentBucket | Storage | AWS | Holds uploaded documents (documents/ prefix) and QA correction files (corrections/ prefix). SSE-S3, versioning, public access blocked, EventBridge notifications enabled. Primary untrusted-input entry point. |
| C002 | AccessLogBucket | Storage | AWS | Stores S3 server access logs for DocumentBucket. SSE-S3, versioning, public access blocked, BucketOwnerEnforced. |
| C003 | DocumentUploadRule / CorrectionUploadRule | Network | AWS | Routes S3 Object Created events: documents/ prefix to the extraction SQS queue, corrections/ prefix to the feedback Lambda. |
| C004 | ExtractionQueue | Network | AWS | Buffers/throttles extraction work between EventBridge and the extraction Lambda. KMS-encrypted; redrive to ExtractionDLQ after 3 attempts. |
| C005 | ExtractionDLQ / LambdaDLQ | Network | AWS | Dead-letter queues: ExtractionDLQ captures poison messages; LambdaDLQ captures failed async Lambda invocations. KMS-encrypted. |
| C006 | ExtractionFunction | Compute | AWS | Reads documents from S3, calls Bedrock (Converse) for extraction, performs low-confidence self-healing retries with correction few-shot examples, writes results to DynamoDB. Reserved concurrency 5, env vars KMS-encrypted, DLQ configured. |
| C007 | FeedbackFunction | Compute | AWS | Validates QA correction files (validate_correction) and ingests them into the DynamoDB correction log. Reserved concurrency 5, env vars KMS-encrypted, DLQ configured. |
| C008 | CorrectionLogTable | Storage | AWS | Field-level correction log (field_name + timestamp). KMS SSE with CMK, point-in-time recovery enabled. Source of few-shot examples and rule graduation. |
| C009 | ExtractionResultsTable | Storage | AWS | Stores extraction outputs (document_id + field_name). KMS SSE with CMK, point-in-time recovery enabled. |
| C010 | Amazon Bedrock | Compute | AWS | LLM inference for extraction (Claude Haiku) and self-healing retries (Claude Sonnet). Receives document text and correction reasons in prompts via Converse API with structured tool-use output. |
| C011 | EncryptionKey | Security | AWS | Customer-managed CMK (rotation enabled) encrypting both DynamoDB tables, all SQS queues, and Lambda environment variables. |

### Connections

| ID | Source | Destination | Protocol | Port | Encrypted | Description |
|---|---|---|---|---|---|---|
| CN001 | C001 | C003 | HTTPS | N/A | Yes | S3 Object Created events emitted to EventBridge |
| CN002 | C003 | C004 | HTTPS | N/A | Yes | EventBridge routes documents/ events to ExtractionQueue (SendMessage) |
| CN003 | C003 | C007 | HTTPS | N/A | Yes | EventBridge invokes FeedbackFunction on corrections/ events |
| CN004 | C004 | C006 | HTTPS | N/A | Yes | SQS event source mapping triggers ExtractionFunction (MaxConcurrency 5) |
| CN005 | C006 | C001 | HTTPS | N/A | Yes | ExtractionFunction reads document from S3 (S3ReadPolicy) |
| CN006 | C006 | C010 | HTTPS | N/A | Yes | ExtractionFunction calls Bedrock InvokeModel with document text (extraction + self-heal) |
| CN007 | C006 | C008 | HTTPS | N/A | Yes | ExtractionFunction reads correction log for self-healing few-shot examples (DynamoDBReadPolicy) |
| CN008 | C006 | C009 | HTTPS | N/A | Yes | ExtractionFunction writes extraction results (DynamoDBCrudPolicy) |
| CN009 | C007 | C001 | HTTPS | N/A | Yes | FeedbackFunction reads correction file from S3 (S3ReadPolicy) |
| CN010 | C007 | C008 | HTTPS | N/A | Yes | FeedbackFunction writes validated corrections to correction log (DynamoDBCrudPolicy) |

### Data Stores

| ID | Name | Type | Classification | Encrypted at Rest | Description |
|---|---|---|---|---|---|
| D001 | Correction Log | NoSQL | Confidential | Yes | DynamoDB CorrectionLogTable — original/corrected values, correction reasons, document excerpts. KMS CMK + PITR. |
| D002 | Extraction Results | NoSQL | Confidential | Yes | DynamoDB ExtractionResultsTable — extracted field values per document. KMS CMK + PITR. |
| D003 | Document & Correction Storage | Object Storage | Confidential | Yes | S3 DocumentBucket — uploaded non-regulated business documents (the sample must not be used with PII/PHI/regulated data) and QA correction files. SSE-S3, versioning, public access blocked. |

## Threat Actors

### Unauthorized Uploader

- **Type**: External
- **Capability Level**: Medium
- **Motivations**: Disruption, Other
- **Resources**: Moderate
- **Relevant**: Yes
- **Priority**: 8/10
- **Description**: A principal who gains write access to the S3 DocumentBucket (misconfigured IAM) and uploads malicious documents or poisoned correction files to bias extraction or trigger cost.

### Malicious Document Author

- **Type**: External
- **Capability Level**: Medium
- **Motivations**: Disruption, Other
- **Resources**: Limited
- **Relevant**: Yes
- **Priority**: 7/10
- **Description**: Author of a document processed by the pipeline who embeds adversarial/prompt-injection content in document text to steer LLM extraction output.

### Malicious/Careless Insider

- **Type**: Insider
- **Capability Level**: Medium
- **Motivations**: Espionage, Revenge, Accidental
- **Resources**: Moderate
- **Relevant**: Yes
- **Priority**: 5/10
- **Description**: A developer/operator in the deploying account with access to S3/DynamoDB who could exfiltrate document content or tamper with the correction log.

## Trust Boundaries

### Trust Zones

#### External Uploader Zone

- **Trust Level**: Untrusted
- **Description**: Whoever can write documents/correction files to the S3 bucket. Supplies attacker-influenceable content.

#### AWS Account Processing Zone

- **Trust Level**: Medium
- **Description**: In-account service mesh: S3, EventBridge, SQS, Lambda, DynamoDB governed by IAM and resource policies.

#### Bedrock Inference Zone

- **Trust Level**: Medium
- **Description**: Amazon Bedrock managed LLM service endpoint; document text leaves account data stores to reach it over TLS.

### Trust Boundaries

#### LLM Data-Egress Boundary (Bedrock)

- **Type**: Account
- **Controls**: TLS in transit; structured tool-use output constrains the model; typed per-field values; correction few-shot examples are delimited with `<correction_example>` tags and marked as data; confidence gating surfaces low-confidence results (`confidence_score`, `self_healed`). Note: the sample surfaces confidence but does NOT itself perform human review of low-confidence results — that is left to adopters.
- **Description**: Boundary where in-account document text leaves for the Bedrock managed service and where prompt-injection risk materializes.

#### Untrusted Input Boundary (S3 Ingress)

- **Type**: Other
- **Controls**: S3 Public Access Block, BucketOwnerEnforced (ACLs disabled), EventBridge SourceArn/SourceAccount conditions, FeedbackFunction schema validation of corrections
- **Description**: Boundary where attacker-influenceable documents and correction files enter the account. Content is untrusted.

## Assets and Flows

### Assets

| ID | Name | Type | Classification | Sensitivity | Criticality | Owner |
|---|---|---|---|---|---|---|
| A001 | Document Content | Data | Confidential | 4 | 4 | N/A |
| A002 | Correction Log Data | Data | Confidential | 4 | 5 | N/A |
| A003 | Extraction Results | Data | Confidential | 3 | 3 | N/A |

### Asset Flows

| ID | Asset | Source | Destination | Protocol | Encrypted | Risk Level |
|---|---|---|---|---|---|---|
| F001 | Document Content | C001 | C006 | HTTPS | Yes | 3 |
| F002 | Document Content | C006 | C010 | HTTPS | Yes | 3 |
| F003 | Correction Log Data | C001 | C008 | HTTPS | Yes | 4 |

## Threats

### Identified Threats

#### T1: An unauthorized principal with write access to the S3 bucket

**Statement**: A An unauthorized principal with write access to the S3 bucket given misconfigured IAM allowing s3:PutObject to documents/ or corrections/ can uploads documents or correction files that were not sanctioned, which leads to poisoning of the correction memory or triggering of unwanted extraction/cost

- **Prerequisites**: given misconfigured IAM allowing s3:PutObject to documents/ or corrections/
- **Action**: uploads documents or correction files that were not sanctioned
- **Impact**: poisoning of the correction memory or triggering of unwanted extraction/cost
- **Impacted Assets**: A002
- **Tags**: STRIDE-S

#### T2: A malicious actor submitting crafted correction files

**Statement**: A A malicious actor submitting crafted correction files with the ability to write to the corrections/ prefix can injects biased or false corrections that become few-shot examples or graduate into rules, which leads to systematic incorrect extractions (correction-log poisoning)

- **Prerequisites**: with the ability to write to the corrections/ prefix
- **Action**: injects biased or false corrections that become few-shot examples or graduate into rules
- **Impact**: systematic incorrect extractions (correction-log poisoning)
- **Impacted Assets**: A002
- **Tags**: STRIDE-T

#### T3: An operator or auditor

**Statement**: A An operator or auditor when the sample does not configure CloudTrail data events can cannot determine who uploaded a document or submitted a correction, which leads to inability to attribute malicious uploads or correction-log poisoning

- **Prerequisites**: when the sample does not configure CloudTrail data events
- **Action**: cannot determine who uploaded a document or submitted a correction
- **Impact**: inability to attribute malicious uploads or correction-log poisoning
- **Tags**: STRIDE-R

#### T4: An attacker with read access to account data stores

**Statement**: A An attacker with read access to account data stores given over-broad IAM or compromised credentials can reads document content or extraction results containing PII/contract terms, which leads to disclosure of sensitive document data

- **Prerequisites**: given over-broad IAM or compromised credentials
- **Action**: reads document content or extraction results (business document terms; the sample must not be used with regulated data)
- **Impact**: disclosure of sensitive business document data
- **Impacted Assets**: A001, A003
- **Tags**: STRIDE-I

#### T5: A mass uploader

**Statement**: A A mass uploader with write access to the bucket can floods the pipeline with documents to drive Bedrock and DynamoDB usage, which leads to cost amplification and processing delay (denial of service / wallet)

- **Prerequisites**: with write access to the bucket
- **Action**: floods the pipeline with documents to drive Bedrock and DynamoDB usage
- **Impact**: cost amplification and processing delay (denial of service / wallet)
- **Tags**: STRIDE-D

#### T6: A crafted document or correction reason

**Statement**: A A crafted document or correction reason processed through the LLM prompt (prompt injection) can embeds adversarial instructions attempting to steer extraction output, which leads to incorrect or attacker-controlled field values

- **Prerequisites**: processed through the LLM prompt (prompt injection)
- **Action**: embeds adversarial instructions attempting to steer extraction output
- **Impact**: incorrect or attacker-controlled field values
- **Impacted Assets**: A001, A003
- **Tags**: STRIDE-T, LLM, PromptInjection

#### T7: A compromised Lambda execution role

**Statement**: A compromised Lambda execution role could attempt to invoke Bedrock models beyond those the sample uses, leading to unexpected model cost or capability.

- **Prerequisites**: compromise of the extraction function's execution role
- **Action**: invokes Bedrock models beyond those intended
- **Impact**: privilege beyond least-privilege intent / unexpected model cost
- **Tags**: STRIDE-E, IAM

#### T8: A poison message that always fails processing

**Statement**: A A poison message that always fails processing entering the extraction queue can is repeatedly reprocessed by the extraction Lambda, which leads to wasted compute and potential processing stall

- **Prerequisites**: entering the extraction queue
- **Action**: is repeatedly reprocessed by the extraction Lambda
- **Impact**: wasted compute and potential processing stall
- **Tags**: STRIDE-D

#### T9: Automatic rule graduation without human review

**Statement**: A recurring correction pattern graduates into a deterministic rule that then answers with high confidence and no LLM call. Because graduation is automatic, a biased or poisoned set of corrections could produce a rule that systematically returns an attacker-preferred value.

- **Prerequisites**: enough corrections for a field/document_type to trigger graduation
- **Action**: a synthesized rule begins answering extractions on its own
- **Impact**: systematically incorrect extractions applied cheaply and silently
- **Impacted Assets**: A002, A003
- **Tags**: STRIDE-T

#### T10: Local dashboard exposure

**Statement**: The optional Streamlit dashboard has no authentication. If bound to a non-loopback interface it could expose correction/extraction data on the network, and a crafted link could previously make it read an arbitrary filesystem path.

- **Prerequisites**: running the dashboard bound to a non-loopback address
- **Action**: an unauthenticated party reads dashboard data or influences the data-source path
- **Impact**: disclosure of correction/extraction data; local file read
- **Impacted Assets**: A002, A003
- **Tags**: STRIDE-I

#### T11: Cross-document correction excerpt reuse

**Statement**: A stored correction includes a `document_excerpt`, which is injected into prompts when extracting *other* documents of the same type. An excerpt captured from one document is therefore reused in the processing context of unrelated documents.

- **Prerequisites**: a correction whose excerpt contains sensitive or misleading text
- **Action**: the excerpt is presented as a few-shot example for later documents
- **Impact**: unintended reuse of one document's text in another's extraction context
- **Impacted Assets**: A002, A001
- **Tags**: STRIDE-I, LLM

## Mitigations

### Resolved Mitigations

#### M1: S3 Public Access Block + BucketOwnerEnforced (ACLs disabled) on DocumentBucket; adopters restrict s3:PutObject to a known role/identity.

**Addresses Threats**: T1

#### M2: FeedbackFunction schema-validates every correction (validate_correction) before writing; adopters gate who can submit corrections and treat them as privileged input. DynamoDB PITR enables rollback.

**Addresses Threats**: T1, T2

#### M3: Structured tool-use output + typed per-field values constrain the model. Correction few-shot examples are wrapped in `<correction_example>` delimiters and the prompt instructs the model to treat them as data, not instructions. Confidence gating surfaces low-confidence results (`confidence_score`, `self_healed`); adopters add human review and output allow-lists for high-stakes fields (the sample does not perform human review itself).

**Addresses Threats**: T6

#### M4: SQS decoupling + Lambda ReservedConcurrentExecutions=5 and event-source MaximumConcurrency=5 cap parallel Bedrock calls; adopters add AWS Budgets alarms and S3 request limits.

**Addresses Threats**: T5

#### M5: ExtractionDLQ (maxReceiveCount 3) and LambdaDLQ capture poison messages and failed async invocations.

**Addresses Threats**: T8

#### M6: Encryption everywhere: S3 SSE + KMS CMK (rotation) on both DynamoDB tables and all SQS queues; least-privilege SAM policy templates; KMS access limited to the stack CMK ARN. Adopters confirm Bedrock data-classification fit.

**Addresses Threats**: T4

### Identified Mitigations

#### M7: S3 server access logging to AccessLogBucket. Adopter guidance: enable CloudTrail data events for S3/DynamoDB and set Lambda log retention per compliance needs (not configured by the sample).

**Addresses Threats**: T3

#### M8: `bedrock:InvokeModel` is scoped to the specific foundation-model and inference-profile ARNs the sample uses (Claude Haiku 4.5, Claude Sonnet 4, Titan Text Embeddings V2), not `Resource: "*"`. Adopter guidance (RR-1): further narrow to the exact model IDs and region enabled in the account before production.

**Addresses Threats**: T7

#### M9: Rules graduate only when they reproduce the corrected value exactly (case-insensitive or normalized-numeric equality; empty and substring matches are rejected). README documents that graduation is automatic and that production use should add a human approval step before a synthesized rule takes effect.

**Addresses Threats**: T9

#### M10: The dashboard is documented as local-use, no-authentication, and is run bound to `127.0.0.1`. The data-source path is read only from the `BENCHMARK_OUTPUT_PATH` environment variable, not from a URL query parameter, preventing arbitrary-path reads.

**Addresses Threats**: T10

#### M11: Correction `document_excerpt` values are length-capped (2,000 chars) and validated as data; adopter guidance is to treat corrections as privileged input and avoid placing sensitive text in excerpts. Reuse across documents of the same type is intentional (that is how self-healing works), so adopters handling sensitive documents should review excerpt content before submission.

**Addresses Threats**: T11

## Assumptions

### A001: Deployment

**Description**: The stack is deployed into a single owner's AWS account; there is no multi-tenant isolation requirement in the sample.

- **Impact**: Removes multi-tenant data isolation threats from scope.
- **Rationale**: This is demonstration/reference code intended for a developer to deploy into their own account.

### A002: Authentication

**Description**: Only trusted principals are granted write access to the S3 DocumentBucket. The sample blocks all public access but does not itself authenticate uploaders.

- **Impact**: Correction-log poisoning and unauthorized extraction depend on the adopter restricting bucket writes.
- **Rationale**: S3 PublicAccessBlock and BucketOwnerEnforced are set, but IAM scoping of uploaders is the adopter's responsibility.

### A003: AWS Services

**Description**: Amazon Bedrock model access is enabled and the deployer accepts sending document text to Bedrock for inference.

- **Impact**: Document content leaves the account's data stores to reach the Bedrock service endpoint.
- **Rationale**: Extraction and self-healing both require Bedrock InvokeModel; data classification fit is the adopter's decision.

### A004: Network

**Description**: Lambda functions run outside a VPC by design; they only reach regional AWS service endpoints (Bedrock, DynamoDB, S3, SQS).

- **Impact**: No VPC network isolation; documented checkov skip CKV_AWS_117. Adopters should add VPC as appropriate.
- **Rationale**: Forcing VPC networking adds undue complexity for a demonstration sample that only calls AWS APIs.

### A005: Residual Risk

**Description**: Residual risks accepted for the sample, with owner actions before production: (RR-1) scope bedrock:InvokeModel to specific model ARNs; (RR-2) confirm document-to-Bedrock data-classification fit; (RR-3) add CloudTrail + budget alarms; (RR-4) gate who can submit corrections (validation is structural only); (RR-5) restrict bucket write access (uploader not authenticated by sample); (RR-6) add output validation + human review for prompt-injection on sensitive fields.

- **Impact**: Residual risk is acceptable for a sample but must be addressed by adopters before production use.
- **Rationale**: This is demonstration/reference code deployed into a single owner's account; several production hardening steps are intentionally left to the adopter and documented as guidance.

## Phase Progress

| Phase | Name | Completion |
|---|---|---|
| 1 | Business Context Analysis | 100% ✅ |
| 2 | Architecture Analysis | 100% ✅ |
| 3 | Threat Actor Analysis | 100% ✅ |
| 4 | Trust Boundary Analysis | 100% ✅ |
| 5 | Asset Flow Analysis | 100% ✅ |
| 6 | Threat Identification | 100% ✅ |
| 7 | Mitigation Planning | 100% ✅ |
| 7.5 | Code Validation Analysis | 100% ✅ |
| 8 | Residual Risk Analysis | 100% ✅ |
| 9 | Output Generation and Documentation | 100% ✅ |

---

*This threat model was initially generated with the Threat Modeling MCP Server and subsequently updated by hand to match the implemented controls (EventBridge SourceArn conditions, scoped Bedrock IAM, correction validation and prompt delimiting, exact-match rule graduation, dashboard hardening) and to add threats T9–T11.*
