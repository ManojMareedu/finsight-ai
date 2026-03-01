import requests
import streamlit as st

st.set_page_config(page_title="FinSight AI", layout="wide")

st.title("📊 FinSight AI — Financial Risk Analyzer")

company = st.text_input("Enter Company Name", "Tesla")

if st.button("Analyze Company"):

    with st.spinner("Running AI analysis..."):

        resp = requests.post(
            "http://localhost:8000/analyze",
            json={"company_name": company},
            timeout=120
        )

        if resp.status_code != 200:
            st.error(f"API Error: {resp.status_code}")
            st.text(resp.text)
            st.stop()

        data = resp.json()

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
