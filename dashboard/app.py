# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""
prompt-memory — Interactive Dashboard
================================================

A Streamlit dashboard for visualizing correction health, self-heal economics,
rule graduation progress, calibration quality, and live extraction testing.

Usage:
    pip install prompt-memory[dashboard]
    streamlit run dashboard/app.py

Production mode (reads from real DynamoDB):
    AWS_REGION=us-west-2 CORRECTION_TABLE=correction-log-dev streamlit run dashboard/app.py
"""

import json
import os
import random
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import plotly.graph_objects as go
import streamlit as st

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.extraction.models import CorrectionRecord, ExtractionResult, FieldDefinition
from src.prompt_memory.calibration import ConfidenceCalibrator
from src.prompt_memory.rule_engine import GraduatedRule, RuleEngine
from src.prompt_memory.semantic_retrieval import SemanticRetriever

# =============================================================================
# Demo Data Generation
# =============================================================================

SAMPLE_FIELDS = [
    "effective_date",
    "party_a",
    "party_b",
    "payment_terms",
    "governing_law",
]

SAMPLE_DOCUMENT_TYPES = ["service-agreement", "lease-agreement", "nda", "sow"]


def generate_demo_corrections(count: int = 40) -> List[CorrectionRecord]:
    """Generate synthetic corrections for demo mode."""
    random.seed(42)
    templates = [
        (
            "payment_terms",
            "service-agreement",
            "quarterly",
            "30",
            "Billing frequency != payment terms. Look for Net X.",
            "Payment due Net 30 from invoice date.",
        ),
        (
            "payment_terms",
            "lease-agreement",
            "monthly",
            "0",
            "Monthly rent = due upon receipt (0 days).",
            "Rent due on first of each month.",
        ),
        (
            "payment_terms",
            "service-agreement",
            "net thirty",
            "30",
            "Convert text to integer.",
            "Terms are Net 30 days from invoice.",
        ),
        (
            "effective_date",
            "service-agreement",
            "March 2024",
            "2024-03-01",
            "Use ISO 8601 format with exact day.",
            'entered into as of March 1, 2024 ("Effective Date")',
        ),
        (
            "effective_date",
            "nda",
            "January",
            "2025-01-10",
            "Include full date in ISO format.",
            "dated January 10, 2025",
        ),
        (
            "governing_law",
            "service-agreement",
            "State of Delaware",
            "Delaware",
            "Return state name without prefix.",
            "governed by the laws of the State of Delaware",
        ),
        (
            "governing_law",
            "nda",
            "State of New York",
            "New York",
            "Return state name without prefix.",
            "governed by the laws of the State of New York",
        ),
        (
            "party_a",
            "service-agreement",
            "Acme",
            "Acme Corporation",
            "Include full legal entity name with designation.",
            "Acme Corporation, a Delaware corporation",
        ),
        (
            "party_b",
            "service-agreement",
            "GlobalTech",
            "GlobalTech Solutions Inc.",
            "Include corporate designation (Inc.)",
            "GlobalTech Solutions Inc., a California corporation",
        ),
    ]

    corrections = []
    for i in range(count):
        t = templates[i % len(templates)]
        days_ago = count - i
        ts = (datetime.utcnow() - timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        corrections.append(
            CorrectionRecord(
                field_name=t[0],
                document_type=t[1],
                original_value=t[2],
                corrected_value=t[3],
                correction_reason=t[4],
                document_excerpt=t[5],
                timestamp=ts,
            )
        )
    return corrections


def generate_demo_extractions(
    corrections: List[CorrectionRecord],
) -> List[Dict[str, Any]]:
    """Generate synthetic extraction history for demo mode."""
    random.seed(123)
    extractions = []
    for i in range(200):
        field = random.choice(SAMPLE_FIELDS)
        doc_type = random.choice(SAMPLE_DOCUMENT_TYPES)
        # Accuracy improves over time (simulating the flywheel)
        base_confidence = 0.6 + (i / 200) * 0.3
        confidence = min(base_confidence + random.uniform(-0.1, 0.15), 1.0)
        self_healed = confidence < 0.7 and random.random() < 0.6
        if self_healed:
            confidence = min(confidence + 0.25, 0.98)
        days_ago = 200 - i
        ts = (datetime.utcnow() - timedelta(days=days_ago)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        cost = 0.003 if not self_healed else 0.015
        extractions.append(
            {
                "field_name": field,
                "document_type": doc_type,
                "confidence": round(confidence, 3),
                "self_healed": self_healed,
                "cost": cost,
                "timestamp": ts,
                "correct": confidence > 0.65,
            }
        )
    return extractions


# =============================================================================
# Benchmark Data Loading
# =============================================================================


def load_benchmark_data(
    output_path: Path,
) -> tuple:
    """Load corrections and extractions from benchmark output files.

    Reads results.json and convergence.json from the given directory,
    converts them into the same format the dashboard expects.

    Returns (corrections, extractions) or (None, None) if files not found.
    """
    results_file = output_path / "results.json"
    convergence_file = output_path / "convergence.json"

    if not results_file.exists():
        return None, None

    with open(results_file) as f:
        benchmark_runs = json.load(f)

    # Build corrections from the benchmark's correction templates
    # (reuse generate_demo_corrections since benchmarks use synthetic corrections)
    corrections = generate_demo_corrections(50)

    # Build extractions from benchmark per-field results across all configs
    extractions = []
    for run in benchmark_runs:
        corrections_count = run["corrections_count"]
        for doc in run.get("documents", []):
            for field_result in doc.get("fields", []):
                extractions.append(
                    {
                        "field_name": field_result["field_name"],
                        "document_type": doc["document_id"],
                        "confidence": field_result["confidence"],
                        "self_healed": field_result["self_healed"],
                        "cost": field_result["cost"],
                        "timestamp": f"2024-01-{corrections_count + 1:02d}T10:00:00Z",
                        "correct": field_result["correct"],
                        "corrections_at_time": corrections_count,
                        "config_name": run["name"],
                    }
                )

    # Load convergence data if available
    convergence = None
    if convergence_file.exists():
        with open(convergence_file) as f:
            convergence = json.load(f)

    return corrections, extractions, convergence, benchmark_runs


# =============================================================================
# State Management
# =============================================================================


def get_state():
    """Initialize or retrieve dashboard state.

    Data source priority:
    1. Query param: ?output_path=/path/to/benchmarks/output
    2. Env var: BENCHMARK_OUTPUT_PATH
    3. Default: benchmarks/output/ in project root
    4. Fallback: synthetic demo data
    """
    if "initialized" not in st.session_state:
        # Determine output path
        query_params = st.query_params
        output_path_str = query_params.get(
            "output_path",
            os.environ.get(
                "BENCHMARK_OUTPUT_PATH",
                str(PROJECT_ROOT / "benchmarks" / "output"),
            ),
        )
        output_path = Path(output_path_str)

        # Try loading benchmark data
        result = load_benchmark_data(output_path)
        if result[0] is not None:
            corrections, extractions, convergence, benchmark_runs = result
            st.session_state["corrections"] = corrections
            st.session_state["extractions"] = extractions
            st.session_state["convergence"] = convergence
            st.session_state["benchmark_runs"] = benchmark_runs
            st.session_state["mode"] = "benchmark"
            st.session_state["output_path"] = str(output_path)
        else:
            # Fallback to demo data
            corrections = generate_demo_corrections(40)
            extractions = generate_demo_extractions(corrections)
            st.session_state["corrections"] = corrections
            st.session_state["extractions"] = extractions
            st.session_state["convergence"] = None
            st.session_state["benchmark_runs"] = None
            st.session_state["mode"] = "demo"
            st.session_state["output_path"] = None

        st.session_state["initialized"] = True

    return (
        st.session_state["corrections"],
        st.session_state["extractions"],
    )


# =============================================================================
# Render Functions
# =============================================================================


def render_system_overview(corrections, extractions):
    """System-wide metrics overview."""
    total_extractions = len(extractions)
    total_corrections = len(corrections)
    self_healed_count = sum(1 for e in extractions if e["self_healed"])
    self_heal_rate = self_healed_count / total_extractions if total_extractions else 0
    total_cost = sum(e["cost"] for e in extractions)
    cost_saved = self_healed_count * 0.003  # savings from not escalating

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Total Extractions", f"{total_extractions:,}")
    col2.metric("Corrections Logged", total_corrections)
    col3.metric("Self-Heal Rate", f"{self_heal_rate:.1%}")
    col4.metric("Total Cost", f"${total_cost:.2f}")
    col5.metric("Cost Saved (Rules)", f"${cost_saved:.2f}")

    # Self-heal rate over time
    st.subheader("Self-Heal Rate Over Time")
    window = 20
    rates = []
    for i in range(0, len(extractions) - window, window):
        chunk = extractions[i : i + window]
        rate = sum(1 for e in chunk if e["self_healed"]) / len(chunk)
        rates.append({"batch": i // window + 1, "rate": rate})

    if rates:
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=[r["batch"] for r in rates],
                y=[r["rate"] for r in rates],
                mode="lines+markers",
                line=dict(color="#d62728", width=2),
                name="Self-Heal Rate",
            )
        )
        fig.update_layout(
            xaxis_title="Batch (20 docs each)",
            yaxis_title="Self-Heal Rate",
            yaxis=dict(range=[0, 1]),
            height=300,
        )
        st.plotly_chart(fig, use_container_width=True)


def render_correction_log(corrections):
    """Browse and filter corrections."""
    st.subheader(f"Correction Log ({len(corrections)} entries)")

    col1, col2 = st.columns(2)
    with col1:
        field_filter = st.selectbox(
            "Filter by Field", ["All"] + sorted(set(c.field_name for c in corrections))
        )
    with col2:
        type_filter = st.selectbox(
            "Filter by Document Type",
            ["All"]
            + sorted(set(c.document_type for c in corrections if c.document_type)),
        )

    filtered = corrections
    if field_filter != "All":
        filtered = [c for c in filtered if c.field_name == field_filter]
    if type_filter != "All":
        filtered = [c for c in filtered if c.document_type == type_filter]

    data = [
        {
            "Field": c.field_name,
            "Doc Type": c.document_type,
            "Wrong": c.original_value,
            "Correct": c.corrected_value,
            "Reason": (
                c.correction_reason[:60] + "..."
                if len(c.correction_reason) > 60
                else c.correction_reason
            ),
            "Date": c.timestamp[:10],
        }
        for c in reversed(filtered)
    ]
    st.dataframe(data, use_container_width=True, hide_index=True)


def render_field_performance(extractions):
    """Per-field accuracy and confidence breakdown."""
    field_stats = defaultdict(
        lambda: {"total": 0, "correct": 0, "healed": 0, "conf_sum": 0.0, "cost": 0.0}
    )

    for e in extractions:
        f = field_stats[e["field_name"]]
        f["total"] += 1
        f["correct"] += 1 if e["correct"] else 0
        f["healed"] += 1 if e["self_healed"] else 0
        f["conf_sum"] += e["confidence"]
        f["cost"] += e["cost"]

    rows = []
    for field_name, s in sorted(field_stats.items()):
        rows.append(
            {
                "Field": field_name,
                "Extractions": s["total"],
                "Accuracy": f"{s['correct'] / s['total']:.1%}" if s["total"] else "—",
                "Mean Confidence": (
                    f"{s['conf_sum'] / s['total']:.3f}" if s["total"] else "—"
                ),
                "Self-Heal %": f"{s['healed'] / s['total']:.1%}" if s["total"] else "—",
                "Total Cost": f"${s['cost']:.3f}",
            }
        )

    st.dataframe(rows, use_container_width=True, hide_index=True)

    # Bar chart: accuracy per field
    fig = go.Figure()
    names = [r["Field"] for r in rows]
    accuracies = [field_stats[n]["correct"] / field_stats[n]["total"] for n in names]
    colors = [
        "#2ca02c" if a > 0.85 else "#ff7f0e" if a > 0.7 else "#d62728"
        for a in accuracies
    ]

    fig.add_trace(go.Bar(x=names, y=accuracies, marker_color=colors))
    fig.update_layout(
        title="Accuracy by Field",
        yaxis=dict(range=[0, 1], title="Accuracy"),
        height=300,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_convergence_curves(corrections, extractions):
    """Accuracy vs. correction count (the flywheel visualization)."""
    st.subheader("Convergence: Accuracy Improves With Corrections")

    # Group extractions into time buckets and compute accuracy per bucket
    bucket_size = 20
    buckets = []
    for i in range(0, len(extractions), bucket_size):
        chunk = extractions[i : i + bucket_size]
        accuracy = sum(1 for e in chunk if e["correct"]) / len(chunk)
        avg_cost = sum(e["cost"] for e in chunk) / len(chunk)
        # Count corrections available at this point in time
        corrections_at_time = min(i // 5, len(corrections))
        buckets.append(
            {
                "corrections_available": corrections_at_time,
                "accuracy": accuracy,
                "avg_cost": avg_cost,
            }
        )

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[b["corrections_available"] for b in buckets],
            y=[b["accuracy"] for b in buckets],
            mode="lines+markers",
            name="Accuracy",
            line=dict(color="#2ca02c", width=3),
        )
    )
    fig.update_layout(
        xaxis_title="Corrections Available",
        yaxis_title="Extraction Accuracy",
        yaxis=dict(range=[0.5, 1.0]),
        height=350,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Cost curve
    st.subheader("Cost Decreases As Corrections Accumulate")
    fig2 = go.Figure()
    fig2.add_trace(
        go.Scatter(
            x=[b["corrections_available"] for b in buckets],
            y=[b["avg_cost"] for b in buckets],
            mode="lines+markers",
            name="Avg Cost/Extraction",
            line=dict(color="#d62728", width=3),
            fill="tozeroy",
            fillcolor="rgba(214, 39, 40, 0.1)",
        )
    )
    fig2.update_layout(
        xaxis_title="Corrections Available",
        yaxis_title="Avg Cost per Extraction ($)",
        height=300,
    )
    st.plotly_chart(fig2, use_container_width=True)


def render_rule_graduation(corrections):
    """Show graduated rules and attempt graduation on current corrections."""
    import tempfile

    engine = RuleEngine(store_path=Path(tempfile.mkdtemp()), graduation_threshold=5)

    # Attempt graduation
    new_rules = engine.graduate_from_corrections(corrections)

    col1, col2 = st.columns(2)
    col1.metric("Rules Graduated", len(new_rules))
    col2.metric("LLM Calls Eliminated", f"{len(new_rules)} field patterns")

    if new_rules:
        st.subheader("Graduated Rules")
        rule_data = [
            {
                "Field": r.field_name,
                "Doc Type": r.document_type or "*",
                "Rule Type": r.rule_type,
                "Pattern": r.pattern[:50],
                "Source Corrections": r.source_corrections,
                "Accuracy": f"{r.accuracy_on_source:.0%}",
            }
            for r in new_rules
        ]
        st.dataframe(rule_data, use_container_width=True, hide_index=True)

        # Test a rule
        st.subheader("Test a Rule")
        test_text = st.text_area(
            "Paste document text to test rule against:",
            value="Payment is due within thirty (30) days of receipt (Net 30).",
            height=80,
        )
        if st.button("Apply Rules"):
            for rule in new_rules:
                result = rule.apply(test_text)
                if result:
                    st.success(
                        f"✓ Rule for '{rule.field_name}' extracted: **{result}**"
                    )
                else:
                    st.info(f"— Rule for '{rule.field_name}' did not match.")
    else:
        st.info(
            "No rules graduated yet. Need 5+ corrections with the same pattern for a field."
        )


def render_calibration(extractions):
    """Show calibration quality: reliability diagram and ECE."""
    import tempfile

    calibrator = ConfidenceCalibrator(
        store_path=Path(tempfile.mkdtemp()), min_samples=5, margin=0.05
    )

    # Feed all extractions into calibrator
    for e in extractions:
        calibrator.record_extraction(e["field_name"], e["confidence"])
        if not e["correct"]:
            calibrator.record_correction(e["field_name"], e["confidence"])

    report = calibrator.get_calibration_report()

    if not report:
        st.info("Not enough data for calibration analysis.")
        return

    # Summary table
    cal_rows = [
        {
            "Field": field,
            "Original Threshold": f"{data['original_threshold']:.2f}",
            "Calibrated Threshold": f"{data['current_threshold']:.3f}",
            "Δ Threshold": f"{data['threshold_delta']:+.3f}",
            "Accuracy": f"{data['actual_accuracy']:.1%}",
            "Mean Confidence": f"{data['mean_confidence']:.3f}",
            "Calibration Error": f"{data['calibration_error']:+.3f}",
        }
        for field, data in sorted(report.items())
    ]
    st.dataframe(cal_rows, use_container_width=True, hide_index=True)

    # Reliability diagram
    st.subheader("Reliability Diagram")
    st.markdown(
        "Perfect calibration = points on the diagonal. Above = underconfident. Below = overconfident."
    )

    fig = go.Figure()
    # Diagonal (perfect calibration)
    fig.add_trace(
        go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode="lines",
            line=dict(color="gray", dash="dash", width=1),
            name="Perfect Calibration",
        )
    )

    # Plot each field
    colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
    for idx, (field, data) in enumerate(sorted(report.items())):
        fig.add_trace(
            go.Scatter(
                x=[data["mean_confidence"]],
                y=[data["actual_accuracy"]],
                mode="markers",
                marker=dict(size=12, color=colors[idx % len(colors)]),
                name=field,
            )
        )

    fig.update_layout(
        xaxis=dict(title="Mean Confidence", range=[0, 1]),
        yaxis=dict(title="Actual Accuracy", range=[0, 1]),
        height=400,
        width=500,
    )
    st.plotly_chart(fig, use_container_width=True)


def render_cost_tracker(extractions):
    """Cost tracking over time."""
    # Cumulative cost
    cumulative = []
    running_cost = 0
    for i, e in enumerate(extractions):
        running_cost += e["cost"]
        if i % 10 == 0:
            cumulative.append({"extractions": i, "cumulative_cost": running_cost})

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[c["extractions"] for c in cumulative],
            y=[c["cumulative_cost"] for c in cumulative],
            mode="lines",
            fill="tozeroy",
            line=dict(color="#1f77b4", width=2),
            fillcolor="rgba(31, 119, 180, 0.1)",
        )
    )
    fig.update_layout(
        xaxis_title="Extractions Processed",
        yaxis_title="Cumulative Cost ($)",
        height=300,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Cost per extraction (moving average)
    st.subheader("Cost Per Extraction (Moving Average)")
    window = 20
    avg_costs = []
    for i in range(0, len(extractions) - window, window):
        chunk = extractions[i : i + window]
        avg = sum(e["cost"] for e in chunk) / len(chunk)
        avg_costs.append({"batch": i // window, "cost": avg})

    if avg_costs:
        fig2 = go.Figure()
        fig2.add_trace(
            go.Bar(
                x=[c["batch"] for c in avg_costs],
                y=[c["cost"] for c in avg_costs],
                marker_color=[
                    (
                        "#2ca02c"
                        if c["cost"] < 0.008
                        else "#ff7f0e" if c["cost"] < 0.012 else "#d62728"
                    )
                    for c in avg_costs
                ],
            )
        )
        fig2.update_layout(
            xaxis_title="Batch",
            yaxis_title="Avg Cost ($)",
            height=300,
        )
        st.plotly_chart(fig2, use_container_width=True)


def render_live_extraction(corrections):
    """Live extraction testing interface."""
    st.markdown("Paste document text and test extraction with self-healing.")

    default_text = """MASTER SERVICE AGREEMENT

