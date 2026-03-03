# src/ui/app.py
import base64
import json
import os
import time
from datetime import datetime

import requests
import streamlit as st

st.set_page_config(
    page_title="FinSight AI",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# When deployed on HuggingFace the API runs on the same container
# so we check for an env var first, then fall back to localhost
API_URL = os.getenv("API_URL", "http://localhost:8000")


# ── Helpers ──────────────────────────────────────────────────────────────────

SEVERITY_COLORS = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🔵",
    "LOW": "🟢",
}

SIGNAL_COLORS = {
    "STRONG_BUY": "🟢",
    "BUY": "🟢",
    "HOLD": "🟡",
    "SELL": "🔴",
    "STRONG_SELL": "🔴",
}


def render_report(report: dict) -> None:
    """Render the DueDiligenceReport dict into Streamlit components."""

    # --- Top metrics row ---
    col1, col2, col3 = st.columns(3)
    signal = report.get("investment_signal", "HOLD")
    confidence = report.get("confidence_score", 0.0)
    col1.metric("Company", report.get("company_name", "—"))
    col2.metric(
        "Investment Signal",
        f"{SIGNAL_COLORS.get(signal, '')} {signal}",
    )
    col3.metric("Confidence", f"{confidence:.0%}")

    st.divider()

    # --- Executive Summary ---
    st.subheader("Executive Summary")
    st.write(report.get("executive_summary", "No summary available."))

    # --- Financial Snapshot + Risk Factors side by side ---
    left, right = st.columns(2)

    with left:
        st.subheader("Financial Snapshot")
        snap = report.get("financial_snapshot") or {}
        if snap:
            st.write(f"**Revenue Trend:** {snap.get('revenue_trend', 'N/A')}")
            st.write(f"**Profitability:** {snap.get('profitability_summary', 'N/A')}")
            st.write(f"**Debt Assessment:** {snap.get('debt_assessment', 'N/A')}")
            metrics = snap.get("key_metrics") or {}
            if metrics:
                st.write("**Key Metrics:**")
                rows = [[k, v] for k, v in metrics.items()]
                # Show as a clean table
                st.table(rows)
        else:
            st.info("No financial snapshot available.")

    with right:
        st.subheader("Risk Factors")
        risks = report.get("risk_factors") or []
        if risks:
            for risk in risks:
                sev = risk.get("severity", "MEDIUM")
                icon = SEVERITY_COLORS.get(sev, "⚪")
                with st.expander(f"{icon} [{sev}] {risk.get('category', '')}"):
                    st.write(risk.get("description", ""))
                    citation = risk.get("source_citation", "")
                    if citation:
                        st.caption(f"Source: {citation}")
        else:
            st.info("No risk factors identified.")

    # --- Competitive Position ---
    st.subheader("Competitive Position")
    st.write(report.get("competitive_position", "No competitive analysis available."))

    # --- Recent Developments ---
    developments = report.get("recent_developments") or []
    if developments:
        st.subheader("Recent Developments")
        for dev in developments:
            st.write(f"• {dev}")

    # --- Data Sources ---
    sources = report.get("data_sources_used") or []
    if sources:
        with st.expander("Data Sources Used"):
            for src in sources:
                st.write(f"- {src}")

    # --- Disclaimer ---
    disclaimer = report.get("disclaimer", "")
    if disclaimer:
        st.divider()
        st.caption(disclaimer)


# ── Main App ──────────────────────────────────────────────────────────────────

def main() -> None:
    # Header
    col1, col2 = st.columns([3, 1])
    with col1:
        st.title("📊 FinSight AI")
        st.caption("Multi-Agent Financial Due Diligence Platform")
    with col2:
        st.link_button("View API Docs", f"{API_URL}/docs")

    st.divider()

    # Input row
    col1, col2, col3 = st.columns([3, 1, 1])
    company = col1.text_input(
        "Company Name",
        placeholder="e.g. Apple, Tesla, Microsoft, Nvidia",
    )
    include_pdf = col2.checkbox("Download PDF", value=False)
    run = col3.button("Analyze", type="primary", use_container_width=True)

    if not run:
        return

    if not company.strip():
        st.warning("Please enter a company name.")
        return

    # Progress display — shows each agent step as it would run
    with st.status("Running multi-agent analysis...", expanded=True) as status:
        st.write("🔍 Research agent: searching web and news...")
        st.write("📄 Filing agent: retrieving SEC EDGAR documents...")
        st.write("⚠️ Risk agent: identifying and scoring risk factors...")
        st.write("✍️ Synthesis agent: generating structured report...")

        start = time.time()
        try:
            resp = requests.post(
                f"{API_URL}/analyze",
                json={
                    "company_name": company.strip(),
                    "include_pdf": include_pdf,
                },
                timeout=180,  # 3 min — free LLMs can be slow
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.exceptions.Timeout:
            status.update(label="Timed out — the LLM took too long.", state="error")
            st.error(
                "The request timed out after 3 minutes. "
                "Free-tier LLMs on OpenRouter can be slow under load. "
                "Try again in a moment."
            )
            return
        except requests.exceptions.HTTPError as e:
            status.update(label=f"API error: {e}", state="error")
            detail = ""
            try:
                detail = resp.json().get("detail", "")
            except Exception:
                pass
            st.error(f"Backend returned an error: {e}\n\n{detail}")
            return
        except Exception as e:
            status.update(label=f"Connection error: {e}", state="error")
            st.error(
                f"Could not reach the backend at `{API_URL}/analyze`.\n\n"
                f"Error: {e}"
            )
            return

        elapsed = round(time.time() - start, 1)
        status.update(label=f"Done in {elapsed}s", state="complete", expanded=False)

    # Validate response shape
    report = data.get("report")
    if not report:
        st.error(
            "The backend returned a response but no report was generated. "
            "Raw response shown below for debugging:"
        )
        st.json(data)
        return

    # Render the report
    render_report(report)

    st.divider()

    # Download buttons
    dl_col1, dl_col2 = st.columns(2)

    with dl_col1:
        st.download_button(
            label="⬇️ Download JSON Report",
            data=json.dumps(report, indent=2),
            file_name=f"finsight_{company.strip().replace(' ', '_')}_{datetime.now():%Y%m%d}.json",
            mime="application/json",
        )

    with dl_col2:
        pdf_b64 = data.get("pdf_base64")
        if include_pdf and pdf_b64:
            pdf_bytes = base64.b64decode(pdf_b64)
            st.download_button(
                label="⬇️ Download PDF Report",
                data=pdf_bytes,
                file_name=f"finsight_{company.strip().replace(' ', '_')}_{datetime.now():%Y%m%d}.pdf",
                mime="application/pdf",
            )
        elif include_pdf and not pdf_b64:
            st.warning("PDF was requested but generation failed. JSON download is still available.")


if __name__ == "__main__":
    main()