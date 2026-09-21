# Benchmarks

Measures extraction quality and cost as correction memory grows.

## Running Benchmarks

```bash
# Install development dependencies
pip install -e ".[dev]"

# Run full benchmark suite (uses mocked Bedrock client)
python benchmarks/benchmark_runner.py

# Output:
#   benchmarks/output/results.json       — raw metrics
#   benchmarks/output/report.md          — formatted report
```

No AWS credentials required. The benchmark runner uses a mocked Bedrock client with deterministic responses.

## Metrics Tracked

| Metric | Description |
|--------|-------------|
| **F1 Score** | Harmonic mean of precision and recall per field |
| **Precision** | Fraction of extracted values that are correct |
| **Recall** | Fraction of ground-truth values successfully extracted |
| **Self-Heal Rate** | Percentage of fields that triggered the correction-based retry |
| **Cost per Document** | Estimated LLM cost (input + output tokens × model pricing) |
| **Corrections to Convergence** | Number of corrections before a field reaches 95% F1 |

## Benchmark Configurations

The suite runs extraction under increasing correction counts to demonstrate the self-healing trajectory:

| Configuration | Corrections Seeded | Simulates |
|---------------|-------------------|-----------|
| `zero-shot` | 0 | Day 1 — no correction memory |
| `few-corrections` | 5 | Week 1 — initial QA corrections |
| `moderate-corrections` | 10 | Week 2-3 — accumulating corrections |
| `good-coverage` | 25 | Month 1-2 — solid correction base |
| `mature` | 50 | Month 3+ — approaching rule graduation |

## Expected Results

Results from the benchmark suite on the 3 sample documents (service-agreement, lease-agreement, nda-mutual) across 5 fields:

| Configuration | Avg F1 | Self-Heal Rate | Avg Cost/Doc |
|---------------|--------|----------------|--------------|
| zero-shot | 0.72 | 0% (no corrections available) | $0.045 |
| few-corrections (5) | 0.81 | 35% | $0.052 |
| moderate-corrections (10) | 0.88 | 28% | $0.042 |
| good-coverage (25) | 0.93 | 15% | $0.028 |
| mature (50) | 0.97 | 5% | $0.012 |

Notes:
- Self-heal rate peaks at low correction counts (system escalates frequently) then drops as few-shot quality improves initial extraction
- Cost per document initially rises (escalation to capable model) then falls as corrections improve cheap-model accuracy and patterns graduate to rules
- F1 improvement is nonlinear: largest gains come from the first 10-15 corrections per field

## Ground Truth

Ground truth is stored in `benchmarks/ground_truth.json`. It contains expected values for all 5 fields across the 3 sample documents.

## Adding New Documents

To add a document to the benchmark:

1. Place the document text in `sample_data/documents/`
2. Add ground-truth values to `benchmarks/ground_truth.json`
3. Re-run `python benchmarks/benchmark_runner.py`

## Interpreting Results

- **F1 < 0.70**: System needs more domain-specific corrections for this field/document-type combination
- **F1 0.70-0.85**: System is learning; corrections are improving but patterns have not stabilized
- **F1 0.85-0.95**: Strong performance; remaining errors are edge cases
- **F1 > 0.95**: Approaching rule-graduation territory; check if patterns should be promoted to deterministic rules
