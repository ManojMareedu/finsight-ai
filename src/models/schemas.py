# src/models/schemas.py
import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class InvestmentSignal(str, Enum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    HOLD = "HOLD"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class RiskFactor(BaseModel):
    category: str = Field(description="E.g. Market Risk, Regulatory Risk")
    description: str = Field(description="What the risk is")
    severity: RiskLevel
    source_citation: str = Field(description="Where this risk was found")


class FinancialSnapshot(BaseModel):
    revenue_trend: str
    profitability_summary: str
    debt_assessment: str
    key_metrics: dict[str, str] = Field(
        description="E.g. {'P/E Ratio': '25.4', 'Revenue Growth': '8.2%'}"
    )


class DueDiligenceReport(BaseModel):
    company_name: str
    report_date: str = Field(
        default_factory=lambda: datetime.date.today().isoformat()
    )
    executive_summary: str = Field(description="3-4 sentences, no jargon")
    financial_snapshot: FinancialSnapshot
    risk_factors: list[RiskFactor]
    competitive_position: str
    recent_developments: list[str]
    investment_signal: InvestmentSignal
    confidence_score: float = Field(ge=0.0, le=1.0)
    data_sources_used: list[str]
    disclaimer: str = Field(
    default=(
        "This report is AI-generated based on the most recent SEC 10-K filing available. "
        "Financial data reflects the filing date shown above, not today's date. "
        "Company circumstances may have changed since that filing. "
        "This does not constitute financial advice."
        )
    )


class AnalyzeRequest(BaseModel):
    company_name: str = Field(min_length=2, max_length=100)
    company_ticker: Optional[str] = None
    include_pdf: bool = False


class AnalyzeResponse(BaseModel):
    company: str
    report: DueDiligenceReport
    pdf_base64: Optional[str] = None
    processing_time_seconds: float