from fastapi import APIRouter
from src.graph.workflow import build_workflow

router = APIRouter()

# build once at startup
workflow = build_workflow()


@router.post("/analyze")
def analyze(payload: dict):
    """
    Run full AI workflow.
    """
    company = payload.get("company_name")

    result = workflow.invoke({
        "company_name": company
    })

    return {
        "company": company,
        "report": result.get("final_report")
    }