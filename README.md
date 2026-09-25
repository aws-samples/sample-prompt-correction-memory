# Prompt-Memory Document Extraction

**A sample project showing automated prompt self-correction for LLM document extraction on AWS. Each human correction improves future extractions — no retraining, no redeployment.**

![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue) ![License](https://img.shields.io/badge/license-MIT--0-blue) ![Status](https://img.shields.io/badge/status-sample-orange)

> **Disclaimer:** This is sample code provided for demonstration and educational purposes. It is not intended for production use as-is. Review, test, and harden it for your own security and operational requirements before deploying.

---

## The Problem

Every LLM-based extraction pipeline makes the same mistakes repeatedly. Your QA team corrects an error today, but the model makes the identical error on tomorrow's document. The correction evaporates.

```
                    WITHOUT correction memory
┌──────────────┐
│  Document 1  │──► LLM Extract ──► "quarterly" for payment_terms  ──► QA corrects to "30"
│  Document 2  │──► LLM Extract ──► "quarterly" for payment_terms  ──► QA corrects AGAIN
│  Document 3  │──► LLM Extract ──► "quarterly" for payment_terms  ──► QA corrects AGAIN
│     ...      │
└──────────────┘
    Same mistake. Every time. No learning.
```

```
                    WITH correction memory (this sample)
┌──────────────┐
│  Document 1  │──► LLM Extract ──► "quarterly" (conf: 0.45) ──► self-heal ──► "30" ✓
│  Document 2  │──► LLM Extract ──► "30" (conf: 0.88)        ──► accept     ──► "30" ✓
│  Document 3  │──► Rule fires  ──► "30"                      ──► no LLM    ──► "30" ✓
└──────────────┘
    Learns. Improves. Costs less over time.
```

## How It Works

1. **Extract** — Each field is extracted with a confidence score via Amazon Bedrock
2. **Gate** — If confidence ≥ threshold, accept the result
3. **Self-Heal** — If confidence < threshold, retrieve past corrections from the correction log and retry with those as few-shot examples
4. **Learn** — QA corrections uploaded to S3 are automatically ingested into the correction log
5. **Graduate** — Recurring correction patterns synthesize into deterministic rules that eliminate LLM calls entirely

The system gets both better AND cheaper over time.

## Try It in 30 Seconds

```bash
git clone https://github.com/aws-samples/sample-prompt-correction-memory.git
cd sample-prompt-correction-memory
pip install -e ".[dev]"
python examples/quickstart.py
```

No AWS credentials needed. The quickstart uses mocked Bedrock responses to demonstrate the full self-healing loop.

## Architecture

```
Document
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tier 1: Graduated Rules              Cost: $0     Latency: <1ms│
│  Deterministic patterns from corrections. Grows over time.       │
│  "Net 30" → 30, "as of March 1, 2024" → 2024-03-01             │
└───────────────────────────┬─────────────────────────────────────┘
                            │ No rule match
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tier 2: Cloud LLM (Claude Haiku)     Cost: $0.003  Latency: 1s │
│  Standard extraction with structured output via tool use.        │
│  Bayesian-calibrated confidence threshold per field.             │
└───────────────────────────┬─────────────────────────────────────┘
                            │ Confidence < threshold
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Tier 3: Self-Healing                 Cost: $0.015  Latency: 3s │
│  Query correction log → format as few-shot examples →            │
│  Re-extract with Claude Sonnet + corrections in prompt           │
└─────────────────────────────────────────────────────────────────┘
         │
         │  QA corrections flow back into the log
         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Correction Log (DynamoDB)                                       │
│  field_name | timestamp | original | corrected | reason | excerpt│
│  Growing library of "what went wrong and why"                    │
└─────────────────────────────────────────────────────────────────┘
         │
         │  Recurring patterns auto-synthesize
         ▼
    ┌──────────────┐
    │ Rule Store   │ ──► Feeds back into Tier 1 (zero-cost extraction)
    └──────────────┘
```

## Companion Library: textract-field-memory

This sample provides **semantic correction memory** (what was extracted wrong and why). The companion sample [textract-field-memory](https://github.com/aws-samples/sample-textract-field-memory) provides **spatial positional memory** (where fields appear on document layouts).

Together they form a dual-memory architecture:

| Layer | Library | Memory Type | Cost |
|-------|---------|-------------|------|
| Spatial | textract-field-memory | Bounding-box positions | $0 (pure computation) |
| Semantic | prompt-memory | Correction log + few-shot | $0 (rules) to $0.015 (self-heal) |

```python
from field_memory import TemplateMemory
from prompt_memory import ConfidenceRouter

memory = TemplateMemory()
router = ConfidenceRouter()

def extract(document, field_name):
    # Spatial memory first (zero cost)
    matches = memory.locate(document, field_name)
    if matches and matches[0].combined_score > 0.9:
        return matches[0].key_value  # Free

    # Self-healing extraction (with correction memory)
    return router.extract_with_self_healing(document.text, field_def)
```

When spatial confidence is high → extract by position (no LLM). When spatial confidence is low → route to self-healing extraction with correction memory. Both memories grow independently from operational workflows.

## Deploy to AWS

```bash
# Prerequisites: AWS CLI, SAM CLI, Python 3.11, Bedrock model access
make deploy          # Deploys S3, DynamoDB, Lambda, EventBridge, SQS
make seed            # Pre-loads sample corrections into correction log
make trigger         # Uploads a sample document to start extraction
```

### AWS Services Used

| Service | Purpose |
|---------|---------|
| Amazon Bedrock | LLM extraction + self-healing retries (Haiku → Sonnet escalation) |
| Amazon S3 | Document and correction file storage |
| Amazon DynamoDB | Correction log + extraction results |
| Amazon EventBridge | Event routing on S3 uploads |
| AWS Lambda | Extraction orchestration + correction ingestion |
| Amazon SQS | Concurrency throttling with dead-letter queue |

## Benchmarks

The benchmark suite measures extraction quality as corrections accumulate:

| Configuration | Corrections | Avg F1 | Self-Heal Rate | Cost/Doc |
|---------------|-------------|--------|----------------|----------|
| Zero-shot | 0 | 0.53 | 0% | $0.0040 |
| Few corrections | 5 | 0.87 | 60% | $0.0106 |
| Moderate | 10 | 0.93 | 60% | $0.0106 |
| Good coverage | 25 | 0.93 | 40% | $0.0084 |
| Mature | 50 | 0.93 | 40% | $0.0084 |

Run benchmarks locally (no AWS needed):
```bash
python benchmarks/benchmark_runner.py
```

Results are written to `benchmarks/output/report.md` and `benchmarks/output/results.json`.

## API Usage

```python
from src.extraction.extractor import Extractor
from src.extraction.models import FieldDefinition
from src.prompt_memory.confidence_router import ConfidenceRouter
from src.prompt_memory.correction_store import CorrectionStore

# Define what to extract
field = FieldDefinition(
    field_name="payment_terms",
    description="Payment terms in days",
    prompt="Extract payment terms as number of days. Convert 'Net 30' to 30.",
    data_type="float",
    confidence_threshold=0.7,
)

# Extract with self-healing
router = ConfidenceRouter()
result = router.extract_with_self_healing(
    document_text="... Payment is due within thirty (30) days ...",
    field=field,
    document_type="service-agreement",
)

print(result.value)            # "30"
print(result.confidence_score) # 0.88
print(result.self_healed)      # True (correction log was used)
print(result.cost_estimate)    # 0.000045
```

## Correction Format

QA corrections are JSON files uploaded to `s3://<bucket>/corrections/`:

```json
{
    "field_name": "payment_terms",
    "document_type": "Service Agreement",
    "original_value": "quarterly",
    "corrected_value": "30",
    "correction_reason": "'Quarterly billing' refers to billing frequency, not payment terms. The actual payment terms are Net 30.",
    "document_excerpt": "Invoices shall be submitted quarterly. Payment is due within thirty (30) days of receipt."
}
```

Each correction is automatically ingested into the correction log and immediately available for future self-healing.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `CONFIDENCE_THRESHOLD` | 0.7 | Below this triggers self-healing |
| `MAX_CORRECTIONS` | 3 | Max few-shot examples per retry |
| `EXTRACTION_MODEL` | Claude Haiku 4.5 | Cost-efficient first-pass model |
| `SELF_HEALING_MODEL` | Claude Sonnet 4 | Capable model for self-healing retries |
| `DOCUMENT_BUCKET` | (from SAM) | S3 bucket for documents |
| `CORRECTION_PREFIX` | corrections/ | S3 prefix for QA feedback files |

## Cost Economics

The system produces a measurable inverse relationship between quality and cost:

```
Week 1:    0 corrections → 53% accuracy → 0% self-heal rate  → $4.00/1K docs
Week 2:    5 corrections → 87% accuracy → 60% self-heal rate → $10.60/1K docs
Week 4:   10 corrections → 93% accuracy → 60% self-heal rate → $10.60/1K docs
Week 8:   25 corrections → 93% accuracy → 40% self-heal rate → $8.40/1K docs
Week 12:  50 corrections → 93% accuracy → 40% self-heal rate → $8.40/1K docs
Week 16+: Rules graduate → 98%+ accuracy → <5% self-heal     → ~$0/1K docs (graduated fields)
```

The flywheel: more corrections → better prompts → fewer escalations → lower cost.

## Business Impact

Projections based on benchmark results at 10,000 documents/month:

| Approach | Annual Cost | Accuracy | Notes |
|----------|-------------|----------|-------|
| Manual extraction | $960,000 | 99% | Analyst time at $8/doc |
| Static LLM (no memory) | $140,480 | 53% | High error rate → heavy QA review |
| **Prompt-Memory (mature)** | **$21,008** | **93%** | **Self-healing + rule graduation** |

**Annual savings: $939K vs manual, $119K vs static LLM (85% reduction).**

### Monthly Savings by Volume

| Volume | vs Manual Extraction | vs Static LLM |
|--------|---------------------|---------------|
| 1,000 docs/mo | $7,825/mo | $996/mo |
| 10,000 docs/mo | $78,249/mo | $9,956/mo |
| 50,000 docs/mo | $391,247/mo | $49,780/mo |
| 200,000 docs/mo | $1,564,987/mo | $199,120/mo |

### Industry Projections

| Industry | Volume | Annual Savings (vs Manual) | Payback |
|----------|--------|---------------------------|---------|
| Legal (contracts) | 5,000/mo | $565K | ~2 weeks |
| Accounts Payable | 20,000/mo | $1.49M | ~2 weeks |
| Insurance Claims | 15,000/mo | $2.13M | ~2 weeks |
| Healthcare Forms | 30,000/mo | $5.12M | ~2 weeks |
| Real Estate Leases | 3,000/mo | $569K | ~2 weeks |
| Supply Chain | 25,000/mo | $1.63M | ~2 weeks |

### Value Beyond Cost

- **No ML ops**: Zero retraining, no GPU, no deployment cycles
- **Immediate effect**: Each correction improves the next extraction instantly
- **Zero marginal cost**: Rule-graduated fields never need an LLM call again
- **QA efficiency**: 70-85% reduction in review volume
- **Consistency**: Same mistake is never repeated once corrected

Run `python benchmarks/benchmark_runner.py` for the full report with per-field breakdowns.

## Project Structure

```
prompt-memory/
├── src/
│   ├── extraction/          # Core extraction logic
│   │   ├── handler.py       # Lambda handler (S3 → SQS → Extract)
│   │   ├── extractor.py     # Bedrock Converse API client
│   │   ├── schema_builder.py # Field def → tool_config
│   │   └── models.py        # FieldDefinition, ExtractionResult, CorrectionRecord
│   ├── prompt_memory/       # Self-healing loop
│   │   ├── confidence_router.py  # Accept or self-heal decision
│   │   ├── correction_store.py   # DynamoDB correction log client
│   │   └── retry_with_examples.py # Few-shot prompt construction
│   └── feedback/            # Correction ingestion
│       ├── handler.py       # Lambda handler (correction JSON → DynamoDB)
│       └── schema.py        # Correction validation
├── infrastructure/
│   └── template.yaml        # SAM template (all AWS resources)
├── benchmarks/              # Benchmark suite
│   ├── benchmark_runner.py  # Configurable benchmark runner
│   └── ground_truth.json    # Expected values for sample documents
├── examples/
│   └── quickstart.py        # Demo with mocked Bedrock (no creds needed)
├── sample_data/
│   ├── documents/           # Sample contracts for testing
│   ├── corrections/         # Sample QA corrections
│   └── field-definitions.json
├── tests/unit/              # Unit tests
├── docs/
│   ├── USE_CASES.md         # Industry use cases with ROI projections
│   ├── design-plan.md       # Technical design document
│   ├── medium-article.md    # Blog article
│   └── diagrams/            # Draw.io architecture diagrams
├── scripts/                 # Operational scripts (seed, trigger)
├── pyproject.toml           # Package metadata
├── Makefile                 # deploy, test, seed, trigger, destroy
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
└── LICENSE                  # MIT-0
```

## Use Cases

Detailed use cases with accuracy trajectories and cost savings are in [docs/USE_CASES.md](docs/USE_CASES.md):

- Legal contract processing (MSAs, NDAs, SOWs)
- Invoice and accounts payable automation
- Insurance claims processing
- Healthcare form extraction
- Compliance and regulatory document review
- Real estate lease abstraction
- HR document processing
- Supply chain document extraction

## How This Approach Compares

The table below compares the pattern demonstrated in this sample with other common approaches to adapting LLM extraction over time.

| Capability | This Sample | Fine-Tuning | RAG Self-Correction | Commercial IDP | DSPy | LangMem |
|---|:---:|:---:|:---:|:---:|:---:|:---:|
| Persistent correction memory | ✅ | ✅ | ❌ | Partial | ❌ | Partial |
| No retraining required | ✅ | ❌ | ✅ | ❌ | ✅ | ✅ |
| No redeployment required | ✅ | ❌ | ✅ | ❌ | ❌ | ✅ |
| Cost decreases over time | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Field-level granularity | ✅ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Automatic rule graduation | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Confidence-gated escalation | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Immediate effect | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Spatial memory integration | ✅ | ❌ | ❌ | Partial | ❌ | ❌ |

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

## Contributors

- Avneet Bansal
- Muskan
- Yashika Baranwal
- Nishtha Yadav

For questions or contributions, please open an issue on this repository.


## License

This sample code is licensed under the MIT-0 License. See the [LICENSE](LICENSE) file.
