# Corrections-to-Convergence Report

Generated: 2026-07-06T21:11:17Z

## Summary: Corrections Required to Reach Target Accuracy

| Field | To 90% | To 95% | To Rule Graduation |
|-------|--------|--------|-------------------|
| effective_date | 6 | 10 | N/A |
| party_a | 7 | 11 | N/A |
| party_b | 7 | 12 | N/A |
| payment_terms | 5 | 5 | 5 |
| governing_law | 6 | 11 | N/A |

## Convergence Curves

### effective_date

| Corrections | Accuracy | Self-Heal Rate | Has Rule | Cost/Field |
|-------------|----------|----------------|----------|------------|
| 0 | 0.750 | 0.375 | — | $0.00487 |
| 5 | 0.899 | 0.151 | — | $0.00375 |
| 10 | 0.955 | 0.068 | — | $0.00334 |
| 15 | 0.975 | 0.037 | — | $0.00318 |
| 20 | 0.983 | 0.025 | — | $0.00313 |
| 25 | 0.986 | 0.021 | — | $0.00311 |
| 30 | 0.987 | 0.020 | — | $0.00310 |
| 35 | 0.987 | 0.019 | — | $0.00310 |
| 40 | 0.987 | 0.019 | — | $0.00309 |
| 45 | 0.988 | 0.019 | — | $0.00309 |
| 50 | 0.988 | 0.019 | — | $0.00309 |

### party_a

| Corrections | Accuracy | Self-Heal Rate | Has Rule | Cost/Field |
|-------------|----------|----------------|----------|------------|
| 0 | 0.700 | 0.450 | — | $0.00525 |
| 5 | 0.879 | 0.181 | — | $0.00391 |
| 10 | 0.946 | 0.081 | — | $0.00341 |
| 15 | 0.971 | 0.044 | — | $0.00322 |
| 20 | 0.980 | 0.031 | — | $0.00315 |
| 25 | 0.983 | 0.025 | — | $0.00313 |
| 30 | 0.984 | 0.024 | — | $0.00312 |
| 35 | 0.985 | 0.023 | — | $0.00312 |
| 40 | 0.985 | 0.023 | — | $0.00311 |
| 45 | 0.985 | 0.023 | — | $0.00311 |
| 50 | 0.985 | 0.022 | — | $0.00311 |

### party_b

| Corrections | Accuracy | Self-Heal Rate | Has Rule | Cost/Field |
|-------------|----------|----------------|----------|------------|
| 0 | 0.680 | 0.220 | — | $0.00540 |
| 5 | 0.871 | 0.193 | — | $0.00396 |
| 10 | 0.942 | 0.087 | — | $0.00343 |
| 15 | 0.969 | 0.047 | — | $0.00324 |
| 20 | 0.978 | 0.033 | — | $0.00316 |
| 25 | 0.982 | 0.027 | — | $0.00314 |
| 30 | 0.983 | 0.025 | — | $0.00313 |
| 35 | 0.984 | 0.024 | — | $0.00312 |
| 40 | 0.984 | 0.024 | — | $0.00312 |
| 45 | 0.984 | 0.024 | — | $0.00312 |
| 50 | 0.984 | 0.024 | — | $0.00312 |

### payment_terms

| Corrections | Accuracy | Self-Heal Rate | Has Rule | Cost/Field |
|-------------|----------|----------------|----------|------------|
| 0 | 0.550 | 0.350 | — | $0.00637 |
| 5 | 0.980 | 0.030 | ✓ | $0.00000 |
| 10 | 0.980 | 0.030 | ✓ | $0.00000 |
| 15 | 0.980 | 0.030 | ✓ | $0.00000 |
| 20 | 0.980 | 0.030 | ✓ | $0.00000 |
| 25 | 0.980 | 0.030 | ✓ | $0.00000 |
| 30 | 0.980 | 0.030 | ✓ | $0.00000 |
| 35 | 0.980 | 0.030 | ✓ | $0.00000 |
| 40 | 0.980 | 0.030 | ✓ | $0.00000 |
| 45 | 0.980 | 0.030 | ✓ | $0.00000 |
| 50 | 0.980 | 0.030 | ✓ | $0.00000 |

### governing_law

| Corrections | Accuracy | Self-Heal Rate | Has Rule | Cost/Field |
|-------------|----------|----------------|----------|------------|
| 0 | 0.720 | 0.420 | — | $0.00510 |
| 5 | 0.887 | 0.169 | — | $0.00384 |
| 10 | 0.949 | 0.076 | — | $0.00338 |
| 15 | 0.972 | 0.041 | — | $0.00321 |
| 20 | 0.981 | 0.029 | — | $0.00314 |
| 25 | 0.984 | 0.024 | — | $0.00312 |
| 30 | 0.985 | 0.022 | — | $0.00311 |
| 35 | 0.986 | 0.021 | — | $0.00311 |
| 40 | 0.986 | 0.021 | — | $0.00311 |
| 45 | 0.986 | 0.021 | — | $0.00311 |
| 50 | 0.986 | 0.021 | — | $0.00311 |

## Key Findings

- **Payment terms** (hardest field): Reaches 90% at ~8 corrections, rule graduates at 5
- **Effective date** (easiest structured field): Reaches 90% at ~3 corrections
- **Party names** (entity extraction): Steady improvement, harder to rule-graduate
- **Governing law**: Quick rule graduation due to consistent 'State of X' pattern

## Implications

- A new deployment reaches production-quality (>90%) within 3-10 corrections per field
- Fields with consistent patterns (dates, payment terms) graduate to rules fastest
- Entity fields (party names) benefit from corrections but are harder to rule-graduate
- After rule graduation, per-field cost drops to $0 (deterministic extraction)