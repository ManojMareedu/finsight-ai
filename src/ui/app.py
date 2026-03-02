import requests
import streamlit as st

st.set_page_config(page_title="FinSight AI", layout="wide")

st.title("📊 FinSight AI — Financial Risk Analyzer")

company = st.text_input("Enter Company Name", "Tesla")

if st.button("Analyze Company"):

    with st.spinner("Running AI analysis..."):

        try:
            resp = requests.post(
                "http://127.0.0.1:8000/analyze",
                json={"company_name": company},
                timeout=600,
            )

            resp.raise_for_status()

            data = resp.json()
            if not data.get("report"):
                st.error("No report returned from backend.")
                st.stop()

        except Exception as e:
            st.error(f"Backend error: {e}")
            st.stop()

    report = data["report"]

    st.subheader("Executive Summary")
    st.write(report["executive_summary"])

    st.subheader("Key Risks")
    for r in report["key_risks"]:
        st.write(f"- {r}")

    st.subheader("Risk Score")
    st.metric("Overall Risk", report["overall_risk_score"])

    st.subheader("Recommendation")
    st.write(report["recommendation"])