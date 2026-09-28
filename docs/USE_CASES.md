# Use Cases

> **Note:** These examples use only non-regulated business documents. Do not use
> this sample with personal data, health data, or other regulated data. See the
> disclaimer in the [README](../README.md).

## 1. Legal Contract Processing

### Problem
Law firms and corporate legal teams extract key terms from thousands of contracts monthly. Manual extraction is slow (15-30 minutes per document) and error-prone. LLM extraction produces inconsistent results across formatting styles from different law firms, jurisdictions, and corporate templates.

### Fields Extracted
- Party names (full legal entity names with corporate designations)
- Effective dates and expiration dates
- Payment terms and amounts
- Termination clauses and notice periods
- Governing law jurisdictions
- Non-compete duration and scope
- Indemnification caps
- Assignment and change-of-control provisions

### How Self-Healing Helps
- **Day 1**: LLM extracts "Acme Corp" instead of "Acme Corporation, Inc." — QA corrects to include full corporate designation
- **Day 5**: System has 3 corrections showing the pattern "always include Inc./LLC/Ltd." — future extractions include designations without escalation
- **Day 30**: The corporate-designation pattern graduates to a post-processing rule: append designation from the document preamble
- **Day 60**: Date format corrections (various text formats → ISO 8601) graduate to regex-based extraction rules

### Accuracy Trajectory
| Timeframe | F1 Score | Self-Heal Rate | Per-Doc Cost |
|-----------|----------|----------------|--------------|
| Week 1 | 0.72 | 35% | $0.045 |
| Week 4 | 0.86 | 22% | $0.032 |
| Week 12 | 0.94 | 8% | $0.018 |
| Week 24 | 0.97 | 3% | $0.008 |

### Cost Savings
- Manual extraction: ~$8.00 per document (analyst time)
- Static LLM extraction: ~$0.05 per document (but 72% accuracy requires manual review of 28%)
- Self-healing at steady state: ~$0.008 per document at 97% accuracy
- Net savings: 85% reduction in QA review volume by week 12

---

## 2. Invoice and Accounts Payable Automation

### Problem
Accounts payable teams process invoices from hundreds of vendors, each with different formats. Line-item extraction is inconsistent. Tax calculations, currency formatting, and payment term conventions vary across vendors and regions. Errors cause payment delays and vendor relationship issues.

### Fields Extracted
- Vendor name and address
- Invoice number and date
- Line item descriptions, quantities, unit prices
- Subtotals, tax amounts, totals
- Payment terms (Net 30, Net 60, due upon receipt)
- Purchase order references
- Currency and bank details

### How Self-Healing Helps
- Corrections for "Net 30" → integer 30 accumulate across vendor types and graduate to a regex rule within weeks
- Tax calculation format corrections (e.g., "$1,234.56" vs "1234.56") become deterministic parsing rules
- Vendor name normalization corrections (e.g., "IBM" → "International Business Machines Corporation") build a lookup table
- Currency format corrections transfer across invoices from the same region

### Accuracy Trajectory
| Timeframe | F1 Score | Self-Heal Rate | Per-Doc Cost |
|-----------|----------|----------------|--------------|
| Week 1 | 0.68 | 42% | $0.052 |
| Week 4 | 0.84 | 25% | $0.035 |
| Week 12 | 0.93 | 10% | $0.015 |
| Week 24 | 0.98 | 2% | $0.005 |

### Cost Savings
- Invoices with graduated rules (recurring vendors): $0.001 per document
- 80% of invoice volume comes from top 20% of vendors — these graduate fastest
- Average AP department processing 10,000 invoices/month saves ~$4,500/month in LLM costs by month 6

---

## 3. Compliance and Regulatory Document Review

### Problem
Compliance teams extract obligations, deadlines, and responsible parties from regulatory filings, audit reports, and policy documents. Regulatory language evolves across jurisdictions and time periods. Terminology differences between regulators (SEC, OCC, FINRA, FCA) create extraction inconsistencies.

### Fields Extracted
- Regulatory body and filing type
- Compliance deadlines and effective dates
- Obligation descriptions
- Responsible parties and departments
- Penalty amounts and escalation clauses
- Cross-references to other regulations
- Exemption conditions
- Reporting frequencies

