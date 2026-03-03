
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import base64
import time
import logging

from src.graph.workflow import build_workflow
from src.utils.pdf_generator import generate_pdf

logger = logging.getLogger(__name__)
router = APIRouter()

# Build once at startup - not per request
workflow = build_workflow()


class AnalyzeRequest(BaseModel):
    company_name: str
    company_ticker: Optional[str] = None
    include_pdf: bool = False


class AnalyzeResponse(BaseModel):
    company: str
    report: dict
    pdf_base64: Optional[str] = None
    processing_time_seconds: float


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(request: AnalyzeRequest):
    """
    Run the full multi-agent due diligence pipeline for a company.
    Set include_pdf=true to receive a base64-encoded PDF in the response.
    """
    start = time.time()
    company = request.company_name.strip()

    if not company:
        raise HTTPException(status_code=422, detail="company_name cannot be empty")

    logger.info(f"Starting analysis for: {company}")

    try:
        result = workflow.invoke({
            "company_name": company,
            "company_ticker": request.company_ticker or "",
            "iterations": 0,
            "research_complete": False,
            "error_messages": [],
            "web_search_results": [],
            "news_articles": [],
            "filing_chunks": [],
            "retrieved_context": [],
            "identified_risks": [],
            "risk_score": 0.0,
            "final_report": None,
        })
    except Exception as e:
        logger.error(f"Workflow failed for {company}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Workflow error: {str(e)}")

    report = result.get("final_report")

    if not report:
        raise HTTPException(
            status_code=500,
            detail="Workflow completed but no report was generated. "
                   "Check that your OPENROUTER_API_KEY is set and the model name is valid."
        )

    # Generate PDF only if requested
    pdf_b64 = None
    if request.include_pdf:
        try:
            from src.models.schemas import DueDiligenceReport
            report_obj = DueDiligenceReport(**report)
            pdf_bytes = generate_pdf(report_obj)
            pdf_b64 = base64.b64encode(pdf_bytes).decode("utf-8")
            logger.info(f"PDF generated for {company}: {len(pdf_bytes):,} bytes")
        except Exception as e:
            # PDF failure should not kill the whole response
            logger.warning(f"PDF generation failed for {company}: {e}")

    return AnalyzeResponse(
        company=company,
        report=report,
        pdf_base64=pdf_b64,
        processing_time_seconds=round(time.time() - start, 2),
    )