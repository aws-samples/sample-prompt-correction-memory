# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Continuous integration workflow running tests on Python 3.9-3.12 plus
  `black`, `isort`, and `pylint` checks.
- Dependabot configuration for weekly `pip` and GitHub Actions updates.
- `SECURITY.md` describing the vulnerability reporting process.
- Issue and pull request templates.

## [0.1.0] - Initial release

First public release of sample-prompt-correction-memory — a sample project
showing automated prompt self-correction for LLM document extraction on AWS.

### Added
- Confidence-gated extraction via Amazon Bedrock with automatic escalation.
- Self-healing retries that reuse past corrections as few-shot examples.
- Persistent correction memory in DynamoDB (field-level correction log).
- Automatic rule graduation: recurring correction patterns become
  deterministic, zero-cost rules.
- Bayesian per-field confidence calibration.
- QA correction ingestion pipeline (S3 -> EventBridge -> Lambda).
- Serverless AWS deployment via SAM (S3, DynamoDB, Lambda, EventBridge, SQS,
  KMS).
- Benchmark suite and Streamlit dashboard.
- Runnable quickstart with mocked Bedrock (no AWS credentials required).
- Threat model (`docs/THREAT_MODEL.md`).