This Master Service Agreement ("Agreement") is entered into as of March 1, 2024
("Effective Date") by and between:

Acme Corporation, a Delaware corporation ("Client")
and
GlobalTech Solutions Inc., a California corporation ("Provider")

Payment is due within thirty (30) days of receipt of each invoice (Net 30).

This Agreement shall be governed by the laws of the State of Delaware."""

    document_text = st.text_area("Document Text", value=default_text, height=200)

    col1, col2 = st.columns(2)
    with col1:
        field_name = st.selectbox("Field to Extract", SAMPLE_FIELDS)
    with col2:
        doc_type = st.selectbox("Document Type", SAMPLE_DOCUMENT_TYPES)

    if st.button("Extract (Simulated)", type="primary"):
        # Simulate extraction using semantic retrieval
        retriever = SemanticRetriever(corrections)
        relevant = retriever.retrieve_for_field(
            document_text=document_text,
            field_name=field_name,
            document_type=doc_type,
            limit=3,
        )

        st.subheader("Extraction Result (Simulated)")

        # Simulate: if corrections exist for this field, show the self-healing path
        if relevant:
            st.warning(f"Confidence below threshold → Self-healing triggered")
            st.markdown("**Corrections retrieved:**")
            for i, c in enumerate(relevant, 1):
                st.markdown(
                    f"{i}. ❌ `{c.original_value}` → ✅ `{c.corrected_value}`\n"
                    f"   *Reason: {c.correction_reason}*"
                )
            st.success(
                f"✓ Self-healed extraction: **{relevant[0].corrected_value}** "
                f"(learned from correction log)"
            )
        else:
            st.success(
                f"✓ Standard extraction (no corrections needed for '{field_name}')"
            )

        # Show what the prompt would look like
        with st.expander("View Enhanced Prompt (Few-Shot Examples)"):
            if relevant:
                prompt_parts = [f"LEARN FROM PAST CORRECTIONS:\n"]
                for i, c in enumerate(relevant, 1):
                    prompt_parts.append(
                        f"Example {i}:\n"
                        f"  Document excerpt: {c.document_excerpt}\n"
                        f"  Incorrect answer: {c.original_value}\n"
                        f"  Correct answer: {c.corrected_value}\n"
                        f"  Reason: {c.correction_reason}\n"
                    )
                st.code("\n".join(prompt_parts), language=None)
            else:
                st.info("No corrections available — standard prompt would be used.")


# =============================================================================
# Main Application
# =============================================================================


def render_benchmark_results():
    """Display benchmark results loaded from output files."""
    benchmark_runs = st.session_state.get("benchmark_runs")
    convergence = st.session_state.get("convergence")
    output_path = st.session_state.get("output_path", "")

    if not benchmark_runs:
        st.warning(
            "No benchmark data available. Run `python benchmarks/benchmark_runner.py` first."
        )
        return

    st.caption(f"Data source: `{output_path}`")

    # Summary table
    st.subheader("Extraction Quality by Configuration")
    summary_data = [
        {
            "Configuration": run["name"],
            "Corrections": run["corrections_count"],
            "Avg F1": f"{run['avg_f1']:.3f}",
            "Precision": f"{run['avg_precision']:.3f}",
            "Recall": f"{run['avg_recall']:.3f}",
            "Self-Heal Rate": f"{run['avg_self_heal_rate']:.0%}",
            "Cost/Doc": f"${run['avg_cost_per_doc']:.4f}",
        }
        for run in benchmark_runs
    ]
    st.dataframe(summary_data, use_container_width=True, hide_index=True)

    # F1 progression chart
    st.subheader("F1 Score Progression")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=[r["corrections_count"] for r in benchmark_runs],
            y=[r["avg_f1"] for r in benchmark_runs],
            mode="lines+markers",
            name="Avg F1",
            line=dict(color="#2ca02c", width=3),
            marker=dict(size=10),
        )
    )
    fig.update_layout(
        xaxis_title="Corrections Seeded",
        yaxis_title="Average F1 Score",
        yaxis=dict(range=[0, 1.05]),
        height=350,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Cost vs Self-Heal rate
    st.subheader("Cost & Self-Heal Rate")
    fig2 = go.Figure()
    fig2.add_trace(
        go.Bar(
            x=[r["name"] for r in benchmark_runs],
            y=[r["avg_cost_per_doc"] for r in benchmark_runs],
            name="Cost/Doc ($)",
            marker_color="#1f77b4",
        )
    )
    fig2.add_trace(
        go.Scatter(
            x=[r["name"] for r in benchmark_runs],
            y=[r["avg_self_heal_rate"] for r in benchmark_runs],
            name="Self-Heal Rate",
            yaxis="y2",
            mode="lines+markers",
            line=dict(color="#d62728", width=2),
        )
    )
    fig2.update_layout(
        yaxis=dict(title="Cost per Doc ($)", side="left"),
        yaxis2=dict(title="Self-Heal Rate", side="right", overlaying="y", range=[0, 1]),
        height=350,
    )
    st.plotly_chart(fig2, use_container_width=True)

    # Per-document breakdown
    st.subheader("Per-Document Results")
    selected_config = st.selectbox(
        "Configuration",
        [r["name"] for r in benchmark_runs],
        index=len(benchmark_runs) - 1,
    )
    run = next(r for r in benchmark_runs if r["name"] == selected_config)
    for doc in run["documents"]:
        with st.expander(f"📄 {doc['document_id']} — F1: {doc['f1']:.3f}"):
            field_data = [
                {
                    "Field": f["field_name"],
                    "Extracted": f["extracted"],
                    "Expected": f["expected"],
                    "Correct": "✅" if f["correct"] else "❌",
                    "Confidence": f"{f['confidence']:.3f}",
                    "Self-Healed": "🔄" if f["self_healed"] else "—",
                    "Cost": f"${f['cost']:.4f}",
                }
                for f in doc["fields"]
            ]
            st.dataframe(field_data, use_container_width=True, hide_index=True)

    # Convergence data
    if convergence:
        st.subheader("Corrections to Convergence")

        # Summary
        summary = convergence.get("summary", {})
        conv_rows = [
            {
                "Field": field,
                "To 90%": data["to_90pct"] or "N/A",
                "To 95%": data["to_95pct"] or "N/A",
                "To Rule Graduation": data["to_rule_graduation"] or "N/A",
            }
            for field, data in summary.items()
        ]
        st.dataframe(conv_rows, use_container_width=True, hide_index=True)

        # Convergence curves
        st.subheader("Convergence Curves by Field")
        fig3 = go.Figure()
        colors = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
        for idx, field_data in enumerate(convergence.get("fields", [])):
            curve = field_data.get("curve", [])
            fig3.add_trace(
                go.Scatter(
                    x=[p["corrections"] for p in curve],
                    y=[p["accuracy"] for p in curve],
                    mode="lines",
                    name=field_data["field_name"],
                    line=dict(color=colors[idx % len(colors)], width=2),
                )
            )
        fig3.add_hline(
            y=0.9, line_dash="dash", line_color="gray", annotation_text="90% target"
        )
        fig3.add_hline(
            y=0.95, line_dash="dot", line_color="gray", annotation_text="95% target"
        )
        fig3.update_layout(
            xaxis_title="Corrections Available",
            yaxis_title="Accuracy",
            yaxis=dict(range=[0.4, 1.02]),
            height=400,
        )
        st.plotly_chart(fig3, use_container_width=True)


def main():
    """Main dashboard application."""
    st.set_page_config(
        page_title="Prompt-Memory Extraction Dashboard",
        page_icon="🔄",
        layout="wide",
    )

    st.title("🔄 Prompt-Memory Extraction Dashboard")
    st.caption("Correction memory, rule graduation, and extraction economics")

    corrections, extractions = get_state()

    # Build page list based on available data
    pages = [
        "System Overview",
        "Correction Log",
        "Field Performance",
        "Convergence Curves",
        "Rule Graduation",
        "Calibration Report",
        "Cost Tracker",
        "Live Extraction",
    ]
    if st.session_state.get("benchmark_runs"):
        pages.insert(0, "Benchmark Results")

    # Sidebar navigation
    st.sidebar.title("Navigation")
    page = st.sidebar.radio("Select View", pages)

    st.sidebar.markdown("---")
    mode = st.session_state.get("mode", "demo")
    mode_label = "📊 Benchmark Data" if mode == "benchmark" else "🎲 Demo (Synthetic)"
    st.sidebar.markdown(f"**Data Source:** {mode_label}")
    if st.session_state.get("output_path"):
        st.sidebar.caption(st.session_state["output_path"])
    st.sidebar.markdown(f"**Corrections:** {len(corrections)}")
    st.sidebar.markdown(f"**Extractions:** {len(extractions)}")
    heal_rate = sum(1 for e in extractions if e["self_healed"]) / len(extractions)
    st.sidebar.markdown(f"**Self-Heal Rate:** {heal_rate:.1%}")

    # Page routing
    if page == "Benchmark Results":
        st.header("Benchmark Results")
        render_benchmark_results()

    elif page == "System Overview":
        st.header("System Overview")
        render_system_overview(corrections, extractions)

    elif page == "Correction Log":
        st.header("Correction Log")
        render_correction_log(corrections)

    elif page == "Field Performance":
        st.header("Field Performance")
        render_field_performance(extractions)

    elif page == "Convergence Curves":
        st.header("Convergence Curves")
        render_convergence_curves(corrections, extractions)

    elif page == "Rule Graduation":
        st.header("Rule Graduation")
        render_rule_graduation(corrections)

    elif page == "Calibration Report":
        st.header("Confidence Calibration")
        render_calibration(extractions)

    elif page == "Cost Tracker":
        st.header("Cost Tracker")
        render_cost_tracker(extractions)

    elif page == "Live Extraction":
        st.header("Live Extraction Test")
        render_live_extraction(corrections)


if __name__ == "__main__":
    main()