### How Self-Healing Helps
- Regulatory body name normalization corrections build authority lookup tables
- Deadline extraction corrections (relative dates like "within 90 days of notification" → absolute dates) improve with context
- Cross-reference format corrections (e.g., "§ 240.10b-5" vs "Rule 10b-5" vs "Exchange Act Section 10(b)") graduate to pattern rules
- Penalty amount corrections handle currency, percentage, and "up to" qualifiers

### Accuracy Trajectory
| Timeframe | F1 Score | Self-Heal Rate | Per-Doc Cost |
|-----------|----------|----------------|--------------|
| Week 1 | 0.62 | 52% | $0.078 |
| Week 4 | 0.76 | 35% | $0.055 |
| Week 12 | 0.88 | 15% | $0.028 |
| Week 24 | 0.94 | 6% | $0.014 |

### Cost Savings
- Compliance analyst manual review: $25-40 per document (senior analyst time)
- Self-healing reduces review burden by 70% at week 12
- Regulatory change monitoring becomes near-real-time vs. quarterly manual review cycles

---

## 4. Real Estate Lease Abstraction

### Problem
Real estate portfolio managers extract terms from commercial leases for rent rolls, critical date tracking, and portfolio analytics. Leases vary from 3-page simple agreements to 200-page complex deals with multiple amendments. Standard lease clauses appear in hundreds of variations.

### Fields Extracted
- Tenant and landlord names
- Premises description and square footage
- Lease commencement and expiration dates
- Base rent and escalation schedule
- CAM charges and pass-through structure
- Renewal options (terms, notice periods, rate adjustments)
- Tenant improvement allowances
- Security deposit amounts
- Insurance requirements
- Assignment and subletting conditions

### How Self-Healing Helps
- Rent escalation format corrections (e.g., "3% annually" vs "$0.50/SF/year" vs "CPI-linked") build per-landlord templates
- Date extraction from amendment chains improves as corrections teach the system to read amendment hierarchies
- Square footage extraction corrections handle "approximately" qualifiers and measurement standard differences (USF vs RSF)
- Renewal option parsing corrections accumulate into structured extraction patterns

### Accuracy Trajectory
| Timeframe | F1 Score | Self-Heal Rate | Per-Doc Cost |
|-----------|----------|----------------|--------------|
| Week 1 | 0.60 | 55% | $0.095 |
| Week 4 | 0.74 | 38% | $0.065 |
| Week 12 | 0.87 | 18% | $0.035 |
| Week 24 | 0.93 | 7% | $0.018 |

### Cost Savings
- Manual lease abstraction: $50-150 per lease (paralegal time, complexity-dependent)
- Self-healing at steady state: $0.08 per lease at 93% accuracy
- Portfolio of 500 leases: annual savings of $25,000-75,000 in abstraction costs

---

## 5. Supply Chain Document Extraction

### Problem
Supply chain teams process bills of lading, packing lists, certificates of origin, customs declarations, and shipping manifests. Documents arrive from global trade partners in multiple languages and formats. Time pressure is high (customs clearance deadlines), and errors cause shipment delays and compliance penalties.

### Fields Extracted
- Shipper and consignee names
- Bill of lading numbers
- Container numbers and seal numbers
- Commodity descriptions and HS codes
- Quantities, weights, and dimensions
- Port of origin and destination
- Vessel/flight information
- Incoterms
- Declared values and currencies

### How Self-Healing Helps
- HS code corrections build per-commodity classification rules
- Port name normalizations (e.g., "Port of LA" vs "Los Angeles" vs "USLAX") graduate to lookup tables
- Weight unit corrections (kg vs lbs vs MT) build unit-detection rules per trade lane
- Container number format corrections (ISO 6346) graduate to regex validation rules quickly

### Accuracy Trajectory
| Timeframe | F1 Score | Self-Heal Rate | Per-Doc Cost |
|-----------|----------|----------------|--------------|
| Week 1 | 0.66 | 45% | $0.060 |
| Week 4 | 0.80 | 28% | $0.040 |
| Week 12 | 0.92 | 10% | $0.018 |
| Week 24 | 0.97 | 3% | $0.007 |

### Cost Savings
- Manual customs data entry: $5-10 per shipment document
- Self-healing at steady state: $0.03 per document at 97% accuracy
- Importer processing 2,000 shipments/month saves ~$12,000/month by month 6
- Customs compliance penalty avoidance (incorrect HS codes): $5,000-50,000 per incident
