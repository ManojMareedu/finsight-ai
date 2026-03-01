from pydantic import BaseModel
from typing import List


class RiskItem(BaseModel):
    category: str
    description: str
    severity: float


class RiskAnalysis(BaseModel):
    risks: List[RiskItem]
    overall_risk_score: float


class FinalReport(BaseModel):
    executive_summary: str
    key_risks: list[str]
    overall_risk_score: float
    recommendation: str